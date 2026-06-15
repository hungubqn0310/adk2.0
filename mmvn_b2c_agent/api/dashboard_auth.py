"""
Dashboard authentication: login, JWT, user management.
Uses dashboard_users table (auto-created) + pyjwt.
Password hashing via hashlib.pbkdf2_hmac (no extra deps).

Security features:
- Concurrent session management: mỗi user chỉ có 1 phiên active (login mới = kick phiên cũ)
- Progressive lockout: sai 3 lần → khóa 30s, mỗi lần sai thêm +30s
- Same-IP kick detection: cùng IP đăng xuất im lặng, khác IP hiện cảnh báo
"""
import hashlib
import hmac
import logging
import os
import random
import secrets
import smtplib
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import re
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

SESSION_SERVICE_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("DASHBOARD_JWT_EXPIRE_HOURS", "24"))

_SYSTEM_NAME = os.getenv("DASHBOARD_SYSTEM_NAME", "AI Chatbot B2C MMVN")
_DASHBOARD_URL = os.getenv("DASHBOARD_BASE_URL", "")

_jwt_secret_from_env = os.getenv("DASHBOARD_JWT_SECRET")
if not _jwt_secret_from_env:
    logger.warning(
        "DASHBOARD_JWT_SECRET not set — using a random secret. "
        "All tokens will be invalidated on server restart."
    )
JWT_SECRET: str = _jwt_secret_from_env or secrets.token_hex(32)

# Lockout config
LOCKOUT_THRESHOLD = 3        # sai bao nhiêu lần thì bắt đầu khóa
LOCKOUT_STEP_SECONDS = 30    # mỗi lần sai thêm sau threshold → cộng thêm 30s

# Kicked-session TTL: giữ record trong bao lâu để phát hiện cùng/khác IP
KICKED_SESSION_TTL_MINUTES = 5

# Password reset OTP
RESET_TOKEN_EXPIRE_MINUTES = 60

dashboard_auth_router = APIRouter(prefix="/dashboard/auth", tags=["dashboard-auth"])
bearer_scheme = HTTPBearer(auto_error=False)

# Role → allowed page keys
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin":          ["overview", "quality", "performance", "config"],
    "business_owner": ["overview", "quality", "performance"],
}

ROLES = list(ROLE_PERMISSIONS.keys())

# Whitelist các field được phép UPDATE trong bảng dashboard_users
_USER_UPDATE_FIELDS: dict[str, str] = {
    "full_name": "full_name = :full_name",
    "email":     "email = :email",
    "role":      "role = :role",
    "is_active": "is_active = :is_active",
}


# ──────────────────────────── DB engine (singleton) ────────────────────────────

_engine: Optional[Engine] = None


def _get_engine() -> Engine:
    """Trả về engine dùng chung — tạo một lần, tái dùng connection pool."""
    global _engine
    if _engine is None:
        is_sqlite = SESSION_SERVICE_URI.startswith("sqlite")
        connect_args = {"check_same_thread": False} if is_sqlite else {}
        _engine = create_engine(
            SESSION_SERVICE_URI,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
    return _engine


def _is_pg() -> bool:
    return SESSION_SERVICE_URI.startswith("postgresql")


# ──────────────────────────── Schema init ────────────────────────────

def _ensure_table_exists() -> None:
    engine = _get_engine()
    pg = _is_pg()
    with engine.connect() as conn:
        _create_users_table(conn, pg)
        _create_sessions_table(conn, pg)
        _create_kicked_sessions_table(conn, pg)
        _create_login_attempts_table(conn, pg)
        _create_password_resets_table(conn, pg)
        conn.commit()


def _create_users_table(conn: Connection, pg: bool) -> None:
    if pg:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_users (
                id            SERIAL PRIMARY KEY,
                username      VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                salt          VARCHAR(64) NOT NULL,
                role          VARCHAR(32) NOT NULL DEFAULT 'business_owner',
                full_name     VARCHAR(128) NOT NULL DEFAULT '',
                email         VARCHAR(255),
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS email VARCHAR(255)"
        ))
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'business_owner',
                full_name     TEXT NOT NULL DEFAULT '',
                email         TEXT,
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        try:
            conn.execute(text("ALTER TABLE dashboard_users ADD COLUMN email TEXT"))
        except Exception:
            pass  # Cột đã tồn tại


