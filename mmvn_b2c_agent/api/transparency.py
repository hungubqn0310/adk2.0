"""
AI Transparency API endpoints — Admin dashboard endpoints for
prompts inspection and ADK agent graph visualization.
"""

import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from mmvn_b2c_agent.api.transparency_builder import build_graph_data, build_prompts_data

logger = logging.getLogger(__name__)

_SESSION_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")

_engine = create_engine(_SESSION_URI, pool_size=5, max_overflow=10)


def _get_engine():
    return _engine


def _init_prompt_table():
    """Create prompt_overrides table if it doesn't exist, and migrate if needed."""
    engine = _get_engine()
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM prompt_overrides LIMIT 1"))
            # Table exists — add missing columns if needed
            try:
                conn.execute(text("SELECT model FROM prompt_overrides LIMIT 1"))
            except Exception:
                conn.rollback()
                conn.execute(text("ALTER TABLE prompt_overrides ADD COLUMN model VARCHAR(128)"))
                conn.commit()
                logger.info("[transparency] Migrated prompt_overrides: added model column")
            try:
                conn.execute(text("SELECT thinking_budget FROM prompt_overrides LIMIT 1"))
            except Exception:
                conn.rollback()
                conn.execute(text("ALTER TABLE prompt_overrides ADD COLUMN thinking_budget INTEGER"))
                conn.commit()
                logger.info("[transparency] Migrated prompt_overrides: added thinking_budget column")
            try:
                conn.execute(text("SELECT temperature FROM prompt_overrides LIMIT 1"))
            except Exception:
                conn.rollback()
                conn.execute(text("ALTER TABLE prompt_overrides ADD COLUMN temperature REAL"))
                conn.commit()
                logger.info("[transparency] Migrated prompt_overrides: added temperature column")
        except Exception:
            # Table doesn't exist — rollback aborted transaction before creating
            conn.rollback()
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS prompt_overrides (
                    agent_name      VARCHAR(128)  PRIMARY KEY,
                    instruction     TEXT          NOT NULL,
                    model           VARCHAR(128),
                    thinking_budget INTEGER,
                    temperature     REAL,
                    updated_by      VARCHAR(128)  DEFAULT 'admin',
                    updated_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            logger.info("[transparency] Created prompt_overrides table")


def _get_override(agent_name: str) -> Optional[tuple[str, Optional[str], Optional[int], Optional[float]]]:
    """Fetch (instruction, model, thinking_budget, temperature) override from DB, or None if not set."""
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT instruction, model, thinking_budget, temperature FROM prompt_overrides WHERE agent_name = :name"),
            {"name": agent_name},
        ).fetchone()
        return (row[0], row[1], row[2], row[3]) if row else None


def _get_overridden_instruction(agent_name: str) -> Optional[str]:
    """Fetch overridden instruction from DB, or None if not set."""
    result = _get_override(agent_name)
    return result[0] if result else None


