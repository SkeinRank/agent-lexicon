# MCP server reference

Agent Lexicon ships an [MCP](https://modelcontextprotocol.io) server that
exposes the lexicon to any MCP-compatible agent over stdio. This lets an agent
resolve terminology, check language, and gate its own tool calls against a
reviewed vocabulary — without embedding the library directly.

## Running the server

```bash
agent-lexicon mcp serve --root . --lexicon lexicon/lexicon.yaml
```

The server speaks MCP protocol version `2024-11-05` over stdio. List the tool
definitions without starting a session:

```bash
agent-lexicon mcp tools
```

## Tools

The server exposes six tools. Every tool accepts an optional `lexicon_path`;
when omitted, the server uses the lexicon passed to `serve`.

### `resolve_term`

Resolve text against the local lexicon and report ambiguity.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `text` | string | yes | Text to resolve. |
| `scopes` | array of string | no | Restrict to these scopes. |
| `lexicon_path` | string | no | Override the server's lexicon file. |
| `include_deprecated` | boolean | no | Whether deprecated surfaces match. |

Returns the resolution status (`resolved` / `ambiguous` / `unknown`), the
candidate terms, and the `lexicon_snapshot_ref` used.

### `check_language`

Check whether text is known, resolved, or ambiguous in the local lexicon. Same
arguments as `resolve_term`. Use this for a lightweight "does this text touch
governed terminology?" check when you do not need full candidate detail.

### `guard_tool_call`

Check whether a requested tool call is safe for the resolved terminology.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `text` | string | yes | Text that triggered the tool call. |
| `tool_name` | string | yes | The tool the agent wants to call. |
| `scopes` | array of string | no | Restrict to these scopes. |
| `lexicon_path` | string | no | Override the server's lexicon file. |
| `include_deprecated` | boolean | no | Whether deprecated surfaces match. |

Returns `allowed`, `blocked`, or `needs_clarification`, plus the reason and the
tools that *are* allowed for the matched terms.

### `find_evidence`

Find lexicon or workspace evidence for a term id or surface.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `term_id` | string | no | Canonical term id to look up. |
| `surface` | string | no | Candidate surface or text to resolve first. |
| `lexicon_path` | string | no | Override the server's lexicon file. |
| `max_results` | integer | no | Cap on evidence spans returned. |

Provide either `term_id` or `surface`. Useful for showing a human *why* a term
exists before they accept a proposal.

### `submit_proposal`

Save a local review decision for a workspace candidate.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `candidate_id` | string | yes | Workspace candidate (normalized surface). |
| `decision` | string | yes | The review decision to record. |
| `note` | string | no | Reviewer note. |
| `reviewer` | string | no | Actor recorded in the provenance log. |

The decision is appended to the workspace decision provenance log, the same
append-only trail the CLI `review` command writes to.

### `get_snapshot`

Return local snapshot metadata from the workspace.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `limit` | integer | no | Number of snapshots to return. |
| `include_payload` | boolean | no | Include the full snapshot payload, not just metadata. |

Returns published snapshot records, each with its `lexicon_snapshot_ref`, so an
agent can confirm which vocabulary content a decision was made against.

## The deterministic boundary still holds

`resolve_term`, `check_language`, and `guard_tool_call` are deterministic: the
same text against the same lexicon content returns the same result over MCP,
just as it does through the CLI and the Python API. `find_evidence`,
`submit_proposal`, and `get_snapshot` touch the local workspace for review and
audit, not the hot resolve/guard path.

## Programmatic access

The same handlers are importable if you are building your own transport:

```python
from agent_lexicon import (
    mcp_tool_definitions,   # list the tool schemas
    call_mcp_tool,          # invoke a single tool
    handle_mcp_message,     # handle a full MCP JSON-RPC message
    run_mcp_stdio_server,   # run the stdio loop
)
```
