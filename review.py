"""Local web proposal inbox for Agent Lexicon.

The inbox is a dependency-free localhost interface for reviewing scout candidates
stored in the SQLite workspace. It uses Python's standard library HTTP server so
local review can run immediately after installing the package.
"""

from __future__ import annotations

import html
import json
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
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
    WorkspaceDecisionAction,
    WorkspaceError,
    WorkspaceReviewItem,
    WorkspaceStore,
    open_workspace,
)


_LEGACY_STARTER_TERM_ID = "project.example_term"
_LEGACY_STARTER_CANONICAL = "example term"
_LOCAL_DISPLAY_ACTOR_IDS = {"", "local", "unknown", "unknown-actor"}


def _git_config_value(root: str | Path, key: str) -> str:
    """Return a local git config value without failing outside git worktrees."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(Path(root).resolve()), "config", "--get", key],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _default_web_actor_id(root: str | Path) -> str | None:
    """Resolve the human actor displayed for web review decisions."""
    name = _git_config_value(root, "user.name")
    if name:
        return name
    return None


def _is_local_display_actor(value: str) -> bool:
    return value.strip().casefold() in _LOCAL_DISPLAY_ACTOR_IDS


def _display_actor_id(actor: dict[str, Any], git: dict[str, Any], reviewer: str) -> str:
    actor_type = str(actor.get("type", "human") or "human")
    actor_id = str(actor.get("id", reviewer) or reviewer).strip()
    if actor_type == "agent" and _is_local_display_actor(actor_id):
        return "Agent"
    if actor_type == "system" and _is_local_display_actor(actor_id):
        return "System"
    if _is_local_display_actor(actor_id):
        git_name = str(git.get("author_name", "") or "").strip()
        if git_name:
            return git_name
        return "Human"
    if actor_type == "human" and "@" in actor_id:
        return "Human"
    return actor_id


def _display_actor_source(actor: dict[str, Any]) -> str:
    actor_type = str(actor.get("type", "human") or "human")
    actor_source = str(actor.get("source", "") or "").strip()
    if actor_source and actor_source.casefold() != "unknown":
        return actor_source
    if actor_type == "agent":
        return "mcp"
    if actor_type == "system":
        return "system"
    return "local"


def _is_web_hidden_starter_term(term: Any) -> bool:
    """Return True for starter placeholder terms that should not be shown.

    Current dictionaries mark generated starter terms with metadata.starter.
    Older workspaces can still contain the original starter term without that
    flag, so the web UI also recognizes the legacy id/canonical pair.
    """
    if bool(getattr(term, "is_starter", False)):
        return True
    return (
        getattr(term, "id", "") == _LEGACY_STARTER_TERM_ID
        and getattr(term, "canonical", "").casefold() == _LEGACY_STARTER_CANONICAL
    )


def _term_as_lexicon_tab_dict(
    term: Any,
    *,
    source: str,
    snapshot_id: str = "",
    snapshot_created_at: str = "",
    publish_checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checkpoint = dict(publish_checkpoint or {})
    review_decision = checkpoint.get("review_decision")
    return {
        "id": term.id,
        "canonical": term.canonical,
        "scopes": list(term.scopes),
        "tools": list(term.tools),
        "aliases": [alias.surface for alias in term.aliases],
        "deprecated": bool(term.deprecated),
        "source": source,
        "snapshot_id": snapshot_id,
        "snapshot_created_at": snapshot_created_at,
        "review_surface": str(checkpoint.get("surface", "") or getattr(term, "canonical", "") or ""),
        "review_normalized_surface": str(checkpoint.get("normalized_surface", "") or getattr(term, "canonical", "") or ""),
        "publish_checkpoint": checkpoint or None,
        "review_decision_provenance": _review_decision_snapshot_as_ui_dict(review_decision)
        if isinstance(review_decision, Mapping)
        else None,
    }


def _terms_from_lexicon(
    lexicon: Any,
    *,
    source: str,
    snapshot_id: str = "",
    snapshot_created_at: str = "",
    publish_records_by_surface: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    records = publish_records_by_surface or {}
    for term in lexicon.terms:
        if _is_web_hidden_starter_term(term):
            continue
        publish_checkpoint = (
            records.get(str(term.canonical).casefold())
            or records.get(str(term.id).casefold())
        )
        terms.append(
            _term_as_lexicon_tab_dict(
                term,
                source=source,
                snapshot_id=snapshot_id,
                snapshot_created_at=snapshot_created_at,
                publish_checkpoint=publish_checkpoint,
            )
        )
    terms.sort(key=lambda t: t["id"])
    return terms


def _latest_published_snapshot_terms(root: str | Path) -> list[dict[str, Any]]:
    """Return terms from the newest published workspace snapshot, if available."""
    try:
        from agent_lexicon.core import AgentLexiconLoadError, load_lexicon
    except ImportError:
        return []
    try:
        state = open_workspace(root, create=False)
        snapshots = state.list_snapshots(limit=1)
    except (WorkspaceError, OSError, ValueError):
        return []
    if not snapshots:
        return []
    snapshot = snapshots[0]
    snapshot_path = Path(snapshot.output_path)
    if not snapshot_path.exists():
        return []
    try:
        lexicon = load_lexicon(snapshot_path, document_format="json")
    except (AgentLexiconLoadError, OSError, ValueError):
        return []
    return _terms_from_lexicon(
        lexicon,
        source="snapshot",
        snapshot_id=snapshot.snapshot_id,
        snapshot_created_at=snapshot.created_at,
        publish_records_by_surface=_published_records_by_surface(root),
    )


def _dictionary_lexicon_terms(root: str | Path) -> list[dict[str, Any]]:
    try:
        from agent_lexicon.dictionary import dictionary_layout_path
        from agent_lexicon.core import AgentLexiconLoadError, load_lexicon
    except ImportError:
        return []
    layout = dictionary_layout_path(root)
    lexicon_path = Path(layout.lexicon_path)
    if not lexicon_path.exists():
        return []
    try:
        lexicon = load_lexicon(lexicon_path)
    except (AgentLexiconLoadError, OSError, ValueError):
        return []
    return _terms_from_lexicon(lexicon, source="dictionary")


def _accepted_terms(root: str | Path) -> list[dict[str, Any]]:
    """Return the latest published terminology shown in the Lexicon tab.

    ``agent-lexicon publish`` writes a local snapshot by default, while
    ``agent-lexicon publish --update-lexicon`` also rewrites lexicon/lexicon.yaml.
    The web UI treats the newest snapshot as the freshest published state so the
    Lexicon tab updates immediately after the normal publish command. If no
    snapshot exists yet, it falls back to the git-tracked dictionary file.
    """
    snapshot_terms = _latest_published_snapshot_terms(root)
    if snapshot_terms:
        return snapshot_terms
    return _dictionary_lexicon_terms(root)


def _published_terms_by_surface(terms: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(term.get("canonical", "")).casefold(): term for term in terms if str(term.get("canonical", "")).strip()}


def _published_records_by_surface(root: str | Path) -> dict[str, dict[str, Any]]:
    """Return publish ledger entries from the newest workspace snapshot."""
    try:
        state = open_workspace(root, create=False)
        latest_snapshots = state.list_snapshots(limit=1)
        if not latest_snapshots:
            return {}
        latest_snapshot_id = latest_snapshots[0].snapshot_id
        records = state.list_decision_records(action=WorkspaceDecisionAction.SNAPSHOT_PUBLISHED)
    except (WorkspaceError, OSError, ValueError):
        return {}

    published: dict[str, dict[str, Any]] = {}
    for record in reversed(records):
        if record.subject != latest_snapshot_id:
            continue
        for decision in _published_decisions_from_record(record):
            surface = str(decision.get("surface", "") or "").strip()
            normalized_surface = str(decision.get("normalized_surface", "") or "").strip()
            term_id = str(decision.get("term_id", "") or "").strip()
            if not surface and not normalized_surface and not term_id:
                continue
            ui_record = _publish_record_as_ui_dict(record, decision)
            for key in {surface.casefold(), normalized_surface.casefold(), term_id.casefold()}:
                if key and key not in published:
                    published[key] = ui_record
    return published


def _publish_history_by_surface(root: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Return publish ledger history keyed by candidate surface.

    This is intentionally based on publish checkpoints, not raw review clicks.
    Each row says what state a reviewed candidate had when a snapshot was
    published and whether that state was included in the lexicon.
    """
    try:
        state = open_workspace(root, create=False)
        records = state.list_decision_records(action=WorkspaceDecisionAction.SNAPSHOT_PUBLISHED)
    except (WorkspaceError, OSError, ValueError):
        return {}

    history: dict[str, list[dict[str, Any]]] = {}
    for record in reversed(records):
        for decision in _published_decisions_from_record(record):
            if not isinstance(decision, Mapping):
                continue
            surface = str(decision.get("surface", "") or "").strip()
            normalized_surface = str(decision.get("normalized_surface", "") or "").strip()
            term_id = str(decision.get("term_id", "") or "").strip()
            if not surface and not normalized_surface and not term_id:
                continue
            ui_record = _publish_record_as_ui_dict(record, decision)
            for key in {surface.casefold(), normalized_surface.casefold(), term_id.casefold()}:
                if key:
                    history.setdefault(key, []).append(ui_record)
    return history