def _create_sessions_table(conn: Connection, pg: bool) -> None:
    if pg:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_sessions (
                username   VARCHAR(64) PRIMARY KEY,
                jti        VARCHAR(64) UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                login_ip   VARCHAR(64),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "ALTER TABLE dashboard_sessions ADD COLUMN IF NOT EXISTS login_ip VARCHAR(64)"
        ))
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_sessions (
                username   TEXT PRIMARY KEY,
                jti        TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                login_ip   TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """))
        # SQLite migration: thêm cột login_ip nếu chưa có (không support IF NOT EXISTS)
        try:
            conn.execute(text("ALTER TABLE dashboard_sessions ADD COLUMN login_ip TEXT"))
        except Exception:
            pass  # Cột đã tồn tại


def _create_kicked_sessions_table(conn: Connection, pg: bool) -> None:
    if pg:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_kicked_sessions (
                jti          VARCHAR(64) PRIMARY KEY,
                kicked_by_ip VARCHAR(64),
                kicked_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_kicked_sessions (
                jti          TEXT PRIMARY KEY,
                kicked_by_ip TEXT,
                kicked_at    TEXT DEFAULT (datetime('now'))
            )
        """))


def _create_login_attempts_table(conn: Connection, pg: bool) -> None:
    if pg:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_login_attempts (
                username        VARCHAR(64) PRIMARY KEY,
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                locked_until    TIMESTAMP,
                last_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_login_attempts (
                username        TEXT PRIMARY KEY,
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                locked_until    TEXT,
                last_attempt_at TEXT DEFAULT (datetime('now'))
            )
        """))


def _create_password_resets_table(conn: Connection, pg: bool) -> None:
    if pg:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_password_resets (
                id         SERIAL PRIMARY KEY,
                username   VARCHAR(64) NOT NULL,
                token      VARCHAR(8) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used       BOOLEAN NOT NULL DEFAULT FALSE
            )
        """))
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_password_resets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT NOT NULL,
                token      TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0
            )
        """))


def _smtp_send(to_email: str, subject: str, body: str, is_html: bool = False) -> None:
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    if not smtp_host or not smtp_user:
        raise ValueError("SMTP chưa được cấu hình (thiếu SMTP_HOST / SMTP_USER)")

    msg = MIMEText(body, "html" if is_html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [to_email], msg.as_string())


def _send_reset_email(to_email: str, username: str, token: str, full_name: str = "") -> None:
    """Template A: user tự yêu cầu đặt lại mật khẩu."""
    display_name = full_name or username

    expire_hours = RESET_TOKEN_EXPIRE_MINUTES // 60 or 1

    if _DASHBOARD_URL:
        encoded_email = urllib.parse.quote(to_email, safe="")
        reset_link = f"{_DASHBOARD_URL}/reset-password?token={token}&u={encoded_email}"
        action_line = f'<a href="{reset_link}">Đặt Lại Mật Khẩu</a>'
    else:
        action_line = f"Mã xác nhận: {token}"

    dashboard_line = (
        f"<br>Để truy cập nhanh vào bảng điều khiển (Dashboard) theo dõi chỉ số hoặc cấu hình "
        f'cài đặt hệ thống, bạn có thể lưu lại liên kết sau: <a href="{_DASHBOARD_URL}">{_DASHBOARD_URL}</a>'
    ) if _DASHBOARD_URL else ""

    body = (
        f"Dear {display_name},<br><br>"
        f"Hệ thống vừa nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn tại {_SYSTEM_NAME}.<br><br>"
        f"Để tiếp tục truy cập vào trang quản lý và theo dõi các chỉ số hoạt động, vui lòng "
        f"nhấn vào liên kết bên dưới để thiết lập mật khẩu mới:<br><br>"
        f"{action_line}<br><br>"
        f"(Lưu ý: Liên kết này chỉ có hiệu lực trong vòng {expire_hours} giờ "
        f"để đảm bảo tính bảo mật. Nếu bạn không yêu cầu thay đổi mật khẩu, "
        f"vui lòng bỏ qua email này)."
        f"{dashboard_line}<br><br>"
        f"Trân trọng,<br>"
        f"Admin"
    )
    _smtp_send(to_email, f"Yêu cầu đặt lại mật khẩu tài khoản - {_SYSTEM_NAME}", body, is_html=True)


def _send_admin_reset_email(to_email: str, username: str, temp_password: str, full_name: str = "") -> None:
    """Template B: admin hỗ trợ thiết lập lại mật khẩu."""
    display_name = full_name or username
    dashboard_link_html = (
        f'<a href="{_DASHBOARD_URL}">{_DASHBOARD_URL}</a>'
        if _DASHBOARD_URL
        else "(liên hệ admin để lấy link đăng nhập)"
    )

    body = (
        f"Dear {display_name},<br><br>"
        f"Quản trị viên vừa hỗ trợ cấp lại mật khẩu cho tài khoản truy cập hệ thống "
        f"<strong>{_SYSTEM_NAME}</strong> của bạn.<br><br>"
        f"Dưới đây là thông tin đăng nhập tạm thời của bạn:<br>"
        f"<strong>Tên đăng nhập:</strong> {username}<br>"
        f"<strong>Mật khẩu tạm thời:</strong> {temp_password}<br><br>"
        f"Để đảm bảo an toàn thông tin và tiếp tục theo dõi các chỉ số vận hành của hệ thống "
        f"E-commerce, bạn vui lòng sử dụng thông tin trên để đăng nhập qua liên kết dưới đây "
        f"và <strong>chủ động đổi lại mật khẩu mới</strong> ngay trong lần truy cập đầu tiên:<br><br>"
        f"{dashboard_link_html}<br><br>"
        f"Trân trọng,<br>"
        f"Admin"
    )
    _smtp_send(
        to_email,
        f"Thông tin đăng nhập tài khoản của bạn đã được cập nhật - {_SYSTEM_NAME}",
        body,
        is_html=True,
    )


def _seed_default_admin() -> None:
    """Tạo admin mặc định từ env nếu chưa có user nào."""
    admin_user = os.getenv("DASHBOARD_ADMIN_USER", "admin")
    admin_pass = os.getenv("DASHBOARD_ADMIN_PASSWORD", "admin123")

    if admin_pass == "admin123":
        logger.warning(
            "Using default admin password 'admin123'. "
            "Set DASHBOARD_ADMIN_PASSWORD in environment for production."
        )

    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM dashboard_users WHERE username = :u"),
            {"u": admin_user},
        ).fetchone()
        if not row:
            salt, hashed = _hash_password(admin_pass)
            conn.execute(text("""
                INSERT INTO dashboard_users (username, password_hash, salt, role, full_name)
                VALUES (:u, :h, :s, 'admin', 'Admin')
            """), {"u": admin_user, "h": hashed, "s": salt})
            conn.commit()
            logger.info("Seeded default admin user: %s", admin_user)


def init_dashboard_auth() -> None:
    """Gọi từ startup để khởi tạo bảng + seed admin."""
    try:
        _ensure_table_exists()
        _seed_default_admin()
    except Exception as exc:
        logger.error("dashboard_auth init failed: %s", exc)


# ──────────────────────────── Password ────────────────────────────

def _hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()
    return salt, hashed


def _verify_password(password: str, salt: str, stored_hash: str) -> bool:
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()
    return hmac.compare_digest(candidate, stored_hash)


# ──────────────────────────── JWT ────────────────────────────

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _create_token(username: str, role: str, full_name: str, login_ip: str = "") -> str:
    jti = str(uuid.uuid4())
    exp = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub":         username,
        "role":        role,
        "full_name":   full_name,
        "permissions": ROLE_PERMISSIONS.get(role, []),
        "exp":         exp,
        "jti":         jti,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    _upsert_session(username, jti, exp, login_ip)
    return token


def _upsert_session(
    username: str,
    jti: str,
    expires_at: datetime,
    login_ip: str = "",
) -> None:
    """Ghi đè phiên cũ bằng phiên mới — đảm bảo chỉ 1 phiên active mỗi user."""
    pg = _is_pg()
    exp_str = expires_at.isoformat()
    engine = _get_engine()
    with engine.connect() as conn:
        # Lưu JTI cũ vào kicked_sessions trước khi overwrite
        old_row = conn.execute(
            text("SELECT jti FROM dashboard_sessions WHERE username = :u"),
            {"u": username},
        ).fetchone()
        if old_row:
            _record_kicked_session(conn, old_jti=old_row.jti, kicked_by_ip=login_ip, pg=pg)

        if pg:
            conn.execute(text("""
                INSERT INTO dashboard_sessions (username, jti, expires_at, login_ip)
                VALUES (:u, :jti, :exp, :ip)
                ON CONFLICT (username) DO UPDATE
                    SET jti = :jti, expires_at = :exp, login_ip = :ip, created_at = NOW()
            """), {"u": username, "jti": jti, "exp": expires_at, "ip": login_ip})
        else:
            conn.execute(text("""
                INSERT OR REPLACE INTO dashboard_sessions (username, jti, expires_at, login_ip)
                VALUES (:u, :jti, :exp, :ip)
            """), {"u": username, "jti": jti, "exp": exp_str, "ip": login_ip})

        conn.commit()


def _record_kicked_session(
    conn: Connection,
    old_jti: str,
    kicked_by_ip: str,
    pg: bool = False,
) -> None:
    """Ghi tạm JTI bị kick để phân biệt cùng IP / khác IP. Tự dọn record cũ hơn TTL."""
    if pg:
        conn.execute(text("""
            INSERT INTO dashboard_kicked_sessions (jti, kicked_by_ip)
            VALUES (:jti, :ip)
            ON CONFLICT (jti) DO UPDATE SET kicked_by_ip = :ip, kicked_at = NOW()
        """), {"jti": old_jti, "ip": kicked_by_ip})
        conn.execute(text(
            f"DELETE FROM dashboard_kicked_sessions "
            f"WHERE kicked_at < NOW() - INTERVAL '{KICKED_SESSION_TTL_MINUTES} minutes'"
        ))
    else:
        conn.execute(text("""
            INSERT OR REPLACE INTO dashboard_kicked_sessions (jti, kicked_by_ip)
            VALUES (:jti, :ip)
        """), {"jti": old_jti, "ip": kicked_by_ip})
        conn.execute(text(
            f"DELETE FROM dashboard_kicked_sessions "
            f"WHERE kicked_at < datetime('now', '-{KICKED_SESSION_TTL_MINUTES} minutes')"
        ))


def _revoke_session(jti: str) -> None:
    """Xóa session khi logout — token bị từ chối ngay lập tức."""
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM dashboard_sessions WHERE jti = :jti"), {"jti": jti})
        conn.execute(text("DELETE FROM dashboard_kicked_sessions WHERE jti = :jti"), {"jti": jti})
        conn.commit()


def _parse_utc(value: object) -> Optional[datetime]:
    """Parse datetime từ DB (hỗ trợ cả str và datetime object), đảm bảo UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _decode_token(token: str, client_ip: str = "") -> dict:
    try:
        payload: dict = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token đã hết hạn")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token không hợp lệ")

    jti: Optional[str] = payload.get("jti")
    if not jti:
        # Token hợp lệ nhưng không có jti — từ chối để đảm bảo session có thể revoke
        raise HTTPException(status_code=401, detail="Token không hợp lệ")

    engine = _get_engine()
    row = None
    kicked_row = None
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT jti FROM dashboard_sessions WHERE jti = :jti"),
            {"jti": jti},
        ).fetchone()
        if not row:
            try:
                kicked_row = conn.execute(
                    text("SELECT kicked_by_ip FROM dashboard_kicked_sessions WHERE jti = :jti"),
                    {"jti": jti},
                ).fetchone()
            except Exception:
                # Bảng chưa tồn tại hoặc lỗi DB — fallback hiện cảnh báo
                kicked_row = None

    if not row:
        same_ip = (
            kicked_row is not None
            and bool(client_ip)
            and kicked_row.kicked_by_ip == client_ip
        )
        if same_ip:
            raise HTTPException(status_code=401, detail="SESSION_KICKED_SAME_IP")
        raise HTTPException(
            status_code=401,
            detail="Phiên đăng nhập không còn hợp lệ. Tài khoản đã được đăng nhập từ thiết bị khác.",
        )

    return payload


