#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from functools import partial

import click
import dotenv
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app

import mmvn_b2c_agent.api
from mmvn_b2c_agent.shared.budget_monitor import check_and_notify_budget
from mmvn_b2c_agent.shared.prompts_builder import build_agent_card_from_json
from scripts.setup_rag import setup_rag

dotenv.load_dotenv(override=True)

# Enable debug logging for google.genai to see raw HTTP responses
logging.basicConfig(level=logging.DEBUG)
logging.getLogger('google.genai').setLevel(logging.DEBUG)
logging.getLogger('google.adk').setLevel(logging.DEBUG)
# Suppress OTel "Token was created in a different Context" noise from asyncio.create_task
logging.getLogger('opentelemetry').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
DEFAULT_SESSION_SERVICE_URI = "sqlite:///./data/sessions.db"
DEFAULT_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ALLOWED_ORIGINS = ["http://localhost", "http://localhost:8080", "*"]
DEFAULT_SERVE_WEB_INTERFACE = False
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 8000
DEFAULT_A2A_APP_NAME = "mmvn_b2c_agent"


def setup_a2a(app: FastAPI, app_name: str, a2a_host: str = DEFAULT_HOST):
    # Create A2A components
    print("Setting up a2a")
    import importlib

    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
    from google.adk import Runner
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.auth.credential_service.in_memory_credential_service import (
        InMemoryCredentialService,
    )
    from google.adk.memory import InMemoryMemoryService
    from google.adk.sessions import InMemorySessionService
    # import root agent from app_name
    try:
        root_agent_module = importlib.import_module(app_name)
        root_agent = root_agent_module.root_agent
        print("OK")
    except (ImportError, AttributeError) as e:
        print(f"Failed to import root_agent from app name {app_name}: {e}")
        raise
    base_a2a_url = "/a2a"
    rpc_path = base_a2a_url
    agent_card_path = f"{base_a2a_url}{AGENT_CARD_WELL_KNOWN_PATH}"
    # extend_agent_card_path = f"{base_a2a_url}{EXTENDED_AGENT_CARD_PATH}"
    task_store = InMemoryTaskStore()

    agent_executor = A2aAgentExecutor(
        runner=Runner(
            app_name=root_agent.name or "adk_agent",
            agent=root_agent,
            # Use minimal services - in a real implementation these could be configured
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            credential_service=InMemoryCredentialService(),
        ),
    )
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor, task_store=task_store
    )

    # build the agent card
    card_builder = AgentCardBuilder(
        agent=root_agent,
        rpc_url=f"{a2a_host.rstrip('/')}{rpc_path}",
    )
    agent_card = asyncio.run(card_builder.build())
    agent_card = build_agent_card_from_json("mmvn_b2c_agent/shared/AgentCard.json")
    # Create the A2A Starlette application
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    routes = a2a_app.routes(
        rpc_url=rpc_path,
        agent_card_url=agent_card_path,
    )
    for new_route in routes:
        app.router.routes.append(new_route)
    logger.info("Successfully configured A2A", )


@click.command(context_settings={"auto_envvar_prefix": "CLI"})
@click.option('--host', default=DEFAULT_HOST, help='Host to run the ADK app on')
@click.option('--a2a', default=False, is_flag=True, help='Enable A2A functionality')
@click.option('--a2a_app_name', default=None,
              help='App name to enable A2A functionality for, required if --a2a is set', )
@click.option('--a2a_host', default=None, required=False,
              help='A2A RPC URL, default to http://host:port if not set. '
                   'Example value: http://localhost:8001 or https://example.com')