def _published_decisions_from_record(record: Any) -> list[Mapping[str, Any]]:
    payload = record.payload if isinstance(record.payload, Mapping) else {}
    decisions = payload.get("published_decisions")
    if not isinstance(decisions, list):
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        publish_metadata = metadata.get("publish", {}) if isinstance(metadata.get("publish", {}), Mapping) else {}
        decisions = publish_metadata.get("published_decisions", [])
    if not isinstance(decisions, list):
        return []
    return [decision for decision in decisions if isinstance(decision, Mapping)]


def _review_decision_snapshot_as_ui_dict(decision: Mapping[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata", {}) if isinstance(decision.get("metadata", {}), Mapping) else {}
    actor = metadata.get("actor", {}) if isinstance(metadata.get("actor", {}), Mapping) else {}
    git = metadata.get("git", {}) if isinstance(metadata.get("git", {}), Mapping) else {}
    reviewer = str(decision.get("reviewer", "local") or "local")
    return {
        "decision": str(decision.get("decision", "") or ""),
        "note": str(decision.get("note", "") or ""),
        "reviewer": reviewer,
        "created_at": str(decision.get("updated_at", decision.get("created_at", "")) or ""),
        "actor": {
            "type": str(actor.get("type", "human") or "human"),
            "id": str(actor.get("id", reviewer) or reviewer),
            "source": str(actor.get("source", "web") or "web"),
            "display_id": _display_actor_id(dict(actor), dict(git), reviewer),
            "display_source": _display_actor_source(dict(actor)),
        },
        "git": {
            "branch": str(git.get("branch", "") or ""),
            "commit": str(git.get("commit", "") or ""),
            "dirty": bool(git.get("dirty", False)),
            "available": bool(git.get("available", False)),
        },
    }


def _publish_record_as_ui_dict(record: Any, decision: Mapping[str, Any]) -> dict[str, Any]:
    metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
    payload = record.payload if isinstance(record.payload, Mapping) else {}
    actor = metadata.get("actor", {}) if isinstance(metadata.get("actor", {}), Mapping) else {}
    git = metadata.get("git", {}) if isinstance(metadata.get("git", {}), Mapping) else {}
    snapshot_payload = payload.get("snapshot", {}) if isinstance(payload.get("snapshot", {}), Mapping) else {}
    publish_metadata = metadata.get("publish", {}) if isinstance(metadata.get("publish", {}), Mapping) else {}
    reviewer = str(actor.get("id", "local") or "local")
    return {
        "surface": str(decision.get("surface", "") or ""),
        "normalized_surface": str(decision.get("normalized_surface", "") or ""),
        "snapshot_id": str(record.subject or snapshot_payload.get("snapshot_id", "") or ""),
        "created_at": str(record.created_at or snapshot_payload.get("created_at", "") or ""),
        "decision": str(decision.get("decision", "") or ""),
        "result": str(decision.get("result", "published") or "published"),
        "term_id": str(decision.get("term_id", "") or ""),
        "included_in_lexicon": bool(decision.get("included_in_lexicon", decision.get("result") in {"generated_term", "skipped_existing_surface"})),
        "review_decision": dict(decision.get("review_decision", {})) if isinstance(decision.get("review_decision", {}), Mapping) else {},
        "accepted_count": int(publish_metadata.get("accepted_count", snapshot_payload.get("accepted_count", 0)) or 0),
        "generated_term_count": int(publish_metadata.get("generated_term_count", snapshot_payload.get("generated_term_count", 0)) or 0),
        "skipped_count": int(publish_metadata.get("skipped_count", snapshot_payload.get("skipped_count", 0)) or 0),
        "actor": {
            "type": str(actor.get("type", "human") or "human"),
            "id": str(actor.get("id", reviewer) or reviewer),
            "source": str(actor.get("source", "cli") or "cli"),
            "display_id": _display_actor_id(dict(actor), dict(git), reviewer),
            "display_source": _display_actor_source(dict(actor)),
        },
        "git": {
            "branch": str(git.get("branch", "") or ""),
            "commit": str(git.get("commit", "") or ""),
            "dirty": bool(git.get("dirty", False)),
            "available": bool(git.get("available", False)),
        },
    }


class ReviewInboxError(ValueError):
    """Raised when the local proposal inbox cannot be rendered or served."""


_MAX_POST_BYTES = 1_048_576  # 1 MiB cap for review-decision POST bodies


_STATUS_LABELS = {
    "unreviewed": "Unreviewed",
    ReviewDecisionStatus.ACCEPTED.value: "Accepted",
    ReviewDecisionStatus.REJECTED.value: "Rejected",
    ReviewDecisionStatus.AMBIGUOUS.value: "Ambiguous",
    ReviewDecisionStatus.NEEDS_SPLIT.value: "Needs split",
}


_THEME_BOOTSTRAP_JS = """
(function(){
  try {
    var key = 'agent-lexicon-theme';
    var mode = localStorage.getItem(key) || 'system';
    if (mode !== 'light' && mode !== 'dark' && mode !== 'system') mode = 'system';
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var resolved = mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode;
    document.documentElement.setAttribute('data-theme-mode', mode);
    document.documentElement.setAttribute('data-theme', resolved);
  } catch (e) {
    document.documentElement.setAttribute('data-theme-mode', 'system');
  }
})();
"""


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
  --accent-contrast: #ffffff;
  --ok: #f2f8f3;
  --ok-text: #1d5f2f;
  --warn: #fff7ea;
  --warn-text: #7a4d00;
  --danger: #fff1f0;
  --danger-text: #87231d;
  --priority-important-bg: #fef3c7;
  --priority-important-text: #7a4d00;
  --scope-bg: #eef;
  --scope-text: #3730a3;
  --code-text: #252522;
  --term-hit-bg: rgba(245, 190, 70, 0.28);
  --term-hit-border: rgba(190, 120, 20, 0.45);
  --term-hit-text: inherit;
  --focus-bg: #ffffff;
  --timeline-sticky-bg: rgba(255, 255, 255, 0.92);
  --shadow: 0 18px 60px rgba(0, 0, 0, 0.035);
  --radius-lg: 24px;
  --radius-md: 16px;
  --radius-sm: 10px;
  color-scheme: light;
}
:root[data-theme="dark"] {
  --bg: #0f1115;
  --panel: #171a21;
  --text: #e7eaf0;
  --muted: #9aa3b2;
  --subtle: #202532;
  --line: #2d3545;
  --strong: #f3f6fb;
  --soft: #141923;
  --accent: #e7eaf0;
  --accent-contrast: #0f1115;
  --ok: #142719;
  --ok-text: #8ad39a;
  --warn: #2b2112;
  --warn-text: #e4b86d;
  --danger: #2d1719;
  --danger-text: #f09a95;
  --priority-important-bg: #30250d;
  --priority-important-text: #e4b86d;
  --scope-bg: #191d3a;
  --scope-text: #aeb8ff;
  --code-text: #d9deea;
  --term-hit-bg: rgba(245, 190, 70, 0.22);
  --term-hit-border: rgba(245, 190, 70, 0.55);
  --term-hit-text: #f8e6b0;
  --focus-bg: #1d2330;
  --timeline-sticky-bg: rgba(23, 26, 33, 0.92);
  --shadow: 0 20px 70px rgba(0, 0, 0, 0.36);
  color-scheme: dark;
}
* { box-sizing: border-box; }
html { height: 100%; }
body {
  height: 100vh;
  overflow: hidden;
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: inherit; text-decoration: none; }
.shell {
  width: min(1180px, calc(100vw - 48px));
  height: 100vh;
  margin: 0 auto;
  padding: 20px 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}
.topbar {
  flex: 0 0 auto;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  margin-bottom: 14px;
}
.eyebrow {
  margin: 0 0 3px;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.1;
  letter-spacing: -0.03em;
}
.topbar .meta {
  max-width: 560px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 20px;
  align-items: stretch;
}
.panel {
  min-height: 0;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
}
.sidebar {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px;
}
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
.priority.important { background: var(--priority-important-bg); color: var(--priority-important-text); }
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
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 4px 10px;
  margin-top: 9px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}
