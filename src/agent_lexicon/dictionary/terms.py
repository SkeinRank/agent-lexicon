"""Dictionary term editing helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_lexicon.core import AgentLexiconLoadError, Alias, Lexicon, Term, load_lexicon
from agent_lexicon.workflows.simple import _write_lexicon_file


class TermEditError(ValueError):
    """Raised when a dictionary term edit cannot be applied."""


@dataclass(frozen=True, slots=True)
class DeprecatedAliasEdit:
    """Result returned after adding or updating a deprecated alias."""

    surface: str
    target_term_id: str
    lexicon_path: str
    updated: bool
    already_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "target_term_id": self.target_term_id,
            "lexicon_path": self.lexicon_path,
            "updated": self.updated,
            "already_present": self.already_present,
        }


def deprecate_alias_in_lexicon(
    lexicon: Lexicon,
    *,
    surface: str,
    target_term_id: str,
    note: str = "",
    source: str = "cli",
) -> tuple[Lexicon, DeprecatedAliasEdit]:
    """Return a lexicon where ``surface`` is a deprecated alias of ``target_term_id``."""
    if not isinstance(lexicon, Lexicon):
        raise TermEditError("lexicon must be a Lexicon")
    cleaned_surface = _clean(surface, field_name="surface")
    cleaned_target = _clean(target_term_id, field_name="target_term_id")
    target = lexicon.get_term(cleaned_target)
    if target is None:
        raise TermEditError(f"canonical term not found: {cleaned_target}")
    if target.deprecated:
        raise TermEditError(f"canonical term is deprecated: {cleaned_target}")

    surface_key = cleaned_surface.casefold()
    for term in lexicon.terms:
        if term.canonical.casefold() == surface_key:
            raise TermEditError(
                f"surface {cleaned_surface!r} is a canonical term ({term.id}); "
                "canonical merges are not handled by alias deprecation"
            )

    updated_terms: list[Term] = []
    changed = False
    already_present = False
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    alias_metadata = {
        "source": source,
        "created_at": now,
    }
    if note.strip():
        alias_metadata["note"] = note.strip()

    for term in lexicon.terms:
        aliases = list(term.aliases)
        if term.id != cleaned_target:
            for alias in aliases:
                if alias.surface.casefold() == surface_key:
                    raise TermEditError(
                        f"surface {cleaned_surface!r} is already an alias of {term.id!r}"
                    )
            updated_terms.append(term)
            continue

        replacement_aliases: list[Alias] = []
        for alias in aliases:
            if alias.surface.casefold() != surface_key:
                replacement_aliases.append(alias)
                continue
            if alias.deprecated:
                already_present = True
                replacement_aliases.append(alias)
            else:
                replacement_aliases.append(
                    replace(
                        alias,
                        deprecated=True,
                        metadata={**dict(alias.metadata), **alias_metadata},
                    )
                )
                changed = True
        if not already_present and not changed:
            replacement_aliases.append(
                Alias(
                    surface=cleaned_surface,
                    term_id=cleaned_target,
                    deprecated=True,
                    metadata=alias_metadata,
                )
            )
            changed = True
        updated_terms.append(replace(term, aliases=tuple(replacement_aliases)))

    if not changed and already_present:
        return lexicon, DeprecatedAliasEdit(
            surface=cleaned_surface,
            target_term_id=cleaned_target,
            lexicon_path="",
            updated=False,
            already_present=True,
        )

    return replace(lexicon, terms=tuple(updated_terms)), DeprecatedAliasEdit(
        surface=cleaned_surface,
        target_term_id=cleaned_target,
        lexicon_path="",
        updated=True,
        already_present=False,
    )


def deprecate_alias_in_lexicon_file(
    lexicon_path: str | Path,
    *,
    surface: str,
    target_term_id: str,
    note: str = "",
    source: str = "cli",
) -> DeprecatedAliasEdit:
    """Add a deprecated alias to a lexicon file and return the edit summary."""
    path = Path(lexicon_path).expanduser().resolve()
    try:
        lexicon = load_lexicon(path)
    except AgentLexiconLoadError as exc:
        raise TermEditError(str(exc)) from exc
    updated_lexicon, edit = deprecate_alias_in_lexicon(
        lexicon,
        surface=surface,
        target_term_id=target_term_id,
        note=note,
        source=source,
    )
    if edit.updated:
        _write_lexicon_file(updated_lexicon, path)
    return DeprecatedAliasEdit(
        surface=edit.surface,
        target_term_id=edit.target_term_id,
        lexicon_path=str(path),
        updated=edit.updated,
        already_present=edit.already_present,
    )


def _clean(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TermEditError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise TermEditError(f"{field_name} must not be empty")
    return cleaned


__all__ = [
    "DeprecatedAliasEdit",
    "TermEditError",
    "deprecate_alias_in_lexicon",
    "deprecate_alias_in_lexicon_file",
]