# ──────────────────────────── Dependency ────────────────────────────

def get_current_dashboard_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    return _decode_token(credentials.credentials, _get_client_ip(request))


def require_permission(page: str):
    """Dependency factory: kiểm tra quyền truy cập page."""
    def _check(user: dict = Depends(get_current_dashboard_user)) -> dict:
        if page not in user.get("permissions", []):
            raise HTTPException(status_code=403, detail=f"Không có quyền truy cập '{page}'")
        return user
    return _check


# ──────────────────────────── Progressive Lockout ────────────────────────────

def _check_lockout(username: str) -> None:
    """Raise 429 nếu user đang trong thời gian khóa."""
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT locked_until FROM dashboard_login_attempts WHERE username = :u"),
            {"u": username},
        ).fetchone()

    if not row or not row.locked_until:
        return

    locked_until = _parse_utc(row.locked_until)
    if locked_until and locked_until > datetime.now(timezone.utc):
        retry_after = int((locked_until - datetime.now(timezone.utc)).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=f"Tài khoản tạm thời bị khóa. Vui lòng thử lại sau {retry_after} giây.",
            headers={"Retry-After": str(retry_after)},
        )


def _record_failed_attempt(username: str) -> None:
    """Tăng bộ đếm sai. Sau LOCKOUT_THRESHOLD lần → khóa lũy tiến."""
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT attempt_count FROM dashboard_login_attempts WHERE username = :u"),
            {"u": username},
        ).fetchone()

        new_count = (row.attempt_count if row else 0) + 1
        locked_until: Optional[datetime] = None

        if new_count >= LOCKOUT_THRESHOLD:
            wait_seconds = (new_count - LOCKOUT_THRESHOLD + 1) * LOCKOUT_STEP_SECONDS
            locked_until = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
            logger.warning(
                "Login lockout: user=%s, attempts=%d, locked=%ds",
                username, new_count, wait_seconds,
            )

        locked_str = locked_until.isoformat() if locked_until else None

        if row:
            conn.execute(text("""
                UPDATE dashboard_login_attempts
                SET attempt_count = :c, locked_until = :lu, last_attempt_at = CURRENT_TIMESTAMP
                WHERE username = :u
            """), {"c": new_count, "lu": locked_str, "u": username})
        else:
            conn.execute(text("""
                INSERT INTO dashboard_login_attempts (username, attempt_count, locked_until)
                VALUES (:u, :c, :lu)
            """), {"u": username, "c": new_count, "lu": locked_str})

        conn.commit()


