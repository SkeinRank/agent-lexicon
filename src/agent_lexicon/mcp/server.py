"""Minimal MCP stdio server for Agent Lexicon.

The implementation is dependency-free and speaks the JSON-RPC subset needed by
local MCP clients: initialize, tools/list, tools/call, notifications/initialized,
and ping. Tool results are returned as JSON text content so agents can consume
runtime decisions without needing a custom transport.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from agent_lexicon.core import AgentLexiconLoadError, guard_tool_call, load_lexicon, resolve_text
from agent_lexicon.policy import (
    LocalPolicyError,
    PolicyAction,
    check_local_policy,
    load_local_policy,
)
from agent_lexicon.workspace import ReviewDecisionStatus, WorkspaceError, open_workspace

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agent-lexicon"
DEFAULT_MCP_TOOLS = (
    "resolve_term",
    "check_language",
    "guard_tool_call",
    "find_evidence",
    "submit_proposal",
    "get_snapshot",
)


class McpServerError(ValueError):
    """Raised when the local MCP server receives an invalid request."""


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """Runtime configuration for the local MCP stdio server."""

    root: Path = Path(".")
    lexicon_path: Path | None = None
    policy_mode: str | None = None
    actor: str = "local"
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())
        if self.lexicon_path is not None:
            object.__setattr__(self, "lexicon_path", Path(self.lexicon_path))
        object.__setattr__(self, "actor", _clean_text(self.actor, field_name="actor"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable config snapshot."""
        return {
            "root": str(self.root),
            "lexicon_path": str(self.lexicon_path) if self.lexicon_path is not None else None,
            "policy_mode": self.policy_mode,
            "actor": self.actor,
            "role": self.role,
        }


def mcp_tool_definitions() -> list[dict[str, Any]]:
    """Return MCP tool definitions exposed by the local server."""
    return [
        {
            "name": "resolve_term",
            "description": "Resolve text against the local Agent Lexicon and report ambiguity.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to resolve."},
                    "scopes": {"type": "array", "items": {"type": "string"}},
                    "lexicon_path": {"type": "string", "description": "Optional lexicon file path."},
                    "include_deprecated": {"type": "boolean"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "check_language",
            "description": "Check whether text is known, resolved, or ambiguous in the local lexicon.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to check."},
                    "scopes": {"type": "array", "items": {"type": "string"}},
                    "lexicon_path": {"type": "string", "description": "Optional lexicon file path."},
                    "include_deprecated": {"type": "boolean"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "guard_tool_call",
            "description": "Check whether a requested tool call is safe for the resolved terminology.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text that triggered the tool call."},
                    "tool_name": {"type": "string", "description": "Requested tool name."},
                    "scopes": {"type": "array", "items": {"type": "string"}},
                    "lexicon_path": {"type": "string", "description": "Optional lexicon file path."},
                    "include_deprecated": {"type": "boolean"},
                },
                "required": ["text", "tool_name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_evidence",
            "description": "Find lexicon or workspace evidence for a term id or surface.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "term_id": {"type": "string", "description": "Canonical term id."},
                    "surface": {"type": "string", "description": "Candidate surface or text to resolve."},
                    "lexicon_path": {"type": "string", "description": "Optional lexicon file path."},
                    "max_results": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "submit_proposal",
            "description": "Save a local review decision for a workspace candidate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string", "description": "Workspace candidate normalized surface."},
                    "decision": {
                        "type": "string",
                        "enum": [status.value for status in ReviewDecisionStatus],
                    },
                    "note": {"type": "string"},
                    "reviewer": {"type": "string"},
                },
                "required": ["candidate_id", "decision"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_snapshot",
            "description": "Return local snapshot metadata from the Agent Lexicon workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1},
                    "include_payload": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    ]


def run_mcp_stdio_server(
    *,
    root: str | Path = ".",
    lexicon_path: str | Path | None = None,
    policy_mode: str | None = None,
    actor: str = "local",
    role: str | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the local MCP server over newline-delimited stdio JSON-RPC."""
    config = McpServerConfig(
        root=Path(root),
        lexicon_path=Path(lexicon_path) if lexicon_path is not None else None,
        policy_mode=policy_mode,
        actor=actor,
        role=role,
    )
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout

    for line in input_stream:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            _write_jsonrpc_response(output_stream, _jsonrpc_error(None, -32700, f"Parse error: {exc}"))
            continue

        responses = handle_mcp_message(payload, config=config)
        for response in responses:
            _write_jsonrpc_response(output_stream, response)
    return 0


def handle_mcp_message(payload: Any, *, config: McpServerConfig) -> list[dict[str, Any]]:
    """Handle one MCP JSON-RPC message or batch and return response objects."""
    if isinstance(payload, list):
        responses: list[dict[str, Any]] = []
        for item in payload:
            response = _handle_single_message(item, config=config)
            if response is not None:
                responses.append(response)
        return responses
    response = _handle_single_message(payload, config=config)
    return [] if response is None else [response]


def call_mcp_tool(name: str, arguments: Mapping[str, Any] | None = None, *, config: McpServerConfig | None = None) -> dict[str, Any]:
    """Call one Agent Lexicon MCP tool and return a JSON-compatible result."""
    config_value = config if config is not None else McpServerConfig()
    args = dict(arguments or {})
    if name == "resolve_term":
        return _resolve_term(args, config=config_value)
    if name == "check_language":
        return _check_language(args, config=config_value)
    if name == "guard_tool_call":
        return _guard_tool_call_mcp(args, config=config_value)
    if name == "find_evidence":
        return _find_evidence(args, config=config_value)
    if name == "submit_proposal":
        return _submit_proposal(args, config=config_value)
    if name == "get_snapshot":
        return _get_snapshot(args, config=config_value)
    raise McpServerError(f"unknown MCP tool: {name}")


def mcp_tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    """Wrap a JSON-compatible payload in an MCP tools/call response."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
            }
        ],
        "isError": is_error,
    }


