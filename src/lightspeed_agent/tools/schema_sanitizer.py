"""Sanitize MCP tool JSON schemas for Vertex AI compatibility.

Vertex AI requires every property in a function declaration schema to have
a ``type`` field.  Some MCP servers (e.g. Red Hat Lightspeed MCP) omit it
for certain parameters, causing INVALID_ARGUMENT errors.

The ADK's ``_to_gemini_schema()`` conversion pipeline (``_dereference_schema``
→ ``_sanitize_schema_formats_for_gemini`` → ``Schema.from_json_schema``)
drops or fails to infer missing ``type`` fields.  Patching individual
functions in that pipeline is unreliable across ADK versions.

Instead, this module replaces each tool's ``_get_declaration()`` to bypass
``_to_gemini_schema()`` entirely and use ``FunctionDeclaration(
parameters_json_schema=...)`` which sends the raw (sanitized) JSON schema
directly to the API.
"""

from __future__ import annotations

import copy
import sys
from typing import Any

from google.adk.tools.mcp_tool import McpToolset

print("[schema_sanitizer] module loading...", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Helper: recursively add missing ``type`` fields
# ---------------------------------------------------------------------------

def _deep_sanitize_schema(schema: dict[str, Any] | Any) -> None:
    """Walk a JSON schema dict and add ``type`` wherever it is missing."""
    if not isinstance(schema, dict):
        return

    # Process $defs / definitions
    for defs_key in ("$defs", "definitions"):
        if defs_key in schema and isinstance(schema[defs_key], dict):
            for def_schema in schema[defs_key].values():
                _deep_sanitize_schema(def_schema)

    # Add type at this level if missing (skip $ref nodes)
    if "type" not in schema and "$ref" not in schema:
        if "properties" in schema:
            schema["type"] = "object"
        elif "items" in schema:
            schema["type"] = "array"
        elif "enum" in schema or schema:
            schema["type"] = "string"

    # Recurse into properties
    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop_schema in schema["properties"].values():
            _deep_sanitize_schema(prop_schema)

    # Recurse into items
    if "items" in schema and isinstance(schema["items"], dict):
        _deep_sanitize_schema(schema["items"])

    # Recurse into composition keywords
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema and isinstance(schema[key], list):
            for sub in schema[key]:
                if isinstance(sub, dict):
                    _deep_sanitize_schema(sub)


# ---------------------------------------------------------------------------
# SanitizedMcpToolset: bypasses _to_gemini_schema entirely
# ---------------------------------------------------------------------------


class SanitizedMcpToolset(McpToolset):
    """McpToolset that sanitizes tool schemas for Vertex AI.

    Replaces each tool's ``_get_declaration`` to use
    ``parameters_json_schema`` (raw JSON schema) instead of going through
    the ADK's ``_to_gemini_schema()`` pipeline which drops missing types.
    """

    async def get_tools(self, *args: Any, **kwargs: Any) -> Any:
        from google.genai.types import FunctionDeclaration

        tools = await super().get_tools(*args, **kwargs)
        print(
            f"[schema_sanitizer] get_tools: {len(tools)} tools, replacing _get_declaration...",
            file=sys.stderr, flush=True,
        )

        for tool in tools:
            # Build a replacement _get_declaration that bypasses _to_gemini_schema
            def _make_declaration_fn(t: Any) -> Any:
                def _get_declaration() -> Any:
                    schema = copy.deepcopy(t._mcp_tool.inputSchema)
                    if schema:
                        _deep_sanitize_schema(schema)
                    return FunctionDeclaration(
                        name=t.name,
                        description=t.description,
                        parameters_json_schema=schema,
                    )
                return _get_declaration

            tool._get_declaration = _make_declaration_fn(tool)

        # Log a sample for debugging
        if tools:
            for t in tools:
                props = (t._mcp_tool.inputSchema or {}).get("properties", {})
                if props:
                    sample_props = {
                        k: v.get("type", "MISSING")
                        for k, v in list(props.items())[:3]
                    }
                    print(
                        f"[schema_sanitizer] Sample '{t.name}' raw props: {sample_props}",
                        file=sys.stderr, flush=True,
                    )
                    break

        return tools


print("[schema_sanitizer] module loaded successfully", file=sys.stderr, flush=True)
