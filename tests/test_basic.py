from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import unittest

import agent_lexicon
from agent_lexicon.cli import main


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
    return env


class AgentLexiconSmokeTests(unittest.TestCase):
    def test_version_is_initialized(self) -> None:
        self.assertEqual(agent_lexicon.__version__, "0.0.1")

    def test_about_mentions_agent_lexicon(self) -> None:
        self.assertIn("Agent Lexicon", agent_lexicon.about())

    def test_cli_version(self) -> None:
        self.assertEqual(main(["--version"]), 0)

    def test_module_entrypoint(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "agent_lexicon", "--version"],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertEqual(completed.stdout.strip(), "0.0.1")


    def test_cli_match_example(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "match",
                "examples/customer_limits/lexicon.yaml",
                "The customer cap and rate limit changed.",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("billing.credit_limit alias billing -> 'customer cap'", completed.stdout)
        self.assertIn("api.rate_limit canonical api -> 'rate limit'", completed.stdout)

    def test_cli_validate_example(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "validate",
                "examples/customer_limits/lexicon.yaml",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("Valid lexicon", completed.stdout)


if __name__ == "__main__":
    unittest.main()
