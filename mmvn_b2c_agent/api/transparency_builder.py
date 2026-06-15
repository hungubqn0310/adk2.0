"""
AI Transparency utilities — introspect ADK Agent objects at runtime
to build prompts list and agent graph for the dashboard.
"""
from __future__ import annotations

import inspect
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# General instruction prefix detection (from prompts_builder)
# ---------------------------------------------------------------------------

try:
    from mmvn_b2c_agent.shared.prompts_builder import (
        GENERAL_SUB_AGENT_TASKS_NO_PLAN,
        GENERAL_SUB_AGENT_TASKS_WITH_PLAN,
    )
    _GENERAL_PREFIXES = [
        GENERAL_SUB_AGENT_TASKS_WITH_PLAN.strip(),
        GENERAL_SUB_AGENT_TASKS_NO_PLAN.strip(),
    ]
except ImportError:
    _GENERAL_PREFIXES = []


def _split_instruction(full: str) -> tuple[str, str]:
    """Split a built instruction into (general_prefix, specific_part).

    Returns ("", full) when no known prefix is detected.
    """
    stripped = full.strip()
    for prefix in _GENERAL_PREFIXES:
        if stripped.startswith(prefix):
            specific = stripped[len(prefix):].strip()
            return prefix, specific
    return "", stripped


# ---------------------------------------------------------------------------
# Routing-rule extraction helpers
# ---------------------------------------------------------------------------

_TRANSFER_RE = re.compile(
    r'(?:transfer_to_agent|transfer\s+to|silently\s+transfer\s+to)\s*'
    r'(?:\(?agent_name\s*=\s*)?"([^"]+)"',
    re.IGNORECASE,
)


def extract_transfer_targets(instruction: str) -> list[dict[str, Any]]:
    """Parse transfer_to_agent(agent_name="...") calls from instruction text."""
    if not isinstance(instruction, str):
        return []
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _TRANSFER_RE.finditer(instruction):
        target = match.group(1)
        if target in seen:
            continue
        seen.add(target)
        start = max(0, match.start() - 200)
        context = instruction[start: match.start()]
        trigger = re.split(r'[-\n]', context)[-1].strip()[-150:]
        targets.append({"target_agent": target, "trigger": trigger, "type": "prompt_transfer"})
    return targets


# ---------------------------------------------------------------------------
# Safe helpers
# ---------------------------------------------------------------------------

def _safe_attr(obj, attr, default=""):
    """Safely get an attribute from a Pydantic/ADK object."""
    try:
        val = getattr(obj, attr, default)
        if isinstance(val, str):
            return val
        if val is None:
            return default
        return str(val)[:200]
    except Exception:
        return default


def _safe_name(obj) -> str:
    """Get name from any agent/tool-like object safely."""
    try:
        # If it's a Pydantic model with name field
        if hasattr(obj, "model_fields") and "name" in obj.model_fields:
            return obj.name
        # Direct attribute
        if hasattr(obj, "name"):
            return _safe_attr(obj, "name", "unknown")
        # Callable/function
        return _safe_attr(obj, "__name__", "unknown")
    except Exception:
        return type(obj).__name__


def _callback_names(callbacks) -> list[str]:
    """Return a list of function names for a callback list."""
    if not callbacks:
        return []
    if isinstance(callbacks, (list, tuple)):
        names = []
        for cb in callbacks:
            try:
                names.append(_safe_attr(cb, "__name__", type(cb).__name__))
            except Exception:
                names.append(type(cb).__name__)
        return names
    try:
        return [_safe_attr(callbacks, "__name__", type(callbacks).__name__)]
    except Exception:
        return [type(callbacks).__name__]


def _has_llm_config(agent) -> bool:
    """Check if agent has LLM config (model, generate_content_config)."""
    return hasattr(agent, "model")


def _extract_model_string(val: str) -> str:
    """Extract model name from ADK model repr like "model='gemini-2.5-flash' speech_config=None ..." """
    if isinstance(val, str):
        m = re.match(r"model='([^']*)'", val)
        if m:
            return m.group(1)
        return val
    return str(val)[:80]


def _model_name(agent) -> str:
    """Return model string from an Agent object.

    Falls through: agent.model → agent.canonical_model → parent_agent.model.
    """
    if not _has_llm_config(agent):
        return "N/A"

    # Direct model field
    model = _safe_attr(agent, "model", None)
    if isinstance(model, str) and model.strip():
        return _extract_model_string(model)

    # canonical_model (inherit LLM config from parent)
    canonical = getattr(agent, "canonical_model", None)
    if canonical is not None:
        return _extract_model_string(str(canonical))

    # Parent agent's model
    parent = getattr(agent, "parent_agent", None)
    if parent is not None and _has_llm_config(parent):
        return _model_name(parent)

    return "inherited"


def _temperature(agent) -> float | None:
    """Return temperature from agent.generate_content_config."""
    if not _has_llm_config(agent):
        return None
    gcc = getattr(agent, "generate_content_config", None)
    if gcc is None:
        return None
    return _safe_attr(gcc, "temperature", None) or None