def _handle_single_message(payload: Any, *, config: McpServerConfig) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return _jsonrpc_error(None, -32600, "Invalid Request")
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {})
    if not isinstance(method, str):
        return _jsonrpc_error(request_id, -32600, "Invalid Request")
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        return _jsonrpc_error(request_id, -32602, "Invalid params")

    try:
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return _jsonrpc_result(request_id, _initialize_result(config))
        if method == "ping":
            return _jsonrpc_result(request_id, {})
        if method == "tools/list":
            return _jsonrpc_result(request_id, {"tools": mcp_tool_definitions()})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str):
                raise McpServerError("tools/call params.name must be a string")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, Mapping):
                raise McpServerError("tools/call params.arguments must be an object")
            result = call_mcp_tool(name, arguments, config=config)
            return _jsonrpc_result(request_id, mcp_tool_result(result))
        return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")
    except Exception as exc:  # keep stdio protocol alive for local agents
        return _jsonrpc_result(request_id, mcp_tool_result({"error": str(exc), "type": exc.__class__.__name__}, is_error=True))


def _initialize_result(config: McpServerConfig) -> dict[str, Any]:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": _package_version(),
        },
        "instructions": (
            "Use Agent Lexicon tools to resolve terminology, check ambiguity, "
            "guard tool calls, inspect evidence, submit local review decisions, "
            "and read local snapshot metadata."
        ),
        "metadata": {"config": config.to_dict()},
    }


def _resolve_term(args: Mapping[str, Any], *, config: McpServerConfig) -> dict[str, Any]:
    text = _required_string(args, "text")
    lexicon, path = _load_configured_lexicon(config, args)
    decision = resolve_text(
        lexicon,
        text,
        scopes=_optional_string_list(args, "scopes"),
        include_deprecated=_optional_bool(args, "include_deprecated", default=True),
    )
    return {
        "tool": "resolve_term",
        "lexicon_path": str(path),
        "decision": decision.to_dict(),
    }


def _check_language(args: Mapping[str, Any], *, config: McpServerConfig) -> dict[str, Any]:
    result = _resolve_term(args, config=config)
    decision = result["decision"]
    result["tool"] = "check_language"
    result["safe_for_agent"] = decision["action"] != "ask_clarification"
    result["requires_clarification"] = decision["status"] == "ambiguous"
    return result


def _guard_tool_call_mcp(args: Mapping[str, Any], *, config: McpServerConfig) -> dict[str, Any]:
    text = _required_string(args, "text")
    tool_name = _required_string(args, "tool_name")
    lexicon, path = _load_configured_lexicon(config, args)
    decision = guard_tool_call(
        lexicon,
        text,
        tool_name=tool_name,
        scopes=_optional_string_list(args, "scopes"),
        include_deprecated=_optional_bool(args, "include_deprecated", default=True),
    )
    return {
        "tool": "guard_tool_call",
        "lexicon_path": str(path),
        "decision": decision.to_dict(),
    }


def _find_evidence(args: Mapping[str, Any], *, config: McpServerConfig) -> dict[str, Any]:
    term_id = _optional_string(args, "term_id")
    surface = _optional_string(args, "surface")
    if term_id is None and surface is None:
        raise McpServerError("find_evidence requires term_id or surface")
    max_results = _optional_int(args, "max_results", default=20)
    if max_results < 1:
        raise McpServerError("max_results must be greater than 0")

    lexicon, path = _load_configured_lexicon(config, args)
    term_ids: list[str] = []
    if term_id is not None:
        term_ids.append(term_id)
    if surface is not None:
        decision = resolve_text(lexicon, surface, include_deprecated=True)
        term_ids.extend(candidate["term_id"] for candidate in decision.to_dict()["candidates"])

    evidence: list[dict[str, Any]] = []
    for current_term_id in dict.fromkeys(term_ids):
        term = lexicon.get_term(current_term_id)
        if term is None:
            continue
        for span in term.evidence:
            if len(evidence) >= max_results:
                break
            item = span.to_dict()
            item["term_id"] = term.id
            item["canonical"] = term.canonical
            item["source"] = "lexicon"
            evidence.append(item)

    if surface is not None and len(evidence) < max_results:
        evidence.extend(_workspace_evidence(config.root, surface, limit=max_results - len(evidence)))

    return {
        "tool": "find_evidence",
        "lexicon_path": str(path),
        "term_ids": list(dict.fromkeys(term_ids)),
        "surface": surface,
        "evidence": evidence[:max_results],
        "evidence_count": min(len(evidence), max_results),
    }