def _reset_attempts(username: str) -> None:
    """Xóa record lockout sau khi đăng nhập thành công."""
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM dashboard_login_attempts WHERE username = :u"),
            {"u": username},
        )
        conn.commit()


# ──────────────────────────── Schemas ────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    full_name: str
    permissions: list[str]


class ForgotPasswordRequest(BaseModel):
    username: str


class ResetPasswordRequest(BaseModel):
    username: str
    token: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str
    full_name: str
    email: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    full_name: str
    email: Optional[str]
    is_active: bool
    created_at: Optional[str]


# ──────────────────────────── Endpoints ────────────────────────────

@dashboard_auth_router.post("/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    _check_lockout(body.username)

    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT username, password_hash, salt, role, full_name, is_active "
                "FROM dashboard_users WHERE username = :u OR (email = :u AND email IS NOT NULL)"
            ),
            {"u": body.username},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập / email hoặc mật khẩu")
    if not row.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản đã bị vô hiệu hoá")
    if not _verify_password(body.password, row.salt, row.password_hash):
        _record_failed_attempt(row.username)
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập / email hoặc mật khẩu")

    _reset_attempts(row.username)
    token = _create_token(row.username, row.role, row.full_name, _get_client_ip(request))
    return LoginResponse(
        access_token=token,
        username=row.username,
        role=row.role,
        full_name=row.full_name,
        permissions=ROLE_PERMISSIONS.get(row.role, []),
    )


