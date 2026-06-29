"""Command line entry point for Agent Lexicon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__, about
from .core import (
    AgentLexiconLoadError,
    ResolutionStatus,
    ToolGuardStatus,
    find_surface_matches,
    guard_tool_call,
    load_lexicon,
    resolve_text,
)
from .evals import EvalDatasetError, load_eval_queries, run_behavior_eval
from .ingest import LocalIngestError, ingest_local_paths
from .scout import (
    EvidencePackError,
    ScoutCandidateError,
    build_evidence_packs,
    discover_scout_candidates,
    existing_surfaces_from_lexicon,
)


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
    validate_queries_parser = subparsers.add_parser(
        "validate-queries",
        help="Validate a JSONL eval query dataset.",
    )
    validate_queries_parser.add_argument("path", help="Path to a queries.jsonl file.")

    check_parser = subparsers.add_parser(
        "check",
        help="Run behavior metrics for a lexicon and queries.jsonl dataset.",
    )
    check_parser.add_argument("lexicon_path", help="Path to a lexicon .json, .yaml, or .yml file.")
    check_parser.add_argument("queries_path", help="Path to a queries.jsonl file.")
    check_parser.add_argument(
        "--exclude-deprecated",
        action="store_true",
        help="Ignore deprecated aliases or deprecated terms.",
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full eval report as JSON.",
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Read local docs, README files, source files, and explicit local files.",
    )
    ingest_parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to ingest. Directories use local project defaults.",
    )
    ingest_parser.add_argument(
        "--root",
        default=None,
        help="Root path used for relative paths in output.",
    )
    ingest_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob to include when scanning directories. Can be provided multiple times.",
    )
    ingest_parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Maximum size of one ingested file in bytes.",
    )
    ingest_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print one JSON object per ingested document, including text.",
    )

    discover_candidates_parser = subparsers.add_parser(
        "discover-candidates",
        help="Discover terminology candidates from local docs and source files.",
    )
    discover_candidates_parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to scan. Directories use local project defaults.",
    )
    discover_candidates_parser.add_argument(
        "--root",
        default=None,
        help="Root path used for relative paths in output.",
    )
    discover_candidates_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob to include when scanning directories. Can be provided multiple times.",
    )
    discover_candidates_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional lexicon document whose existing surfaces should be ignored.",
    )
    discover_candidates_parser.add_argument(
        "--min-score",
        type=float,
        default=0.25,
        help="Minimum candidate score from 0.0 to 1.0.",
    )
    discover_candidates_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum number of candidates to print.",
    )
    discover_candidates_parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Maximum file size to read during local ingest.",
    )
    discover_candidates_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full candidate report as JSON.",
    )
    discover_candidates_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print one JSON candidate per line.",
    )

    build_evidence_parser = subparsers.add_parser(
        "build-evidence",
        help="Build line-numbered evidence packs for discovered candidates.",
    )
    build_evidence_parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to scan. Directories use local project defaults.",
    )
    build_evidence_parser.add_argument(
        "--root",
        default=None,
        help="Root path used for relative paths in output.",
    )
    build_evidence_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob to include when scanning directories. Can be provided multiple times.",
    )
    build_evidence_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional lexicon document whose existing surfaces should be ignored.",
    )
    build_evidence_parser.add_argument(
        "--min-score",
        type=float,
        default=0.25,
        help="Minimum candidate score from 0.0 to 1.0.",
    )
    build_evidence_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum number of candidate evidence packs to build.",
    )
    build_evidence_parser.add_argument(
        "--context-lines",
        type=int,
        default=1,
        help="Number of context lines before and after each evidence line.",
    )
    build_evidence_parser.add_argument(
        "--max-positive-snippets",
        type=int,
        default=3,
        help="Maximum positive snippets per evidence pack.",
    )
    build_evidence_parser.add_argument(
        "--max-negative-snippets",
        type=int,
        default=3,
        help="Maximum negative snippets per evidence pack.",
    )
    build_evidence_parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Maximum file size to read during local ingest.",
    )
    build_evidence_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full evidence report as JSON.",
    )
    build_evidence_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print one JSON evidence pack per line.",
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

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve known terminology and report ambiguity.",
    )
    resolve_parser.add_argument("path", help="Path to a lexicon .json, .yaml, or .yml file.")
    resolve_parser.add_argument("text", help="Text to resolve against the lexicon.")
    resolve_parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Limit resolution to a scope. Can be provided multiple times.",
    )
    resolve_parser.add_argument(
        "--exclude-deprecated",
        action="store_true",
        help="Ignore deprecated aliases or deprecated terms.",
    )

    guard_parser = subparsers.add_parser(
        "guard",
        help="Check whether a requested tool call is safe for resolved terminology.",
    )
    guard_parser.add_argument("path", help="Path to a lexicon .json, .yaml, or .yml file.")
    guard_parser.add_argument("text", help="Text that triggered the requested tool call.")
    guard_parser.add_argument(
        "--tool",
        required=True,
        help="Name of the tool the agent wants to call.",
    )
    guard_parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Limit resolution to a scope. Can be provided multiple times.",
    )
    guard_parser.add_argument(
        "--exclude-deprecated",
        action="store_true",
        help="Ignore deprecated aliases or deprecated terms.",
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

    if args.command == "validate-queries":
        return _validate_queries_command(path=Path(args.path))

    if args.command == "check":
        return _check_command(
            lexicon_path=Path(args.lexicon_path),
            queries_path=Path(args.queries_path),
            include_deprecated=not args.exclude_deprecated,
            as_json=args.json,
        )

    if args.command == "ingest":
        return _ingest_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            max_file_bytes=args.max_file_bytes,
            as_jsonl=args.jsonl,
        )

    if args.command == "discover-candidates":
        return _discover_candidates_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
            max_file_bytes=args.max_file_bytes,
            as_json=args.json,
            as_jsonl=args.jsonl,
        )

    if args.command == "build-evidence":
        return _build_evidence_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
            context_lines=args.context_lines,
            max_positive_snippets=args.max_positive_snippets,
            max_negative_snippets=args.max_negative_snippets,
            max_file_bytes=args.max_file_bytes,
            as_json=args.json,
            as_jsonl=args.jsonl,
        )

    if args.command == "match":
        return _match_command(
            path=Path(args.path),
            text=args.text,
            scopes=args.scope,
            include_deprecated=not args.exclude_deprecated,
            longest_only=args.longest_only,
        )

    if args.command == "resolve":
        return _resolve_command(
            path=Path(args.path),
            text=args.text,
            scopes=args.scope,
            include_deprecated=not args.exclude_deprecated,
        )

    if args.command == "guard":
        return _guard_command(
            path=Path(args.path),
            text=args.text,
            tool_name=args.tool,
            scopes=args.scope,
            include_deprecated=not args.exclude_deprecated,
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


def _validate_queries_command(*, path: Path) -> int:
    try:
        queries = load_eval_queries(path)
    except EvalDatasetError as exc:
        print(f"Invalid eval dataset: {exc}")
        return 1

    tool_call_count = sum(len(query.tool_calls) for query in queries)
    scoped_count = sum(1 for query in queries if query.scopes)
    print(
        "Valid eval dataset: "
        f"{len(queries)} queries, "
        f"{tool_call_count} tool call expectations, "
        f"{scoped_count} scoped queries"
    )
    return 0


def _check_command(
    *,
    lexicon_path: Path,
    queries_path: Path,
    include_deprecated: bool,
    as_json: bool,
) -> int:
    try:
        lexicon = load_lexicon(lexicon_path)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1
    try:
        queries = load_eval_queries(queries_path)
    except EvalDatasetError as exc:
        print(f"Invalid eval dataset: {exc}")
        return 1

    report = run_behavior_eval(lexicon, queries, include_deprecated=include_deprecated)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.passed else 1

    metrics = report.metrics
    print(
        "Behavior check: "
        f"{metrics.passed_checks}/{metrics.total_checks} checks passed "
        f"across {metrics.query_count} queries"
    )
    _print_metric("Overall accuracy", metrics.overall_accuracy)
    _print_metric("Ambiguity detection", metrics.ambiguity_detection_rate)
    _print_metric("Canonicalization", metrics.canonicalization_accuracy)
    _print_metric("Wrong tool prevention", metrics.wrong_tool_prevention_rate)
    _print_metric("Tool status", metrics.tool_status_accuracy)
    _print_metric("Tool allowed", metrics.tool_allowed_accuracy)

    failed = [result for result in report.results if not result.passed]
    if failed:
        print("Failed queries:")
        for result in failed:
            print(f"- {result.query.id}: {result.query.text}")
    return 0 if report.passed else 1


def _print_metric(label: str, value: float | None) -> None:
    if value is None:
        print(f"{label}: n/a")
        return
    print(f"{label}: {value * 100:.1f}%")


def _ingest_command(
    *,
    paths: list[Path],
    root: Path | None,
    include_globs: list[str] | None,
    max_file_bytes: int,
    as_jsonl: bool,
) -> int:
    try:
        report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            max_file_bytes=max_file_bytes,
        )
    except LocalIngestError as exc:
        print(f"Invalid local ingest input: {exc}")
        return 1

    if as_jsonl:
        for document in report.documents:
            print(document.to_json_line(include_text=True))
        return 0

    print(
        "Local ingest: "
        f"{report.document_count} documents, "
        f"{report.total_lines} lines, "
        f"{report.total_size_bytes} bytes"
    )
    for document in report.documents:
        print(
            f"- {document.relative_path} "
            f"({document.kind.value}, {document.line_count} lines, {document.size_bytes} bytes)"
        )
    if report.skipped_paths:
        print("Skipped paths:")
        for skipped_path in report.skipped_paths:
            print(f"- {skipped_path}")
    return 0


def _discover_candidates_command(
    *,
    paths: list[Path],
    root: Path | None,
    include_globs: list[str] | None,
    lexicon_path: Path | None,
    min_score: float,
    max_candidates: int,
    max_file_bytes: int,
    as_json: bool,
    as_jsonl: bool,
) -> int:
    if as_json and as_jsonl:
        print("Invalid candidate discovery input: choose either --json or --jsonl")
        return 1

    try:
        ingest_report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            max_file_bytes=max_file_bytes,
        )
    except LocalIngestError as exc:
        print(f"Invalid local ingest input: {exc}")
        return 1

    existing_surfaces: tuple[str, ...] = ()
    if lexicon_path is not None:
        try:
            lexicon = load_lexicon(lexicon_path)
        except AgentLexiconLoadError as exc:
            print(f"Invalid lexicon: {exc}")
            return 1
        existing_surfaces = existing_surfaces_from_lexicon(lexicon)

    try:
        report = discover_scout_candidates(
            ingest_report.documents,
            existing_surfaces=existing_surfaces,
            min_score=min_score,
            max_candidates=max_candidates,
        )
    except ScoutCandidateError as exc:
        print(f"Invalid candidate discovery input: {exc}")
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    if as_jsonl:
        for candidate in report.candidates:
            print(candidate.to_json_line())
        return 0

    print(
        "Candidate discovery: "
        f"{report.candidate_count} candidates from "
        f"{report.document_count} documents"
    )
    for candidate in report.candidates:
        print(
            f"- {candidate.surface} "
            f"({candidate.kind.value}, score={candidate.score:.3f}, "
            f"jargon={candidate.jargon_score:.3f}, "
            f"background_penalty={candidate.background_penalty:.3f}, "
            f"occurrences={candidate.occurrence_count}, "
            f"documents={candidate.document_count})"
        )
        if candidate.occurrences:
            occurrence = candidate.occurrences[0]
            print(f"  {occurrence.document_path}:{occurrence.line_number} {occurrence.line_text}")
    return 0



def _build_evidence_command(
    *,
    paths: list[Path],
    root: Path | None,
    include_globs: list[str] | None,
    lexicon_path: Path | None,
    min_score: float,
    max_candidates: int,
    context_lines: int,
    max_positive_snippets: int,
    max_negative_snippets: int,
    max_file_bytes: int,
    as_json: bool,
    as_jsonl: bool,
) -> int:
    if as_json and as_jsonl:
        print("Invalid evidence input: choose either --json or --jsonl")
        return 1

    try:
        ingest_report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            max_file_bytes=max_file_bytes,
        )
    except LocalIngestError as exc:
        print(f"Invalid local ingest input: {exc}")
        return 1

    existing_surfaces: tuple[str, ...] = ()
    if lexicon_path is not None:
        try:
            lexicon = load_lexicon(lexicon_path)
        except AgentLexiconLoadError as exc:
            print(f"Invalid lexicon: {exc}")
            return 1
        existing_surfaces = existing_surfaces_from_lexicon(lexicon)

    try:
        candidate_report = discover_scout_candidates(
            ingest_report.documents,
            existing_surfaces=existing_surfaces,
            min_score=min_score,
            max_candidates=max_candidates,
        )
        evidence_report = build_evidence_packs(
            ingest_report.documents,
            candidate_report.candidates,
            context_lines=context_lines,
            max_positive_snippets=max_positive_snippets,
            max_negative_snippets=max_negative_snippets,
        )
    except (ScoutCandidateError, EvidencePackError) as exc:
        print(f"Invalid evidence input: {exc}")
        return 1

    if as_json:
        print(json.dumps(evidence_report.to_dict(), indent=2, sort_keys=True))
        return 0
    if as_jsonl:
        for pack in evidence_report.packs:
            print(pack.to_json_line())
        return 0

    print(
        "Evidence packs: "
        f"{evidence_report.pack_count} packs from "
        f"{evidence_report.document_count} documents "
        f"({evidence_report.positive_count} positive, "
        f"{evidence_report.negative_count} negative snippets)"
    )
    for pack in evidence_report.packs:
        print(
            f"- {pack.surface} "
            f"({pack.candidate_kind.value}, score={pack.score:.3f}, "
            f"positive={pack.positive_count}, negative={pack.negative_count})"
        )
        if pack.positive_snippets:
            snippet = pack.positive_snippets[0]
            print(f"  + {snippet.document_path}:{snippet.start_line}-{snippet.end_line} {snippet.text.splitlines()[0]}")
        if pack.negative_snippets:
            snippet = pack.negative_snippets[0]
            print(f"  - {snippet.document_path}:{snippet.start_line}-{snippet.end_line} {snippet.text.splitlines()[0]}")
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


def _resolve_command(
    *,
    path: Path,
    text: str,
    scopes: list[str] | None,
    include_deprecated: bool,
) -> int:
    try:
        lexicon = load_lexicon(path)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1

    decision = resolve_text(
        lexicon,
        text,
        scopes=scopes,
        include_deprecated=include_deprecated,
    )
    print(f"Status: {decision.status.value}")
    print(f"Action: {decision.action.value}")
    if decision.message:
        print(f"Message: {decision.message}")

    if decision.status == ResolutionStatus.UNKNOWN:
        return 0

    print("Candidates:")
    for candidate in decision.candidates:
        scope_label = ",".join(candidate.scopes) if candidate.scopes else "global"
        surface_label = ", ".join(repr(surface) for surface in candidate.matched_surfaces)
        deprecated_label = " deprecated" if candidate.deprecated else ""
        print(
            f"- {candidate.term_id} "
            f"({candidate.canonical}) "
            f"scopes={scope_label} "
            f"matches={surface_label}" 
            f"{deprecated_label}"
        )
    return 0



def _guard_command(
    *,
    path: Path,
    text: str,
    tool_name: str,
    scopes: list[str] | None,
    include_deprecated: bool,
) -> int:
    try:
        lexicon = load_lexicon(path)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1

    decision = guard_tool_call(
        lexicon,
        text,
        tool_name=tool_name,
        scopes=scopes,
        include_deprecated=include_deprecated,
    )
    print(f"Status: {decision.status.value}")
    print(f"Action: {decision.action.value}")
    print(f"Allowed: {'yes' if decision.is_allowed else 'no'}")
    print(f"Reason: {decision.reason}")
    print(f"Resolution: {decision.resolution.status.value}")

    if decision.matched_term_ids:
        print("Matched terms:")
        for term_id in decision.matched_term_ids:
            print(f"- {term_id}")
    if decision.allowed_tool_names:
        print("Allowed tools:")
        for allowed_tool_name in decision.allowed_tool_names:
            print(f"- {allowed_tool_name}")

    return 0 if decision.status in {ToolGuardStatus.ALLOWED, ToolGuardStatus.NO_MATCH} else 2
