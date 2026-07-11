from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _CloseTrackingStdout:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_main_returns_zero_and_closes_stdout_on_broken_pipe(monkeypatch) -> None:
    from agent_lexicon import cli

    fake_stdout = _CloseTrackingStdout()

    def raise_broken_pipe(argv: list[str] | None = None) -> int:
        raise BrokenPipeError

    monkeypatch.setattr(cli, "_run", raise_broken_pipe)
    monkeypatch.setattr(cli.sys, "stdout", fake_stdout)

    assert cli.main(["--version"]) == 0
    assert fake_stdout.closed is True


def test_cli_process_exits_cleanly_when_stdout_pipe_closes() -> None:
    code = """
from agent_lexicon import cli

def noisy(argv=None):
    for index in range(1_000_000):
        print(f\"line {index}\")
    return 0

cli._run = noisy
raise SystemExit(cli.main([]))
"""
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    src_path = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdout is not None
    proc.stdout.readline()
    proc.stdout.close()
    _, stderr = proc.communicate(timeout=10)

    assert proc.returncode == 0
    assert "BrokenPipeError" not in stderr
