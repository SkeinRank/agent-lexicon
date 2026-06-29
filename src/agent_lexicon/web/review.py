"""Local web proposal inbox for Agent Lexicon.

The inbox is a dependency-free localhost interface for reviewing scout candidates
stored in the SQLite workspace. It uses Python's standard library HTTP server so
local review can run immediately after installing the package.
"""

from __future__ import annotations

import html
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from agent_lexicon.policy import (
    LocalPolicyError,
    PolicyAction,
    PolicyDecision,
    check_local_policy,
    load_local_policy,
)
from agent_lexicon.workspace import (
    ReviewDecisionStatus,
    WorkspaceError,
    WorkspaceReviewItem,
    WorkspaceState,
    open_workspace,
)


class ReviewInboxError(ValueError):
    """Raised when the local proposal inbox cannot be rendered or served."""


_DECISION_LABELS = {
    ReviewDecisionStatus.ACCEPTED.value: "Accept",
    ReviewDecisionStatus.REJECTED.value: "Reject",
    ReviewDecisionStatus.AMBIGUOUS.value: "Mark ambiguous",
    ReviewDecisionStatus.NEEDS_SPLIT.value: "Needs split",
}


_STATUS_LABELS = {
    "unreviewed": "Unreviewed",
    ReviewDecisionStatus.ACCEPTED.value: "Accepted",
    ReviewDecisionStatus.REJECTED.value: "Rejected",
    ReviewDecisionStatus.AMBIGUOUS.value: "Ambiguous",
    ReviewDecisionStatus.NEEDS_SPLIT.value: "Needs split",
}


_CSS = """
:root {
  --bg: #f7f7f5;
  --panel: #ffffff;
  --text: #171717;
  --muted: #6f6f68;
  --subtle: #ecebe7;
  --line: #deddd7;
  --strong: #111111;
  --soft: #fafaf8;
  --accent: #20201d;
  --ok: #f2f8f3;
  --warn: #fff7ea;
  --danger: #fff1f0;
  --radius-lg: 24px;
  --radius-md: 16px;
  --radius-sm: 10px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: inherit; text-decoration: none; }
.shell {
  width: min(1180px, calc(100vw - 48px));
  margin: 0 auto;
  padding: 32px 0 48px;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  margin-bottom: 24px;
}
.eyebrow {
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: 30px;
  line-height: 1.1;
  letter-spacing: -0.035em;
}
.summary {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.pill {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 999px;
  padding: 7px 11px;
  color: var(--muted);
  font-size: 12px;
}
.grid {
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.035);
}
.sidebar { padding: 12px; }
.list-title {
  display: flex;
  justify-content: space-between;
  padding: 8px 10px 12px;
  color: var(--muted);
  font-size: 12px;
}
.item {
  display: block;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  padding: 13px;
  margin-bottom: 8px;
  background: transparent;
}
.item:hover { background: var(--soft); }
.item.active {
  background: var(--soft);
  border-color: var(--line);
}
.item-main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.surface {
  font-weight: 650;
  letter-spacing: -0.01em;
  word-break: break-word;
}
.priority {
  font-size: 11px;
  padding: 3px 7px;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.priority.important { background: #fef3c7; color: #7a4d00; }
.priority.later { background: var(--soft); color: var(--muted); }
.score {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.meta {
  color: var(--muted);
  font-size: 12px;
  margin-top: 7px;
}
.status {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 8px;
  margin-top: 9px;
  color: var(--muted);
  font-size: 11px;
}
.status.accepted { background: var(--ok); color: #1d5f2f; }
.status.rejected { background: var(--danger); color: #87231d; }
.status.ambiguous, .status.needs_split { background: var(--warn); color: #7a4d00; }
.detail { padding: 24px; }
.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--line);
}
.detail-title {
  margin: 0;
  font-size: 26px;
  line-height: 1.18;
  letter-spacing: -0.03em;
  word-break: break-word;
}
.kind {
  color: var(--muted);
  font-size: 13px;
  margin-top: 7px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 20px 0;
}
.metric {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--soft);
  padding: 13px;
}
.metric-label { color: var(--muted); font-size: 12px; }
.metric-value { margin-top: 4px; font-size: 18px; font-weight: 650; font-variant-numeric: tabular-nums; }
.section-title {
  margin: 24px 0 10px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.snippet {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin-bottom: 10px;
  background: var(--panel);
}
.snippet.positive { border-left: 4px solid #6aa66f; }
.snippet.negative { border-left: 4px solid #d8a34a; }
.snippet-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: var(--soft);
  color: var(--muted);
  font-size: 12px;
  border-bottom: 1px solid var(--line);
}
pre {
  margin: 0;
  padding: 13px 14px;
  overflow-x: auto;
  white-space: pre-wrap;
  color: #252522;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.actions {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}
textarea {
  width: 100%;
  min-height: 74px;
  resize: vertical;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 12px;
  font: inherit;
  background: var(--soft);
  color: var(--text);
  outline: none;
}
textarea:focus { border-color: var(--strong); background: #fff; }
.button-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}
button {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text);
  padding: 10px 12px;
  font: inherit;
  cursor: pointer;
}
button:hover { border-color: var(--strong); }
button:disabled { color: var(--muted); cursor: not-allowed; background: var(--subtle); }
button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.notice {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--soft);
  padding: 12px;
  color: var(--muted);
}
.empty {
  padding: 54px 24px;
  text-align: center;
  color: var(--muted);
}
.empty strong { display: block; color: var(--text); font-size: 18px; margin-bottom: 6px; }
.code {
  display: inline-block;
  margin-top: 14px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--soft);
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
@media (max-width: 860px) {
  .shell { width: min(100vw - 28px, 1180px); padding-top: 20px; }
  .topbar { align-items: flex-start; flex-direction: column; }
  .summary { justify-content: flex-start; }
  .grid { grid-template-columns: 1fr; }
  .metrics, .button-row { grid-template-columns: 1fr; }
  .detail-head { flex-direction: column; }
}
"""


