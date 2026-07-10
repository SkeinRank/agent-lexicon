from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_lexicon import (
    build_evidence_packs,
    build_review_inbox_html,
    discover_scout_candidates,
    ingest_local_paths,
    init_workspace,
)




def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
    return env

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workspace_with_evidence(root: Path):
    _write(
        root / "docs" / "billing.md",
        "Use `billing.update_credit_limit` for credit limit changes.\nCredit limit review happens first.\n",
    )
    ingest_report = ingest_local_paths([root / "docs"], root=root)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)
    state = init_workspace(root)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)
    state.store_evidence_report(evidence_report)
    return state


def test_review_inbox_html_renders_candidates_and_evidence(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    import json as _json

    # The candidate data is embedded as a JSON payload the client app renders.
    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    assert html.count('id="review-data"') == 1
    assert payload["items"], "expected at least one candidate"
    surfaces = {item["surface"] for item in payload["items"]}
    assert "billing.update_credit_limit" in surfaces
    # Evidence travels in the payload, not in server-rendered HTML.
    target = next(i for i in payload["items"] if i["surface"] == "billing.update_credit_limit")
    assert isinstance(target["positive"], list)
    # Client-side labels and controls are present in the page.
    assert "Where it appears" in html
    assert "Accept" in html
    assert "Export JSONL" in html


def test_review_inbox_html_renders_saved_decision(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "ambiguous", note="Needs owner review")

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    import json as _json

    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    target = next(i for i in payload["items"] if i["surface"] == "billing.update_credit_limit")
    assert target["decision"] == "ambiguous"
    assert target["note"] == "Needs owner review"


def test_review_cli_help_does_not_start_server() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "agent_lexicon", "review", "--help"],
        check=True,
        text=True,
        capture_output=True,
        env=_subprocess_env(),
    )
    assert "Open the local web proposal inbox" in completed.stdout
    assert "--no-browser" in completed.stdout


def test_review_inbox_html_respects_read_only_policy(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(
        state,
        selected_surface="billing.update_credit_limit",
        actor="reader-1",
        role="reader",
        policy_mode="locked",
    )

    import json as _json

    # The "Read-only policy mode" notice is a client-side literal in the app script.
    assert "Read-only policy mode" in html

    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])
    assert payload["readOnly"] is True
    assert payload["policy"] == "locked · reader"


def test_review_inbox_hides_starter_lexicon_term(tmp_path: Path) -> None:
    import json as _json
    from agent_lexicon import init_dictionary_layout, init_workspace

    init_dictionary_layout(tmp_path)
    state = init_workspace(tmp_path)

    html = build_review_inbox_html(state)
    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    assert "lexicon" in payload
    assert payload["lexicon"] == []
    assert "project.example_term" not in html
    assert "example term" not in html
    # Lexicon tab button is present, but shows an empty accepted vocabulary.
    assert 'data-view="lexicon"' in html
    assert "No accepted terminology yet" in html
    # Action bar is sticky and present for a writable policy.
    assert "actionbar" in html


def test_review_inbox_includes_real_lexicon_terms(tmp_path: Path) -> None:
    import json as _json
    from agent_lexicon import init_dictionary_layout, init_workspace

    init_dictionary_layout(tmp_path)
    lexicon_path = tmp_path / "lexicon" / "lexicon.yaml"
    lexicon_path.write_text(
        """version: 1
metadata:
  name: Project terminology
scopes:
  - id: project
    label: Project
terms:
  - id: project.billing_limit
    canonical: billing limit
    scopes: [project]
    aliases:
      - surface: credit limit
        scopes: [project]
proposals: []
""",
        encoding="utf-8",
    )
    state = init_workspace(tmp_path)

    html = build_review_inbox_html(state)
    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    ids = {t["id"] for t in payload["lexicon"]}
    assert ids == {"project.billing_limit"}
    term = payload["lexicon"][0]
    assert term["canonical"] == "billing limit"
    assert term["aliases"] == ["credit limit"]


