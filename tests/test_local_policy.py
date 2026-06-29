from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    LocalPolicy,
    LocalPolicyMode,
    LocalPolicyRole,
    PolicyAction,
    check_local_policy,
    init_local_policy,
    load_local_policy,
    policy_path,
)
from agent_lexicon.cli import main


def test_default_local_policy_is_solo_and_allows_local_actions(tmp_path: Path) -> None:
    policy = load_local_policy(tmp_path)

    decision = check_local_policy(policy, PolicyAction.PUBLISH_SNAPSHOT, actor="anyone", role="reader")

    assert policy.mode == LocalPolicyMode.SOLO
    assert decision.is_allowed is True
    assert decision.role == LocalPolicyRole.READER


def test_team_policy_allows_reviewers_to_review_but_not_publish() -> None:
    policy = LocalPolicy(mode="team", actors={"alice": "reviewer"})

    review_decision = check_local_policy(policy, "review_candidate", actor="alice")
    publish_decision = check_local_policy(policy, "publish_snapshot", actor="alice")

    assert review_decision.is_allowed is True
    assert publish_decision.is_allowed is False
    assert "allowed roles" in publish_decision.reason


def test_locked_policy_is_read_only_for_reader() -> None:
    policy = LocalPolicy(mode="locked", default_role="reader")

    read_decision = check_local_policy(policy, PolicyAction.READ_WORKSPACE, actor="reader-1")
    review_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor="reader-1")

    assert read_decision.is_allowed is True
    assert review_decision.is_allowed is False


def test_init_local_policy_writes_policy_json(tmp_path: Path) -> None:
    policy = init_local_policy(tmp_path, mode="team", actor="maxim", role="owner")

    path = policy_path(tmp_path)
    assert path.exists()
    assert policy.mode == LocalPolicyMode.TEAM

    loaded = load_local_policy(tmp_path)
    assert loaded.mode == LocalPolicyMode.TEAM
    assert loaded.role_for_actor("maxim") == LocalPolicyRole.OWNER


def test_cli_policy_status_and_check(tmp_path: Path, capsys) -> None:
    assert main(["policy", "init", "--root", str(tmp_path), "--mode", "team", "--actor", "maxim", "--role", "owner"]) == 0
    captured = capsys.readouterr()
    assert "Local policy initialized:" in captured.out

    assert main(["policy", "status", "--root", str(tmp_path), "--actor", "maxim", "--json"]) == 0
    captured = capsys.readouterr()
    status = json.loads(captured.out)
    assert status["policy"]["mode"] == "team"
    assert status["effective_role"] == "owner"

    assert main(["policy", "check", "--root", str(tmp_path), "--action", "publish_snapshot", "--actor", "reviewer-1", "--role", "reviewer"]) == 2
    captured = capsys.readouterr()
    assert "Policy check: denied" in captured.out
    assert "publish_snapshot" in captured.out


def test_workspace_sync_respects_locked_policy_for_reader(tmp_path: Path, capsys) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("Use `billing.update_credit_limit` for credit limit changes.\n", encoding="utf-8")

    assert main(["workspace", "sync", str(docs), "--root", str(tmp_path), "--policy-mode", "locked", "--role", "reader"]) == 2
    captured = capsys.readouterr()
    assert "Policy denied:" in captured.out
    assert "sync_workspace" in captured.out
