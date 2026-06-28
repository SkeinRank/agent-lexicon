"""Command line entry point for Agent Lexicon."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__, about
from .core import AgentLexiconLoadError, find_surface_matches, load_lexicon


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
    match_parser = subparsers.add_parser(
        "match",
        help="Find known canonical terms and aliases in text.",
    )
    match_parser.add_argument("path", help="Path to a lexicon .json, .yaml, or .yml file.")
    match_parser.add_argument("text", help="Text to scan for known surfaces.")
    match_parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Limit matches to a scope. Can be provided multiple times.",
    )
    match_parser.add_argument(
        "--exclude-deprecated",
        action="store_true",
        help="Do not return deprecated aliases or deprecated terms.",
    )
    match_parser.add_argument(
        "--longest-only",
        action="store_true",
        help="Return the longest non-overlapping surface matches.",
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

    if args.command == "match":
        return _match_command(
            path=Path(args.path),
            text=args.text,
            scopes=args.scope,
            include_deprecated=not args.exclude_deprecated,
            longest_only=args.longest_only,
        )

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


def _match_command(
    *,
    path: Path,
    text: str,
    scopes: list[str] | None,
    include_deprecated: bool,
    longest_only: bool,
) -> int:
    try:
        lexicon = load_lexicon(path)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1

    matches = find_surface_matches(
        lexicon,
        text,
        scopes=scopes,
        include_deprecated=include_deprecated,
        longest_only=longest_only,
    )
    if not matches:
        print("No matches")
        return 0

    for match in matches:
        scope_label = ",".join(match.scopes) if match.scopes else "global"
        deprecated_label = " deprecated" if match.deprecated else ""
        print(
            f"{match.start}:{match.end} "
            f"{match.term_id} "
            f"{match.kind.value} "
            f"{scope_label}{deprecated_label} "
            f"-> {match.matched_text!r}"
        )
    return 0
