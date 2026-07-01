"""Command line entry point for Agent Lexicon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from . import __version__, about
from .config import (
    AgentLexiconConfigError,
    effective_exclude_globs,
    effective_include_globs,
    effective_max_file_bytes,
    load_project_config,
)
from .dictionary import (
    DictionaryLayoutError,
    SemanticDiffError,
    SemanticMergeError,
    diff_lexicon_files,
    merge_lexicon_files,
    run_dictionary_pr_checks,
    init_dictionary_layout,
    inspect_dictionary_layout,
    validate_dictionary_layout,
    write_dictionary_manifest,
    write_merged_lexicon_json,
)
from .core import (
    AgentLexiconLoadError,
    ResolutionStatus,
    ToolGuardStatus,
    find_surface_matches,
    guard_tool_call,
    load_lexicon,
    lint_lexicon_file,
    resolve_text,
)
from .evals import EvalDatasetError, load_eval_queries, run_behavior_eval
from .ingest import LocalIngestError, ingest_local_paths
from .mcp import McpServerError, mcp_tool_definitions, run_mcp_stdio_server
from .review_agent import (
    ReviewAgentError,
    ReviewDatasetError,
    ReviewDatasetQuality,
    build_review_agent_prompt,
    build_review_dataset,
    export_review_dataset_jsonl,
    run_review_agent,
    run_review_agent_consensus,
)
from .policy import (
    LocalPolicyError,
    LocalPolicyMode,
    LocalPolicyRole,
    PolicyAction,
    check_local_policy,
    init_local_policy,
    load_local_policy,
    policy_path,
)
from .safety import PromptSafetyError, scan_documents_for_prompt_injection
from .scout import (
    CanonicalMigrationError,
    EvidencePackError,
    ScoutCandidateError,
    build_evidence_packs,
    build_scout_quality_report,
    ScoutQualityReportError,
    BgeSemanticNearMissBackend,
    DEFAULT_BGE_BASE_MODEL,
    DEFAULT_BGE_SEMANTIC_THRESHOLD,
    SemanticNearMissBackend,
    SemanticNearMissError,
    GitMergeCheckError,
    check_git_merge_terminology,
    discover_canonical_migration_candidates,
    discover_scout_candidates,
    existing_surfaces_from_lexicon,
)
from .web import ReviewInboxError, run_review_inbox
from .workflows import (
    DEFAULT_SCAN_PATHS,
    SimpleWorkflowError,
    run_simple_analyze,
    run_simple_init,
    run_simple_publish,
    run_simple_scan,
)
from .workspace import (
    ReviewDecisionStatus,
    WorkspaceDecisionAction,
    SnapshotPublishError,
    WorkspaceError,
    init_workspace,
    open_workspace,
    publish_local_snapshot,
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
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a local Agent Lexicon project.",
    )
    init_parser.add_argument("--root", default=".", help="Project root for lexicon/ and .agent-lexicon/.")
    init_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    init_parser.add_argument(
        "--policy-mode",
        choices=tuple(mode.value for mode in LocalPolicyMode),
        default=LocalPolicyMode.SOLO.value,
        help="Local policy mode to initialize.",
    )
    init_parser.add_argument("--actor", default="local", help="Actor to store in the local policy.")
    init_parser.add_argument(
        "--role",
        choices=tuple(role.value for role in LocalPolicyRole),
        default=LocalPolicyRole.OWNER.value,
        help="Role assigned to --actor in the local policy.",
    )
    init_parser.add_argument("--force", action="store_true", help="Overwrite generated starter files and policy.")
    init_parser.add_argument("--reset-workspace", action="store_true", help="Recreate the local workspace database.")
    init_parser.add_argument("--json", action="store_true", help="Print the init report as JSON.")

    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan local project files and save candidates plus evidence to the workspace.",
    )
    scan_parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to scan. Defaults to README.md, docs, and src when present.",
    )
    scan_parser.add_argument("--root", default=".", help="Project root for lexicon/ and .agent-lexicon/.")
    scan_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    scan_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional lexicon document whose existing surfaces should be ignored.",
    )
    scan_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob to include when scanning directories. Overrides scan.include from config when provided.",
    )
    scan_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob to exclude when scanning directories. Overrides scan.exclude from config when provided.",
    )
    scan_parser.add_argument(
        "--config",
        default=None,
        help="Optional Agent Lexicon config path. Defaults to .agent-lexicon/config.yaml when present.",
    )
    scan_parser.add_argument("--min-score", type=float, default=0.25, help="Minimum candidate score from 0.0 to 1.0.")
    scan_parser.add_argument("--max-candidates", type=int, default=20, help="Maximum number of candidates to save.")
    scan_parser.add_argument(
        "--oov-tokenizer",
        default=None,
        help="Optional Scout tokenizer for OOV scoring. Use 'auto' for BAAI/bge-small-en-v1.5 or 'proxy' for the dependency-free scorer.",
    )
    scan_parser.add_argument("--context-lines", type=int, default=1, help="Context lines around each evidence match.")
    scan_parser.add_argument("--max-positive-snippets", type=int, default=3, help="Maximum positive snippets per candidate.")
    scan_parser.add_argument("--max-negative-snippets", type=int, default=3, help="Maximum negative snippets per candidate.")
    scan_parser.add_argument("--max-file-bytes", type=int, default=None, help="Maximum file size to read. Defaults to scan.max_file_bytes from config.")
    _add_local_policy_options(scan_parser)
    scan_parser.add_argument("--quality-report", action="store_true", help="Print Scout quality metrics after scanning.")
    scan_parser.add_argument("--json", action="store_true", help="Print the scan report as JSON.")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Summarize important local terminology candidates from the workspace.",
    )
    analyze_parser.add_argument("--root", default=".", help="Project root where .agent-lexicon/ is stored.")
    analyze_parser.add_argument("--limit", type=int, default=10, help="Maximum number of candidates to summarize.")
    analyze_parser.add_argument(
        "--priority",
        choices=("all", "important", "later"),
        default="all",
        help="Filter analysis output by priority bucket.",
    )
    analyze_parser.add_argument(
        "--review-agent",
        action="store_true",
        help="Include deterministic Review Agent recommendations in the analysis.",
    )
    analyze_parser.add_argument(
        "--consensus",
        action="store_true",
        help="Use the Review Agent consensus and abstention wrapper when --review-agent is enabled.",
    )
    _add_local_policy_options(analyze_parser)
    analyze_parser.add_argument("--quality-report", action="store_true", help="Print Scout quality metrics from the workspace.")
    analyze_parser.add_argument("--json", action="store_true", help="Print the analysis report as JSON.")

    publish_parser = subparsers.add_parser(
        "publish",
        help="Publish accepted local review decisions as a local lexicon snapshot.",
    )
    publish_parser.add_argument("--root", default=".", help="Project root where .agent-lexicon/ is stored.")
    publish_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    publish_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional base lexicon. Defaults to lexicon/lexicon.yaml when present.",
    )
    publish_parser.add_argument("--output", default=None, help="Optional output path for the snapshot JSON file.")
    publish_parser.add_argument("--snapshot-id", default=None, help="Optional stable snapshot id.")
    _add_local_policy_options(publish_parser)
    publish_parser.add_argument("--json", action="store_true", help="Print the publish report as JSON.")

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
    validate_parser.add_argument(
        "--lint",
        action="store_true",
        help="Also print lexicon quality warnings.",
    )
    validate_parser.add_argument(
        "--strict-lint",
        action="store_true",
        help="Return exit code 1 when lint warnings are found.",
    )

    lint_parser = subparsers.add_parser(
        "lint",
        help="Report lexicon quality warnings for broad or risky surfaces.",
    )
    lint_parser.add_argument("path", help="Path to a lexicon .json, .yaml, or .yml file.")
    lint_parser.add_argument(
        "--format",
        choices=("json", "yaml", "yml"),
        default=None,
        help="Override lexicon format detection.",
    )
    lint_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return exit code 1 when lint warnings are found.",
    )
    lint_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the lint report as JSON.",
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

    check_merge_parser = subparsers.add_parser(
        "check-merge",
        help="Review terminology drift in added lines between git refs.",
    )
    check_merge_parser.add_argument("--root", default=".", help="Git repository root.")
    check_merge_parser.add_argument(
        "--lexicon",
        default=None,
        help="Lexicon file. Defaults to lexicon/lexicon.yaml under --root.",
    )
    check_merge_parser.add_argument("--base", default="main", help="Base git ref for the merge comparison.")
    check_merge_parser.add_argument("--head", default="HEAD", help="Head git ref for the merge comparison.")
    check_merge_parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Limit terminology checks to a scope. Can be provided multiple times.",
    )
    check_merge_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Limit checked paths with a git-style glob such as 'src/**'. Overrides scan.include from config when provided.",
    )
    check_merge_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Exclude checked paths with a git-style glob such as 'dist/**'. Overrides scan.exclude from config when provided.",
    )
    check_merge_parser.add_argument(
        "--config",
        default=None,
        help="Optional Agent Lexicon config path. Defaults to .agent-lexicon/config.yaml when present.",
    )
    check_merge_parser.add_argument(
        "--exclude-deprecated",
        action="store_true",
        help="Ignore deprecated aliases or deprecated terms.",
    )
    check_merge_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.42,
        help="Minimum confidence for near-miss review suggestions.",
    )
    check_merge_parser.add_argument(
        "--max-suggestions",
        type=int,
        default=3,
        help="Maximum near-miss suggestions per unknown identifier.",
    )
    check_merge_parser.add_argument(
        "--fail-on-review",
        action="store_true",
        help="Return exit code 1 when reviewable unknown identifiers are found.",
    )
    check_merge_parser.add_argument(
        "--include-unresolved-unknowns",
        action="store_true",
        help="Also include low-signal unknown identifiers that are hidden by the default merge review.",
    )
    check_merge_parser.add_argument(
        "--semantic-near-miss",
        action="store_true",
        help="Use the optional BGE semantic reranker for gray-zone near-miss suggestions.",
    )
    check_merge_parser.add_argument(
        "--semantic-model",
        default=DEFAULT_BGE_BASE_MODEL,
        help="Semantic near-miss model name used with --semantic-near-miss.",
    )
    check_merge_parser.add_argument(
        "--semantic-threshold",
        type=float,
        default=DEFAULT_BGE_SEMANTIC_THRESHOLD,
        help="Minimum semantic similarity used with --semantic-near-miss.",
    )
    check_merge_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the merge terminology report as JSON.",
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
        "--exclude",
        action="append",
        default=None,
        help="Glob to exclude when scanning directories. Can be provided multiple times.",
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
        "--exclude",
        action="append",
        default=None,
        help="Glob to exclude when scanning directories. Can be provided multiple times.",
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
        "--oov-tokenizer",
        default=None,
        help="Optional Scout tokenizer for OOV scoring. Use 'auto' for BAAI/bge-small-en-v1.5 or 'proxy' for the dependency-free scorer.",
    )
    discover_candidates_parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Maximum file size to read during local ingest.",
    )
    discover_candidates_parser.add_argument(
        "--quality-report",
        action="store_true",
        help="Print Scout quality metrics after candidate discovery.",
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
        "--exclude",
        action="append",
        default=None,
        help="Glob to exclude when scanning directories. Can be provided multiple times.",
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
        "--oov-tokenizer",
        default=None,
        help="Optional Scout tokenizer for OOV scoring. Use 'auto' for BAAI/bge-small-en-v1.5 or 'proxy' for the dependency-free scorer.",
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
        "--skip-prompt-safety",
        action="store_true",
        help="Do not annotate evidence snippets with prompt-safety findings.",
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

    safety_parser = subparsers.add_parser(
        "safety",
        help="Scan local docs for prompt-injection indicators before LLM review.",
    )
    safety_subparsers = safety_parser.add_subparsers(dest="safety_command")
    safety_scan_parser = safety_subparsers.add_parser(
        "scan",
        help="Scan local docs, README files, source files, and explicit local files for prompt-injection indicators.",
    )
    safety_scan_parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to scan. Directories use local project defaults.",
    )
    safety_scan_parser.add_argument(
        "--root",
        default=None,
        help="Root path used for relative paths in output.",
    )
    safety_scan_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob to include when scanning directories. Can be provided multiple times.",
    )
    safety_scan_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob to exclude when scanning directories. Can be provided multiple times.",
    )
    safety_scan_parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Maximum file size to read during local ingest.",
    )
    safety_scan_parser.add_argument(
        "--fail-on-high-risk",
        action="store_true",
        help="Return exit code 1 when high-risk findings are detected.",
    )
    safety_scan_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full prompt-safety report as JSON.",
    )

    policy_parser = subparsers.add_parser(
        "policy",
        help="Manage local RBAC-lite policy modes.",
    )
    policy_subparsers = policy_parser.add_subparsers(dest="policy_command")

    policy_init_parser = policy_subparsers.add_parser(
        "init",
        help="Create a local policy file under .agent-lexicon/.",
    )
    policy_init_parser.add_argument("--root", default=".", help="Project root where .agent-lexicon/ is stored.")
    policy_init_parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in LocalPolicyMode),
        default=LocalPolicyMode.SOLO.value,
        help="Local policy mode to write.",
    )
    policy_init_parser.add_argument("--actor", default="local", help="Actor to store in the policy file.")
    policy_init_parser.add_argument(
        "--role",
        choices=tuple(role.value for role in LocalPolicyRole),
        default=LocalPolicyRole.OWNER.value,
        help="Role assigned to --actor.",
    )
    policy_init_parser.add_argument("--force", action="store_true", help="Overwrite an existing local policy file.")
    policy_init_parser.add_argument("--json", action="store_true", help="Print the policy document as JSON.")

    policy_status_parser = policy_subparsers.add_parser(
        "status",
        help="Show the effective local policy.",
    )
    policy_status_parser.add_argument("--root", default=".", help="Project root where .agent-lexicon/ is stored.")
    policy_status_parser.add_argument(
        "--policy-mode",
        choices=tuple(mode.value for mode in LocalPolicyMode),
        default=None,
        help="Temporarily override the local policy mode.",
    )
    policy_status_parser.add_argument("--actor", default="local", help="Actor used for role resolution.")
    policy_status_parser.add_argument(
        "--role",
        choices=tuple(role.value for role in LocalPolicyRole),
        default=None,
        help="Explicit role override for this command.",
    )
    policy_status_parser.add_argument("--json", action="store_true", help="Print policy status as JSON.")

    policy_check_parser = policy_subparsers.add_parser(
        "check",
        help="Check whether a local action is allowed.",
    )
    policy_check_parser.add_argument(
        "--action",
        required=True,
        choices=tuple(action.value for action in PolicyAction),
        help="Local action to check.",
    )
    policy_check_parser.add_argument("--root", default=".", help="Project root where .agent-lexicon/ is stored.")
    policy_check_parser.add_argument(
        "--policy-mode",
        choices=tuple(mode.value for mode in LocalPolicyMode),
        default=None,
        help="Temporarily override the local policy mode.",
    )
    policy_check_parser.add_argument("--actor", default="local", help="Actor used for role resolution.")
    policy_check_parser.add_argument(
        "--role",
        choices=tuple(role.value for role in LocalPolicyRole),
        default=None,
        help="Explicit role override for this command.",
    )
    policy_check_parser.add_argument("--json", action="store_true", help="Print the policy decision as JSON.")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run the local Model Context Protocol server.",
    )
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")

    mcp_serve_parser = mcp_subparsers.add_parser(
        "serve",
        help="Run a dependency-free MCP stdio server for local agents.",
    )
    mcp_serve_parser.add_argument(
        "--root",
        default=".",
        help="Project root used for lexicon/ and .agent-lexicon/ state.",
    )
    mcp_serve_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional lexicon file path. Defaults to lexicon/lexicon.yaml under --root.",
    )
    _add_local_policy_options(mcp_serve_parser)

    mcp_tools_parser = mcp_subparsers.add_parser(
        "tools",
        help="List Agent Lexicon MCP tools as JSON.",
    )
    mcp_tools_parser.add_argument("--json", action="store_true", help="Print the tool list as JSON.")


    review_agent_parser = subparsers.add_parser(
        "review-agent",
        help="Run local proposal pre-review for workspace candidates.",
    )
    review_agent_subparsers = review_agent_parser.add_subparsers(dest="review_agent_command")

    review_agent_prompt_parser = review_agent_subparsers.add_parser(
        "prompt",
        help="Build a safe data-only LLM review prompt for one candidate.",
    )
    review_agent_prompt_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    review_agent_prompt_parser.add_argument(
        "--surface",
        default=None,
        help="Normalized candidate surface. Defaults to the highest-priority unreviewed candidate.",
    )
    review_agent_prompt_parser.add_argument(
        "--max-chars",
        type=int,
        default=16_000,
        help="Maximum prompt length in characters.",
    )
    _add_local_policy_options(review_agent_prompt_parser)
    review_agent_prompt_parser.add_argument(
        "--json",
        action="store_true",
        help="Print prompt payload as JSON.",
    )

    review_agent_assess_parser = review_agent_subparsers.add_parser(
        "assess",
        help="Produce a structured review recommendation for one candidate.",
    )
    review_agent_assess_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    review_agent_assess_parser.add_argument(
        "--surface",
        default=None,
        help="Normalized candidate surface. Defaults to the highest-priority unreviewed candidate.",
    )
    review_agent_assess_parser.add_argument(
        "--llm-response",
        default=None,
        help="Optional path to a structured JSON LLM review response.",
    )
    _add_local_policy_options(review_agent_assess_parser)
    review_agent_assess_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the review recommendation as JSON.",
    )

    review_agent_consensus_parser = review_agent_subparsers.add_parser(
        "consensus",
        help="Aggregate Review Agent samples and abstain when they disagree.",
    )
    review_agent_consensus_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    review_agent_consensus_parser.add_argument(
        "--surface",
        default=None,
        help="Normalized candidate surface. Defaults to the highest-priority unreviewed candidate.",
    )
    review_agent_consensus_parser.add_argument(
        "--llm-response",
        action="append",
        default=None,
        help="Path to a structured JSON LLM review response. Can be provided multiple times.",
    )
    review_agent_consensus_parser.add_argument(
        "--min-agreement",
        type=float,
        default=0.67,
        help="Minimum vote agreement ratio required for consensus.",
    )
    review_agent_consensus_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.65,
        help="Minimum average confidence required for consensus.",
    )
    _add_local_policy_options(review_agent_consensus_parser)
    review_agent_consensus_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the consensus report as JSON.",
    )

    review_agent_dataset_parser = review_agent_subparsers.add_parser(
        "dataset",
        help="Export review-quality dataset examples from local review events.",
    )
    review_agent_dataset_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    review_agent_dataset_parser.add_argument(
        "--output",
        default=None,
        help="Optional JSONL output path. If omitted, JSONL is printed to stdout unless --json is used.",
    )
    review_agent_dataset_parser.add_argument(
        "--quality",
        choices=tuple(quality.value for quality in ReviewDatasetQuality),
        default=None,
        help="Export only examples with a specific quality label.",
    )
    review_agent_dataset_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of review events to read.",
    )
    review_agent_dataset_parser.add_argument(
        "--include-review-agent",
        action="store_true",
        help="Include deterministic Review Agent suggestions next to human decisions.",
    )
    _add_local_policy_options(review_agent_dataset_parser)
    review_agent_dataset_parser.add_argument(
        "--json",
        action="store_true",
        help="Print dataset report metadata and examples as JSON.",
    )

    discover_migrations_parser = subparsers.add_parser(
        "discover-migrations",
        help="Discover canonical migration candidates from deprecated terms.",
    )
    discover_migrations_parser.add_argument(
        "path",
        help="Path to a lexicon .json, .yaml, or .yml file.",
    )
    discover_migrations_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.35,
        help="Minimum migration confidence from 0.0 to 1.0.",
    )
    discover_migrations_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum number of migration candidates to print.",
    )
    discover_migrations_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full migration report as JSON.",
    )
    discover_migrations_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print one migration candidate per line.",
    )

    dictionary_parser = subparsers.add_parser(
        "dictionary",
        help="Manage the git-tracked dictionary-as-code layout.",
    )
    dictionary_subparsers = dictionary_parser.add_subparsers(dest="dictionary_command")

    dictionary_init_parser = dictionary_subparsers.add_parser(
        "init",
        help="Create a git-tracked dictionary-as-code layout.",
    )
    dictionary_init_parser.add_argument(
        "--root",
        default=".",
        help="Project root where the dictionary layout is stored.",
    )
    dictionary_init_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    dictionary_init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite generated starter files if they already exist.",
    )
    dictionary_init_parser.add_argument(
        "--json",
        action="store_true",
        help="Print layout status as JSON.",
    )

    dictionary_status_parser = dictionary_subparsers.add_parser(
        "status",
        help="Inspect a dictionary-as-code layout.",
    )
    dictionary_status_parser.add_argument(
        "--root",
        default=".",
        help="Project root where the dictionary layout is stored.",
    )
    dictionary_status_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    dictionary_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print layout status as JSON.",
    )

    dictionary_validate_parser = dictionary_subparsers.add_parser(
        "validate",
        help="Validate a dictionary-as-code layout.",
    )
    dictionary_validate_parser.add_argument(
        "--root",
        default=".",
        help="Project root where the dictionary layout is stored.",
    )
    dictionary_validate_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    dictionary_validate_parser.add_argument(
        "--manifest",
        default=None,
        help="Optional JSON manifest output path for the validated layout.",
    )
    dictionary_validate_parser.add_argument(
        "--json",
        action="store_true",
        help="Print layout status as JSON.",
    )
    dictionary_validate_parser.add_argument(
        "--lint",
        action="store_true",
        help="Also print lexicon quality warnings for the layout lexicon.",
    )
    dictionary_validate_parser.add_argument(
        "--strict-lint",
        action="store_true",
        help="Return exit code 1 when lint warnings are found.",
    )


    dictionary_diff_parser = dictionary_subparsers.add_parser(
        "diff",
        help="Compare two lexicon files by terminology semantics.",
    )
    dictionary_diff_parser.add_argument("before_path", help="Previous lexicon .json, .yaml, or .yml file.")
    dictionary_diff_parser.add_argument("after_path", help="Current lexicon .json, .yaml, or .yml file.")
    dictionary_diff_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full semantic diff report as JSON.",
    )
    dictionary_diff_parser.add_argument(
        "--fail-on-change",
        action="store_true",
        help="Return exit code 1 when semantic changes are detected.",
    )

    dictionary_merge_parser = dictionary_subparsers.add_parser(
        "merge",
        help="Merge three lexicon files by terminology semantics.",
    )
    dictionary_merge_parser.add_argument("base_path", help="Common base lexicon .json, .yaml, or .yml file.")
    dictionary_merge_parser.add_argument("ours_path", help="Our lexicon .json, .yaml, or .yml file.")
    dictionary_merge_parser.add_argument("theirs_path", help="Their lexicon .json, .yaml, or .yml file.")
    dictionary_merge_parser.add_argument(
        "--output",
        default=None,
        help="Output path for the merged lexicon JSON file.",
    )
    dictionary_merge_parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether the semantic merge is clean without writing a merged lexicon.",
    )
    dictionary_merge_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full semantic merge report as JSON.",
    )

    dictionary_pr_check_parser = dictionary_subparsers.add_parser(
        "pr-check",
        help="Run dictionary-as-code checks for CI and pull requests.",
    )
    dictionary_pr_check_parser.add_argument(
        "--root",
        default=".",
        help="Project root where the dictionary layout is stored.",
    )
    dictionary_pr_check_parser.add_argument(
        "--layout-dir",
        default="lexicon",
        help="Dictionary layout directory relative to the project root.",
    )
    dictionary_pr_check_parser.add_argument(
        "--base-lexicon",
        default=None,
        help="Optional base lexicon file for PR semantic diff output.",
    )
    dictionary_pr_check_parser.add_argument(
        "--fail-on-semantic-change",
        action="store_true",
        help="Return exit code 1 when --base-lexicon produces semantic changes.",
    )
    dictionary_pr_check_parser.add_argument(
        "--merge-base",
        default=None,
        help="Optional common-base lexicon for three-way semantic merge validation.",
    )
    dictionary_pr_check_parser.add_argument(
        "--merge-ours",
        default=None,
        help="Optional ours lexicon for three-way semantic merge validation.",
    )
    dictionary_pr_check_parser.add_argument(
        "--merge-theirs",
        default=None,
        help="Optional theirs lexicon for three-way semantic merge validation.",
    )
    dictionary_pr_check_parser.add_argument(
        "--exclude-deprecated",
        action="store_true",
        help="Ignore deprecated aliases or deprecated terms during behavior checks.",
    )
    dictionary_pr_check_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full dictionary PR check report as JSON.",
    )

    workspace_parser = subparsers.add_parser(
        "workspace",
        help="Manage local SQLite workspace state.",
    )
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command")

    workspace_init_parser = workspace_subparsers.add_parser(
        "init",
        help="Create the local SQLite workspace database.",
    )
    workspace_init_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    workspace_init_parser.add_argument(
        "--reset",
        action="store_true",
        help="Recreate the workspace database if it already exists.",
    )

    workspace_status_parser = workspace_subparsers.add_parser(
        "status",
        help="Print local workspace table counts.",
    )
    workspace_status_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    workspace_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print workspace status as JSON.",
    )

    workspace_sync_parser = workspace_subparsers.add_parser(
        "sync",
        help="Store local ingest, candidates, and evidence packs in SQLite.",
    )
    workspace_sync_parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to scan. Directories use local project defaults.",
    )
    workspace_sync_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored and relative paths are based.",
    )
    workspace_sync_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob to include when scanning directories. Can be provided multiple times.",
    )
    workspace_sync_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob to exclude when scanning directories. Can be provided multiple times.",
    )
    workspace_sync_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional lexicon document whose existing surfaces should be ignored.",
    )
    workspace_sync_parser.add_argument(
        "--min-score",
        type=float,
        default=0.25,
        help="Minimum candidate score from 0.0 to 1.0.",
    )
    workspace_sync_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum number of candidate evidence packs to store.",
    )
    workspace_sync_parser.add_argument(
        "--oov-tokenizer",
        default=None,
        help="Optional Scout tokenizer for OOV scoring. Use 'auto' for BAAI/bge-small-en-v1.5 or 'proxy' for the dependency-free scorer.",
    )
    workspace_sync_parser.add_argument(
        "--context-lines",
        type=int,
        default=1,
        help="Number of context lines before and after each evidence line.",
    )
    workspace_sync_parser.add_argument(
        "--max-positive-snippets",
        type=int,
        default=3,
        help="Maximum positive snippets per evidence pack.",
    )
    workspace_sync_parser.add_argument(
        "--max-negative-snippets",
        type=int,
        default=3,
        help="Maximum negative snippets per evidence pack.",
    )
    workspace_sync_parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Maximum file size to read during local ingest.",
    )
    _add_local_policy_options(workspace_sync_parser)
    workspace_sync_parser.add_argument(
        "--json",
        action="store_true",
        help="Print workspace status as JSON after sync.",
    )

    workspace_export_events_parser = workspace_subparsers.add_parser(
        "export-review-events",
        help="Export local review events as JSONL.",
    )
    workspace_export_events_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    workspace_export_events_parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. If omitted, JSONL is printed to stdout.",
    )
    _add_local_policy_options(workspace_export_events_parser)
    workspace_export_events_parser.add_argument(
        "--decision",
        choices=tuple(status.value for status in ReviewDecisionStatus),
        default=None,
        help="Export only events with a specific review decision.",
    )

    workspace_export_decision_log_parser = workspace_subparsers.add_parser(
        "export-decision-log",
        help="Export append-only decision provenance records as JSONL.",
    )
    workspace_export_decision_log_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    workspace_export_decision_log_parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. If omitted, JSONL is printed to stdout.",
    )
    _add_local_policy_options(workspace_export_decision_log_parser)
    workspace_export_decision_log_parser.add_argument(
        "--action",
        choices=tuple(action.value for action in WorkspaceDecisionAction),
        default=None,
        help="Export only provenance records with a specific action.",
    )
    workspace_export_decision_log_parser.add_argument(
        "--decision-actor",
        default=None,
        help="Export only provenance records created by this actor. This is separate from --actor used for local policy checks.",
    )

    workspace_publish_snapshot_parser = workspace_subparsers.add_parser(
        "publish-snapshot",
        help="Publish accepted local review decisions to a lexicon snapshot.",
    )
    workspace_publish_snapshot_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    workspace_publish_snapshot_parser.add_argument(
        "--lexicon",
        default=None,
        help="Optional base lexicon to include before accepted local terms.",
    )
    workspace_publish_snapshot_parser.add_argument(
        "--output",
        default=None,
        help="Output snapshot path. Defaults to .agent-lexicon/snapshots/<snapshot-id>.json.",
    )
    workspace_publish_snapshot_parser.add_argument(
        "--snapshot-id",
        default=None,
        help="Optional stable snapshot id. If omitted, one is generated.",
    )
    _add_local_policy_options(workspace_publish_snapshot_parser)
    workspace_publish_snapshot_parser.add_argument(
        "--json",
        action="store_true",
        help="Print snapshot publish summary as JSON.",
    )


    review_parser = subparsers.add_parser(
        "review",
        help="Open the local web proposal inbox for workspace candidates.",
        description="Open the local web proposal inbox for workspace candidates.",
    )
    review_parser.add_argument(
        "--root",
        default=".",
        help="Project root where .agent-lexicon/ is stored.",
    )
    review_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the local inbox.",
    )
    review_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for the local inbox.",
    )
    _add_local_policy_options(review_parser)
    review_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the inbox URL in a browser automatically.",
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
    resolve_parser.add_argument(
        "--semantic-near-miss",
        action="store_true",
        help="Use the optional BGE semantic reranker for gray-zone near-miss suggestions.",
    )
    resolve_parser.add_argument(
        "--semantic-model",
        default=DEFAULT_BGE_BASE_MODEL,
        help="Semantic near-miss model name used with --semantic-near-miss.",
    )
    resolve_parser.add_argument(
        "--semantic-threshold",
        type=float,
        default=DEFAULT_BGE_SEMANTIC_THRESHOLD,
        help="Minimum semantic similarity used with --semantic-near-miss.",
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
    guard_parser.add_argument(
        "--allow-risky-unicode",
        action="store_true",
        help="Do not block tool calls when bidi-control Unicode characters are found after normalization.",
    )
    return parser


def _add_local_policy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", default="local", help="Local policy actor for this command.")
    parser.add_argument(
        "--role",
        choices=tuple(role.value for role in LocalPolicyRole),
        default=None,
        help="Explicit local policy role for this command.",
    )
    parser.add_argument(
        "--policy-mode",
        choices=tuple(mode.value for mode in LocalPolicyMode),
        default=None,
        help="Temporarily override the local policy mode.",
    )



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.command == "init":
        return _simple_init_command(args)

    if args.command == "scan":
        return _simple_scan_command(args)

    if args.command == "analyze":
        return _simple_analyze_command(args)

    if args.command == "publish":
        return _simple_publish_command(args)

    if args.command == "validate":
        return _validate_command(
            path=Path(args.path),
            document_format=args.format,
            include_lint=args.lint,
            strict_lint=args.strict_lint,
        )

    if args.command == "lint":
        return _lint_command(
            path=Path(args.path),
            document_format=args.format,
            strict=args.strict,
            as_json=args.json,
        )

    if args.command == "validate-queries":
        return _validate_queries_command(path=Path(args.path))

    if args.command == "check":
        return _check_command(
            lexicon_path=Path(args.lexicon_path),
            queries_path=Path(args.queries_path),
            include_deprecated=not args.exclude_deprecated,
            as_json=args.json,
        )

    if args.command == "check-merge":
        return _check_merge_command(
            root=Path(args.root),
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            base=args.base,
            head=args.head,
            scopes=args.scope,
            include_globs=args.include,
            exclude_globs=args.exclude,
            config_path=Path(args.config) if args.config else None,
            include_deprecated=not args.exclude_deprecated,
            min_confidence=args.min_confidence,
            max_suggestions=args.max_suggestions,
            fail_on_review=args.fail_on_review,
            include_unresolved_unknowns=args.include_unresolved_unknowns,
            semantic_near_miss=args.semantic_near_miss,
            semantic_model=args.semantic_model,
            semantic_threshold=args.semantic_threshold,
            as_json=args.json,
        )

    if args.command == "ingest":
        return _ingest_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            exclude_globs=args.exclude,
            max_file_bytes=args.max_file_bytes,
            as_jsonl=args.jsonl,
        )

    if args.command == "discover-candidates":
        return _discover_candidates_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            exclude_globs=args.exclude,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
            oov_tokenizer=args.oov_tokenizer,
            max_file_bytes=args.max_file_bytes,
            as_json=args.json,
            as_jsonl=args.jsonl,
            quality_report=args.quality_report,
        )

    if args.command == "build-evidence":
        return _build_evidence_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            exclude_globs=args.exclude,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
            oov_tokenizer=args.oov_tokenizer,
            context_lines=args.context_lines,
            max_positive_snippets=args.max_positive_snippets,
            max_negative_snippets=args.max_negative_snippets,
            max_file_bytes=args.max_file_bytes,
            include_prompt_safety=not args.skip_prompt_safety,
            as_json=args.json,
            as_jsonl=args.jsonl,
        )

    if args.command == "safety":
        return _safety_command(args)

    if args.command == "policy":
        return _policy_command(args)

    if args.command == "mcp":
        return _mcp_command(args)

    if args.command == "review-agent":
        return _review_agent_command(args)

    if args.command == "discover-migrations":
        return _discover_migrations_command(
            path=Path(args.path),
            min_confidence=args.min_confidence,
            max_candidates=args.max_candidates,
            as_json=args.json,
            as_jsonl=args.jsonl,
        )

    if args.command == "dictionary":
        return _dictionary_command(args)

    if args.command == "workspace":
        return _workspace_command(args)

    if args.command == "review":
        return _review_command(
            root=Path(args.root),
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
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
            semantic_near_miss=args.semantic_near_miss,
            semantic_model=args.semantic_model,
            semantic_threshold=args.semantic_threshold,
        )

    if args.command == "guard":
        return _guard_command(
            path=Path(args.path),
            text=args.text,
            tool_name=args.tool,
            scopes=args.scope,
            include_deprecated=not args.exclude_deprecated,
            block_on_unicode_risk=not args.allow_risky_unicode,
        )

    print(about())
    return 0




def _simple_init_command(args: argparse.Namespace) -> int:
    try:
        report = run_simple_init(
            Path(args.root),
            layout_dir=args.layout_dir,
            policy_mode=args.policy_mode,
            actor=args.actor,
            role=args.role,
            force=args.force,
            reset_workspace=args.reset_workspace,
        )
    except SimpleWorkflowError as exc:
        print(f"Invalid init input: {exc}")
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    print("Agent Lexicon initialized")
    print(f"Dictionary: {report.dictionary.layout.layout_path}")
    print(f"Workspace: {report.workspace.db_path}")
    print(f"Policy: {report.policy_path} ({report.policy_mode})")
    print(f"Config: {report.config_path}")
    print("Next: agent-lexicon scan")
    return 0


def _simple_scan_command(args: argparse.Namespace) -> int:
    policy_exit = _check_policy_or_print(
        root=Path(args.root),
        action=PolicyAction.SYNC_WORKSPACE,
        actor=args.actor,
        role=args.role,
        policy_mode=args.policy_mode,
    )
    if policy_exit != 0:
        return policy_exit
    try:
        report = run_simple_scan(
            args.paths,
            root=Path(args.root),
            layout_dir=args.layout_dir,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            include_globs=args.include,
            exclude_globs=args.exclude,
            config_path=args.config,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
            oov_tokenizer=args.oov_tokenizer,
            context_lines=args.context_lines,
            max_positive_snippets=args.max_positive_snippets,
            max_negative_snippets=args.max_negative_snippets,
            max_file_bytes=args.max_file_bytes,
        )
    except SimpleWorkflowError as exc:
        print(f"Invalid scan input: {exc}")
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    print(
        "Agent Lexicon scan: "
        f"{report.document_count} documents, "
        f"{report.candidate_count} candidates, "
        f"{report.evidence_pack_count} evidence packs saved, "
        f"{report.candidates.important_count} important"
    )
    print(
        "Prompt safety: "
        f"risk={report.safety.highest_risk.value}, "
        f"action={report.safety.action.value}, "
        f"findings={report.safety.finding_count}"
    )
    if getattr(args, "quality_report", False):
        print(report.quality_report.to_text())
    for candidate in report.candidates.candidates[:5]:
        print(f"- {candidate.surface} score={candidate.score:.3f} jargon={candidate.jargon_score:.3f}")
    print("Next: agent-lexicon analyze")
    return 0


def _simple_analyze_command(args: argparse.Namespace) -> int:
    policy_exit = _check_policy_or_print(
        root=Path(args.root),
        action=PolicyAction.READ_WORKSPACE,
        actor=args.actor,
        role=args.role,
        policy_mode=args.policy_mode,
    )
    if policy_exit != 0:
        return policy_exit
    if args.review_agent:
        llm_policy_exit = _check_policy_or_print(
            root=Path(args.root),
            action=PolicyAction.RUN_LLM_REVIEW,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
        )
        if llm_policy_exit != 0:
            return llm_policy_exit
    try:
        report = run_simple_analyze(
            Path(args.root),
            limit=args.limit,
            include_review_agent=args.review_agent,
            include_review_agent_consensus=args.consensus,
            priority=args.priority,
        )
    except SimpleWorkflowError as exc:
        print(f"Invalid analyze input: {exc}")
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    print(
        "Agent Lexicon analyze: "
        f"{report.important_count} important, "
        f"{report.later_count} later, "
        f"{report.item_count} shown"
    )
    if getattr(args, "quality_report", False):
        print(report.quality_report.to_text())
    if not report.items:
        print("No workspace candidates found. Run: agent-lexicon scan README.md docs src")
        return 0
    for item in report.items:
        label = "IMPORTANT" if item.priority == "important" else "LATER"
        print(
            f"[{label}] {item.surface} "
            f"priority={item.priority_score:.3f} "
            f"score={item.score:.3f} "
            f"oov={item.oov_score:.3f} "
            f"oov_source={item.oov_source} "
            f"cluster={item.cluster_size} "
            f"status={item.review_status}"
        )
        if item.priority_reasons:
            print(f"  reasons={', '.join(item.priority_reasons[:4])}")
        if item.recommendation:
            print(f"  review-agent={item.recommendation}: {item.reviewer_note}")
        if item.consensus_status:
            print(
                f"  consensus={item.consensus_status} "
                f"agreement={item.agreement_ratio:.2f} "
                f"confidence={item.consensus_confidence:.2f}"
            )
    print("Next: agent-lexicon review")
    return 0


def _simple_publish_command(args: argparse.Namespace) -> int:
    policy_exit = _check_policy_or_print(
        root=Path(args.root),
        action=PolicyAction.PUBLISH_SNAPSHOT,
        actor=args.actor,
        role=args.role,
        policy_mode=args.policy_mode,
    )
    if policy_exit != 0:
        return policy_exit
    try:
        report = run_simple_publish(
            Path(args.root),
            layout_dir=args.layout_dir,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            output_path=Path(args.output) if args.output else None,
            snapshot_id=args.snapshot_id,
        )
    except SimpleWorkflowError as exc:
        print(f"Invalid publish input: {exc}")
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    print(f"Snapshot published: {report.output_path}")
    print(f"Snapshot id: {report.snapshot_id}")
    print(
        "Terms: "
        f"{report.term_count} total, "
        f"{report.generated_term_count} generated from "
        f"{report.accepted_count} accepted decisions, "
        f"{report.skipped_count} skipped"
    )
    return 0

def _review_agent_command(args: argparse.Namespace) -> int:
    if args.review_agent_command not in {"prompt", "assess", "consensus", "dataset"}:
        print("Review agent command required: prompt, assess, consensus, or dataset")
        return 1

    if args.review_agent_command == "dataset":
        return _review_agent_dataset_command(args)

    policy_exit = _check_policy_or_print(
        root=Path(args.root),
        action=PolicyAction.RUN_LLM_REVIEW,
        actor=args.actor,
        role=args.role,
        policy_mode=args.policy_mode,
    )
    if policy_exit != 0:
        return policy_exit

    try:
        state = open_workspace(Path(args.root), create=True)
        item = _select_review_agent_item(state, normalized_surface=args.surface)
        if args.review_agent_command == "prompt":
            prompt = build_review_agent_prompt(item, max_chars=args.max_chars)
            if args.json:
                print(json.dumps(prompt.to_dict(), indent=2, sort_keys=True))
            else:
                if not prompt.llm_review_allowed:
                    print("Review prompt warning: high-risk evidence should not be sent to an LLM reviewer.")
                print(prompt.prompt)
            return 0 if prompt.llm_review_allowed else 2

        if args.review_agent_command == "consensus":
            llm_responses = None
            if args.llm_response:
                llm_responses = [Path(path).read_text(encoding="utf-8") for path in args.llm_response]
            report = run_review_agent_consensus(
                item,
                llm_responses=llm_responses,
                min_agreement=args.min_agreement,
                min_confidence=args.min_confidence,
            )
            if args.json:
                print(report.to_json())
            else:
                print(f"Review agent consensus: {report.status.value} ({report.confidence:.2f})")
                print(f"Surface: {report.surface}")
                print(f"Recommendation: {report.decision.recommendation.value}")
                print(f"Workspace decision: {report.decision.review_decision_status}")
                print(f"Agreement: {report.agreement_count}/{report.sample_count} ({report.agreement_ratio:.2f})")
                print(f"Reason: {report.reason}")
                if report.decision.risk_flags:
                    print(f"Risk flags: {', '.join(report.decision.risk_flags)}")
            return 0 if not report.abstained else 2

        llm_response = None
        if args.llm_response is not None:
            llm_response = Path(args.llm_response).read_text(encoding="utf-8")
        decision = run_review_agent(item, llm_response=llm_response)
        if args.json:
            print(decision.to_json())
        else:
            print(f"Review agent: {decision.recommendation.value} ({decision.confidence:.2f})")
            print(f"Surface: {decision.surface}")
            print(f"Canonical name: {decision.canonical_name or '-'}")
            print(f"Workspace decision: {decision.review_decision_status}")
            print(f"LLM review allowed: {'yes' if decision.llm_review_allowed else 'no'}")
            if decision.risk_flags:
                print(f"Risk flags: {', '.join(decision.risk_flags)}")
            print(f"Note: {decision.reviewer_note}")
        return 0 if decision.llm_review_allowed else 2
    except (WorkspaceError, ReviewAgentError, OSError) as exc:
        print(f"Review agent error: {exc}")
        return 1


def _review_agent_dataset_command(args: argparse.Namespace) -> int:
    policy_exit = _check_policy_or_print(
        root=Path(args.root),
        action=PolicyAction.EXPORT_REVIEW_EVENTS,
        actor=args.actor,
        role=args.role,
        policy_mode=args.policy_mode,
    )
    if policy_exit != 0:
        return policy_exit
    if args.include_review_agent:
        llm_policy_exit = _check_policy_or_print(
            root=Path(args.root),
            action=PolicyAction.RUN_LLM_REVIEW,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
        )
        if llm_policy_exit != 0:
            return llm_policy_exit

    try:
        state = open_workspace(Path(args.root), create=True)
        if args.json:
            report = build_review_dataset(
                state,
                include_review_agent=args.include_review_agent,
                quality=args.quality,
                limit=args.limit,
            )
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
            return 0

        content = export_review_dataset_jsonl(
            state,
            output_path=Path(args.output) if args.output else None,
            include_review_agent=args.include_review_agent,
            quality=args.quality,
            limit=args.limit,
        )
        if args.output:
            report = build_review_dataset(
                state,
                include_review_agent=args.include_review_agent,
                quality=args.quality,
                limit=args.limit,
            )
            print(f"Review dataset exported: {report.example_count} examples -> {args.output}")
        else:
            print(content, end="")
        return 0
    except (WorkspaceError, ReviewDatasetError, ReviewAgentError, OSError) as exc:
        print(f"Review dataset error: {exc}")
        return 1


def _select_review_agent_item(state: object, *, normalized_surface: str | None) -> object:
    if normalized_surface:
        item = state.get_review_item(normalized_surface)
        if item is None:
            raise ReviewAgentError(f"workspace candidate not found: {normalized_surface}")
        return item
    items = state.list_review_items(limit=1)
    if not items:
        raise ReviewAgentError("workspace has no candidates; run workspace sync first")
    return items[0]


def _mcp_command(args: argparse.Namespace) -> int:
    if args.mcp_command == "tools":
        tools = mcp_tool_definitions()
        if args.json:
            print(json.dumps({"tools": tools}, indent=2, sort_keys=True))
        else:
            print("Agent Lexicon MCP tools:")
            for tool in tools:
                print(f"- {tool['name']}: {tool['description']}")
        return 0

    if args.mcp_command == "serve":
        try:
            return run_mcp_stdio_server(
                root=Path(args.root),
                lexicon_path=Path(args.lexicon) if args.lexicon else None,
                policy_mode=args.policy_mode,
                actor=args.actor,
                role=args.role,
            )
        except McpServerError as exc:
            print(f"MCP server error: {exc}")
            return 1

    print("MCP command required: serve or tools")
    return 1



def _validate_command(
    *,
    path: Path,
    document_format: str | None,
    include_lint: bool = False,
    strict_lint: bool = False,
) -> int:
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
    if not include_lint:
        return 0
    lint_exit = _lint_command(path=path, document_format=document_format, strict=strict_lint, as_json=False)
    return lint_exit if strict_lint else 0


def _lint_command(
    *,
    path: Path,
    document_format: str | None,
    strict: bool,
    as_json: bool,
) -> int:
    try:
        report = lint_lexicon_file(path, document_format=document_format)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1
    if as_json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 1 if strict and report.findings else 0


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


def _semantic_backend_from_cli(
    *,
    enabled: bool,
    model_name: str,
    min_semantic_score: float,
) -> SemanticNearMissBackend | None:
    if not enabled:
        return None
    return BgeSemanticNearMissBackend(
        model_name=model_name,
        min_semantic_score=min_semantic_score,
    )


def _check_merge_command(
    *,
    root: Path,
    lexicon_path: Path | None,
    base: str,
    head: str,
    scopes: list[str] | None,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    config_path: Path | None,
    include_deprecated: bool,
    min_confidence: float,
    max_suggestions: int,
    fail_on_review: bool,
    include_unresolved_unknowns: bool,
    semantic_near_miss: bool,
    semantic_model: str,
    semantic_threshold: float,
    as_json: bool,
) -> int:
    root_path = root.resolve()
    try:
        config = load_project_config(root_path, config_path=config_path)
    except AgentLexiconConfigError as exc:
        print(f"Invalid Agent Lexicon config: {exc}")
        return 1
    effective_include = effective_include_globs(include_globs, config)
    effective_exclude = effective_exclude_globs(exclude_globs, config)
    resolved_lexicon_path = lexicon_path or (root_path / "lexicon" / "lexicon.yaml")
    if not resolved_lexicon_path.is_absolute():
        resolved_lexicon_path = root_path / resolved_lexicon_path
    try:
        lexicon = load_lexicon(resolved_lexicon_path)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1

    try:
        semantic_backend = _semantic_backend_from_cli(
            enabled=semantic_near_miss,
            model_name=semantic_model,
            min_semantic_score=semantic_threshold,
        )
    except SemanticNearMissError as exc:
        print(f"Invalid semantic near-miss input: {exc}")
        return 1

    try:
        report = check_git_merge_terminology(
            lexicon,
            root=root_path,
            lexicon_path=resolved_lexicon_path,
            base=base,
            head=head,
            scopes=scopes,
            include_deprecated=include_deprecated,
            include_globs=effective_include,
            exclude_globs=effective_exclude,
            max_suggestions_per_identifier=max_suggestions,
            min_confidence=min_confidence,
            include_unresolved_unknowns=include_unresolved_unknowns,
            semantic_backend=semantic_backend,
        )
    except GitMergeCheckError as exc:
        print(f"Invalid merge check input: {exc}")
        return 1
    except SemanticNearMissError as exc:
        print(f"Invalid semantic near-miss input: {exc}")
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.to_text())
    return 1 if fail_on_review and report.has_review_items else 0


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
    exclude_globs: list[str] | None,
    max_file_bytes: int,
    as_jsonl: bool,
) -> int:
    try:
        report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
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
    exclude_globs: list[str] | None,
    lexicon_path: Path | None,
    min_score: float,
    max_candidates: int,
    oov_tokenizer: str | None,
    max_file_bytes: int,
    as_json: bool,
    as_jsonl: bool,
    quality_report: bool = False,
) -> int:
    if as_json and as_jsonl:
        print("Invalid candidate discovery input: choose either --json or --jsonl")
        return 1


    try:
        ingest_report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
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
            oov_tokenizer=oov_tokenizer,
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
    if quality_report:
        try:
            print(build_scout_quality_report(report).to_text())
        except ScoutQualityReportError as exc:
            print(f"Scout quality report error: {exc}")
            return 1
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
    exclude_globs: list[str] | None,
    lexicon_path: Path | None,
    min_score: float,
    max_candidates: int,
    oov_tokenizer: str | None,
    context_lines: int,
    max_positive_snippets: int,
    max_negative_snippets: int,
    max_file_bytes: int,
    include_prompt_safety: bool,
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
            exclude_globs=exclude_globs,
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
            oov_tokenizer=oov_tokenizer,
        )
        evidence_report = build_evidence_packs(
            ingest_report.documents,
            candidate_report.candidates,
            context_lines=context_lines,
            max_positive_snippets=max_positive_snippets,
            max_negative_snippets=max_negative_snippets,
            include_prompt_safety=include_prompt_safety,
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
    prompt_safety = dict(evidence_report.metadata.get("prompt_safety", {}))
    if prompt_safety:
        print(
            "Prompt safety: "
            f"risk={prompt_safety.get('highest_risk', 'none')}, "
            f"action={prompt_safety.get('action', 'allow')}, "
            f"findings={prompt_safety.get('finding_count', 0)}"
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



def _safety_command(args: argparse.Namespace) -> int:
    if args.safety_command == "scan":
        return _safety_scan_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root) if args.root is not None else None,
            include_globs=args.include,
            exclude_globs=args.exclude,
            max_file_bytes=args.max_file_bytes,
            fail_on_high_risk=args.fail_on_high_risk,
            as_json=args.json,
        )
    print("Safety command required: scan")
    return 1


def _safety_scan_command(
    *,
    paths: list[Path],
    root: Path | None,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    max_file_bytes: int,
    fail_on_high_risk: bool,
    as_json: bool,
) -> int:
    try:
        ingest_report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            max_file_bytes=max_file_bytes,
        )
        report = scan_documents_for_prompt_injection(ingest_report.documents)
    except (LocalIngestError, PromptSafetyError) as exc:
        print(f"Invalid prompt-safety input: {exc}")
        return 1

    if as_json:
        print(report.to_json())
        return 1 if fail_on_high_risk and report.high_count > 0 else 0

    print(
        "Prompt safety scan: "
        f"risk={report.highest_risk.value}, "
        f"action={report.action.value}, "
        f"findings={report.finding_count}, "
        f"documents={report.source_count}"
    )
    if report.findings:
        for finding in report.findings:
            print(
                f"[{finding.risk.value.upper()}] "
                f"{finding.source_path}:{finding.line_number} "
                f"{finding.rule_id} — {finding.message} "
                f"scope={finding.scan_scope.value} "
                f"({finding.matched_text!r})"
            )
    else:
        print("No prompt-injection indicators found.")
    return 1 if fail_on_high_risk and report.high_count > 0 else 0


def _policy_command(args: argparse.Namespace) -> int:
    if args.policy_command == "init":
        try:
            policy = init_local_policy(
                Path(args.root),
                mode=args.mode,
                actor=args.actor,
                role=args.role,
                force=args.force,
            )
        except LocalPolicyError as exc:
            print(f"Invalid local policy input: {exc}")
            return 1
        if args.json:
            print(policy.to_json())
            return 0
        print(f"Local policy initialized: {policy_path(args.root)}")
        print(f"Mode: {policy.mode.value}")
        print(f"Actor: {args.actor} -> {args.role}")
        return 0

    if args.policy_command == "status":
        try:
            policy = load_local_policy(Path(args.root), mode=args.policy_mode)
            decision = check_local_policy(
                policy,
                PolicyAction.READ_WORKSPACE,
                actor=args.actor,
                role=args.role,
            )
        except LocalPolicyError as exc:
            print(f"Invalid local policy input: {exc}")
            return 1
        if args.json:
            print(json.dumps({"policy": policy.to_dict(), "effective_role": decision.role.value, "path": str(policy_path(args.root))}, indent=2, sort_keys=True))
            return 0
        print(f"Local policy: {policy.mode.value}")
        print(f"Path: {policy_path(args.root)}")
        print(f"Default role: {policy.default_role.value if policy.default_role is not None else 'none'}")
        print(f"Effective actor: {decision.actor} ({decision.role.value})")
        return 0

    if args.policy_command == "check":
        try:
            policy = load_local_policy(Path(args.root), mode=args.policy_mode)
            decision = check_local_policy(policy, args.action, actor=args.actor, role=args.role)
        except LocalPolicyError as exc:
            print(f"Invalid local policy input: {exc}")
            return 1
        if args.json:
            print(decision.to_json())
            return 0 if decision.is_allowed else 2
        print(f"Policy check: {'allowed' if decision.is_allowed else 'denied'}")
        print(f"Mode: {decision.mode.value}")
        print(f"Actor: {decision.actor}")
        print(f"Role: {decision.role.value}")
        print(f"Action: {decision.action.value}")
        print(f"Reason: {decision.reason}")
        return 0 if decision.is_allowed else 2

    print("Policy command required: init, status, or check")
    return 1


def _check_policy_or_print(
    *,
    root: Path,
    action: PolicyAction,
    actor: str,
    role: str | None,
    policy_mode: str | None,
) -> int:
    try:
        policy = load_local_policy(root, mode=policy_mode)
        decision = check_local_policy(policy, action, actor=actor, role=role)
    except LocalPolicyError as exc:
        print(f"Invalid local policy input: {exc}")
        return 1
    if decision.is_allowed:
        return 0
    print(f"Policy denied: {decision.reason}")
    return 2


def _discover_migrations_command(
    *,
    path: Path,
    min_confidence: float,
    max_candidates: int,
    as_json: bool,
    as_jsonl: bool,
) -> int:
    if as_json and as_jsonl:
        print("Invalid migration input: choose either --json or --jsonl")
        return 1
    try:
        lexicon = load_lexicon(path)
        report = discover_canonical_migration_candidates(
            lexicon,
            min_confidence=min_confidence,
            max_candidates=max_candidates,
        )
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1
    except CanonicalMigrationError as exc:
        print(f"Invalid migration input: {exc}")
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    if as_jsonl:
        for candidate in report.candidates:
            print(candidate.to_json_line())
        return 0

    print(
        "Migration candidates: "
        f"{report.candidate_count} candidates from "
        f"{report.deprecated_term_count} deprecated terms "
        f"and {report.active_term_count} active terms"
    )
    for candidate in report.candidates:
        print(
            f"- {candidate.deprecated_term_id} -> {candidate.replacement_term_id} "
            f"confidence={candidate.confidence:.3f} risk={candidate.risk.value}"
        )
        print(f"  {candidate.rationale}")
        if candidate.surfaces_to_preserve:
            print(f"  preserve aliases: {', '.join(candidate.surfaces_to_preserve)}")
    return 0


def _dictionary_command(args: argparse.Namespace) -> int:
    if args.dictionary_command == "init":
        try:
            summary = init_dictionary_layout(
                Path(args.root),
                layout_dir=args.layout_dir,
                force=args.force,
            )
        except DictionaryLayoutError as exc:
            print(f"Invalid dictionary layout input: {exc}")
            return 1
        if args.json:
            print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
            return 0
        print(f"Dictionary layout initialized: {summary.layout.layout_path}")
        _print_dictionary_summary(summary)
        return 0

    if args.dictionary_command == "status":
        try:
            summary = inspect_dictionary_layout(Path(args.root), layout_dir=args.layout_dir)
        except DictionaryLayoutError as exc:
            print(f"Invalid dictionary layout input: {exc}")
            return 1
        if args.json:
            print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
            return 0
        _print_dictionary_summary(summary)
        return 0 if summary.exists else 1

    if args.dictionary_command == "validate":
        try:
            summary = validate_dictionary_layout(Path(args.root), layout_dir=args.layout_dir)
            lint_report = (
                lint_lexicon_file(Path(summary.layout.lexicon_path))
                if args.lint or args.strict_lint
                else None
            )
            if args.manifest:
                write_dictionary_manifest(summary, Path(args.manifest))
        except (DictionaryLayoutError, AgentLexiconLoadError) as exc:
            print(f"Invalid dictionary layout: {exc}")
            return 1
        if args.json:
            payload = summary.to_dict()
            if lint_report is not None:
                payload["lint"] = lint_report.to_dict()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1 if args.strict_lint and lint_report is not None and lint_report.findings else 0
        print(f"Valid dictionary layout: {summary.layout.layout_path}")
        _print_dictionary_summary(summary)
        if lint_report is not None:
            print(lint_report.to_text())
        if args.manifest:
            print(f"Manifest written: {args.manifest}")
        return 1 if args.strict_lint and lint_report is not None and lint_report.findings else 0

    if args.dictionary_command == "diff":
        return _dictionary_diff_command(
            before_path=Path(args.before_path),
            after_path=Path(args.after_path),
            as_json=args.json,
            fail_on_change=args.fail_on_change,
        )

    if args.dictionary_command == "merge":
        return _dictionary_merge_command(
            base_path=Path(args.base_path),
            ours_path=Path(args.ours_path),
            theirs_path=Path(args.theirs_path),
            output_path=Path(args.output) if args.output else None,
            check_only=args.check,
            as_json=args.json,
        )

    if args.dictionary_command == "pr-check":
        return _dictionary_pr_check_command(
            root=Path(args.root),
            layout_dir=args.layout_dir,
            base_lexicon_path=Path(args.base_lexicon) if args.base_lexicon else None,
            merge_base_path=Path(args.merge_base) if args.merge_base else None,
            merge_ours_path=Path(args.merge_ours) if args.merge_ours else None,
            merge_theirs_path=Path(args.merge_theirs) if args.merge_theirs else None,
            fail_on_semantic_change=args.fail_on_semantic_change,
            include_deprecated=not args.exclude_deprecated,
            as_json=args.json,
        )

    print("Dictionary command required: init, status, validate, diff, merge, or pr-check")
    return 1


def _dictionary_diff_command(
    *,
    before_path: Path,
    after_path: Path,
    as_json: bool,
    fail_on_change: bool,
) -> int:
    try:
        report = diff_lexicon_files(before_path, after_path)
    except SemanticDiffError as exc:
        print(f"Invalid semantic diff input: {exc}")
        return 1

    if as_json:
        print(report.to_json())
        return 1 if fail_on_change and report.has_changes else 0

    summary = report.summary
    print(
        "Semantic diff: "
        f"{summary.total} changes "
        f"({summary.added} added, {summary.removed} removed, {summary.changed} changed)"
    )
    print(f"Before: {report.before_label}")
    print(f"After: {report.after_label}")
    if not report.has_changes:
        print("No semantic changes.")
        return 0

    for change in report.changes:
        print(change.to_text())
    return 1 if fail_on_change else 0


def _dictionary_merge_command(
    *,
    base_path: Path,
    ours_path: Path,
    theirs_path: Path,
    output_path: Path | None,
    check_only: bool,
    as_json: bool,
) -> int:
    try:
        report = merge_lexicon_files(base_path, ours_path, theirs_path)
    except SemanticMergeError as exc:
        print(f"Invalid semantic merge input: {exc}")
        return 1

    if report.has_conflicts:
        if as_json:
            print(report.to_json())
        else:
            print(f"Semantic merge: conflict ({report.conflict_count} conflicts)")
            print(f"Base: {report.base_label}")
            print(f"Ours: {report.ours_label}")
            print(f"Theirs: {report.theirs_label}")
            for conflict in report.conflicts:
                print(conflict.to_text())
            if report.has_warnings:
                print(f"Semantic warnings: {report.warning_count}")
                for warning in report.warnings:
                    print(warning.to_text())
        return 1

    if output_path is not None and not check_only:
        try:
            written_path = write_merged_lexicon_json(report, output_path)
        except SemanticMergeError as exc:
            print(f"Invalid semantic merge output: {exc}")
            return 1
    else:
        written_path = None

    if as_json:
        print(report.to_json(include_merged_lexicon=output_path is None and not check_only))
        return 0

    summary = report.merged_diff_summary
    print(
        "Semantic merge: clean "
        f"({summary.total} merged changes; "
        f"{summary.added} added, {summary.removed} removed, {summary.changed} changed)"
    )
    print(f"Base: {report.base_label}")
    print(f"Ours: {report.ours_label}")
    print(f"Theirs: {report.theirs_label}")
    if report.has_warnings:
        print(f"Semantic warnings: {report.warning_count}")
        for warning in report.warnings:
            print(warning.to_text())
    if check_only:
        print("Check only: no merged lexicon was written.")
    elif written_path is not None:
        print(f"Merged lexicon written: {written_path}")
    else:
        print("No output path provided. Use --output to write the merged lexicon.")
    return 0


def _dictionary_pr_check_command(
    *,
    root: Path,
    layout_dir: str,
    base_lexicon_path: Path | None,
    merge_base_path: Path | None,
    merge_ours_path: Path | None,
    merge_theirs_path: Path | None,
    fail_on_semantic_change: bool,
    include_deprecated: bool,
    as_json: bool,
) -> int:
    report = run_dictionary_pr_checks(
        root,
        layout_dir=layout_dir,
        base_lexicon_path=base_lexicon_path,
        merge_base_path=merge_base_path,
        merge_ours_path=merge_ours_path,
        merge_theirs_path=merge_theirs_path,
        fail_on_semantic_change=fail_on_semantic_change,
        include_deprecated=include_deprecated,
    )
    if as_json:
        print(report.to_json())
        return 0 if report.passed else 1

    print(
        "Dictionary PR check: "
        f"{'passed' if report.passed else 'failed'} "
        f"({report.passed_count} passed, {report.failed_count} failed, {report.skipped_count} skipped)"
    )
    for item in report.checks:
        print(item.to_text())
    return 0 if report.passed else 1


def _print_dictionary_summary(summary) -> None:
    metadata = dict(summary.metadata)
    print(
        "Dictionary status: "
        f"valid={'yes' if summary.valid else 'no'}, "
        f"scopes={metadata.get('scope_count', 0)}, "
        f"terms={metadata.get('term_count', 0)}, "
        f"queries={metadata.get('query_count', 0)}, "
        f"proposal_files={summary.proposal_file_count}, "
        f"snapshot_files={summary.snapshot_file_count}, "
        f"review_event_files={summary.review_event_file_count}"
    )
    print(f"Lexicon: {summary.layout.lexicon_path}")
    print(f"Queries: {summary.layout.queries_path}")
    if summary.lexicon_error:
        print(f"Lexicon error: {summary.lexicon_error}")
    if summary.queries_error:
        print(f"Queries error: {summary.queries_error}")


def _workspace_command(args: argparse.Namespace) -> int:
    if args.workspace_command == "init":
        try:
            state = init_workspace(Path(args.root), reset=args.reset)
        except WorkspaceError as exc:
            print(f"Invalid workspace input: {exc}")
            return 1
        print(f"Workspace initialized: {state.db_path}")
        return 0

    if args.workspace_command == "status":
        try:
            state = open_workspace(Path(args.root), create=False)
            summary = state.summary()
        except WorkspaceError as exc:
            print(f"Invalid workspace input: {exc}")
            return 1
        if args.json:
            print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
            return 0
        print(
            "Workspace status: "
            f"{summary.document_count} documents, "
            f"{summary.candidate_count} candidates, "
            f"{summary.evidence_pack_count} evidence packs, "
            f"{summary.review_decision_count} review decisions, "
            f"{summary.review_event_count} review events, "
            f"{summary.decision_record_count} decision records, "
            f"{summary.snapshot_count} snapshots"
        )
        print(f"Database: {summary.db_path}")
        return 0

    if args.workspace_command == "sync":
        return _workspace_sync_command(
            paths=[Path(path) for path in args.paths],
            root=Path(args.root),
            include_globs=args.include,
            exclude_globs=args.exclude,
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
            oov_tokenizer=args.oov_tokenizer,
            context_lines=args.context_lines,
            max_positive_snippets=args.max_positive_snippets,
            max_negative_snippets=args.max_negative_snippets,
            max_file_bytes=args.max_file_bytes,
            as_json=args.json,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
        )

    if args.workspace_command == "export-review-events":
        return _workspace_export_review_events_command(
            root=Path(args.root),
            output_path=Path(args.output) if args.output else None,
            decision=args.decision,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
        )

    if args.workspace_command == "export-decision-log":
        return _workspace_export_decision_log_command(
            root=Path(args.root),
            output_path=Path(args.output) if args.output else None,
            action=args.action,
            actor_filter=args.decision_actor,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
        )

    if args.workspace_command == "publish-snapshot":
        return _workspace_publish_snapshot_command(
            root=Path(args.root),
            lexicon_path=Path(args.lexicon) if args.lexicon else None,
            output_path=Path(args.output) if args.output else None,
            snapshot_id=args.snapshot_id,
            as_json=args.json,
            actor=args.actor,
            role=args.role,
            policy_mode=args.policy_mode,
        )

    print("Workspace command required: init, status, sync, export-review-events, export-decision-log, or publish-snapshot")
    return 1


def _workspace_sync_command(
    *,
    paths: list[Path],
    root: Path,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    lexicon_path: Path | None,
    min_score: float,
    max_candidates: int,
    oov_tokenizer: str | None,
    context_lines: int,
    max_positive_snippets: int,
    max_negative_snippets: int,
    max_file_bytes: int,
    as_json: bool,
    actor: str,
    role: str | None,
    policy_mode: str | None,
) -> int:
    policy_exit_code = _check_policy_or_print(
        root=root,
        action=PolicyAction.SYNC_WORKSPACE,
        actor=actor,
        role=role,
        policy_mode=policy_mode,
    )
    if policy_exit_code != 0:
        return policy_exit_code

    try:
        ingest_report = ingest_local_paths(
            paths,
            root=root,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
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
            oov_tokenizer=oov_tokenizer,
        )
        evidence_report = build_evidence_packs(
            ingest_report.documents,
            candidate_report.candidates,
            context_lines=context_lines,
            max_positive_snippets=max_positive_snippets,
            max_negative_snippets=max_negative_snippets,
        )
        state = init_workspace(root)
        state.store_ingest_report(ingest_report)
        state.store_candidate_report(candidate_report)
        state.store_evidence_report(evidence_report)
        summary = state.summary()
    except (ScoutCandidateError, EvidencePackError, WorkspaceError) as exc:
        print(f"Invalid workspace input: {exc}")
        return 1

    if as_json:
        print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
        return 0

    print(
        "Workspace sync: "
        f"{ingest_report.document_count} documents, "
        f"{candidate_report.candidate_count} candidates, "
        f"{evidence_report.pack_count} evidence packs saved"
    )
    print(f"Database: {summary.db_path}")
    return 0



def _workspace_export_review_events_command(
    *,
    root: Path,
    output_path: Path | None,
    decision: str | None,
    actor: str,
    role: str | None,
    policy_mode: str | None,
) -> int:
    policy_exit_code = _check_policy_or_print(
        root=root,
        action=PolicyAction.EXPORT_REVIEW_EVENTS,
        actor=actor,
        role=role,
        policy_mode=policy_mode,
    )
    if policy_exit_code != 0:
        return policy_exit_code

    try:
        state = open_workspace(root, create=False)
        content = state.export_review_events_jsonl(output_path, decision=decision)
    except WorkspaceError as exc:
        print(f"Invalid workspace input: {exc}")
        return 1

    if output_path is None:
        print(content, end="")
        return 0

    event_count = content.count("\n") if content else 0
    print(f"Review events exported: {event_count} events -> {output_path}")
    return 0



def _workspace_export_decision_log_command(
    *,
    root: Path,
    output_path: Path | None,
    action: str | None,
    actor_filter: str | None,
    actor: str,
    role: str | None,
    policy_mode: str | None,
) -> int:
    policy_exit_code = _check_policy_or_print(
        root=root,
        action=PolicyAction.EXPORT_REVIEW_EVENTS,
        actor=actor,
        role=role,
        policy_mode=policy_mode,
    )
    if policy_exit_code != 0:
        return policy_exit_code

    try:
        state = open_workspace(root, create=False)
        content = state.export_decision_records_jsonl(output_path, action=action, actor=actor_filter)
    except WorkspaceError as exc:
        print(f"Invalid workspace input: {exc}")
        return 1

    if output_path is None:
        print(content, end="")
        return 0

    record_count = content.count("\n") if content else 0
    print(f"Decision log exported: {record_count} records -> {output_path}")
    return 0


def _workspace_publish_snapshot_command(
    *,
    root: Path,
    lexicon_path: Path | None,
    output_path: Path | None,
    snapshot_id: str | None,
    as_json: bool,
    actor: str,
    role: str | None,
    policy_mode: str | None,
) -> int:
    policy_exit_code = _check_policy_or_print(
        root=root,
        action=PolicyAction.PUBLISH_SNAPSHOT,
        actor=actor,
        role=role,
        policy_mode=policy_mode,
    )
    if policy_exit_code != 0:
        return policy_exit_code

    try:
        state = open_workspace(root, create=False)
    except WorkspaceError as exc:
        print(f"Invalid workspace input: {exc}")
        return 1

    base_lexicon = None
    if lexicon_path is not None:
        try:
            base_lexicon = load_lexicon(lexicon_path)
        except AgentLexiconLoadError as exc:
            print(f"Invalid lexicon: {exc}")
            return 1

    try:
        snapshot = publish_local_snapshot(
            state,
            output_path=output_path,
            base_lexicon=base_lexicon,
            snapshot_id=snapshot_id,
        )
    except (SnapshotPublishError, WorkspaceError) as exc:
        print(f"Invalid snapshot publish input: {exc}")
        return 1

    if as_json:
        print(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
        return 0

    print(f"Snapshot published: {snapshot.output_path}")
    print(f"Snapshot id: {snapshot.snapshot_id}")
    print(
        "Terms: "
        f"{snapshot.term_count} total, "
        f"{snapshot.generated_term_count} generated from "
        f"{snapshot.accepted_count} accepted decisions, "
        f"{snapshot.skipped_count} skipped"
    )
    return 0



def _review_command(
    *,
    root: Path,
    host: str,
    port: int,
    open_browser: bool,
    actor: str,
    role: str | None,
    policy_mode: str | None,
) -> int:
    try:
        run_review_inbox(
            root,
            host=host,
            port=port,
            open_browser=open_browser,
            actor=actor,
            role=role,
            policy_mode=policy_mode,
        )
    except (ReviewInboxError, WorkspaceError, LocalPolicyError, OSError) as exc:
        print(f"Invalid review inbox input: {exc}")
        return 1
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
    semantic_near_miss: bool,
    semantic_model: str,
    semantic_threshold: float,
) -> int:
    try:
        lexicon = load_lexicon(path)
    except AgentLexiconLoadError as exc:
        print(f"Invalid lexicon: {exc}")
        return 1

    try:
        semantic_backend = _semantic_backend_from_cli(
            enabled=semantic_near_miss,
            model_name=semantic_model,
            min_semantic_score=semantic_threshold,
        )
    except SemanticNearMissError as exc:
        print(f"Invalid semantic near-miss input: {exc}")
        return 1

    try:
        decision = resolve_text(
            lexicon,
            text,
            scopes=scopes,
            include_deprecated=include_deprecated,
            near_miss_semantic_backend=semantic_backend,
        )
    except SemanticNearMissError as exc:
        print(f"Invalid semantic near-miss input: {exc}")
        return 1
    print(f"Status: {decision.status.value}")
    print(f"Action: {decision.action.value}")
    if decision.message:
        print(f"Message: {decision.message}")
    _print_lexicon_snapshot(decision.metadata)
    if decision.metadata.get("unicode_findings"):
        print("Unicode findings:")
        for finding in decision.metadata.get("unicode_findings", []):
            if isinstance(finding, dict):
                print(f"- {finding.get('kind')} at {finding.get('start')} ({finding.get('name')})")
    _print_near_miss_suggestions(decision.metadata)

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



def _print_lexicon_snapshot(metadata: Mapping[str, object]) -> None:
    snapshot_ref = metadata.get("lexicon_snapshot_ref") if metadata else None
    if isinstance(snapshot_ref, str) and snapshot_ref:
        print(f"Lexicon snapshot: {snapshot_ref}")


def _print_near_miss_suggestions(metadata: Mapping[str, object]) -> None:
    suggestions = metadata.get("near_miss_suggestions") if metadata else None
    if not isinstance(suggestions, list) or not suggestions:
        return
    print("Near-miss suggestions:")
    for report in suggestions:
        if not isinstance(report, dict):
            continue
        surface = report.get("surface")
        items = report.get("suggestions")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            target_term_id = item.get("target_term_id")
            target_canonical = item.get("target_canonical")
            confidence = item.get("confidence")
            matched_surface = item.get("matched_surface")
            reasons = item.get("reasons")
            reason_label = ",".join(str(reason) for reason in reasons) if isinstance(reasons, list) else "shape"
            try:
                confidence_label = f"{float(confidence):.3f}"
            except (TypeError, ValueError):
                confidence_label = "n/a"
            semantic_label = _semantic_escalation_label(item.get("metadata"))
            print(
                f"- {surface!r} -> {target_term_id} "
                f"({target_canonical}) confidence={confidence_label} "
                f"via {matched_surface!r} reasons={reason_label}{semantic_label}"
            )



def _semantic_escalation_label(metadata: object) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    labels: list[str] = []
    if metadata.get("semantic_applied") is True:
        backend = metadata.get("semantic_backend") or metadata.get("semantic_model") or "semantic"
        score = metadata.get("semantic_score")
        try:
            labels.append(f"semantic_score={float(score):.3f}")
        except (TypeError, ValueError):
            labels.append("semantic_score=n/a")
        labels.append(f"semantic_backend={backend}")
    semantic = metadata.get("semantic_escalation")
    if isinstance(semantic, Mapping) and semantic.get("recommended") is True:
        reasons = semantic.get("reasons")
        reason_label = ",".join(str(reason) for reason in reasons) if isinstance(reasons, list) else "recommended"
        labels.append(f"semantic_escalation={reason_label}")
    return " " + " ".join(labels) if labels else ""

def _guard_command(
    *,
    path: Path,
    text: str,
    tool_name: str,
    scopes: list[str] | None,
    include_deprecated: bool,
    block_on_unicode_risk: bool,
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
        block_on_unicode_risk=block_on_unicode_risk,
    )
    print(f"Status: {decision.status.value}")
    print(f"Action: {decision.action.value}")
    print(f"Allowed: {'yes' if decision.is_allowed else 'no'}")
    print(f"Reason: {decision.reason}")
    print(f"Resolution: {decision.resolution.status.value}")
    _print_lexicon_snapshot(decision.metadata)
    if decision.metadata.get("unicode_findings"):
        print("Unicode findings:")
        for finding in decision.metadata.get("unicode_findings", []):
            if isinstance(finding, dict):
                print(f"- {finding.get('kind')} at {finding.get('start')} ({finding.get('name')})")
    _print_near_miss_suggestions(decision.metadata)

    if decision.matched_term_ids:
        print("Matched terms:")
        for term_id in decision.matched_term_ids:
            print(f"- {term_id}")
    if decision.allowed_tool_names:
        print("Allowed tools:")
        for allowed_tool_name in decision.allowed_tool_names:
            print(f"- {allowed_tool_name}")

    return 0 if decision.status in {ToolGuardStatus.ALLOWED, ToolGuardStatus.NO_MATCH} else 2
