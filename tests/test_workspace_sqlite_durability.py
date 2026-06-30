from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_lexicon import (
    DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
    DEFAULT_SQLITE_JOURNAL_MODE,
    DEFAULT_SQLITE_SYNCHRONOUS,
    init_workspace,
    open_workspace,
)
from agent_lexicon.workspace.state import _connect


def test_workspace_sqlite_connection_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    state = init_workspace(tmp_path)

    with _connect(state.db_path) as connection:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        busy_timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])

    assert journal_mode == DEFAULT_SQLITE_JOURNAL_MODE
    assert busy_timeout == DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    assert synchronous == 1  # NORMAL
    assert DEFAULT_SQLITE_SYNCHRONOUS == "NORMAL"
    assert foreign_keys == 1


def test_workspace_sqlite_accepts_parallel_review_writers(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    def save_decisions(worker: int) -> int:
        state = open_workspace(tmp_path)
        saved = 0
        for index in range(20):
            state.save_review_decision(
                f"surface_{worker}_{index}",
                "accepted",
                note="parallel writer",
                reviewer=f"worker-{worker}",
            )
            saved += 1
        return saved

    with ThreadPoolExecutor(max_workers=8) as executor:
        counts = list(executor.map(save_decisions, range(8)))

    assert sum(counts) == 160
    summary = open_workspace(tmp_path).summary()
    assert summary.review_decision_count == 160
    assert summary.review_event_count == 160
