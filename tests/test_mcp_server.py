from __future__ import annotations

import io
import json
from pathlib import Path

from agent_lexicon import (
    McpServerConfig,
    call_mcp_tool,
    handle_mcp_message,
    mcp_tool_definitions,
    run_mcp_stdio_server,
)
from agent_lexicon.cli import main


EXAMPLE_LEXICON = Path(__file__).resolve().parents[1] / "examples" / "customer_limits" / "lexicon.yaml"


def test_mcp_tool_definitions_include_runtime_tools() -> None:
    names = {tool["name"] for tool in mcp_tool_definitions()}

    assert "resolve_term" in names
    assert "guard_tool_call" in names
    assert "find_evidence" in names
    assert "submit_proposal" in names
    assert "get_snapshot" in names


def test_call_mcp_tool_resolves_ambiguous_language() -> None:
    config = McpServerConfig(root=EXAMPLE_LEXICON.parents[0], lexicon_path=EXAMPLE_LEXICON)

    result = call_mcp_tool("resolve_term", {"text": "increase the limit"}, config=config)

    assert result["tool"] == "resolve_term"
    assert result["decision"]["status"] == "ambiguous"
    assert {candidate["term_id"] for candidate in result["decision"]["candidates"]} == {
        "api.rate_limit",
        "billing.credit_limit",
    }


def test_call_mcp_tool_guards_tool_call() -> None:
    config = McpServerConfig(root=EXAMPLE_LEXICON.parents[0], lexicon_path=EXAMPLE_LEXICON)

    result = call_mcp_tool(
        "guard_tool_call",
        {"text": "increase the limit", "tool_name": "api.update_rate_limit"},
        config=config,
    )

    assert result["decision"]["status"] == "needs_clarification"
    assert result["decision"]["is_allowed"] is False


def test_handle_mcp_initialize_and_tools_list() -> None:
    config = McpServerConfig(root=Path("."), lexicon_path=EXAMPLE_LEXICON)

    initialize = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        config=config,
    )
    tools = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        config=config,
    )

    assert initialize[0]["result"]["serverInfo"]["name"] == "agent-lexicon"
    assert any(tool["name"] == "resolve_term" for tool in tools[0]["result"]["tools"])


def test_mcp_stdio_server_handles_tool_call() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "resolve_term",
            "arguments": {"text": "increase the limit"},
        },
    }
    stdin = io.StringIO(json.dumps(request) + "\n")
    stdout = io.StringIO()

    exit_code = run_mcp_stdio_server(
        root=EXAMPLE_LEXICON.parents[0],
        lexicon_path=EXAMPLE_LEXICON,
        stdin=stdin,
        stdout=stdout,
    )

    assert exit_code == 0
    response = json.loads(stdout.getvalue().strip())
    content = json.loads(response["result"]["content"][0]["text"])
    assert response["id"] == 7
    assert content["decision"]["status"] == "ambiguous"


def test_mcp_submit_proposal_uses_policy(tmp_path: Path) -> None:
    config = McpServerConfig(root=tmp_path, policy_mode="team", actor="reviewer", role="reviewer")

    result = call_mcp_tool(
        "submit_proposal",
        {"candidate_id": "billing.update_credit_limit", "decision": "accepted", "note": "Looks canonical"},
        config=config,
    )

    assert result["policy"]["allowed"] is True
    assert result["review_decision"]["decision"] == "accepted"


def test_cli_mcp_tools(capsys) -> None:
    assert main(["mcp", "tools"]) == 0

    captured = capsys.readouterr()
    assert "Agent Lexicon MCP tools:" in captured.out
    assert "resolve_term" in captured.out