def _upsert_instruction(
    agent_name: str,
    instruction: str,
    model: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    temperature: Optional[float] = None,
    updated_by: str = "admin",
):
    """Insert or update prompt/model override in DB."""
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO prompt_overrides (agent_name, instruction, model, thinking_budget, temperature, updated_by, updated_at)
            VALUES (:name, :instruction, :model, :thinking_budget, :temperature, :updated_by, :updated_at)
            ON CONFLICT(agent_name) DO UPDATE
                SET instruction     = :instruction,
                    model           = :model,
                    thinking_budget = :thinking_budget,
                    temperature     = :temperature,
                    updated_by      = :updated_by,
                    updated_at      = :updated_at
        """), {
            "name": agent_name,
            "instruction": instruction,
            "model": model,
            "thinking_budget": thinking_budget,
            "temperature": temperature,
            "updated_by": updated_by,
            "updated_at": datetime.now(timezone.utc),
        })
        conn.commit()


def _delete_instruction(agent_name: str) -> bool:
    """Delete prompt override. Returns True if row was deleted."""
    engine = _get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("DELETE FROM prompt_overrides WHERE agent_name = :name"),
            {"name": agent_name},
        )
        conn.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Response models (Pydantic) — follow project pattern
# ---------------------------------------------------------------------------

class PromptAgentEntry(BaseModel):
    name: str
    model: str
    temperature: Optional[float] = None
    instruction: str
    general_instruction: str = ""   # Shared prefix from build_sub_agent_instruction
    specific_instruction: str = ""  # Agent-specific part (stripped of general prefix)
    description: str = ""
    is_overridden: bool = False  # True if using DB override instead of code prompt
    model_overridden: bool = False  # True if model is overridden in DB
    sub_agents: list[str] = []


class PromptsResponse(BaseModel):
    success: bool
    agents: list[PromptAgentEntry]
    total: int
    error_message: Optional[str] = None


class UpdatePromptRequest(BaseModel):
    instruction: str
    model: Optional[str] = None
    thinking_budget: Optional[int] = None  # -1=auto, 0=disabled, >0=fixed tokens
    temperature: Optional[float] = None    # 0.0=deterministic, 1.0=creative
    updated_by: Optional[str] = "admin"


class PromptDetailResponse(BaseModel):
    success: bool
    agent_name: str
    default_instruction: Optional[str] = None
    overridden_instruction: Optional[str] = None
    instruction: str
    model: Optional[str] = None
    thinking_budget: Optional[int] = None
    temperature: Optional[float] = None
    is_overridden: bool
    model_overridden: bool = False
    error_message: Optional[str] = None


class DeletePromptResponse(BaseModel):
    success: bool
    message: str
    agent_name: str
    error_message: Optional[str] = None


class AgentFlowResponse(BaseModel):
    success: bool
    graph: dict[str, Any]
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# These will be populated at startup by setup_transparency_router()
# ---------------------------------------------------------------------------
_all_agents_ctx: dict = {"agents": [], "root_agent": None}


def _find_agent_by_name(agent_name: str):
    """Find a live agent object by name from the global context."""
    for agent in _all_agents_ctx.get("agents", []):
        try:
            if getattr(agent, "name", None) == agent_name:
                return agent
        except Exception:
            pass
    return None


def _requires_thinking(model_name: str) -> bool:
    """Pro models only work in thinking mode (budget=0 is rejected)."""
    name = model_name.lower()
    return "pro" in name


def _apply_thinking_budget(agent, budget: int) -> None:
    """Directly set thinking_budget on agent's planner."""
    planner = getattr(agent, "planner", None)
    if planner is None:
        return
    try:
        from google.genai.types import ThinkingConfig
        new_thinking = ThinkingConfig(include_thoughts=True, thinking_budget=budget)
        try:
            planner.thinking_config = new_thinking
        except Exception:
            object.__setattr__(planner, "thinking_config", new_thinking)
        logger.info("[transparency] Set thinking_budget=%d for agent '%s'", budget, getattr(agent, "name", "?"))
    except Exception as exc:
        logger.warning("[transparency] Could not set thinking_budget: %s", exc)


def _update_planner_thinking(agent, model_name: str) -> None:
    """Auto-update thinking_budget based on model name (pro → -1, others → 0)."""
    budget = -1 if _requires_thinking(model_name) else 0
    _apply_thinking_budget(agent, budget)


def _apply_temperature(agent, temperature: float) -> None:
    """Set temperature on agent's generate_content_config at runtime."""
    cfg = getattr(agent, "generate_content_config", None)
    if cfg is None:
        return
    try:
        cfg.temperature = temperature
        logger.info("[transparency] Set temperature=%.2f for agent '%s'", temperature, getattr(agent, "name", "?"))
    except Exception as exc:
        logger.warning("[transparency] Could not set temperature: %s", exc)


def _apply_model_to_agent(agent, model_name: str) -> bool:
    """
    Attempt to set agent.model at runtime.
    Returns True if successful.
    """
    try:
        from mmvn_b2c_agent.shared.constants import DEFAULT_RETRY_OPTION, GEMINI_BASE_URL
        from google.adk.models.google_llm import Gemini

        new_model = Gemini(model=model_name, retry_options=DEFAULT_RETRY_OPTION, base_url=GEMINI_BASE_URL)
        try:
            agent.model = new_model
        except Exception:
            # Pydantic may be frozen — bypass with object.__setattr__
            object.__setattr__(agent, "model", new_model)
        _update_planner_thinking(agent, model_name)
        logger.info("[transparency] Applied model override '%s' to agent '%s'", model_name, getattr(agent, "name", "?"))
        return True
    except Exception as exc:
        logger.warning("[transparency] Could not apply model override to agent: %s", exc)
        return False