def build_review_inbox_html(
    state: WorkspaceState,
    *,
    selected_surface: str | None = None,
    limit: int = 100,
    actor: str = "local",
    role: str | None = None,
    policy_mode: str | None = None,
) -> str:
    """Render the local proposal inbox as a complete HTML document."""
    if not isinstance(state, WorkspaceState):
        raise ReviewInboxError("state must be a WorkspaceState")
    items = state.list_review_items(limit=limit)
    selected = _select_item(state, items, selected_surface=selected_surface)
    policy = load_local_policy(state.root, mode=policy_mode)
    policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor, role=role)
    return _render_page(items=items, selected=selected, root=str(state.root), policy_decision=policy_decision)


def run_review_inbox(
    root: str | Path = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    actor: str = "local",
    role: str | None = None,
    policy_mode: str | None = None,
) -> None:
    """Run the local proposal inbox until interrupted."""
    if not host:
        raise ReviewInboxError("host must not be empty")
    if port < 1 or port > 65535:
        raise ReviewInboxError("port must be between 1 and 65535")
    state = open_workspace(root, create=True)
    policy = load_local_policy(state.root, mode=policy_mode)
    policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor, role=role)
    handler = _handler_for_state(state, actor=actor, policy_decision=policy_decision)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Review inbox: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Review inbox stopped.")
    finally:
        server.server_close()


