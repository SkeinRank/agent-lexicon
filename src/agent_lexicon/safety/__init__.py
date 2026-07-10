"""Internal prompt-injection screening for outbound LLM calls.

This module is intentionally not part of the user-facing scan pipeline:
scanning arbitrary project documents for injection indicators produced
false positives on legitimate corpora (verified against the Apache Airflow
docs), and document screening is not this tool's job. It remains in-tree
for one legitimate internal purpose - screening and sanitizing evidence
snippets before they are sent to an LLM by the optional review-agent - and
as the seed of a future standalone agent-memory governance project, where
admission-time screening is the core concern rather than a side feature.

Deterministic bidi/Unicode hardening of the resolve pipeline lives in
``core.resolver`` / ``core.tool_guard`` and is unrelated to this module.
"""

from __future__ import annotations

from .prompt_injection import (
    PromptInjectionRisk,
    PromptSafetyAction,
    PromptSafetyError,
    PromptSafetyFinding,
    PromptSafetyScanScope,
    PromptSafetyReport,
    format_evidence_pack_for_llm_review,
    sanitize_text_for_llm_review,
    scan_documents_for_prompt_injection,
    scan_prompt_injection_text,
)

__all__ = [
    "PromptInjectionRisk",
    "PromptSafetyAction",
    "PromptSafetyError",
    "PromptSafetyFinding",
    "PromptSafetyScanScope",
    "PromptSafetyReport",
    "format_evidence_pack_for_llm_review",
    "sanitize_text_for_llm_review",
    "scan_documents_for_prompt_injection",
    "scan_prompt_injection_text",
]
