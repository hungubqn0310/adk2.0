"""
OpenTelemetry Metrics Module for LLM Token Tracking

Module này cung cấp Counter metrics để track:
- Input tokens (tokens gửi vào LLM)
- Output tokens (tokens LLM generate ra)
- Total tokens (tổng input + output)
- Cached tokens (tokens từ prompt cache)
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any
from contextlib import contextmanager

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)


class LLMMetrics:
    """
    Centralized metrics manager cho LLM token tracking.

    Sử dụng Counter metrics để track tổng số tokens tiêu thụ.
    """

    def __init__(
        self,
        service_name: str = "mmvn_chatbot",
        endpoint: Optional[str] = None,
        export_interval_millis: int = 60000,  # Export mỗi 60 giây
        db_uri: Optional[str] = None,
    ):
        """
        Khởi tạo metrics provider.

        Args:
            service_name: Tên service (mặc định: mmvn_chatbot)
            endpoint: OTLP endpoint URL (vd: http://jaeger:4317)
            export_interval_millis: Chu kỳ export metrics (mặc định: 60s)
            db_uri: SQLAlchemy URI để persist token usage vào DB (optional)
        """
        self.service_name = service_name
        self.endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

        # Setup DB persistence
        self._db_engine = None
        _uri = db_uri or os.getenv("SESSION_SERVICE_URI")
        if _uri:
            self._setup_db(_uri)

        # Setup metrics provider
        self._setup_meter_provider(export_interval_millis)

        # Tạo meter
        self.meter = metrics.get_meter(__name__, version="1.0.0")

        # Khởi tạo metrics
        self._init_token_counters()
        self._init_request_counters()

    def _setup_db(self, db_uri: str) -> None:
        """Setup SQLAlchemy engine và tạo bảng token_usage nếu chưa có."""
        try:
            from sqlalchemy import create_engine, text
            self._db_engine = create_engine(db_uri, pool_pre_ping=True)
            with self._db_engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS token_usage (
                        id BIGSERIAL PRIMARY KEY,
                        billing_month TEXT NOT NULL,
                        model TEXT NOT NULL,
                        agent TEXT NOT NULL DEFAULT 'unknown',
                        session_id TEXT,
                        user_id TEXT,
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        cached_tokens INTEGER NOT NULL DEFAULT 0,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_token_usage_billing_month
                    ON token_usage (billing_month)
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_token_usage_session_id
                    ON token_usage (session_id)
                """))
                # Add columns if upgrading from older schema
                conn.execute(text("""
                    ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS session_id TEXT
                """))
                conn.execute(text("""
                    ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS user_id TEXT
                """))
                conn.commit()
            logger.info("[OTEL Metrics] token_usage table ready")
        except Exception as e:
            logger.warning(f"[OTEL Metrics] DB setup failed, tokens won't be persisted: {e}")
            self._db_engine = None

    def _setup_meter_provider(self, export_interval_millis: int):
        """Setup OpenTelemetry MeterProvider với OTLP exporter."""
        resource = Resource(attributes={
            "service.name": self.service_name,
            "service.version": "1.0.0",
        })

        if self.endpoint:
            # Cấu hình OTLP exporter
            exporter = OTLPMetricExporter(
                endpoint=self.endpoint,
                insecure=True  # Không dùng TLS cho local development
            )
            reader = PeriodicExportingMetricReader(
                exporter=exporter,
                export_interval_millis=export_interval_millis,
            )
            provider = MeterProvider(resource=resource, metric_readers=[reader])
            print(f"[OTEL Metrics] Initialized with endpoint: {self.endpoint}")
        else:
            # Không có exporter - metrics vẫn được collect nhưng không export
            provider = MeterProvider(resource=resource)
            print("[OTEL Metrics] Initialized without exporter (metrics collected locally)")

        metrics.set_meter_provider(provider)

    def _init_token_counters(self):
        """Khởi tạo Counter metrics cho tokens."""
        # Counter: Tổng input tokens
        self.input_tokens_counter = self.meter.create_counter(
            name="llm.tokens.input",
            description="Total number of input tokens sent to LLM",
            unit="tokens",
        )

        # Counter: Tổng output tokens
        self.output_tokens_counter = self.meter.create_counter(
            name="llm.tokens.output",
            description="Total number of output tokens generated by LLM",
            unit="tokens",
        )

        # Counter: Tổng tất cả tokens (input + output)
        self.total_tokens_counter = self.meter.create_counter(
            name="llm.tokens.total",
            description="Total number of tokens (input + output)",
            unit="tokens",
        )

        # Counter: Cached tokens (từ prompt caching)
        self.cached_tokens_counter = self.meter.create_counter(
            name="llm.tokens.cached",
            description="Total number of cached tokens reused from prompt cache",
            unit="tokens",
        )

    def _init_request_counters(self):
        """Khởi tạo Counter metrics cho LLM requests."""
        # Counter: Tổng số requests
        self.request_counter = self.meter.create_counter(
            name="llm.requests.total",
            description="Total number of LLM requests",
            unit="requests",
        )

        # Counter: Số requests lỗi
        self.request_errors_counter = self.meter.create_counter(
            name="llm.requests.errors",
            description="Total number of failed LLM requests",
            unit="errors",
        )

    # ===== Token Tracking Methods =====

    def record_tokens(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        cached_tokens: int = 0,
        agent_name: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **extra_labels,
    ):
        """
        Record tokens từ một LLM call.

        Args:
            input_tokens: Số input tokens
            output_tokens: Số output tokens
            model: Tên model (vd: 'gemini-2.5-flash')
            cached_tokens: Số tokens được cache (nếu có)
            agent_name: Tên agent gọi LLM (optional)
            **extra_labels: Thêm labels tùy chỉnh

        Example:
            metrics.record_tokens(
                input_tokens=150,
                output_tokens=50,
                model="gemini-2.5-flash",
                cached_tokens=100,
                agent_name="cng_agent"
            )
        """
        # Tạo attributes cho metrics
        attributes = {
            "model": model,
            "service": self.service_name,
            "billing_month": datetime.now().strftime("%Y-%m"),  # Thêm billing month (vd: 2025-11)
        }
        if agent_name:
            attributes["agent"] = agent_name
        if extra_labels:
            attributes.update(extra_labels)

        # Record counters
        self.input_tokens_counter.add(input_tokens, attributes=attributes)
        self.output_tokens_counter.add(output_tokens, attributes=attributes)
        self.total_tokens_counter.add(input_tokens + output_tokens, attributes=attributes)

        # Record cached tokens nếu có
        if cached_tokens > 0:
            self.cached_tokens_counter.add(cached_tokens, attributes=attributes)

        # Persist to DB for durability across restarts
        if self._db_engine:
            try:
                from sqlalchemy import text
                with self._db_engine.connect() as conn:
                    conn.execute(text("""
                        INSERT INTO token_usage
                            (billing_month, model, agent, session_id, user_id, input_tokens, output_tokens, cached_tokens)
                        VALUES
                            (:billing_month, :model, :agent, :session_id, :user_id, :input_tokens, :output_tokens, :cached_tokens)
                    """), {
                        "billing_month": attributes["billing_month"],
                        "model": model,
                        "agent": agent_name or "unknown",
                        "session_id": session_id,
                        "user_id": user_id,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cached_tokens": cached_tokens,
                    })
                    conn.commit()
            except Exception as e:
                logger.warning(f"[OTEL Metrics] Failed to persist tokens to DB: {e}")

        # Fire-and-forget: check token alert thresholds after each usage record
        try:
            import asyncio as _asyncio
            from mmvn_b2c_agent.shared.alert_mailer import check_and_notify_token_thresholds
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.run_in_executor(None, check_and_notify_token_thresholds)
            else:
                check_and_notify_token_thresholds()
        except Exception as e:
            logger.debug("[OTEL Metrics] Token threshold check skipped: %s", e)

    def record_request(
        self,
        model: str,
        success: bool = True,
        agent_name: Optional[str] = None,
        **extra_labels,
    ):
        """
        Record một LLM request.

        Args:
            model: Tên model
            success: Request thành công hay không
            agent_name: Tên agent (optional)
            **extra_labels: Labels tùy chỉnh
        """
        attributes = {
            "model": model,
            "success": str(success),
            "service": self.service_name,
            "billing_month": datetime.now().strftime("%Y-%m"),  # Thêm billing month
        }
        if agent_name:
            attributes["agent"] = agent_name
        if extra_labels:
            attributes.update(extra_labels)

        # Record request count
        self.request_counter.add(1, attributes=attributes)

        # Record error nếu failed
        if not success:
            self.request_errors_counter.add(1, attributes=attributes)

    # ===== Context Manager =====

    @contextmanager
    def track_request(
        self,
        model: str,
        agent_name: Optional[str] = None,
        **extra_labels,
    ):
        """
        Context manager để tự động track request success/failure.

        Usage:
            with metrics.track_request("gemini-2.5-flash", agent_name="cng_agent"):
                response = await llm.generate(prompt)
                metrics.record_tokens(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model="gemini-2.5-flash"
                )
        """
        success = True
        try:
            yield
        except Exception:
            success = False
            raise
        finally:
            self.record_request(model, success, agent_name, **extra_labels)


# Global metrics instance (singleton pattern)
_metrics_instance: Optional[LLMMetrics] = None


def get_metrics(
    service_name: str = "mmvn_chatbot",
    endpoint: Optional[str] = None,
) -> LLMMetrics:
    """
    Lấy global metrics instance (singleton).

    Args:
        service_name: Service name
        endpoint: OTLP endpoint (nếu None sẽ lấy từ env OTEL_EXPORTER_OTLP_ENDPOINT)

    Returns:
        Global LLMMetrics instance

    Example:
        metrics = get_metrics()
        metrics.record_tokens(input_tokens=100, output_tokens=50, model="gemini-2.5-flash")
    """
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = LLMMetrics(service_name=service_name, endpoint=endpoint)
    return _metrics_instance


def reset_metrics():
    """Reset global metrics instance (chủ yếu dùng cho testing)."""
    global _metrics_instance
    _metrics_instance = None