def _apply_instruction_to_agent(agent, instruction: str) -> bool:
    """
    Apply an instruction override to a live agent's static_instruction at runtime.
    Returns True if successful.
    """
    try:
        from google.genai import types

        new_instruction = types.Content(parts=[types.Part(text=instruction)])
        try:
            agent.static_instruction = new_instruction
        except Exception:
            # Pydantic may be frozen — bypass with object.__setattr__
            object.__setattr__(agent, "static_instruction", new_instruction)
        logger.info("[transparency] Applied instruction override to agent '%s'", getattr(agent, "name", "?"))
        return True
    except Exception as exc:
        logger.warning("[transparency] Could not apply instruction override to agent: %s", exc)
        return False


def _apply_all_db_overrides():
    """Apply all model and instruction overrides from DB to live agents at startup."""
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT agent_name, instruction, model FROM prompt_overrides")
            ).fetchall()
        for agent_name, instruction, model_name in rows:
            agent = _find_agent_by_name(agent_name)
            if agent:
                if model_name:
                    _apply_model_to_agent(agent, model_name)
                if instruction:
                    _apply_instruction_to_agent(agent, instruction)
    except Exception as exc:
        logger.warning("[transparency] Could not apply DB overrides at startup: %s", exc)


def _sync_code_prompts_to_db():
    """Push current Python-defined prompts to DB on startup (code is source of truth).

    Instruction is always overwritten from code. Model, thinking_budget, and
    temperature overrides set via the dashboard are preserved.
    """
    from mmvn_b2c_agent.api.transparency_builder import build_prompts_data
    try:
        data = build_prompts_data(agents=_all_agents_ctx["agents"])
        synced = 0
        for agent_data in data.get("agents", []):
            name = agent_data.get("name")
            instruction = agent_data.get("instruction")
            if not name or not instruction:
                continue
            existing = _get_override(name)
            _upsert_instruction(
                name,
                instruction,
                model=existing[1] if existing else None,
                thinking_budget=existing[2] if existing else None,
                temperature=existing[3] if existing else None,
                updated_by="startup-sync",
            )
            synced += 1
        logger.info("[transparency] Synced %d agent prompt(s) from code to DB", synced)
    except Exception as exc:
        logger.warning("[transparency] Could not sync code prompts to DB: %s", exc)