def _agent_type(agent) -> str:
    """Get agent class name without serializing the object."""
    return type(agent).__name__


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_prompts_data(agents: list, agent_name: str | None = None) -> dict[str, Any]:
    """Build the /prompts endpoint payload by introspecting Agent objects."""
    result: list[dict[str, Any]] = []
    for agent in agents:
        if not _has_llm_config(agent):
            continue

        name = _safe_name(agent)
        if agent_name and name != agent_name:
            continue

        try:
            # Resolve instruction — prefer static_instruction (always the explicit full system
            # prompt from prompts.py), then fall back to instruction (may be a dynamic template).
            instr_text = ""

            # 1. static_instruction takes priority (Content object with parts[0].text)
            static_instr = getattr(agent, "static_instruction", None)
            if static_instr is not None and hasattr(static_instr, "parts"):
                for part in static_instr.parts:
                    if hasattr(part, "text") and part.text:
                        instr_text = part.text
                        break

            # 2. Fallback to instruction field if static_instruction is absent
            if not instr_text:
                instr = getattr(agent, "instruction", None)
                if callable(instr):
                    instr_text = "<callable instruction — evaluated at runtime>"
                elif isinstance(instr, str) and instr.strip():
                    instr_text = instr

            # Sub-agent names — safely
            sub_list = getattr(agent, "sub_agents", []) or []
            sub_agents = [_safe_name(s) for s in sub_list]

            # Tool names
            tools = []
            for t in getattr(agent, "tools", []) or []:
                tools.append(_safe_name(t))

            # Routing rules
            routing_rules = extract_transfer_targets(instr_text)

            description = getattr(agent, "description", "") or ""
            general_instr, specific_instr = _split_instruction(instr_text)

            result.append({
                "name": name,
                "model": _model_name(agent),
                "temperature": _temperature(agent),
                "instruction": instr_text,
                "general_instruction": general_instr,
                "specific_instruction": specific_instr,
                "description": description,
                "sub_agents": sub_agents,
            })
        except Exception:
            logger.warning("[transparency] Error building prompts for agent %r", name)
            continue

    return {"agents": result, "total": len(result)}


def _collect_all_agents(root_agent) -> list:
    """Recursively collect root + all sub-agents into a flat list."""
    collected: list = []

    def _walk(a):
        collected.append(a)
        for s in getattr(a, "sub_agents", []) or []:
            _walk(s)

    try:
        _walk(root_agent)
    except Exception:
        collected = [root_agent]
    return collected


def build_graph_data(root_agent) -> dict[str, Any]:
    """Build the /agent-flow endpoint payload from a root Agent."""
    all_agents = _collect_all_agents(root_agent)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    all_routing_rules: list[dict[str, Any]] = []

    agent_priority_counter: dict[str, int] = {}

    for agent in all_agents:
        try:
            name = _safe_name(agent)
            parent = getattr(agent, "parent_agent", None)
            parent_name = _safe_name(parent) if parent else None
            if parent_name in (None, "", "unknown"):
                parent_name = None

            tools = [_safe_name(t) for t in getattr(agent, "tools", []) or []]
            nodes.append({
                "id": name,
                "type": _agent_type(agent),
                "model": _model_name(agent),
                "tools": tools,
            })

            # Sub-agent edges
            for s in getattr(agent, "sub_agents", []) or []:
                sname = _safe_name(s)
                edges.append({
                    "from": name,
                    "to": sname,
                    "type": "sub_agent",
                })

            # Tool edges — only count for routing_rule display, not as nodes
            # Tools are listed in /prompts endpoint, skip graph nodes for cleanliness

            # Routing rules from prompts — prefer static_instruction (full system prompt)
            instr_text = ""
            static_instr = getattr(agent, "static_instruction", None)
            if static_instr is not None and hasattr(static_instr, "parts"):
                for part in static_instr.parts:
                    if hasattr(part, "text") and part.text:
                        instr_text = part.text
                        break
            if not instr_text:
                raw_instr = getattr(agent, "instruction", None)
                if isinstance(raw_instr, str) and raw_instr.strip():
                    instr_text = raw_instr

            if not instr_text.strip():
                continue
            rules = extract_transfer_targets(instr_text)
            count = agent_priority_counter.get(name, 0)
            for rule in rules:
                count += 1
                rule_with_agent = dict(rule)
                rule_with_agent["agent"] = name
                rule_with_agent["priority"] = count
                edges.append({
                    "from": name,
                    "to": rule["target_agent"],
                    "type": "routing_rule",
                    "trigger": rule.get("trigger", ""),
                })
            agent_priority_counter[name] = count
            all_routing_rules.extend([dict(r) for r in rules])

        except Exception:
            logger.warning("[transparency] Skipping agent in graph builder")
            continue

    logger.info(
        "[transparency] Graph: %d nodes, %d edges from %d agents",
        len(nodes), len(edges), len(all_agents),
    )

    return {"graph": {"nodes": nodes, "edges": edges}}