@dashboard_auth_router.post("/logout", status_code=204)
async def logout(user: dict = Depends(get_current_dashboard_user)) -> None:
    """Hủy phiên hiện tại — token sẽ bị từ chối ngay lập tức."""
    jti = user.get("jti")
    if jti:
        _revoke_session(jti)


@dashboard_auth_router.get("/me")
async def get_me(user: dict = Depends(get_current_dashboard_user)) -> dict:
    return user


@dashboard_auth_router.get("/users", response_model=list[UserResponse])
async def list_users(user: dict = Depends(require_permission("config"))) -> list[UserResponse]:
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, username, role, full_name, email, is_active, created_at "
                "FROM dashboard_users ORDER BY id"
            )
        ).fetchall()
    return [
        UserResponse(
            id=r.id,
            username=r.username,
            role=r.role,
            full_name=r.full_name,
            email=r.email,
            is_active=r.is_active,
            created_at=str(r.created_at) if r.created_at else None,
        )
        for r in rows
    ]


@dashboard_auth_router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    user: dict = Depends(require_permission("config")),
) -> UserResponse:
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Role không hợp lệ. Các role: {ROLES}")

    salt, hashed = _hash_password(body.password)
    params = {"u": body.username, "h": hashed, "s": salt, "r": body.role, "fn": body.full_name, "em": body.email}
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            if _is_pg():
                row = conn.execute(text("""
                    INSERT INTO dashboard_users (username, password_hash, salt, role, full_name, email)
                    VALUES (:u, :h, :s, :r, :fn, :em)
                    RETURNING id, username, role, full_name, email, is_active, created_at
                """), params).fetchone()
                conn.commit()
            else:
                conn.execute(text("""
                    INSERT INTO dashboard_users (username, password_hash, salt, role, full_name, email)
                    VALUES (:u, :h, :s, :r, :fn, :em)
                """), params)
                conn.commit()
                row = conn.execute(
                    text(
                        "SELECT id, username, role, full_name, email, is_active, created_at "
                        "FROM dashboard_users WHERE username = :u"
                    ),
                    {"u": body.username},
                ).fetchone()
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Username đã tồn tại")
        raise

    return UserResponse(
        id=row.id,
        username=row.username,
        role=row.role,
        full_name=row.full_name,
        email=row.email,
        is_active=row.is_active,
        created_at=str(row.created_at) if row.created_at else None,
    )


