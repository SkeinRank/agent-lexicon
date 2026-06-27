from __future__ import annotations

import subprocess
import sys
import unittest

import agent_lexicon
from agent_lexicon.cli import main


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
        )
        self.assertEqual(completed.stdout.strip(), "0.0.1")


if __name__ == "__main__":
    unittest.main()