def _drive_post_response(state, body: bytes, headers: dict) -> tuple[int | None, dict[str, str], str]:
    """Drive the review POST handler without a real socket."""
    import io
    from http.client import HTTPMessage
    from agent_lexicon.web.review import _handler_for_state
    from agent_lexicon.policy import load_local_policy, check_local_policy, PolicyAction

    pd = check_local_policy(
        load_local_policy(state.root), PolicyAction.REVIEW_CANDIDATE, actor="local", role=None
    )
    Handler = _handler_for_state(state, actor="local", policy_decision=pd)

    class H(Handler):
        def __init__(self, raw):
            self.rfile = io.BytesIO(raw)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 1)
            self.path = "/decision"
            self.command = "POST"
            self.headers = HTTPMessage()
            self.response_status = None
            self.response_headers = {}
            for k, v in headers.items():
                self.headers[k] = v

        def send_response(self, code, message=None):
            self.response_status = code
            self.wfile.write(f"STATUS {code}\n".encode())

        def send_header(self, k, v):
            self.response_headers[k] = v

        def end_headers(self):
            self.wfile.write(b"\n")

    h = H(body)
    h.do_POST()
    raw = h.wfile.getvalue().decode(errors="replace")
    body_text = raw.split("\n\n", 1)[1] if "\n\n" in raw else ""
    return h.response_status, h.response_headers, body_text


def _drive_post(state, body: bytes, headers: dict) -> str:
    """Drive the review POST handler without a real socket; return status line."""
    status, _headers, _body = _drive_post_response(state, body, headers)
    return f"STATUS {status}"