def setup_transparency_router(root_agent) -> APIRouter:
    """
    Configure and return the transparency router.

    Must be called once at startup (from run_adk.py) with the live root agent
    so that introspection reads the actual runtime objects.
    """
    from mmvn_b2c_agent.api.transparency_builder import _collect_all_agents

    effective_root = root_agent

    _all_agents_ctx["root_agent"] = effective_root
    _all_agents_ctx["agents"] = _collect_all_agents(effective_root)

    # Init DB table for prompt overrides
    _init_prompt_table()

    # Push code prompts to DB (code is source of truth; model/temp overrides preserved)
    _sync_code_prompts_to_db()

    # Apply model/temp overrides from DB back to live agents
    _apply_all_db_overrides()

    logger.info(
        "[transparency] Registered %d agents (root=%s)",
        len(_all_agents_ctx["agents"]),
        getattr(effective_root, "name", "?"),
    )

    router = APIRouter(prefix="/admin/transparency", tags=["admin/transparency"])

    # ---------------------------------------------------------------
    # GET /admin/transparency/prompts  (List all agents)
    # ---------------------------------------------------------------
    @router.get("/prompts", response_model=PromptsResponse)
    async def get_prompts(
        agent_name: Optional[str] = Query(
            None, title="Agent name filter (optional)",
            description="If empty, returns all agents"
        ),
        request: Request = None,
        response: Response = None,
    ):
        """
        Return the prompt for each agent.

        If an agent has a DB override, the overridden instruction is returned.
        Otherwise, the default code instruction is used.
        """
        try:
            data = build_prompts_data(
                agents=_all_agents_ctx["agents"],
                agent_name=agent_name,
            )
            results = []
            for agent_data in data.get("agents", []):
                name = agent_data["name"]
                override = _get_override(name)
                is_overridden = override is not None
                overridden_instruction = override[0] if override else None
                overridden_model = override[1] if override else None
                full_instruction = overridden_instruction if is_overridden else agent_data["instruction"]
                # Re-split whenever instruction is overridden so the UI still shows correct sections
                from mmvn_b2c_agent.api.transparency_builder import _split_instruction
                gen_instr, spec_instr = _split_instruction(full_instruction)
                results.append(PromptAgentEntry(
                    name=name,
                    model=overridden_model if overridden_model else agent_data["model"],
                    temperature=agent_data.get("temperature"),
                    instruction=full_instruction,
                    general_instruction=gen_instr,
                    specific_instruction=spec_instr,
                    description=agent_data.get("description", ""),
                    is_overridden=is_overridden,
                    model_overridden=overridden_model is not None,
                    sub_agents=agent_data.get("sub_agents", []),
                ))
            return PromptsResponse(
                success=True,
                agents=results,
                total=len(results),
            )
        except Exception as exc:
            logger.error(
                "Error building prompts data: %s\n%s",
                exc, traceback.format_exc()
            )
            if response:
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return PromptsResponse(
                success=False,
                agents=[],
                total=0,
                error_message=str(exc),
            )

    # ---------------------------------------------------------------
    # GET /admin/transparency/prompts/{agent_name}  (Detail)
    # ---------------------------------------------------------------
    @router.get("/prompts/{agent_name}", response_model=PromptDetailResponse)
    async def get_prompt_detail(
        agent_name: str,
        request: Request = None,
        response: Response = None,
    ):
        """
        Get detailed prompt info for a specific agent.

        Shows both the default code instruction and the current override (if any).
        """
        try:
            data = build_prompts_data(
                agents=_all_agents_ctx["agents"],
                agent_name=agent_name,
            )
            agents = data.get("agents", [])
            if not agents:
                if response:
                    response.status_code = status.HTTP_404_NOT_FOUND
                return PromptDetailResponse(
                    success=False,
                    agent_name=agent_name,
                    default_instruction=None,
                    overridden_instruction=None,
                    instruction="",
                    is_overridden=False,
                    error_message=f"Agent '{agent_name}' not found",
                )
            agent_data = agents[0]
            overridden = _get_overridden_instruction(agent_name)
            is_overridden = overridden is not None
            return PromptDetailResponse(
                success=True,
                agent_name=agent_name,
                default_instruction=agent_data["instruction"],
                overridden_instruction=overridden,
                instruction=overridden if is_overridden else agent_data["instruction"],
                is_overridden=is_overridden,
            )
        except Exception as exc:
            logger.error(
                "Error getting prompt detail: %s\n%s",
                exc, traceback.format_exc()
            )
            if response:
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return PromptDetailResponse(
                success=False,
                agent_name=agent_name,
                instruction="",
                is_overridden=False,
                error_message=str(exc),
            )

    # ---------------------------------------------------------------
    # PUT /admin/transparency/prompts/{agent_name}
    # ---------------------------------------------------------------
    @router.put("/prompts/{agent_name}", response_model=PromptDetailResponse)
    async def update_prompt(
        agent_name: str,
        body: UpdatePromptRequest,
        request: Request = None,
        response: Response = None,
    ):
        """
        Override the prompt for a specific agent.

        The override is stored in DB and takes precedence over the code prompt.
        The change takes effect immediately for new requests.
        """
        try:
            # Verify agent exists
            data = build_prompts_data(
                agents=_all_agents_ctx["agents"],
                agent_name=agent_name,
            )
            agents = data.get("agents", [])
            if not agents:
                if response:
                    response.status_code = status.HTTP_404_NOT_FOUND
                return PromptDetailResponse(
                    success=False,
                    agent_name=agent_name,
                    instruction="",
                    is_overridden=False,
                    error_message=f"Agent '{agent_name}' not found",
                )

            agent_data = agents[0]
            default_instruction = agent_data["instruction"]

            # Save to DB
            _upsert_instruction(
                agent_name,
                body.instruction,
                model=body.model or None,
                thinking_budget=body.thinking_budget,
                temperature=body.temperature,
                updated_by=body.updated_by or "admin",
            )

            # Apply overrides to live agent immediately
            live_agent = _find_agent_by_name(agent_name)
            if live_agent:
                _apply_instruction_to_agent(live_agent, body.instruction)
                if body.model:
                    _apply_model_to_agent(live_agent, body.model)
                if body.thinking_budget is not None:
                    _apply_thinking_budget(live_agent, body.thinking_budget)
                if body.temperature is not None:
                    _apply_temperature(live_agent, body.temperature)

            override = _get_override(agent_name)
            overridden_instruction = override[0] if override else None
            overridden_model = override[1] if override else None
            overridden_budget = override[2] if override else None
            overridden_temp = override[3] if override else None
            return PromptDetailResponse(
                success=True,
                agent_name=agent_name,
                default_instruction=default_instruction,
                overridden_instruction=overridden_instruction,
                instruction=overridden_instruction,
                model=overridden_model,
                thinking_budget=overridden_budget,
                temperature=overridden_temp,
                is_overridden=True,
                model_overridden=overridden_model is not None,
            )
        except Exception as exc:
            logger.error(
                "Error updating prompt: %s\n%s",
                exc, traceback.format_exc()
            )
            if response:
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return PromptDetailResponse(
                success=False,
                agent_name=agent_name,
                instruction="",
                is_overridden=False,
                error_message=str(exc),
            )

    # ---------------------------------------------------------------
    # DELETE /admin/transparency/prompts/{agent_name}
    # ---------------------------------------------------------------
    @router.delete("/prompts/{agent_name}", response_model=DeletePromptResponse)
    async def delete_prompt_override(
        agent_name: str,
        request: Request = None,
        response: Response = None,
    ):
        """
        Delete the prompt override for a specific agent.

        After deletion, the agent reverts to the default code prompt.
        """
        try:
            # Verify agent exists
            data = build_prompts_data(
                agents=_all_agents_ctx["agents"],
                agent_name=agent_name,
            )
            agents = data.get("agents", [])
            if not agents:
                if response:
                    response.status_code = status.HTTP_404_NOT_FOUND
                return DeletePromptResponse(
                    success=False,
                    message="Agent not found",
                    agent_name=agent_name,
                    error_message=f"Agent '{agent_name}' not found",
                )

            # Was it actually overridden?
            was_overridden = _get_overridden_instruction(agent_name) is not None

            # Delete override
            _delete_instruction(agent_name)

            msg = (
                "Prompt override deleted — agent reverted to default code prompt"
                if was_overridden
                else "No override found — nothing to delete"
            )
            return DeletePromptResponse(
                success=True,
                message=msg,
                agent_name=agent_name,
            )
        except Exception as exc:
            logger.error(
                "Error deleting prompt override: %s\n%s",
                exc, traceback.format_exc()
            )
            if response:
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return DeletePromptResponse(
                success=False,
                message="Internal server error",
                agent_name=agent_name,
                error_message=str(exc),
            )

    # ---------------------------------------------------------------
    # GET /admin/transparency/agent-flow
    # ---------------------------------------------------------------
    @router.get("/agent-flow", response_model=AgentFlowResponse)
    async def get_agent_flow(
        request: Request = None,
        response: Response = None,
    ):
        """
        Return the agent dependency graph (nodes/edges).

        Returns:
            Graph with nodes (agents) and edges (connections).
        """
        try:
            root = _all_agents_ctx["root_agent"]
            if root is None:
                if response:
                    response.status_code = status.HTTP_404_NOT_FOUND
                return AgentFlowResponse(
                    success=False,
                    graph={"nodes": [], "edges": []},
                    error_message="Transparency router not initialised — no root agent",
                )
            data = build_graph_data(root)
            return AgentFlowResponse(
                success=True,
                graph=data.get("graph", {"nodes": [], "edges": []}),
            )
        except Exception as exc:
            logger.error(
                "Error building graph data: %s\n%s",
                exc, traceback.format_exc()
            )
            if response:
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return AgentFlowResponse(
                success=False,
                graph={"nodes": [], "edges": []},
                error_message=str(exc),
            )

    return router