@click.option('--port', default=DEFAULT_PORT, help='Port to run the ADK app on')
@click.option('--session_service_uri', default=None, help='URI for the session service')
@click.option('--agent_dir', default=DEFAULT_AGENT_DIR, help='Directory containing agent files')
@click.option('--allow_origins', default=DEFAULT_ALLOWED_ORIGINS, help='Allowed origins for CORS')
@click.option('--web', is_flag=True, default=DEFAULT_SERVE_WEB_INTERFACE, help='Serve the web interface')
def main(host, a2a, a2a_app_name, a2a_host, port, session_service_uri, agent_dir, allow_origins, web):
    if a2a and not a2a_host:
        if not (a2a_host := os.getenv('A2A_HOST')):
            raise click.UsageError("--a2a_host must be provided when --a2a is set")
    if a2a and not a2a_app_name:
        if not (a2a_app_name := os.getenv('A2A_APP_NAME')):
            raise click.UsageError("--a2a_app_name must be provided when --a2a is set")

    if not session_service_uri:
        if not (session_service_uri := os.getenv('SESSION_SERVICE_URI')):
            session_service_uri = DEFAULT_SESSION_SERVICE_URI

    # ADK 2.0: DatabaseSessionService dùng ASYNC engine → cần asyncpg.
    # Nhưng các helper create_engine() (migrations, delete_empty_sessions) là SYNC → cần psycopg2.
    # Giữ session_service_uri (sync) cho create_engine; derive bản async cho ADK services.
    adk_session_uri = session_service_uri
    for _pre in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://", "postgres://"):
        if adk_session_uri.startswith(_pre):
            adk_session_uri = "postgresql+asyncpg://" + adk_session_uri[len(_pre):]
            break
    if adk_session_uri.startswith("sqlite://") and not adk_session_uri.startswith("sqlite+"):
        adk_session_uri = "sqlite+aiosqlite://" + adk_session_uri[len("sqlite://"):]

    from google.adk.cli.utils import logs
    from google.adk.sessions import DatabaseSessionService

    # Setup ADK logging to ensure logs are visible when using uvicorn directly
    logs.setup_adk_logger(level=logging.DEBUG)

    session_service = DatabaseSessionService(
        db_url=adk_session_uri
    )

    def delete_empty_sessions(db_path=session_service_uri):
        from sqlalchemy import create_engine, text
        engine = create_engine(db_path)

        try:
            with engine.connect() as conn:
                if db_path.startswith("postgresql"):
                    conn.execute(text("""
                                      DELETE
                                      FROM sessions
                                      WHERE create_time <= NOW() - INTERVAL '6 hours'
                                        AND NOT EXISTS (SELECT 1
                                                        FROM events e
                                                        WHERE e.app_name = sessions.app_name
                                                          AND e.user_id = sessions.user_id
                                                          AND e.session_id = sessions.id)
                                      """))
                elif db_path.startswith("sqlite"):
                    conn.execute(text("""
                                      DELETE
                                      FROM sessions
                                      WHERE create_time <= datetime('now', '-6 hours')
                                        AND NOT EXISTS (SELECT 1
                                                        FROM events e
                                                        WHERE e.app_name = sessions.app_name
                                                          AND e.user_id = sessions.user_id
                                                          AND e.session_id = sessions.id)
                                      """))
                conn.commit()
        finally:
            engine.dispose()

    def _run_db_migrations(db_url: str) -> None:
        """Apply schema migrations that are not yet in the ADK-managed tables."""
        from sqlalchemy import create_engine, text, inspect
        engine = create_engine(db_url)
        try:
            with engine.begin() as conn:
                inspector = inspect(conn)

                # events table migrations
                event_cols = {c["name"] for c in inspector.get_columns("events")}
                if "language_code" not in event_cols:
                    conn.execute(text(
                        "ALTER TABLE events ADD COLUMN language_code VARCHAR(20)"
                    ))
                    logger.info("[Migration] Added language_code column to events table")

                # token_usage table migrations
                if inspector.has_table("token_usage"):
                    token_cols = {c["name"] for c in inspector.get_columns("token_usage")}
                    if "session_id" not in token_cols:
                        conn.execute(text("ALTER TABLE token_usage ADD COLUMN session_id TEXT"))
                        logger.info("[Migration] Added session_id column to token_usage table")
                    if "user_id" not in token_cols:
                        conn.execute(text("ALTER TABLE token_usage ADD COLUMN user_id TEXT"))
                        logger.info("[Migration] Added user_id column to token_usage table")
        except Exception as e:
            logger.warning(f"[Migration] migration skipped: {e}")
        finally:
            engine.dispose()

    @asynccontextmanager
    async def _lifespan(_):
        """A lifespan context manager for the FastAPI app."""
        _run_db_migrations(session_service_uri)

        scheduler = BackgroundScheduler()
        job = scheduler.add_job(partial(delete_empty_sessions, session_service_uri), "interval", hours=1)
        scheduler.add_job(check_and_notify_budget, "interval", hours=1, id="budget_monitor")
        try:
            scheduler.start()
            print("Job scheduled. Next run time:", job.next_run_time)

            # setup qdrant
            setup_rag()
            yield  # Startup is done, now app is running
        finally:
            scheduler.shutdown()

    app = get_fast_api_app(
        agents_dir=agent_dir,
        session_service_uri=adk_session_uri,  # ADK 2.0 cần asyncpg
        allow_origins=allow_origins,
        web=web,
        lifespan=_lifespan,
        host=host,
        port=port,
        # a2a=a2a,
    )

    # Additional endpoints for the ADK app
    app.include_router(mmvn_b2c_agent.api.semantic_search_router)  # semantic search endpoint
    app.include_router(mmvn_b2c_agent.api.setup_session_title_api(session_service))  # session title endpoint
    app.include_router(mmvn_b2c_agent.api.setup_summarize_session_api(session_service))  # summarize session endpoint
    app.include_router(mmvn_b2c_agent.api.voice_stt_router)  # voice STT endpoint
    _dashboard_auth = mmvn_b2c_agent.api.get_current_dashboard_user
    app.include_router(mmvn_b2c_agent.api.metrics_dashboard_router,
                       dependencies=[Depends(_dashboard_auth)])
    app.include_router(mmvn_b2c_agent.api.metrics_tracking_router,
                       dependencies=[Depends(_dashboard_auth)])
    app.include_router(mmvn_b2c_agent.api.metrics_search_quality_router,
                       dependencies=[Depends(_dashboard_auth)])
    app.include_router(mmvn_b2c_agent.api.feedback_router)  # message feedback endpoint (chatbot gọi, ko cần auth)
    app.include_router(mmvn_b2c_agent.api.dashboard_auth_router)  # dashboard auth endpoint
    mmvn_b2c_agent.api.init_dashboard_auth()  # init dashboard_users table + seed admin

    @app.get("/health", tags=["system"])
    async def health_check():
        from mmvn_b2c_agent.shared.config_service import config_service
        return {
            "agent_name": os.getenv("A2A_APP_NAME", "mmvn_b2c_agent"),
            "version": "1.0.0",
            "model": os.getenv("GOOGLE_GENAI_MODEL", "gemini-2.0-flash"),
            "status": "running",
            "token_hard_limit": config_service.token_hard_limit,
            "compact_context_threshold_pct": config_service.compact_context_threshold_pct,
        }
    from mmvn_b2c_agent.agents.root_agent import root_agent
    app.include_router(mmvn_b2c_agent.api.setup_transparency_router(root_agent))  # AI transparency endpoints
    app.include_router(mmvn_b2c_agent.api.admin_config_router,
                       dependencies=[Depends(_dashboard_auth)])  # admin config (budget, tokens)
    app.include_router(mmvn_b2c_agent.api.gemini_proxy_router)  # Gemini API proxy with key rotation
    app.include_router(mmvn_b2c_agent.api.rag_management_router,
                       dependencies=[Depends(_dashboard_auth)])  # RAG knowledge base management

    # ------------------------------------------------------------------
    # Per-user sliding-window rate limiter for /run_sse (chat endpoint).
    # Key: "uid:<customer_uid>" for authenticated users,
    #      "ip:<client_ip>" for anonymous (userId == "user").
    # Limit is hot-reloaded from config_service every request.
    # ------------------------------------------------------------------
    _rl_store: dict[str, deque] = {}
    _rl_lock = asyncio.Lock()
    _RL_WINDOW = 60.0  # seconds

    @app.middleware("http")
    async def rate_limit_chat(request: Request, call_next):
        if request.url.path == "/run_sse":
            body = await request.body()
            try:
                user_id = json.loads(body).get("userId", "user")
            except Exception:
                user_id = "user"

            if user_id == "user":
                # Behind Docker/nginx the real client IP is in X-Forwarded-For.
                # Falling back to request.client.host would key ALL users to the
                # Docker bridge IP (172.x.x.x), collapsing them into one bucket.
                forwarded_for = request.headers.get("X-Forwarded-For")
                if forwarded_for:
                    client_ip = forwarded_for.split(",")[0].strip()
                else:
                    client_ip = request.headers.get("X-Real-IP") or (
                        request.client.host if request.client else "unknown"
                    )
                key = f"ip:{client_ip}"
            else:
                key = f"uid:{user_id}"

            from mmvn_b2c_agent.shared.config_service import config_service
            limit = config_service.rate_limit_per_user
            now = time.monotonic()
            cutoff = now - _RL_WINDOW

            async with _rl_lock:
                dq = _rl_store.get(key)
                if dq is None:
                    dq = deque()
                    _rl_store[key] = dq
                while dq and dq[0] <= cutoff:
                    dq.popleft()
                if len(dq) >= limit:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": f"Rate limit exceeded ({limit} req/min). Please try again later."},
                    )
                dq.append(now)
                if len(_rl_store) > 10_000:
                    idle = [k for k, v in _rl_store.items() if not v or v[-1] <= cutoff]
                    for k in idle:
                        del _rl_store[k]

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(request.scope, receive)

        return await call_next(request)

    # ------------------------------------------------------------------
    # HTTP error tracking middleware — catches ADK crashes, timeouts, etc.
    # Logs to the same events table so /metrics/stats/errors has data.
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def track_http_errors(request: Request, call_next):
        """Record HTTP 5xx errors to DB for error metrics dashboard."""
        try:
            response = await call_next(request)
            # Log server errors and ADK stream errors (5xx only)
            if response.status_code >= 500:
                from mmvn_b2c_agent.agents.root_agent import record_error
                record_error(
                    app_name="http",
                    user_id="unknown",
                    session_id="unknown",
                    invocation_id=request.url.path,
                    error_code=f"http_{response.status_code}",
                    error_message=f"HTTP {response.status_code} on {request.method} {request.url.path}",
                )
            return response
        except Exception as e:
            # Catch unhandled exceptions (ADK crash, timeout, DB error, etc.)
            from mmvn_b2c_agent.agents.root_agent import record_error
            error_msg = str(e)[:500]
            if "timeout" in error_msg.lower():
                error_code = "timeout"
            elif "connection" in error_msg.lower() or "refused" in error_msg.lower():
                error_code = "system_error"
            elif "authentication" in error_msg.lower() or "401" in error_msg or "unauthorized" in error_msg.lower():
                error_code = "auth_error"
            else:
                error_code = "system_error"

            try:
                record_error(
                    app_name="http",
                    user_id="unknown",
                    session_id="unknown",
                    invocation_id=request.url.path,
                    error_code=error_code,
                    error_message=f"Unhandled exception on {request.method} {request.url.path}: {error_msg}",
                    interrupted=True,
                )
            except Exception:
                pass  # Don't let logging crash the error handler

            # Re-raise so ADK still handles it
            raise

    # Add CORSMiddleware AFTER all custom middlewares so it wraps the outermost
    # layer (LIFO execution: this runs first on requests / last on responses).
    # This ensures 429 responses from rate_limit_chat always carry CORS headers
    # without needing to add them manually per-response.
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Endpoint to set Odoo config for the app session
    @app.post("/app/{app_name}/session/odoo_config")
    async def set_app_session_config(app_name: str, state: dict,
                                     request: Request, response: Response):
        """
        Set the Odoo configuration for the app session.
        This will create/update an app-level session with the config from Odoo, allowing you to change ADK config through API.
        The model name is saved in state['app:odoo_config']['model_name']. The model config will only affect agents with the `dynamic_llm_model` callback
        """
        try:
            # verify the state config state
            if not state:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {
                    "success": False,
                    "error_message": "state must not be empty"
                }

            logger.info(f"Setting Odoo state for app {app_name}: {state}")
            state_delta = {"app:odoo_config": state or {}}
            session = mmvn_b2c_agent.api.update_session(
                session_service, app_name, 'user', 'odoo_config_session', state_delta)
            if session:
                response.status_code = status.HTTP_200_OK
                return {"success": True, "app_name": app_name, "state": state}
            else:
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                logger.error(f"Error setting Odoo config for app {app_name}.\nState: {state_delta}")
                return {"success": False, "error_message": "Failed to create/update session"}
        except Exception as e:
            logger.error(f"Error setting Odoo config for app {app_name}: {e}\n{traceback.format_exc()}")
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": str(e)}

    # update session state endpoint
    @app.put("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def set_app_session_config(app_name: str, user_id: str, session_id: str, state_delta: dict,
                                     request: Request, response: Response):
        """
        Update the session state for a given app, user, and session ID.
        """
        try:
            state_delta = {'state': state_delta}
            session = await mmvn_b2c_agent.api.update_session(session_service, app_name,
                                                              user_id, session_id,
                                                              state_delta, auto_create=False)
            if not session:
                response.status_code = status.HTTP_404_NOT_FOUND
                return {"success": False, "error_message": "Session not found"}
            response.status_code = status.HTTP_200_OK
            return {"success": True, "app_name": app_name, "user_id": user_id, "session_id": session_id,
                    "state": state_delta}
        except Exception as e:
            logger.error(f"Error updating session for app {app_name} user {user_id} session {session_id}: "
                         f"{e}\n{traceback.format_exc()}")
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": str(e)}

    # A2A endpoints
    if a2a:
        setup_a2a(app, a2a_app_name, a2a_host)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        reload=False,
    )
    server = uvicorn.Server(config)
    server.run()
    # server.should_exit = True  # this is how you stop the server programmatically.


if __name__ == '__main__':
    main()