def test_review_inbox_undo_targets_selected_candidate_history(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "var hasLocalUndo = lastHistoryIndexFor(idx) > -1" in html
    assert "var historyIndex = lastHistoryIndexFor(idx)" in html
    assert "history.splice(historyIndex, 1)" in html
    assert "history.pop()" not in html


def test_review_inbox_cluster_accept_records_targeted_history_entries(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "function rememberDecisionChange(i)" in html
    assert "surface: it.normalized_surface" in html
    assert "rememberDecisionChange(i); it.decision='accepted'" in html


def test_post_rejects_non_numeric_content_length(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    # Regression: a malformed Content-Length used to crash the handler thread.
    assert _drive_post(state, b"surface=x&decision=accepted", {"Content-Length": "abc"}) .startswith("STATUS 400")


def test_post_rejects_negative_content_length(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    assert _drive_post(state, b"x", {"Content-Length": "-5"}) .startswith("STATUS 400")


def test_post_rejects_non_utf8_body(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    # Regression: a non-UTF-8 body used to raise UnicodeDecodeError.
    assert _drive_post(state, b"surface=\xff\xfe&decision=accepted", {"Content-Length": "20"}) .startswith("STATUS 400")


def test_post_rejects_oversized_body(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    assert _drive_post(state, b"x", {"Content-Length": "99999999"}) .startswith("STATUS 413")


def test_post_clear_decision_returns_item_to_unreviewed(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")

    body = b"surface=billing.update_credit_limit&action=clear&note=Reset"
    assert _drive_post(state, body, {"Content-Length": str(len(body))}).startswith("STATUS 303")

    item = state.get_review_item("billing.update_credit_limit")
    assert item is not None
    assert item.review_status == "unreviewed"
    assert item.review_decision is None


def test_review_inbox_shows_clear_decision_control_for_saved_decision(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "Clear decision" in html
    assert "clearDecision" in html


def test_review_inbox_payload_includes_decision_history_actor_metadata(tmp_path: Path) -> None:
    import json as _json

    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision(
        "billing.update_credit_limit",
        "accepted",
        reviewer="local",
        actor_type="human",
        actor_source="web",
        actor_id="Maxim Nikolaev",
        git_metadata={
            "available": True,
            "author_name": "Maxim Nikolaev",
            "author_email": "maxim@example.com",
            "branch": "main",
            "commit": "abc1234",
            "dirty": True,
        },
    )

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")
    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    target = next(i for i in payload["items"] if i["surface"] == "billing.update_credit_limit")
    assert target["history"][0]["actor"] == {
        "type": "human",
        "id": "Maxim Nikolaev",
        "source": "web",
        "display_id": "Maxim Nikolaev",
        "display_source": "web",
    }
    assert target["history"][0]["git"] == {
        "available": True,
        "branch": "main",
        "commit": "abc1234",
        "dirty": True,
    }
    assert target["decision_metadata"]["actor"]["id"] == "Maxim Nikolaev"


def test_review_inbox_renders_provenance_ui_and_centered_status_styles(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "currentProvenanceLine" in html
    assert "Show decision history" in html
    assert "history-box" in html
    assert "data-history-surface" in html
    assert "historyOpen" in html
    assert "align-items: center" in html
    assert "detail-head .status" in html


def test_review_post_resolves_web_actor_from_git_config(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Maxim"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "nkvmaxim@gmail.com"], cwd=tmp_path, check=True)
    state = _workspace_with_evidence(tmp_path)

    body = b"surface=billing.update_credit_limit&decision=accepted&note="
    assert _drive_post(state, body, {"Content-Length": str(len(body))}).startswith("STATUS 303")

    event = state.list_review_events()[0]
    assert event.metadata["actor"] == {"type": "human", "id": "Maxim", "source": "web"}
    assert event.metadata["git"]["author_name"] == "Maxim"


def test_review_post_json_response_returns_updated_item_history(tmp_path: Path) -> None:
    import json as _json

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Maxim"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "nkvmaxim@gmail.com"], cwd=tmp_path, check=True)
    state = _workspace_with_evidence(tmp_path)

    body = b"surface=billing.update_credit_limit&decision=accepted&note=&response=json"
    status, headers, response_body = _drive_post_response(state, body, {"Content-Length": str(len(body))})

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    payload = _json.loads(response_body)
    assert payload["ok"] is True
    assert payload["item"]["decision"] == "accepted"
    assert payload["item"]["history"][-1]["actor"]["display_id"] == "Maxim"
    assert payload["item"]["history"][-1]["actor"]["display_source"] == "web"


def test_review_redirect_preserves_open_history_flag(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    body = b"surface=billing.update_credit_limit&decision=accepted&note=&history=1"
    status, headers, _response_body = _drive_post_response(state, body, {"Content-Length": str(len(body))})

    assert status == 303
    assert headers["Location"].endswith("&history=1")


def test_review_inbox_can_render_history_open_for_selected_candidate(tmp_path: Path) -> None:
    import json as _json

    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")

    html = build_review_inbox_html(
        state,
        selected_surface="billing.update_credit_limit",
        history_open=True,
    )
    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    assert payload["historyOpen"] is True
    assert "data-history-surface" in html
    assert "if (DATA.historyOpen && sel) historyOpen[sel] = true" in html


def test_review_inbox_actor_label_avoids_local_via_unknown_copy(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "function isLocalActorId" in html
    assert "Local decision" in html
    assert "via unknown" not in html
    assert "via local" not in html


def test_review_inbox_normalizes_legacy_local_actor_display(tmp_path: Path) -> None:
    import json as _json

    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision(
        "billing.update_credit_limit",
        "accepted",
        reviewer="local",
        actor_type="human",
        actor_source="web",
        actor_id="local",
        git_metadata={"available": False, "author_name": "Maxim", "author_email": "", "branch": "", "commit": "", "dirty": False},
    )

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")
    start = html.index('<script id="review-data" type="application/json">') + len(
        '<script id="review-data" type="application/json">'
    )
    end = html.index("</script>", start)
    payload = _json.loads(html[start:end])

    target = next(i for i in payload["items"] if i["surface"] == "billing.update_credit_limit")
    actor = target["history"][0]["actor"]
    assert actor["id"] == "local"
    assert actor["display_id"] == "Maxim"
    assert actor["display_source"] == "web"
    assert "local via unknown" not in html
