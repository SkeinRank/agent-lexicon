"""Tool-call safety checks backed by terminology resolution."""

from __future__ import annotations

from collections.abc import Iterable

from .models import Lexicon, ResolutionStatus, ToolGuardAction, ToolGuardDecision, ToolGuardStatus
from .resolver import LexiconResolver, resolve_text


class ToolGuard:
    """Check whether an agent tool call is safe for the resolved terminology."""

    def __init__(self, lexicon: Lexicon, *, include_deprecated: bool = True) -> None:
        self._lexicon = lexicon
        self._include_deprecated = include_deprecated
        self._resolver = LexiconResolver.from_lexicon(lexicon, include_deprecated=include_deprecated)

    @classmethod
    def from_lexicon(cls, lexicon: Lexicon, *, include_deprecated: bool = True) -> "ToolGuard":
        """Build a tool guard for a loaded lexicon."""
        return cls(lexicon, include_deprecated=include_deprecated)

    def guard(
        self,
        text: str,
        *,
        tool_name: str,
        scopes: Iterable[str] | None = None,
        include_deprecated: bool | None = None,
    ) -> ToolGuardDecision:
        """Return a decision for a requested tool call.

        Ambiguous terminology is blocked with an ``ask_clarification`` action.
        Resolved terminology is allowed only when the resolved term has no tool
        restrictions or the requested tool is listed in that term's ``tools``.
        """
        effective_include_deprecated = self._include_deprecated if include_deprecated is None else include_deprecated
        resolution = self._resolver.resolve(
            text,
            scopes=scopes,
            include_deprecated=effective_include_deprecated,
        )

        if resolution.status == ResolutionStatus.UNKNOWN:
            return ToolGuardDecision(
                text=text,
                tool_name=tool_name,
                status=ToolGuardStatus.NO_MATCH,
                action=ToolGuardAction.PROCEED,
                resolution=resolution,
                reason="No known terminology matched the requested tool call text.",
            )

        matched_term_ids = tuple(candidate.term_id for candidate in resolution.candidates)
        allowed_tool_names = _allowed_tools_for_terms(self._lexicon, matched_term_ids)

        if resolution.status == ResolutionStatus.AMBIGUOUS:
            return ToolGuardDecision(
                text=text,
                tool_name=tool_name,
                status=ToolGuardStatus.NEEDS_CLARIFICATION,
                action=ToolGuardAction.ASK_CLARIFICATION,
                resolution=resolution,
                reason="Terminology is ambiguous; ask for clarification before calling a tool.",
                allowed_tool_names=allowed_tool_names,
                matched_term_ids=matched_term_ids,
            )

        if not allowed_tool_names:
            return ToolGuardDecision(
                text=text,
                tool_name=tool_name,
                status=ToolGuardStatus.ALLOWED,
                action=ToolGuardAction.PROCEED,
                resolution=resolution,
                reason="Resolved terminology has no tool restrictions.",
                allowed_tool_names=allowed_tool_names,
                matched_term_ids=matched_term_ids,
            )

        if tool_name in allowed_tool_names:
            return ToolGuardDecision(
                text=text,
                tool_name=tool_name,
                status=ToolGuardStatus.ALLOWED,
                action=ToolGuardAction.PROCEED,
                resolution=resolution,
                reason="Requested tool is allowed for the resolved terminology.",
                allowed_tool_names=allowed_tool_names,
                matched_term_ids=matched_term_ids,
            )

        return ToolGuardDecision(
            text=text,
            tool_name=tool_name,
            status=ToolGuardStatus.BLOCKED,
            action=ToolGuardAction.BLOCK,
            resolution=resolution,
            reason="Requested tool is not allowed for the resolved terminology.",
            allowed_tool_names=allowed_tool_names,
            matched_term_ids=matched_term_ids,
        )


def guard_tool_call(
    lexicon: Lexicon,
    text: str,
    *,
    tool_name: str,
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = True,
) -> ToolGuardDecision:
    """Convenience helper for checking a tool call against a lexicon."""
    return ToolGuard.from_lexicon(lexicon, include_deprecated=include_deprecated).guard(
        text,
        tool_name=tool_name,
        scopes=scopes,
        include_deprecated=include_deprecated,
    )


def _allowed_tools_for_terms(lexicon: Lexicon, term_ids: tuple[str, ...]) -> tuple[str, ...]:
    tools: list[str] = []
    for term_id in term_ids:
        term = lexicon.get_term(term_id)
        if term is None:
            continue
        tools.extend(term.tools)
    return tuple(dict.fromkeys(tools))


__all__ = [
    "ToolGuard",
    "guard_tool_call",
]
