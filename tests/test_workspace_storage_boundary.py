from __future__ import annotations

from pathlib import Path

import pytest

from agent_lexicon import (
    DEFAULT_WORKSPACE_STORAGE_BACKEND,
    SQLiteWorkspaceStore,
    WorkspaceError,
    WorkspaceStorageBackend,
    WorkspaceStorageConfig,
    WorkspaceStore,
    init_workspace,
    normalize_workspace_storage_backend,
    open_workspace,
    workspace_path,
)


def test_workspace_storage_config_defaults_to_sqlite() -> None:
    config = WorkspaceStorageConfig()

    assert config.backend is WorkspaceStorageBackend.SQLITE
    assert config.backend is DEFAULT_WORKSPACE_STORAGE_BACKEND
    assert config.to_dict() == {
        "backend": "sqlite",
        "workspace_dir": ".agent-lexicon",
        "database_name": "agent_lexicon.db",
    }


def test_normalize_workspace_storage_backend_accepts_sqlite() -> None:
    assert normalize_workspace_storage_backend("sqlite") is WorkspaceStorageBackend.SQLITE
    assert normalize_workspace_storage_backend(WorkspaceStorageBackend.SQLITE) is WorkspaceStorageBackend.SQLITE


@pytest.mark.parametrize("backend", ["postgres", "mysql", ""])
def test_normalize_workspace_storage_backend_rejects_unsupported_values(backend: str) -> None:
    with pytest.raises(ValueError):
        normalize_workspace_storage_backend(backend)


def test_workspace_path_accepts_explicit_sqlite_backend(tmp_path: Path) -> None:
    path = workspace_path(tmp_path, storage_backend="sqlite")

    assert path == tmp_path.resolve() / ".agent-lexicon" / "agent_lexicon.db"


def test_open_workspace_rejects_unsupported_backend(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        open_workspace(tmp_path, storage_backend="postgres")

    assert "unsupported workspace storage backend" in str(excinfo.value)


def test_workspace_state_implements_store_protocol(tmp_path: Path) -> None:
    state = init_workspace(tmp_path, storage_backend=WorkspaceStorageBackend.SQLITE)

    assert isinstance(state, SQLiteWorkspaceStore)
    assert isinstance(state, WorkspaceStore)
    assert state.summary().schema_version >= 1