def _submit_proposal(args: Mapping[str, Any], *, config: McpServerConfig) -> dict[str, Any]:
    candidate_id = _required_string(args, "candidate_id")
    decision = ReviewDecisionStatus(_required_string(args, "decision"))
    note = _optional_string(args, "note") or ""
    reviewer = _optional_string(args, "reviewer") or config.actor

    policy = load_local_policy(config.root, mode=config.policy_mode)
    policy_decision = check_local_policy(
        policy,
        PolicyAction.REVIEW_CANDIDATE,
        actor=config.actor,
        role=config.role,
    )
    if not policy_decision.is_allowed:
        raise McpServerError(f"policy denied review_candidate: {policy_decision.reason}")

    state = open_workspace(config.root)
    saved = state.save_review_decision(candidate_id, decision, note=note, reviewer=reviewer)
    return {
        "tool": "submit_proposal",
        "review_decision": saved.to_dict(),
        "policy": policy_decision.to_dict(),
    }


def _get_snapshot(args: Mapping[str, Any], *, config: McpServerConfig) -> dict[str, Any]:
    limit = _optional_int(args, "limit", default=5)
    include_payload = _optional_bool(args, "include_payload", default=False)
    if limit < 1:
        raise McpServerError("limit must be greater than 0")
    state = open_workspace(config.root)
    snapshots = []
    for snapshot in state.list_snapshots(limit=limit):
        payload = snapshot.to_dict()
        if not include_payload:
            payload.pop("payload", None)
        snapshots.append(payload)
    return {
        "tool": "get_snapshot",
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
    }


def _workspace_evidence(root: Path, surface: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        state = open_workspace(root, create=False)
    except WorkspaceError:
        return []
    normalized = surface.casefold().strip()
    items = state.list_review_items(limit=200)
    evidence: list[dict[str, Any]] = []
    for item in items:
        if item.normalized_surface != normalized and item.surface.casefold() != normalized:
            continue
        pack = dict(item.evidence_payload)
        for kind in ("positive_snippets", "negative_snippets"):
            snippets = pack.get(kind, [])
            if not isinstance(snippets, list):
                continue
            for snippet in snippets:
                if len(evidence) >= limit:
                    return evidence
                if isinstance(snippet, Mapping):
                    row = dict(snippet)
                    row["source"] = "workspace"
                    row["candidate_id"] = item.normalized_surface
                    evidence.append(row)
    return evidence


def _load_configured_lexicon(config: McpServerConfig, args: Mapping[str, Any]) -> tuple[Any, Path]:
    path = _optional_string(args, "lexicon_path")
    if path is not None:
        lexicon_path = Path(path)
        if not lexicon_path.is_absolute():
            lexicon_path = config.root / lexicon_path
    elif config.lexicon_path is not None:
        lexicon_path = config.lexicon_path
        if not lexicon_path.is_absolute():
            lexicon_path = config.root / lexicon_path
    else:
        lexicon_path = _default_lexicon_path(config.root)
    try:
        return load_lexicon(lexicon_path), lexicon_path
    except AgentLexiconLoadError as exc:
        raise McpServerError(f"invalid lexicon for MCP tool: {exc}") from exc


def _default_lexicon_path(root: Path) -> Path:
    candidates = (
        root / "lexicon" / "lexicon.yaml",
        root / "lexicon" / "lexicon.yml",
        root / "lexicon" / "lexicon.json",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise McpServerError("no default lexicon found; pass --lexicon or tool argument lexicon_path")


def _write_jsonrpc_response(output_stream: TextIO, response: Mapping[str, Any]) -> None:
    output_stream.write(json.dumps(dict(response), ensure_ascii=False, separators=(",", ":")) + "\n")
    output_stream.flush()


def _jsonrpc_result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpServerError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise McpServerError(f"{key} must be a string")
    stripped = value.strip()
    return stripped or None


def _optional_string_list(payload: Mapping[str, Any], key: str) -> tuple[str, ...] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise McpServerError(f"{key} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise McpServerError(f"{key} must contain only non-empty strings")
        items.append(item.strip())
    return tuple(items)


def _optional_bool(payload: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise McpServerError(f"{key} must be a boolean")
    return value


def _optional_int(payload: Mapping[str, Any], key: str, *, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int):
        raise McpServerError(f"{key} must be an integer")
    return value


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise McpServerError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise McpServerError(f"{field_name} must not be empty")
    return cleaned


def _package_version() -> str:
    from agent_lexicon import __version__

    return __version__


__all__ = [
    "DEFAULT_MCP_TOOLS",
    "MCP_PROTOCOL_VERSION",
    "McpServerConfig",
    "McpServerError",
    "call_mcp_tool",
    "handle_mcp_message",
    "mcp_tool_definitions",
    "mcp_tool_result",
    "run_mcp_stdio_server",
]
