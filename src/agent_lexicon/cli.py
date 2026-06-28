"""Command line entry point for Agent Lexicon."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__, about
from .core import AgentLexiconLoadError, load_lexicon


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-lexicon",
        description="Shared terminology memory for AI agents.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the Agent Lexicon package version and exit.",
    )

    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a JSON or YAML lexicon document.",
    )
    validate_parser.add_argument("path", help="Path to a lexicon .json, .yaml, or .yml file.")
    validate_parser.add_argument(
        "--format",
        choices=("json", "yaml", "yml"),
        default=None,
        help="Override lexicon format detection.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.command == "validate":
        return _validate_command(path=Path(args.path), document_format=args.format)

    print(about())
    return 0


def _validate_command(*, path: Path, document_format: str | None) -> int:
    try:
        lexicon = load_lexicon(path, document_format=document_format)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1

    print(
        "Valid lexicon: "
        f"{len(lexicon.scopes)} scopes, "
        f"{len(lexicon.terms)} terms, "
        f"{len(lexicon.proposals)} proposals"
    )
    return 0