@dashboard_auth_router.delete("/users/{username}", status_code=204)
async def delete_user(
    username: str,
    user: dict = Depends(require_permission("config")),
) -> None:
    if username == user["sub"]:
        raise HTTPException(status_code=400, detail="Không thể xoá chính mình")
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM dashboard_users WHERE username = :u"), {"u": username})
        conn.commit()


@dashboard_auth_router.patch("/users/{username}", status_code=204)
async def update_user(
    username: str,
    body: dict,
    user: dict = Depends(get_current_dashboard_user),
) -> None:
    """Admin sửa được hết; user thường chỉ sửa full_name của chính mình."""
    is_admin = user["role"] == "admin"
    is_self  = user["sub"] == username

    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail="Không có quyền cập nhật user này")

    set_clauses: list[str] = []
    params: dict = {"u": username}

    if "full_name" in body:
        set_clauses.append(_USER_UPDATE_FIELDS["full_name"])
        params["full_name"] = body["full_name"]

    if "email" in body:
        set_clauses.append(_USER_UPDATE_FIELDS["email"])
        params["email"] = body["email"] or None

    if is_admin:
        if "role" in body:
            if body["role"] not in ROLES:
                raise HTTPException(status_code=400, detail="Role không hợp lệ")
            set_clauses.append(_USER_UPDATE_FIELDS["role"])
            params["role"] = body["role"]
        if "is_active" in body:
            set_clauses.append(_USER_UPDATE_FIELDS["is_active"])
            params["is_active"] = bool(body["is_active"])

    if not set_clauses:
        return

    sql = f"UPDATE dashboard_users SET {', '.join(set_clauses)} WHERE username = :u"
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text(sql), params)
        conn.commit()


@dashboard_auth_router.patch("/users/{username}/password", status_code=204)
async def change_password(
    username: str,
    body: dict,
    user: dict = Depends(get_current_dashboard_user),
) -> None:
    if user["sub"] != username and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Không có quyền")
    new_pass: Optional[str] = body.get("password")
    if not new_pass or len(new_pass) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu tối thiểu 6 ký tự")
    salt, hashed = _hash_password(new_pass)
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE dashboard_users SET password_hash = :h, salt = :s WHERE username = :u"),
            {"h": hashed, "s": salt, "u": username},
        )
        conn.commit()


