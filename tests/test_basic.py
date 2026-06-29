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
        self.assertEqual(agent_lexicon.__version__, "0.5.0")

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
        self.assertEqual(completed.stdout.strip(), "0.5.0")


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


    def test_cli_resolve_example(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "resolve",
                "examples/customer_limits/lexicon.yaml",
                "increase the limit",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("Status: ambiguous", completed.stdout)
        self.assertIn("Action: ask_clarification", completed.stdout)
        self.assertIn("billing.credit_limit", completed.stdout)
        self.assertIn("api.rate_limit", completed.stdout)



    def test_cli_guard_blocks_ambiguous_tool_call(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "guard",
                "examples/customer_limits/lexicon.yaml",
                "increase the limit",
                "--tool",
                "api.update_rate_limit",
            ],
            check=False,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Status: needs_clarification", completed.stdout)
        self.assertIn("Allowed: no", completed.stdout)

    def test_cli_guard_allows_scoped_tool_call(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "guard",
                "examples/customer_limits/lexicon.yaml",
                "increase the limit",
                "--tool",
                "billing.update_credit_limit",
                "--scope",
                "billing",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("Status: allowed", completed.stdout)
        self.assertIn("Allowed: yes", completed.stdout)

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

    def test_cli_validate_queries_example(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "validate-queries",
                "examples/customer_limits/queries.jsonl",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("Valid eval dataset", completed.stdout)
        self.assertIn("5 queries", completed.stdout)

    def test_cli_discover_candidates_example(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "discover-candidates",
                "examples/customer_limits/docs",
                "--root",
                "examples/customer_limits",
                "--max-candidates",
                "5",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("Candidate discovery:", completed.stdout)
        self.assertIn("billing.update_credit_limit", completed.stdout)

    def test_cli_build_evidence_example(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_lexicon",
                "build-evidence",
                "examples/customer_limits/docs",
                "--root",
                "examples/customer_limits",
                "--max-candidates",
                "5",
            ],
            check=True,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
        )
        self.assertIn("Evidence packs:", completed.stdout)
        self.assertIn("billing.update_credit_limit", completed.stdout)



if __name__ == "__main__":
    unittest.main()