.detail-head .status {
  align-self: flex-start;
  min-height: 44px;
  min-width: 86px;
  padding: 0 14px;
  margin-top: 0;
}
.status.accepted { background: var(--ok); color: var(--ok-text); }
.status.rejected { background: var(--danger); color: var(--danger-text); }
.status.ambiguous, .status.needs_split { background: var(--warn); color: var(--warn-text); }
.detail {
  min-height: 0;
  max-height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
  scroll-padding-bottom: 96px;
  padding: 24px;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
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
.state-summary {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 10px;
  max-width: 760px;
}
.state-row {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 12px;
}
.state-row strong { color: var(--text); font-weight: 650; }
.state-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
.state-badge { border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; color: var(--muted); background: var(--soft); font-size: 11px; }
.state-badge.ok { background: var(--ok); color: var(--ok-text); }
.state-badge.warn { background: var(--warn); color: var(--warn-text); }
.publish-history { margin: 14px 0 4px; }
.publish-history > summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--muted);
  font-size: 12px;
}
.publish-history-count { border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; color: var(--muted); background: var(--soft); font-size: 11px; }
.publish-ledger-box { margin-top: 10px; border: 1px solid var(--line); border-radius: var(--radius-md); overflow: hidden; background: var(--panel); }
.publish-history-row { display: grid; grid-template-columns: minmax(145px, 0.78fr) minmax(0, 2fr); gap: 14px; padding: 12px 14px; border-bottom: 1px solid var(--line); font-size: 12px; color: var(--muted); }
.publish-history-row:last-child { border-bottom: 0; }
.publish-history-row strong { color: var(--text); }
.publish-history-snapshot { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-word; color: var(--text); }
.publish-history-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.publish-history-note { color: var(--muted); font-size: 11px; margin-top: 3px; }
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
  color: var(--code-text);
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
mark.term-hit {
  background: var(--term-hit-bg);
  border-bottom: 1px solid var(--term-hit-border);
  border-radius: 4px;
  color: var(--term-hit-text);
  padding: 0 2px;
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
textarea:focus { border-color: var(--strong); background: var(--focus-bg); }
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
button.primary { background: var(--accent); color: var(--accent-contrast); border-color: var(--accent); }
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
  body { height: auto; min-height: 100vh; overflow: auto; }
  .shell { width: min(100vw - 28px, 1180px); height: auto; min-height: 100vh; overflow: visible; padding-top: 20px; }
  .topbar { align-items: flex-start; flex-direction: column; }
  .summary { justify-content: flex-start; }
  .grid { grid-template-columns: 1fr; min-height: auto; }
  .sidebar, .detail { max-height: none; overflow: visible; }
  .metrics, .button-row { grid-template-columns: 1fr; }
  .detail-head { flex-direction: column; }
}
.progress-wrap { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); }
.progress-track { width: 150px; height: 6px; background: var(--subtle); border-radius: 999px; overflow: hidden; }
.progress-bar { height: 100%; width: 0; background: var(--accent); border-radius: 999px; transition: width 0.2s; }
.toolbar { display: flex; gap: 8px; padding: 4px 6px 12px; }
.search { flex: 1; border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 11px; font: inherit; background: var(--soft); color: var(--text); outline: none; }
.search:focus { border-color: var(--strong); background: var(--focus-bg); }
.filter { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; font: inherit; background: var(--soft); color: var(--text); cursor: pointer; }
.sidebar-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding-right: 2px; }
.timeline-section { margin-bottom: 12px; }
.timeline-section-head {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 8px 10px 7px;
  margin: 2px 0 6px;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  background: var(--timeline-sticky-bg);
  border-bottom: 1px solid var(--subtle);
  backdrop-filter: blur(8px);
}
.timeline-section-head span:last-child {
  letter-spacing: 0;
  text-transform: none;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 1px 7px;
  background: var(--soft);
}
.cluster-head { display: flex; align-items: center; gap: 8px; padding: 9px 10px; border-radius: var(--radius-sm); cursor: pointer; color: var(--muted); font-size: 12px; }
.cluster-head:hover { background: var(--soft); }
.cluster-head .caret { transition: transform 0.15s; }
.cluster-head.open .caret { transform: rotate(90deg); }
.cluster-count { margin-left: auto; border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; }
.cluster-body { padding-left: 8px; }
.item.done { opacity: 0.5; }
.item .dec-icon { margin-right: 5px; }
.chip { display: inline-block; font-size: 11px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); background: var(--soft); margin: 0 5px 5px 0; }
.chip.accent { border-color: var(--line); color: var(--text); }
.kbd { font: 11px ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--soft); border: 1px solid var(--line); border-radius: 5px; padding: 1px 6px; color: var(--muted); }
.hint-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); }
.hint-bar .keys { display: flex; gap: 14px; flex-wrap: wrap; }
.scores-panel { margin-top: 12px; margin-bottom: 12px; }
.scores-toggle { cursor: pointer; color: var(--muted); font-size: 12px; margin-top: 6px; }
.scores-box { margin-top: 10px; max-width: 320px; }
.scores-row { display: flex; justify-content: space-between; padding: 5px 0; font-size: 12px; border-bottom: 1px solid var(--subtle); }
.scores-row span:last-child { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cluster-note { display: inline-flex; align-items: center; gap: 5px; margin-top: 8px; font-size: 12px; color: var(--muted); }
.accept-cluster { margin-left: auto; }
.tabs { display: flex; gap: 4px; }
.tab { border: 0.5px solid transparent; border-radius: 999px; padding: 6px 14px; font-size: 13px; color: var(--muted); cursor: pointer; background: transparent; }
.tab:hover { background: var(--soft); }
.tab.active { background: var(--panel); border-color: var(--line); color: var(--text); }
.theme-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel);
}
.theme-switch button {
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  padding: 5px 9px;
  font-size: 12px;
  line-height: 1;
}
.theme-switch button:hover { color: var(--text); background: var(--soft); }
.theme-switch button.active { background: var(--accent); color: var(--accent-contrast); }
.theme-switch-label { color: var(--muted); font-size: 11px; padding: 0 4px 0 7px; }
.actionbar { position: sticky; bottom: 0; z-index: 5; background: var(--panel); border-top: 1px solid var(--line); padding: 14px 24px; margin: 18px -24px -24px; display: flex; gap: 10px; align-items: center; flex-wrap: nowrap; overflow-x: auto; border-bottom-left-radius: var(--radius-lg); border-bottom-right-radius: var(--radius-lg); scrollbar-width: thin; }
.actionbar button { flex: 0 0 auto; white-space: nowrap; }
.lex-term { border: 1px solid var(--line); border-radius: var(--radius-md); padding: 14px 16px; margin-bottom: 10px; background: var(--panel); }
.lex-canonical { font-size: 16px; font-weight: 650; }
.lex-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--muted); }
.lex-alias { display: inline-block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: var(--soft); border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; margin: 4px 4px 0 0; }
.lex-scope { display: inline-block; font-size: 11px; background: var(--scope-bg); border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; margin-right: 5px; color: var(--scope-text); }
.lex-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; padding: 4px 4px 16px; border-bottom: 1px solid var(--line); margin-bottom: 14px; }
.lex-header h2 { margin: 0; font-size: 22px; line-height: 1.15; letter-spacing: -0.03em; }
.lex-header .meta { max-width: 640px; }
.lex-term { padding: 0; overflow: hidden; }
.lex-term > summary { list-style: none; cursor: pointer; padding: 14px 16px; }
.lex-term > summary::-webkit-details-marker { display: none; }
.lex-term > summary:hover { background: var(--soft); }
.lex-summary { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
.lex-detail { border-top: 1px solid var(--line); padding: 14px 16px 16px; background: var(--soft); }
.lex-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 10px 0 12px; }
.lex-detail-card { border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel); padding: 10px 12px; }
.lex-detail-card strong { display:block; margin-bottom: 4px; }
.lex-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top: 12px; }
.lex-actions button { padding: 8px 10px; font-size: 12px; }
.lex-actions button.primary { color: var(--accent-contrast); }
@media (max-width: 860px) { .lex-header { flex-direction: column; } .lex-detail-grid { grid-template-columns: 1fr; } }
"""


_APP_JS = r"""
(function(){
  var el = document.getElementById('review-data');
  var DATA = JSON.parse(el.textContent);
  var app = document.getElementById('app');
  var items = DATA.items;
  var lexicon = DATA.lexicon || [];
  var termTargets = DATA.termTargets || [];
  var view = 'review';
  var idx = 0;
  var search = '';
  var filter = 'all';
  var collapsed = {};
  var THEME_STORAGE_KEY = 'agent-lexicon-theme';
  var themeMode = readThemeMode();

  function readThemeMode(){
    try {
      var stored = localStorage.getItem(THEME_STORAGE_KEY);
      if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
    } catch (e) {}
    return 'system';
  }

  function systemPrefersDark(){
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function resolvedTheme(mode){
    return mode === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : mode;
  }

  function applyTheme(mode){
    if (mode !== 'light' && mode !== 'dark' && mode !== 'system') mode = 'system';
    themeMode = mode;
    document.documentElement.setAttribute('data-theme-mode', mode);
    document.documentElement.setAttribute('data-theme', resolvedTheme(mode));
    try { localStorage.setItem(THEME_STORAGE_KEY, mode); } catch (e) {}
    updateThemeSwitch();
  }

  items.forEach(function(it){
    if (it.cluster_key && it.cluster_size > 1) {
      if (collapsed[it.cluster_key] === undefined) collapsed[it.cluster_key] = true;
    }
  });
  var sel = DATA.selected;
  if (sel) { for (var i=0;i<items.length;i++){ if(items[i].normalized_surface===sel){ idx=i; break; } } }

  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function isDecided(it){ return it.decision != null; }
  function priorityWord(it){ return it.priority === 'important' ? (it.score >= 0.6 ? 'high' : 'medium') : 'low'; }

  function hasReviewHistory(it){
    return isDecided(it) || !!(it.publish_history && it.publish_history.length);
  }

  function visibleIndexes(){
    var out = [];
    for (var i=0;i<items.length;i++){
      var it = items[i];
      if (filter === 'unreviewed' && isDecided(it)) continue;
      if (filter === 'important' && it.priority !== 'important') continue;
      if (filter === 'history' && !hasReviewHistory(it)) continue;
      if (search && it.surface.toLowerCase().indexOf(search.toLowerCase()) === -1) continue;
      out.push(i);
    }
    return out;
  }

  function groupVisible(vis){
    var groups = [];
    var byKey = {};
    vis.forEach(function(i){
      var it = items[i];
      var key = (it.cluster_key && it.cluster_size > 1) ? it.cluster_key : null;
      if (key === null){ groups.push({key:null, idxs:[i]}); return; }
      if (byKey[key] === undefined){ byKey[key] = {key:key, idxs:[]}; groups.push(byKey[key]); }
      byKey[key].idxs.push(i);
    });
    return groups;
  }

  function reviewedCount(){ var n=0; items.forEach(function(it){ if(isDecided(it)) n++; }); return n; }

  function decIcon(it){
    if (it.decision === 'accepted') return '<span class="dec-icon" style="color:#1d5f2f">\u2713</span>';
    if (it.decision === 'rejected') return '<span class="dec-icon" style="color:#87231d">\u2717</span>';
    if (it.decision) return '<span class="dec-icon" style="color:#7a4d00">\u2022</span>';
    return '';
  }

  function renderSidebar(){
    return renderToolbar() + '<div class="sidebar-list" id="sblist">' + renderListInner() + '</div>';
  }

  function renderToolbar(){
    var h = '<div class="toolbar">';
    h += '<input class="search" id="q" placeholder="Search terms" value="'+esc(search)+'">';
    h += '<select class="filter" id="f">';
    h += '<option value="all"'+(filter==='all'?' selected':'')+'>All</option>';
    h += '<option value="unreviewed"'+(filter==='unreviewed'?' selected':'')+'>Unreviewed</option>';
    h += '<option value="important"'+(filter==='important'?' selected':'')+'>Important</option>';
    h += '<option value="history"'+(filter==='history'?' selected':'')+'>History</option>';
    h += '</select>';
    h += '</div>';
    return h;
  }

  function renderListInner(){
    var vis = visibleIndexes();
    var sections = timelineSections(vis);
    var h = '';
    if (vis.length === 0){ h += '<div class="meta" style="padding:16px">No terms match.</div>'; }
    sections.forEach(function(section){
      h += '<div class="timeline-section">';
      h += '<div class="timeline-section-head"><span>'+esc(section.label)+'</span><span>'+section.idxs.length+'</span></div>';
      h += renderGroupedItems(section.idxs);
      h += '</div>';
    });
    return h;
  }

  function renderGroupedItems(idxs){
    var groups = groupVisible(idxs);
    var h = '';
    groups.forEach(function(g){
      if (g.key === null){
        g.idxs.forEach(function(i){ h += itemRow(i); });
      } else {
        var open = !collapsed[g.key];
        var pending = g.idxs.filter(function(i){ return !isDecided(items[i]); }).length;
        h += '<div class="cluster-head '+(open?'open':'')+'" data-cluster="'+esc(g.key)+'">';
        h += '<span class="caret">\u203a</span>';
        h += '<span style="font-family:ui-monospace,Menlo,monospace">'+esc(g.key)+'</span>';
        h += '<span class="cluster-count">'+pending+' / '+g.idxs.length+'</span>';
        h += '</div>';
        if (open){ h += '<div class="cluster-body">'; g.idxs.forEach(function(i){ h += itemRow(i); }); h += '</div>'; }
      }
    });
    return h;
  }

  function timelineSections(vis){
    var buckets = {needs: [], today: [], yesterday: [], older: []};
    vis.forEach(function(i){ buckets[timelineBucket(items[i])].push(i); });
    var defs = [
      ['needs', 'Needs review'],
      ['today', 'Reviewed today'],
      ['yesterday', 'Reviewed yesterday'],
      ['older', 'Reviewed earlier']
    ];
    return defs.filter(function(def){ return buckets[def[0]].length > 0; })
      .map(function(def){ return {key:def[0], label:def[1], idxs:buckets[def[0]]}; });
  }

  function timelineBucket(it){
    if (!hasReviewHistory(it)) return 'needs';
    var d = itemTimelineDate(it);
    if (!d) return 'older';
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    var itemDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    if (itemDay.getTime() === today.getTime()) return 'today';
    if (itemDay.getTime() === yesterday.getTime()) return 'yesterday';
    return 'older';
  }

  function itemTimelineDate(it){
    var raw = '';
    if (it.decision_provenance && it.decision_provenance.created_at) raw = it.decision_provenance.created_at;
    if (!raw && it.publish_history && it.publish_history.length && it.publish_history[0].created_at) raw = it.publish_history[0].created_at;
    if (!raw) return null;
    var parsed = new Date(raw);
    if (isNaN(parsed.getTime())) return null;
    return parsed;
  }

  function itemRow(i){
    var it = items[i];
    var active = (i === idx) ? ' active' : '';
    var done = isDecided(it) ? ' done' : '';
    return '<a class="item'+active+done+'" data-idx="'+i+'">'
      + '<div class="item-main">'
      + '<span class="surface" style="font-family:ui-monospace,Menlo,monospace;font-size:13px">'+esc(it.surface)+'</span>'
      + '<span class="score">'+decIcon(it)+priorityWord(it)+'</span>'
      + '</div>'
      + '<div class="meta">'+esc(it.kind)+' \u00b7 '+it.occurrences+' uses \u00b7 '+it.documents+' files</div>'
      + '</a>';
  }

  function renderDetail(){
    var it = items[idx];
    if (!it) return '<section class="panel detail"><div class="empty"><strong>Nothing to review</strong><span>Run a scan first.</span></div></section>';
    var chips = it.reasons.map(function(r){ return '<span class="chip accent">'+esc(r)+'</span>'; }).join('');
    var pos = it.positive.map(function(s){ return snippet(s, 'positive', it); }).join('');
    var neg = it.negative.map(function(s){ return snippet(s, 'negative', it); }).join('');
    var nums = Object.keys(it.nums).map(function(k){ return '<div class="scores-row"><span>'+esc(k)+'</span><span>'+it.nums[k].toFixed(3)+'</span></div>'; }).join('');
    var clusterNote = (it.cluster_key && it.cluster_size > 1) ? '<div class="cluster-note">\u25c8 part of '+esc(it.cluster_key)+' ('+it.cluster_size+' variants)</div>' : '';
    var ro = DATA.readOnly;
    var h = '<section class="panel detail">';
    h += '<div class="detail-head"><div>';
    h += '<h2 class="detail-title" style="font-family:ui-monospace,Menlo,monospace">'+esc(it.surface)+'</h2>';
    h += '<div class="kind">'+esc(it.kind)+' \u00b7 appears '+it.occurrences+' times in '+it.documents+' files \u00b7 '+priorityWord(it)+' priority</div>';
    h += compactStateSummary(it);
    h += clusterNote;
    h += '</div>';
    h += '<span class="status '+statusClass(it)+'">'+esc(statusLabel(it))+'</span>';
    h += '</div>';
    if (chips) h += '<div style="margin:16px 0 4px">'+chips+'</div>';
    h += publishHistorySection(it);
    h += '<h3 class="section-title">Where it appears</h3>';
    h += pos || '<div class="meta">No positive evidence stored.</div>';
    if (neg){ h += '<h3 class="section-title">Low-confidence matches</h3>' + neg; }
    h += '<details class="scores-panel"><summary class="scores-toggle">Show scores</summary><div class="scores-box">'+nums+'</div></details>';
    if (!ro){
      h += '<div class="actionbar">';
      h += '<button class="primary" data-act="accepted">\u2713 Accept <span class="kbd" style="border-color:currentColor;background:transparent">a</span></button>';
      h += '<button data-act="rejected">\u2717 Reject <span class="kbd">r</span></button>';
      h += '<button data-act="ambiguous">Ambiguous <span class="kbd">m</span></button>';
      h += '<button data-deprecate="1">Deprecate alias <span class="kbd">d</span></button>';
      h += '<button data-skip="1">Skip <span class="kbd">s</span></button>';
      var hasLocalUndo = lastHistoryIndexFor(idx) > -1;
      var undoLabel = hasLocalUndo ? '\u21a9 Undo' : '\u21a9 Clear decision';
      var undoDisabled = (!hasLocalUndo && !isDecided(it)) ? ' disabled' : '';
      h += '<button data-undo="1"'+undoDisabled+'>'+undoLabel+' <span class="kbd">u</span></button>';
      if (it.cluster_key && it.cluster_size > 1) h += '<button class="accept-cluster" data-cluster-accept="'+esc(it.cluster_key)+'">\u2713 Accept cluster <span class="kbd">A</span></button>';
      h += '</div>';
    } else {
      h += '<div class="actionbar"><div class="notice" style="margin:0">Read-only policy mode.</div></div>';
    }
    h += '</section>';
    return h;
  }

  function evidenceHighlightTerms(it){
    var terms = [];
    function add(value){
      var v = String(value || '').trim();
      if (v.length < 3) return;
      var key = v.toLowerCase();
      for (var i = 0; i < terms.length; i++) {
        if (terms[i].toLowerCase() === key) return;
      }
      terms.push(v);
    }
    if (it) {
      add(it.surface);
      add(it.normalized_surface);
    }
    terms.sort(function(a, b){ return b.length - a.length; });
    return terms;
  }

  function highlightEvidenceText(text, terms){
    var raw = String(text || '');
    var hits = [];
    var lower = raw.toLowerCase();
    (terms || []).forEach(function(term){
      var needle = String(term || '').toLowerCase();
      if (!needle) return;
      var at = lower.indexOf(needle);
      while (at !== -1) {
        hits.push({start: at, end: at + needle.length});
        at = lower.indexOf(needle, at + Math.max(needle.length, 1));
      }
    });
    if (!hits.length) return esc(raw);
    hits.sort(function(a, b){
      if (a.start !== b.start) return a.start - b.start;
      return (b.end - b.start) - (a.end - a.start);
    });
    var selected = [];
    var cursor = -1;
    hits.forEach(function(hit){
      if (hit.start < cursor) return;
      selected.push(hit);
      cursor = hit.end;
    });
    var out = '';
    var pos = 0;
    selected.forEach(function(hit){
      out += esc(raw.slice(pos, hit.start));
      out += '<mark class="term-hit">' + esc(raw.slice(hit.start, hit.end)) + '</mark>';
      pos = hit.end;
    });
    out += esc(raw.slice(pos));
    return out;
  }

  function snippet(s, kind, it){
    return '<article class="snippet '+kind+'"><div class="snippet-head"><span>'+esc(s.path)+':'+esc(s.start)+'-'+esc(s.end)+'</span><span>'+esc(s.reason)+'</span></div><pre>'+highlightEvidenceText(s.text, evidenceHighlightTerms(it))+'</pre></article>';
  }

  function decisionVerb(ev){
    if (!ev) return 'Decision';
    if (ev.event_type === 'review_decision_cleared') return 'Cleared';
    if (ev.decision === 'accepted') return 'Accepted';
    if (ev.decision === 'rejected') return 'Rejected';
    if (ev.decision === 'ambiguous') return 'Marked ambiguous';
    if (ev.decision === 'needs_split') return 'Marked needs split';
    if (ev.decision === 'deprecate_alias') return 'Marked deprecated alias';
    return 'Reviewed';
  }

  function currentDecisionEvent(it){
    return it.decision_provenance || null;
  }

  function isLocalActorId(value){
    var cleaned = String(value || '').trim().toLowerCase();
    return cleaned === '' || cleaned === 'local' || cleaned === 'unknown' || cleaned === 'unknown-actor';
  }

  function actorLabel(ev){
    if (!ev || !ev.actor) return 'Local decision';
    var id = ev.actor.display_id || ev.actor.id || ev.reviewer || 'Human';
    var source = ev.actor.display_source || ev.actor.source || 'local';
    id = isLocalActorId(id) ? 'Human' : id;
    source = String(source || '').trim().toLowerCase();
    if (!source || source === 'unknown' || source === 'local') {
      return id === 'Human' ? 'Local decision' : id + ' local decision';
    }
    return id + ' via ' + source;
  }

  function gitLabel(ev){
    if (!ev || !ev.git || !ev.git.commit) return '';
    var branch = ev.git.branch || 'detached';
    var label = branch + '@' + ev.git.commit;
    if (ev.git.dirty) label += ' · dirty';
    return label;
  }

  function shortDate(value){
    if (!value) return '';
    return String(value).replace('T', ' ').replace('+00:00', ' UTC');
  }

  function compactStateSummary(it){
    var rows = [];
    if (isDecided(it)) rows.push(currentStateRow(it));
    var p = latestPublishCheckpoint(it);
    if (p) rows.push(latestPublishRow(p));
    if (!rows.length) return '';
    return '<div class="state-summary">'+rows.join('')+'</div>';
  }

  function currentStateRow(it){
    var ev = currentDecisionEvent(it);
    var bits = ['<span class="state-label">Current</span>', '<strong>'+esc(statusLabel(it))+'</strong>'];
    if (ev) {
      bits.push(esc(actorLabel(ev)));
    } else {
      bits.push('Local decision');
    }
    if (it.decision === 'accepted' || it.decision === 'deprecate_alias') {
      var cls = (it.published && it.published.is_published) ? 'ok' : 'warn';
      var label = (it.published && it.published.is_published) ? 'published' : 'publish pending';
      bits.push('<span class="state-badge '+cls+'">'+label+'</span>');
    }
    if (it.decision === 'deprecate_alias' && it.decision_metadata && it.decision_metadata.deprecate_alias) {
      bits.push('target ' + esc(it.decision_metadata.deprecate_alias.target_term_id || ''));
    }
    return '<div class="state-row">'+bits.join(' · ')+'</div>';
  }

  function latestPublishCheckpoint(it){
    return it.publish_checkpoint || (it.published && it.published.provenance) || null;
  }

  function latestPublishRow(p){
    var bits = ['<span class="state-label">Latest publish</span>', '<strong>'+esc(publishDecisionLabel(p))+'</strong>'];
    bits.push(esc(shortSnapshot(p.snapshot_id || 'snapshot')));
    bits.push('<span class="state-badge '+(p.included_in_lexicon ? 'ok' : 'warn')+'">'+(p.included_in_lexicon ? 'in lexicon' : 'not in lexicon')+'</span>');
    return '<div class="state-row">'+bits.join(' · ')+'</div>';
  }

  function shortSnapshot(snapshotId){
    var value = String(snapshotId || 'snapshot');
    if (value.length <= 28) return value;
    return value.slice(0, 18) + '…' + value.slice(-8);
  }

  function publishDecisionLabel(p){
    if (!p) return 'Published';
    if (p.decision === 'accepted') return 'Accepted';
    if (p.decision === 'rejected') return 'Rejected';
    if (p.decision === 'ambiguous') return 'Ambiguous';
    if (p.decision === 'needs_split') return 'Needs split';
    if (p.decision === 'deprecate_alias') return 'Deprecated alias';
    return p.included_in_lexicon ? 'Accepted' : 'Reviewed';
  }

  function publishHistorySection(it){
    var rows = it.publish_history || [];
    if (!rows.length) return '';
    var body = rows.map(function(p, i){
      var stateBadge = '<span class="state-badge '+(p.included_in_lexicon ? 'ok' : 'warn')+'">'+(p.included_in_lexicon ? 'in lexicon' : 'not in lexicon')+'</span>';
      var bits = ['<strong>'+esc(publishDecisionLabel(p))+'</strong>', stateBadge, esc(actorLabel(p))];
      var git = gitLabel(p);
      if (git) bits.push(esc(git));
      if (p.created_at) bits.push(esc(shortDate(p.created_at)));
      var marker = i === 0 ? '<div class="publish-history-note">latest publish checkpoint</div>' : '';
      return '<div class="publish-history-row"><div><div class="publish-history-snapshot">'+esc(p.snapshot_id || 'snapshot')+'</div>'+marker+'</div><div><div class="publish-history-meta">'+bits.join(' · ')+'</div></div></div>';
    }).join('');
    var label = rows.length === 1 ? '1 checkpoint' : rows.length + ' checkpoints';
    return '<details class="publish-history"><summary><span>Published history</span><span class="publish-history-count">'+label+'</span></summary><div class="publish-ledger-box">'+body+'</div></details>';
  }

  function statusClass(it){ if(it.decision==='accepted')return'accepted'; if(it.decision==='rejected')return'rejected'; if(it.decision)return'ambiguous'; return''; }
  function statusLabel(it){ if(it.decision==='accepted')return'Accepted'; if(it.decision==='rejected')return'Rejected'; if(it.decision==='ambiguous')return'Ambiguous'; if(it.decision==='deprecate_alias')return'Deprecated alias'; if(it.decision)return'Reviewed'; return'Unreviewed'; }

  function renderThemeSwitch(){
    var options = [
      ['system', 'System'],
      ['light', 'Light'],
      ['dark', 'Dark']
    ];
    var h = '<div class="theme-switch" role="group" aria-label="Color theme"><span class="theme-switch-label">Theme</span>';
    options.forEach(function(opt){
      h += '<button type="button" data-theme-choice="'+opt[0]+'" class="'+(themeMode===opt[0]?'active':'')+'">'+opt[1]+'</button>';
    });
    h += '</div>';
    return h;
  }

  function updateThemeSwitch(){
    app.querySelectorAll('[data-theme-choice]').forEach(function(button){
      button.classList.toggle('active', button.dataset.themeChoice === themeMode);
    });
  }

  function bindThemeSwitch(){
    app.querySelectorAll('[data-theme-choice]').forEach(function(button){
      button.onclick = function(){ applyTheme(button.dataset.themeChoice); };
    });
  }

  function renderHeader(){
    var total = items.length, done = reviewedCount();
    var pct = total ? Math.round(done/total*100) : 0;
    var tabs = '<div class="tabs">'
      + '<button class="tab '+(view==='review'?'active':'')+'" data-view="review">Review</button>'
      + '<button class="tab '+(view==='lexicon'?'active':'')+'" data-view="lexicon">Lexicon ('+lexicon.length+')</button>'
      + '</div>';
    var right = view === 'review'
      ? '<div class="progress-wrap"><span>'+done+' of '+total+' reviewed</span><div class="progress-track"><div class="progress-bar" style="width:'+pct+'%"></div></div></div>'
      : '';
    return '<header class="topbar"><div>'
      + '<p class="eyebrow">Agent Lexicon</p><h1>'+(view==='review'?'Review':'Lexicon')+'</h1>'
      + '<div class="meta">Workspace root: '+esc(DATA.root)+'</div>'
      + '<div style="margin-top:8px">'+tabs+'</div></div>'
      + '<div class="summary" style="align-items:center">'
      + right
      + renderThemeSwitch()
      + '<span class="pill">policy: '+esc(DATA.policy)+'</span>'
      + '<a class="pill" href="/review-events.jsonl">Export JSONL</a>'
      + '</div></header>';
  }

  function renderLexicon(){
    if (!lexicon.length){
      return '<section class="panel detail" style="max-height:none"><div class="empty"><strong>No published terminology yet</strong><span>Accept candidates in the Review tab, then run</span><span class="code">poetry run alex publish</span></div></section>';
    }
    var q = (search || '').toLowerCase();
    var shown = lexicon.filter(function(t){
      if (!q) return true;
      if (t.canonical.toLowerCase().indexOf(q) > -1) return true;
      if (t.id.toLowerCase().indexOf(q) > -1) return true;
      return t.aliases.some(function(a){ return a.toLowerCase().indexOf(q) > -1; });
    });
    var source = lexicon[0] && lexicon[0].source === 'snapshot' ? 'latest snapshot' : 'dictionary file';
    var snap = lexicon[0] && lexicon[0].snapshot_id ? lexicon[0].snapshot_id : '';
    var header = '<div class="lex-header"><div>'
      + '<p class="eyebrow">Published lexicon</p>'
      + '<h2>Published source of truth</h2>'
      + '<div class="meta">Read-only view of the terminology agents should use. Change decisions in Review, then publish a new snapshot.</div>'
      + '</div><div class="summary">'
      + '<span class="pill">'+lexicon.length+' terms</span>'
      + '<span class="pill">'+esc(source)+(snap ? ' · '+esc(shortSnapshot(snap)) : '')+'</span>'
      + '</div></div>';
    var rows = shown.map(function(t, i){ return lexiconTermCard(t, i); }).join('');
    return '<section class="panel detail" style="max-height:none">'+header+'<div style="display:flex; align-items:center; justify-content:space-between; gap:12px; padding:0 4px 12px"><input class="search" id="lq" placeholder="Search published terms" value="'+esc(search)+'" style="max-width:320px"><span class="meta">Read-only</span></div>'
      + (shown.length ? rows : '<div class="meta" style="padding:12px">No terms match.</div>')
      + '</section>';
  }

  function lexiconTermCard(t, visibleIndex){
    var aliases = t.aliases.length ? t.aliases.map(function(a){ return '<span class="lex-alias">'+esc(a)+'</span>'; }).join('') : '<span class="meta">no aliases</span>';
    var scopes = t.scopes.length ? t.scopes.map(function(s){ return '<span class="lex-scope">'+esc(s)+'</span>'; }).join('') : '<span class="meta">no scopes</span>';
    var tools = t.tools.length ? '<div class="meta" style="margin-top:8px">tools: '+t.tools.map(esc).join(', ')+'</div>' : '';
    var publish = t.publish_checkpoint || null;
    var review = t.review_decision_provenance || null;
    var reviewIndex = findReviewIndexForLexiconTerm(t);
    var origin = t.source === 'snapshot' && t.snapshot_id ? 'published in '+esc(shortSnapshot(t.snapshot_id)) : 'from dictionary file';
    var summary = '<summary><div class="lex-summary"><div>'
      + '<div class="lex-canonical">'+esc(t.canonical)+(t.deprecated?' <span class="meta">(deprecated)</span>':'')+'</div>'
      + '<div class="meta">canonical term · '+(t.aliases.length ? t.aliases.length+' aliases' : 'no aliases')+' · '+origin+'</div>'
      + '</div><span class="lex-id">'+esc(t.id)+'</span></div></summary>';
    var publishCard = publish
      ? lexiconProvenanceCard('Publish provenance', publishDecisionLabel(publish)+' · '+(publish.included_in_lexicon ? 'in lexicon' : 'not in lexicon'), actorLabel(publish), gitLabel(publish), publish.created_at, publish.snapshot_id)
      : lexiconProvenanceCard('Publish provenance', origin, '', '', t.snapshot_created_at || '', t.snapshot_id || '');
    var reviewCard = review
      ? lexiconProvenanceCard('Review decision', publishDecisionLabel(review), actorLabel(review), gitLabel(review), review.created_at, '')
      : lexiconProvenanceCard('Review decision', 'No linked review decision', '', '', '', '');
    var action = reviewIndex > -1
      ? '<button class="primary" data-view-review="'+reviewIndex+'">View in Review</button>'
      : '<button disabled>View in Review</button>';
    return '<details class="lex-term" data-lex-card="'+visibleIndex+'">'+summary+'<div class="lex-detail">'
      + '<div class="lex-detail-grid">'+publishCard+reviewCard+'</div>'
      + '<div><strong style="font-size:12px">Aliases</strong><div style="margin-top:6px">'+aliases+'</div></div>'
      + '<div style="margin-top:10px"><strong style="font-size:12px">Scopes</strong><div style="margin-top:6px">'+scopes+'</div></div>'
      + tools
      + '<div class="lex-actions">'+action+'<span class="meta">Lexicon is read-only. Use Review to change the decision.</span></div>'
      + '</div></details>';
  }

  function lexiconProvenanceCard(title, state, actor, git, createdAt, snapshotId){
    var bits = [];
    if (actor) bits.push(esc(actor));
    if (git) bits.push(esc(git));
    if (createdAt) bits.push(esc(shortDate(createdAt)));
    if (snapshotId) bits.push(esc(shortSnapshot(snapshotId)));
    return '<div class="lex-detail-card"><strong>'+esc(title)+'</strong><div>'+esc(state || 'Unknown')+'</div>'
      + (bits.length ? '<div class="meta" style="margin-top:5px">'+bits.join(' · ')+'</div>' : '')
      + '</div>';
  }

  function findReviewIndexForLexiconTerm(t){
    var keys = [t.review_normalized_surface, t.review_surface, t.canonical, t.id]
      .filter(function(v){ return String(v || '').trim(); })
      .map(function(v){ return String(v).toLowerCase(); });
    for (var i = 0; i < items.length; i++){
      var it = items[i];
      var itemKeys = [it.normalized_surface, it.surface].map(function(v){ return String(v || '').toLowerCase(); });
      for (var k = 0; k < keys.length; k++){
        if (itemKeys.indexOf(keys[k]) > -1) return i;
      }
    }
    return -1;
  }

  function render(){
    if (view === 'lexicon'){
      app.innerHTML = renderHeader() + '<section class="grid" style="grid-template-columns:1fr">' + renderLexicon() + '</section>';
      bindTabs();
      bindThemeSwitch();
      bindLexicon();
      var lq = document.getElementById('lq');
      if (lq) lq.oninput = function(){ search = lq.value; render(); var el=document.getElementById('lq'); if(el){el.focus(); el.setSelectionRange(el.value.length, el.value.length);} };
      return;
    }
    app.innerHTML = renderHeader()
      + '<section class="grid"><aside class="panel sidebar">'+renderSidebar()+'</aside>'+renderDetail()+'</section>';
    bind();
    bindTabs();
    bindThemeSwitch();
    var active = app.querySelector('.item.active');
    if (active) active.scrollIntoView({block:'nearest'});
  }

  function bindTabs(){
    app.querySelectorAll('.tab').forEach(function(t){ t.onclick = function(){ view = t.dataset.view; search=''; render(); }; });
  }

  function bindLexicon(){
    app.querySelectorAll('[data-view-review]').forEach(function(b){
      b.onclick = function(ev){
        ev.preventDefault();
        var nextIdx = parseInt(b.dataset.viewReview);
        if (!isNaN(nextIdx) && items[nextIdx]) {
          idx = nextIdx;
          view = 'review';
          search = '';
          filter = 'all';
          render();
        }
      };
    });
  }

  function bind(){
    var q = document.getElementById('q');
    if (q) q.oninput = function(){ search = q.value; var v=visibleIndexes(); if(v.indexOf(idx)===-1 && v.length) idx=v[0]; updateList(); };
    var f = document.getElementById('f');
    if (f) f.onchange = function(){ filter = f.value; var v=visibleIndexes(); if(v.indexOf(idx)===-1 && v.length) idx=v[0]; updateList(); };
    bindList();
    app.querySelectorAll('[data-act]').forEach(function(b){ b.onclick = function(){ decide(idx, b.dataset.act); }; });
    var dep = app.querySelector('[data-deprecate]'); if (dep) dep.onclick = function(){ deprecateAlias(idx); };
    var sk = app.querySelector('[data-skip]'); if (sk) sk.onclick = function(){ next(); };
    var un = app.querySelector('[data-undo]'); if (un) un.onclick = function(){ undo(); };
    var ca = app.querySelector('[data-cluster-accept]'); if (ca) ca.onclick = function(){ acceptCluster(ca.dataset.clusterAccept); };
  }

  function bindList(){
    app.querySelectorAll('.item').forEach(function(a){ a.onclick = function(){ idx = parseInt(a.dataset.idx); render(); }; });
    app.querySelectorAll('.cluster-head').forEach(function(c){ c.onclick = function(){ var k=c.dataset.cluster; collapsed[k]=!collapsed[k]; updateList(); }; });
  }


  function updateList(){
    var listEl = document.getElementById('sblist');
    if (listEl) listEl.innerHTML = renderListInner();
    bindList();
  }

  function replaceItem(updated){
    if (!updated || !updated.normalized_surface) return;
    for (var i = 0; i < items.length; i++){
      if (items[i].normalized_surface === updated.normalized_surface){
        items[i] = updated;
        return;
      }
    }
  }

  function postAction(body){
    body += '&response=json';
    return fetch('/decision', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: body})
      .then(function(resp){
        if (!resp.ok) throw new Error('review decision failed');
        return resp.json();
      })
      .then(function(payload){
        if (payload && payload.item) replaceItem(payload.item);
        return payload;
      });
  }

  function post(surface, decision){
    var body = 'surface='+encodeURIComponent(surface)+'&decision='+encodeURIComponent(decision)+'&note=';
    return postAction(body);
  }

  function postDeprecatedAlias(surface, targetTermId){
    var body = 'surface='+encodeURIComponent(surface)
      + '&decision=deprecate_alias&target_term_id='+encodeURIComponent(targetTermId)+'&note=';
    return postAction(body);
  }

  function targetScore(surface, target){
    var hay = (String(target.id || '') + ' ' + String(target.canonical || '')).toLowerCase();
    var words = String(surface || '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    var score = 0;
    words.forEach(function(w){ if (w.length > 2 && hay.indexOf(w) > -1) score += w.length; });
    return score;
  }

  function suggestedTargets(surface){
    return termTargets.slice().sort(function(a, b){
      return targetScore(surface, b) - targetScore(surface, a) || String(a.id).localeCompare(String(b.id));
    });
  }

  function chooseDeprecationTarget(it){
    if (!termTargets.length) {
      window.alert('No published terms are available yet. Accept and publish a canonical term first.');
      return '';
    }
    var suggestions = suggestedTargets(it.surface).slice(0, 5);
    var lines = suggestions.map(function(t){ return '  ' + t.id + (t.canonical ? ' — ' + t.canonical : ''); });
    var promptText = 'Target canonical term id for deprecated alias "' + it.surface + '":';
    if (lines.length) promptText += '\n\nSuggestions:\n' + lines.join('\n');
    var initial = suggestions[0] ? suggestions[0].id : '';
    return String(window.prompt(promptText, initial) || '').trim();
  }

  function deprecateAlias(i){
    if (DATA.readOnly) return;
    var it = items[i];
    if (!it) return;
    var target = chooseDeprecationTarget(it);
    if (!target) return;
    rememberDecisionChange(i);
    it.decision = 'deprecate_alias';
    it.decision_metadata = {deprecate_alias: {target_term_id: target}};
    postDeprecatedAlias(it.normalized_surface, target).then(function(){ render(); }).catch(function(){ window.location = '/?surface=' + encodeURIComponent(it.normalized_surface); });
    render();
    setTimeout(next, 100);
  }

  function clearDecision(surface){
    var body = 'surface='+encodeURIComponent(surface)+'&action=clear&note=';
    return postAction(body);
  }

  var undoHistory = [];

  function rememberDecisionChange(i){
    var it = items[i];
    if (!it) return;
    undoHistory.push({i:i, surface: it.normalized_surface, prev: it.decision || null});
  }

  function lastHistoryIndexFor(i){
    var it = items[i];
    if (!it) return -1;
    for (var h = undoHistory.length - 1; h >= 0; h--){
      if (undoHistory[h].surface === it.normalized_surface || undoHistory[h].i === i) return h;
    }
    return -1;
  }

  function itemIndexForHistoryEntry(entry){
    for (var i = 0; i < items.length; i++){
      if (items[i].normalized_surface === entry.surface) return i;
    }
    return entry.i;
  }

  function decide(i, decision){
    if (DATA.readOnly) return;
    var it = items[i];
    rememberDecisionChange(i);
    it.decision = decision;
    post(it.normalized_surface, decision).then(function(){ render(); }).catch(function(){ window.location = '/?surface=' + encodeURIComponent(it.normalized_surface); });
    render();
    setTimeout(next, 100);
  }

  function undo(){
    if (DATA.readOnly) return;
    var current = items[idx];
    if (!current) return;
    var historyIndex = lastHistoryIndexFor(idx);
    if (historyIndex > -1){
      var last = undoHistory.splice(historyIndex, 1)[0];
      var targetIndex = itemIndexForHistoryEntry(last);
      var it = items[targetIndex];
      if (!it) return;
      it.decision = last.prev;
      var undoRequest = last.prev ? post(it.normalized_surface, last.prev) : clearDecision(it.normalized_surface);
      idx = targetIndex;
      undoRequest.then(function(){ render(); }).catch(function(){ window.location = '/?surface=' + encodeURIComponent(it.normalized_surface); });
      render();
      return;
    }
    if (!isDecided(current)) return;
    current.decision = null;
    clearDecision(current.normalized_surface).then(function(){ render(); }).catch(function(){ window.location = '/?surface=' + encodeURIComponent(current.normalized_surface); });
    render();
  }

  function acceptCluster(key){
    if (DATA.readOnly) return;
    var requests = [];
    items.forEach(function(it, i){ if (it.cluster_key === key){ rememberDecisionChange(i); it.decision='accepted'; requests.push(post(it.normalized_surface,'accepted')); } });
    Promise.all(requests).then(function(){ render(); }).catch(function(){ render(); });
    render();
    setTimeout(next, 100);
  }

  function next(){ var v=visibleIndexes(); var p=v.indexOf(idx); if(p>-1 && p<v.length-1){ idx=v[p+1]; render(); } else if(p===-1 && v.length){ idx=v[0]; render(); } }
  function prev(){ var v=visibleIndexes(); var p=v.indexOf(idx); if(p>0){ idx=v[p-1]; render(); } }

  document.addEventListener('keydown', function(e){
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (view !== 'review') return;
    var k = e.key;
    if (k==='j'){ next(); e.preventDefault(); }
    else if (k==='k'){ prev(); e.preventDefault(); }
    else if (k==='a'){ decide(idx,'accepted'); e.preventDefault(); }
    else if (k==='r'){ decide(idx,'rejected'); e.preventDefault(); }
    else if (k==='m'){ decide(idx,'ambiguous'); e.preventDefault(); }
    else if (k==='d'){ deprecateAlias(idx); e.preventDefault(); }
    else if (k==='s'){ next(); e.preventDefault(); }
    else if (k==='u'){ undo(); e.preventDefault(); }
    else if (k==='A'){ var it=items[idx]; if(it.cluster_key && it.cluster_size>1) acceptCluster(it.cluster_key); e.preventDefault(); }
  });

  applyTheme(themeMode);
  if (window.matchMedia) {
    var themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    var onSystemThemeChange = function(){ if (themeMode === 'system') applyTheme('system'); };
    if (themeMedia.addEventListener) themeMedia.addEventListener('change', onSystemThemeChange);
    else if (themeMedia.addListener) themeMedia.addListener(onSystemThemeChange);
  }
  render();
})();
"""


def build_review_inbox_html(
    state: WorkspaceStore,
    *,
    selected_surface: str | None = None,
    limit: int = 100,
    actor: str = "local",
    role: str | None = None,
    policy_mode: str | None = None,
    history_open: bool = False,
) -> str:
    """Render the local proposal inbox as a complete HTML document."""
    _ = history_open  # Backward-compatible no-op; raw click history is not shown in the UI.
    if not isinstance(state, WorkspaceStore):
        raise ReviewInboxError("state must implement WorkspaceStore")
    items = state.list_review_items(limit=limit)
    selected = _select_item(state, items, selected_surface=selected_surface)
    policy = load_local_policy(state.root, mode=policy_mode)
    policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor, role=role)
    return _render_page(
        items=items,
        selected=selected,
        root=str(state.root),
        policy_decision=policy_decision,
    )


def _is_unreviewed(item: WorkspaceReviewItem) -> bool:
    """True when a review item has no saved decision yet."""
    return getattr(item, "review_decision", None) is None


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

    items = state.list_review_items(limit=1000)
    if not items:
        print("Nothing to review yet. Run a scan first, for example:")
        print("  agent-lexicon scan README.md docs src")
        return
    unreviewed = sum(1 for item in items if _is_unreviewed(item))

    handler = _handler_for_state(state, actor=actor, policy_decision=policy_decision)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Review inbox: {url}")
    print(f"{unreviewed} of {len(items)} candidates waiting for review.")
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
    state: WorkspaceStore,
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
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                self._send_text("Invalid Content-Length header\n", status=400)
                return
            if length < 0:
                self._send_text("Invalid Content-Length header\n", status=400)
                return
            if length > _MAX_POST_BYTES:
                self._send_text("Request body too large\n", status=413)
                return
            try:
                payload = self.rfile.read(length).decode("utf-8")
            except UnicodeDecodeError:
                self._send_text("Request body must be UTF-8\n", status=400)
                return
            form = parse_qs(payload)
            normalized_surface = form.get("surface", [""])[0]
            decision = form.get("decision", [""])[0]
            action = form.get("action", ["save"])[0]
            note = form.get("note", [""])[0]
            target_term_id = form.get("target_term_id", [""])[0].strip()
            response_mode = form.get("response", ["redirect"])[0]
            if not policy_decision.is_allowed:
                self._send_text(f"Policy denied review decision: {policy_decision.reason}\n", status=403)
                return
            try:
                if action == "clear":
                    state.clear_review_decision(
                        normalized_surface,
                        note=note,
                        reviewer=policy_decision.actor,
                        actor_type="human",
                        actor_source="web",
                        actor_id=_default_web_actor_id(state.root),
                    )
                elif action in {"", "save"}:
                    metadata: dict[str, Any] = {}
                    if decision == ReviewDecisionStatus.DEPRECATE_ALIAS.value:
                        if not target_term_id:
                            self._send_text("Deprecated alias decisions require target_term_id\n", status=400)
                            return
                        metadata["deprecate_alias"] = {"target_term_id": target_term_id}
                    state.save_review_decision(
                        normalized_surface,
                        decision,
                        note=note,
                        reviewer=policy_decision.actor,
                        actor_type="human",
                        actor_source="web",
                        actor_id=_default_web_actor_id(state.root),
                        metadata=metadata,
                    )
                else:
                    self._send_text(f"Invalid review action: {action}\n", status=400)
                    return
            except (ValueError, WorkspaceError) as exc:
                self._send_text(f"Invalid review decision: {exc}\n", status=400)
                return
            if response_mode == "json":
                item = state.get_review_item(normalized_surface)
                self._send_json(
                    {
                        "ok": True,
                        "surface": normalized_surface,
                        "item": _item_as_dict(
                            item,
                            published_terms_by_surface=_published_terms_by_surface(_accepted_terms(state.root)),
                            published_records_by_surface=_published_records_by_surface(state.root),
                            publish_history_by_surface=_publish_history_by_surface(state.root),
                        ) if item else None,
                    }
                )
                return
            location = f"/?surface={quote(normalized_surface)}"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _send_json(self, payload: Mapping[str, Any], *, status: int = 200) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

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
    state: WorkspaceStore,
    items: tuple[WorkspaceReviewItem, ...],
    *,
    selected_surface: str | None,
) -> WorkspaceReviewItem | None:
    if selected_surface:
        item = state.get_review_item(unquote(selected_surface))
        if item is not None:
            return item
    return items[0] if items else None


_REASON_PHRASES = {
    "code_style_surface": "written like code",
    "high_oov_proxy": "unusual word",
    "high_oov_signal": "unusual word",
    "tokenizer_oov_signal": "unusual word",
    "high_surface_risk": "risky to match",
    "high_jargon_score": "domain jargon",
    "clustered_variants": "has spelling variants",
    "identifier_variants": "has spelling variants",
    "multi_document_signal": "seen across many files",
}


def _humanize_reasons(reasons: Any) -> list[str]:
    if not isinstance(reasons, list):
        return []
    seen: list[str] = []
    for code in reasons:
        phrase = _REASON_PHRASES.get(str(code))
        if phrase and phrase not in seen:
            seen.append(phrase)
    return seen[:4]


def _snippets_as_dicts(snippets: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(snippets, list):
        return result
    for snippet in snippets:
        if not isinstance(snippet, dict):
            continue
        start = snippet.get("start_line", "?")
        result.append(
            {
                "path": str(snippet.get("document_path", "unknown")),
                "start": str(start),
                "end": str(snippet.get("end_line", start)),
                "reason": str(snippet.get("reason", "evidence")),
                "text": str(snippet.get("text", "")),
            }
        )
    return result


def _item_as_dict(
    item: WorkspaceReviewItem,
    *,
    published_terms_by_surface: Mapping[str, dict[str, Any]] | None = None,
    published_records_by_surface: Mapping[str, dict[str, Any]] | None = None,
    publish_history_by_surface: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    cluster = _item_cluster(item)
    cluster_key = str(cluster.get("cluster_key", "") or "")
    priority = _item_priority(item)
    reasons_raw = _item_quality(item).get("priority_reasons", [])
    decision_provenance = (
        _review_decision_provenance_as_ui_dict(item.review_decision)
        if item.review_decision is not None
        else None
    )
    published_term = (published_terms_by_surface or {}).get(item.surface.casefold())
    publish_record = (published_records_by_surface or {}).get(item.surface.casefold()) or (published_records_by_surface or {}).get(item.normalized_surface.casefold())
    publish_history = (publish_history_by_surface or {}).get(item.surface.casefold()) or (publish_history_by_surface or {}).get(item.normalized_surface.casefold()) or []
    included_by_record = bool((publish_record or {}).get("included_in_lexicon", False))
    is_published = bool(published_term or included_by_record)
    published_snapshot_id = str((published_term or {}).get("snapshot_id", "") or ((publish_record or {}).get("snapshot_id", "") if is_published else "") or "")
    published_source = str((published_term or {}).get("source", "") or ("snapshot" if published_snapshot_id else ""))
    published = {
        "is_published": is_published,
        "snapshot_id": published_snapshot_id,
        "source": published_source,
        "created_at": str((published_term or {}).get("snapshot_created_at", "") or ((publish_record or {}).get("created_at", "") if is_published else "") or ""),
        "provenance": publish_record if is_published else None,
    }
    return {
        "surface": item.surface,
        "normalized_surface": item.normalized_surface,
        "kind": item.candidate_kind,
        "score": round(float(item.score), 3),
        "occurrences": item.occurrence_count,
        "documents": item.document_count,
        "positive_count": item.positive_count,
        "negative_count": item.negative_count,
        "priority": priority,
        "cluster_key": cluster_key,
        "cluster_size": _item_cluster_size(item),
        "reasons": _humanize_reasons(reasons_raw),
        "status": item.review_status,
        "decision": item.review_decision.decision.value if item.review_decision else None,
        "note": item.review_decision.note if item.review_decision else "",
        "decision_metadata": dict(item.review_decision.metadata) if item.review_decision else {},
        "decision_provenance": decision_provenance,
        "published": published,
        "publish_checkpoint": publish_record,
        "publish_history": list(publish_history),
        "nums": {
            "Score": round(float(item.score), 3),
            "Jargon": round(float(item.jargon_score), 3),
            "OOV proxy": round(_item_quality_float(item, "oov_proxy_score"), 3),
            "Surface risk": round(_item_quality_float(item, "surface_risk_score"), 3),
            "Background penalty": round(float(item.background_penalty), 3),
        },
        "positive": _snippets_as_dicts(item.evidence_payload.get("positive_snippets", [])),
        "negative": _snippets_as_dicts(item.evidence_payload.get("negative_snippets", [])),
    }


def _review_decision_provenance_as_ui_dict(decision: Any) -> dict[str, Any]:
    metadata = decision.metadata if isinstance(decision.metadata, dict) else {}
    actor = metadata.get("actor", {}) if isinstance(metadata, dict) else {}
    git = metadata.get("git", {}) if isinstance(metadata, dict) else {}
    if not isinstance(actor, dict):
        actor = {}
    if not isinstance(git, dict):
        git = {}
    return {
        "decision": decision.decision.value,
        "note": decision.note,
        "reviewer": decision.reviewer,
        "created_at": decision.updated_at,
        "actor": {
            "type": str(actor.get("type", "unknown") or "unknown"),
            "id": str(actor.get("id", decision.reviewer) or decision.reviewer),
            "source": str(actor.get("source", "unknown") or "unknown"),
            "display_id": _display_actor_id(actor, git, decision.reviewer),
            "display_source": _display_actor_source(actor),
        },
        "git": {
            "branch": str(git.get("branch", "") or ""),
            "commit": str(git.get("commit", "") or ""),
            "dirty": bool(git.get("dirty", False)),
            "available": bool(git.get("available", False)),
        },
    }


def _render_page(
    *,
    items: tuple[WorkspaceReviewItem, ...],
    selected: WorkspaceReviewItem | None,
    root: str,
    policy_decision: PolicyDecision,
) -> str:
    lexicon_terms = _accepted_terms(root)
    published_terms_by_surface = _published_terms_by_surface(lexicon_terms)
    publish_history_by_surface = _publish_history_by_surface(root)
    published_records_by_surface = _published_records_by_surface(root)
    payload = {
        "items": [
            _item_as_dict(
                item,
                published_terms_by_surface=published_terms_by_surface,
                published_records_by_surface=published_records_by_surface,
                publish_history_by_surface=publish_history_by_surface,
            )
            for item in items
        ],
        "root": root,
        "readOnly": not policy_decision.is_allowed,
        "policy": f"{policy_decision.mode.value} · {policy_decision.role.value}",
        "selected": selected.normalized_surface if selected is not None else "",
        "lexicon": lexicon_terms,
        "termTargets": [
            {"id": str(term.get("id", "")), "canonical": str(term.get("canonical", ""))}
            for term in lexicon_terms
            if str(term.get("id", "")).strip()
        ],
    }
    data_json = json.dumps(payload).replace("</", "<\\/")
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Agent Lexicon Review</title>",
            f"<script>{_THEME_BOOTSTRAP_JS}</script>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            '<main class="shell" id="app"></main>',
            f'<script id="review-data" type="application/json">{data_json}</script>',
            f"<script>{_APP_JS}</script>",
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


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


def _status_class(status: str) -> str:
    return status.replace("_", "-").replace(" ", "-")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