@dashboard_auth_router.post("/users/{username}/admin-reset", status_code=204)
async def admin_reset_password(
    username: str,
    user: dict = Depends(require_permission("config")),
) -> None:
    """Admin tự sinh mật khẩu tạm thời và gửi email thông báo cho user."""
    if username == user["sub"]:
        raise HTTPException(status_code=400, detail="Dùng đổi mật khẩu thông thường cho chính mình")

    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT username, email, full_name FROM dashboard_users WHERE username = :u AND is_active IS TRUE"),
            {"u": username},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    if not row.email:
        raise HTTPException(status_code=422, detail="Tài khoản chưa có email. Cập nhật email trước khi reset.")

    temp_password = secrets.token_urlsafe(9)  # ~12 ký tự URL-safe
    salt, hashed = _hash_password(temp_password)

    with engine.connect() as conn:
        conn.execute(
            text("UPDATE dashboard_users SET password_hash = :h, salt = :s WHERE username = :u"),
            {"h": hashed, "s": salt, "u": username},
        )
        conn.commit()

    try:
        _send_admin_reset_email(row.email, row.username, temp_password, full_name=row.full_name or "")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("Không thể gửi email admin-reset: %s", exc)
        raise HTTPException(status_code=500, detail="Không thể gửi email. Vui lòng liên hệ admin.")


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@dashboard_auth_router.post("/forgot-password", status_code=204)
async def forgot_password(body: ForgotPasswordRequest) -> None:
    """Gửi OTP 6 số đến email user đã đăng ký."""
    if not _EMAIL_RE.match(body.username.strip()):
        raise HTTPException(status_code=422, detail="Vui lòng nhập địa chỉ email hợp lệ")

    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT username, email, full_name FROM dashboard_users "
                "WHERE (username = :u OR (email = :u AND email IS NOT NULL)) AND is_active IS TRUE"
            ),
            {"u": body.username},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản. Vui lòng kiểm tra lại.")
    if not row.email:
        raise HTTPException(status_code=422, detail="Tài khoản chưa có email đăng ký. Liên hệ admin để cập nhật.")

    token = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)

    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM dashboard_password_resets WHERE username = :u"),
            {"u": row.username},
        )
        conn.execute(
            text("""
                INSERT INTO dashboard_password_resets (username, token, expires_at)
                VALUES (:u, :t, :e)
            """),
            {"u": row.username, "t": token, "e": expires_at.isoformat()},
        )
        conn.commit()

    try:
        _send_reset_email(row.email, row.username, token, full_name=row.full_name or "")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("Không thể gửi email reset: %s", exc)
        raise HTTPException(status_code=500, detail="Không thể gửi email. Vui lòng liên hệ admin.")


@dashboard_auth_router.post("/reset-password", status_code=204)
async def reset_password(body: ResetPasswordRequest) -> None:
    """Xác thực OTP và đặt mật khẩu mới."""
    if not body.password or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu tối thiểu 6 ký tự")

    engine = _get_engine()

    # Resolve username thực từ DB (body.username có thể là email)
    with engine.connect() as conn:
        user_row = conn.execute(
            text(
                "SELECT username FROM dashboard_users "
                "WHERE (username = :u OR (email = :u AND email IS NOT NULL)) AND is_active IS TRUE"
            ),
            {"u": body.username},
        ).fetchone()

    if not user_row:
        raise HTTPException(status_code=400, detail="Mã xác nhận không hợp lệ hoặc đã hết hạn")

    actual_username = user_row.username

    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT token, expires_at, used
                FROM dashboard_password_resets
                WHERE username = :u
                ORDER BY id DESC LIMIT 1
            """),
            {"u": actual_username},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="Mã xác nhận không hợp lệ hoặc đã hết hạn")

    if row.used:
        raise HTTPException(status_code=400, detail="Mã xác nhận đã được sử dụng")

    expires_at = _parse_utc(row.expires_at)
    if not expires_at or expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Mã xác nhận đã hết hạn")

    if not hmac.compare_digest(str(row.token), str(body.token)):
        raise HTTPException(status_code=400, detail="Mã xác nhận không đúng")

    salt, hashed = _hash_password(body.password)
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE dashboard_users SET password_hash = :h, salt = :s WHERE username = :u"),
            {"h": hashed, "s": salt, "u": actual_username},
        )
        conn.execute(
            text("UPDATE dashboard_password_resets SET used = TRUE WHERE username = :u"),
            {"u": actual_username},
        )
        conn.commit()