def _handler_for_state(
    state: WorkspaceState,
    *,
    actor: str = "local",
    policy_decision: PolicyDecision | None = None,
) -> type[BaseHTTPRequestHandler]:
    if policy_decision is None:
        policy = load_local_policy(state.root)
        policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor)
    class ReviewInboxHandler(BaseHTTPRequestHandler):
        server_version = "AgentLexiconReview/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._send_text("ok\n", status=200)
                return
            if parsed.path == "/review-events.jsonl":
                try:
                    self._send_jsonl(state.export_review_events_jsonl())
                except WorkspaceError as exc:
                    self._send_text(f"Review event export error: {exc}\n", status=500)
                return
            if parsed.path != "/":
                self._send_text("Not found\n", status=404)
                return
            params = parse_qs(parsed.query)
            selected_surface = params.get("surface", [None])[0]
            try:
                content = build_review_inbox_html(
                    state,
                    selected_surface=selected_surface,
                    actor=policy_decision.actor,
                    role=policy_decision.role.value,
                    policy_mode=policy_decision.mode.value,
                )
            except (ReviewInboxError, WorkspaceError, LocalPolicyError) as exc:
                self._send_text(f"Review inbox error: {exc}\n", status=500)
                return
            self._send_html(content)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path != "/decision":
                self._send_text("Not found\n", status=404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            form = parse_qs(payload)
            normalized_surface = form.get("surface", [""])[0]
            decision = form.get("decision", [""])[0]
            note = form.get("note", [""])[0]
            if not policy_decision.is_allowed:
                self._send_text(f"Policy denied review decision: {policy_decision.reason}\n", status=403)
                return
            try:
                state.save_review_decision(normalized_surface, decision, note=note, reviewer=policy_decision.actor)
            except (ValueError, WorkspaceError) as exc:
                self._send_text(f"Invalid review decision: {exc}\n", status=400)
                return
            location = f"/?surface={quote(normalized_surface)}"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _send_html(self, content: str, *, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_text(self, content: str, *, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_jsonl(self, content: str, *, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="review-events.jsonl"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ReviewInboxHandler


def _select_item(
    state: WorkspaceState,
    items: tuple[WorkspaceReviewItem, ...],
    *,
    selected_surface: str | None,
) -> WorkspaceReviewItem | None:
    if selected_surface:
        item = state.get_review_item(unquote(selected_surface))
        if item is not None:
            return item
    return items[0] if items else None


def _render_page(
    *,
    items: tuple[WorkspaceReviewItem, ...],
    selected: WorkspaceReviewItem | None,
    root: str,
    policy_decision: PolicyDecision,
) -> str:
    reviewed_count = sum(1 for item in items if item.review_decision is not None)
    unreviewed_count = len(items) - reviewed_count
    selected_surface = selected.normalized_surface if selected is not None else ""
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Agent Lexicon Proposal Inbox</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            '<header class="topbar">',
            "<div>",
            '<p class="eyebrow">Agent Lexicon</p>',
            "<h1>Proposal Inbox</h1>",
            f'<div class="meta">Workspace root: {_escape(root)}</div>',
            "</div>",
            '<div class="summary">',
            f'<span class="pill">{len(items)} candidates</span>',
            f'<span class="pill">{unreviewed_count} unreviewed</span>',
            f'<span class="pill">{reviewed_count} reviewed</span>',
            f'<span class="pill">policy: {_escape(policy_decision.mode.value)} · {_escape(policy_decision.role.value)}</span>',
            '<a class="pill" href="/review-events.jsonl">Export JSONL</a>',
            "</div>",
            "</header>",
            '<section class="grid">',
            _render_sidebar(items, selected_surface=selected_surface),
            _render_detail(selected, policy_decision=policy_decision),
            "</section>",
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _item_quality(item: WorkspaceReviewItem) -> dict[str, Any]:
    metadata = item.candidate_payload.get("metadata", {}) if isinstance(item.candidate_payload, dict) else {}
    quality = metadata.get("quality", {}) if isinstance(metadata, dict) else {}
    return dict(quality) if isinstance(quality, dict) else {}


def _item_cluster(item: WorkspaceReviewItem) -> dict[str, Any]:
    metadata = item.candidate_payload.get("metadata", {}) if isinstance(item.candidate_payload, dict) else {}
    cluster = metadata.get("cluster", {}) if isinstance(metadata, dict) else {}
    return dict(cluster) if isinstance(cluster, dict) else {}


def _item_priority(item: WorkspaceReviewItem) -> str:
    priority = str(_item_quality(item).get("priority", "later"))
    return priority if priority in {"important", "later"} else "later"


def _item_quality_float(item: WorkspaceReviewItem, key: str) -> float:
    try:
        return float(_item_quality(item).get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _item_cluster_size(item: WorkspaceReviewItem) -> int:
    cluster = _item_cluster(item)
    try:
        return max(1, int(cluster.get("candidate_count", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _render_priority_reasons(item: WorkspaceReviewItem) -> str:
    quality = _item_quality(item)
    reasons = quality.get("priority_reasons", [])
    if not isinstance(reasons, list) or not reasons:
        return '<div class="meta">Priority reasons: none recorded.</div>'
    pills = "".join(f'<span class="pill">{_escape(str(reason))}</span>' for reason in reasons[:6])
    return f'<div class="summary">{pills}</div>'


def _render_sidebar(items: tuple[WorkspaceReviewItem, ...], *, selected_surface: str) -> str:
    if not items:
        return '<aside class="panel sidebar"><div class="empty"><strong>No candidates yet</strong><span>Run workspace sync to populate the inbox.</span></div></aside>'
    rows = [
        '<aside class="panel sidebar">',
        '<div class="list-title"><span>Candidates</span><span>Score</span></div>',
    ]
    for item in items:
        active = " active" if item.normalized_surface == selected_surface else ""
        priority = _item_priority(item)
        rows.append(
            f'<a class="item{active}" href="/?surface={quote(item.normalized_surface)}">'
            '<div class="item-main">'
            f'<span class="surface">{_escape(item.surface)}</span>'
            f'<span class="score">{item.score:.3f}</span>'
            '</div>'
            f'<div class="meta">{_escape(item.candidate_kind)} · {item.positive_count} positive · {item.negative_count} negative</div>'
            f'<span class="priority {priority}">{_escape(priority.upper())}</span>'
            f'<span class="status {_status_class(item.review_status)}">{_escape(_status_label(item.review_status))}</span>'
            '</a>'
        )
    rows.append("</aside>")
    return "\n".join(rows)


def _render_detail(item: WorkspaceReviewItem | None, *, policy_decision: PolicyDecision) -> str:
    if item is None:
        return (
            '<section class="panel detail">'
            '<div class="empty">'
            '<strong>No review items</strong>'
            '<span>Populate the workspace, then reopen this inbox.</span>'
            '<span class="code">agent-lexicon workspace sync docs --root .</span>'
            '</div>'
            '</section>'
        )
    return "\n".join(
        [
            '<section class="panel detail">',
            '<div class="detail-head">',
            "<div>",
            f'<h2 class="detail-title">{_escape(item.surface)}</h2>',
            f'<div class="kind">{_escape(item.candidate_kind)} · {item.occurrence_count} occurrences · {item.document_count} documents</div>',
            "</div>",
            f'<span class="status {_status_class(item.review_status)}">{_escape(_status_label(item.review_status))}</span>',
            "</div>",
            '<div class="metrics">',
            _metric("Score", f"{item.score:.3f}"),
            _metric("Jargon", f"{item.jargon_score:.3f}"),
            _metric("Background penalty", f"{item.background_penalty:.3f}"),
            _metric("OOV proxy", f"{_item_quality_float(item, 'oov_proxy_score'):.3f}"),
            _metric("Surface risk", f"{_item_quality_float(item, 'surface_risk_score'):.3f}"),
            _metric("Cluster", str(_item_cluster_size(item))),
            "</div>",
            _render_priority_reasons(item),
            _render_snippet_group("Positive evidence", item.evidence_payload.get("positive_snippets", []), "positive"),
            _render_snippet_group("Negative evidence", item.evidence_payload.get("negative_snippets", []), "negative"),
            _render_actions(item, policy_decision=policy_decision),
            "</section>",
        ]
    )


def _render_snippet_group(title: str, snippets: Any, css_kind: str) -> str:
    rows = [f'<h3 class="section-title">{_escape(title)}</h3>']
    if not isinstance(snippets, list) or not snippets:
        rows.append('<div class="meta">No snippets stored for this group.</div>')
        return "\n".join(rows)
    for snippet in snippets:
        if not isinstance(snippet, dict):
            continue
        path = _escape(str(snippet.get("document_path", "unknown")))
        start = _escape(str(snippet.get("start_line", "?")))
        end = _escape(str(snippet.get("end_line", start)))
        reason = _escape(str(snippet.get("reason", "evidence")))
        text = _escape(str(snippet.get("text", "")))
        rows.append(
            f'<article class="snippet {css_kind}">'
            '<div class="snippet-head">'
            f'<span>{path}:{start}-{end}</span>'
            f'<span>{reason}</span>'
            '</div>'
            f'<pre>{text}</pre>'
            '</article>'
        )
    return "\n".join(rows)


def _render_actions(item: WorkspaceReviewItem, *, policy_decision: PolicyDecision) -> str:
    note = item.review_decision.note if item.review_decision else ""
    buttons = []
    disabled = "" if policy_decision.is_allowed else " disabled"
    for decision, label in _DECISION_LABELS.items():
        class_name = ' class="primary"' if decision == ReviewDecisionStatus.ACCEPTED.value else ""
        buttons.append(f'<button{class_name}{disabled} type="submit" name="decision" value="{_escape(decision)}">{_escape(label)}</button>')
    notice = ""
    if not policy_decision.is_allowed:
        notice = f'<div class="notice">Read-only policy mode: {_escape(policy_decision.reason)}</div>'
    return "\n".join(
        [
            '<form class="actions" method="post" action="/decision">',
            f'<input type="hidden" name="surface" value="{_escape(item.normalized_surface)}">',
            '<h3 class="section-title">Review decision</h3>',
            notice,
            f'<textarea name="note" placeholder="Optional reviewer note"{disabled}>{_escape(note)}</textarea>',
            '<div class="button-row">',
            *buttons,
            "</div>",
            "</form>",
        ]
    )


def _metric(label: str, value: str) -> str:
    return (
        '<div class="metric">'
        f'<div class="metric-label">{_escape(label)}</div>'
        f'<div class="metric-value">{_escape(value)}</div>'
        '</div>'
    )


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


def _status_class(status: str) -> str:
    return status.replace("_", "-").replace(" ", "-")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
