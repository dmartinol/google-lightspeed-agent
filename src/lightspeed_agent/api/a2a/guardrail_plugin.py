"""ADK plugin: bind MCP tool org arguments to the authenticated caller.

If tool arguments contain no org-style parameters, the plugin does not run.
Otherwise every org value must equal ``get_request_org_id()`` from the JWT
request context; mismatches are blocked. If org parameters are present but
request ``org_id`` is missing, the call is blocked as well (cannot verify tenant).

Downstream MCP and APIs still enforce auth; this mitigates cross-tenant
argument injection at the agent layer.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from lightspeed_agent.auth.middleware import get_request_org_id
from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)

# Normalized key names (lowercase, underscores) that may carry tenant org IDs.
_ORG_ARG_NAMES = frozenset(
    {
        "org_id",
        "organization_id",
        "rh_org_id",
        "orgid",
        "organizationid",
    }
)

_BLOCK_CODE = "guardrail_org_mismatch"
_MSG_MISMATCH = (
    "Tool blocked: organization parameter does not match the authenticated caller."
)
_MSG_NO_ORG_CONTEXT = (
    "Tool blocked: organization parameter present but caller has no organization "
    "context."
)


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_")


def _scalar_org_value(val: Any) -> str | None:
    """Extract a comparable org id string, or None if not a scalar."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, str):
        s = val.strip()
        return s or None
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(int(val)) if val.is_integer() else str(val)
    return None


def _append_org_values_for_key(v: Any, found: list[str]) -> None:
    """Append scalar org id strings for *v* when the parent key is org-related.

    ``v`` may be a single scalar or a list/tuple of scalars (e.g. ``org_id``:
    ``["111", "222"]``). Non-scalar elements (dicts) are skipped here and left
    to recursive traversal.
    """
    if isinstance(v, list | tuple):
        for item in v:
            s = _scalar_org_value(item)
            if s is not None:
                found.append(s)
    else:
        s = _scalar_org_value(v)
        if s is not None:
            found.append(s)


def _collect_org_values(obj: Any) -> list[str]:
    """Recursively collect org-related scalar values from tool arguments."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _normalize_key(str(k)) in _ORG_ARG_NAMES:
                _append_org_values_for_key(v, found)
            found.extend(_collect_org_values(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_collect_org_values(item))
    return found


def _block_response(message: str) -> dict[str, Any]:
    return {
        "error": message,
        "code": _BLOCK_CODE,
        "blocked": True,
    }


class GuardrailPlugin(BasePlugin):
    """Plugin that enforces org/tenant consistency on tool inputs."""

    def __init__(self) -> None:
        super().__init__(name="guardrail")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        if not get_settings().guardrail_org_args_enabled:
            return None

        tool_name = getattr(tool, "name", type(tool).__name__)
        org_values = _collect_org_values(tool_args)
        if not org_values:
            return None

        expected = get_request_org_id()
        if expected is None:
            logger.warning(
                "Guardrail blocked tool=%s invocation_id=%s: org args %s but "
                "no request org_id",
                tool_name,
                tool_context.invocation_id,
                org_values,
            )
            return _block_response(_MSG_NO_ORG_CONTEXT)

        exp = expected.strip()
        for value in org_values:
            if value != exp:
                logger.warning(
                    "Guardrail blocked tool=%s invocation_id=%s: org arg %r != %r",
                    tool_name,
                    tool_context.invocation_id,
                    value,
                    exp,
                )
                return _block_response(_MSG_MISMATCH)

        return None
