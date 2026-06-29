"""Load and validate Agent Lexicon documents.

The loader accepts JSON and YAML documents that describe scopes, canonical
terms, aliases, evidence spans, and proposal candidates. It performs structural
validation and returns immutable core model objects that can be used by the
runtime and command line tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # pragma: no cover - import branch depends on the environment
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from .models import (
    AgentLexiconModelError,
    Alias,
    EvidenceSpan,
    Lexicon,
    ProposalCandidate,
    Scope,
    Term,
)


class AgentLexiconLoadError(ValueError):
    """Raised when a lexicon document cannot be loaded or validated."""


SUPPORTED_FORMATS = {"json", "yaml", "yml"}


def load_lexicon(path: str | Path, *, document_format: str | None = None) -> Lexicon:
    """Load a lexicon document from a JSON or YAML file."""
    source_path = Path(path)
    if not source_path.exists():
        raise AgentLexiconLoadError(f"lexicon file does not exist: {source_path}")
    if not source_path.is_file():
        raise AgentLexiconLoadError(f"lexicon path is not a file: {source_path}")

    resolved_format = _resolve_document_format(source_path, document_format=document_format)
    return loads_lexicon(source_path.read_text(encoding="utf-8"), document_format=resolved_format, source_path=source_path)


def loads_lexicon(text: str, *, document_format: str, source_path: str | Path | None = None) -> Lexicon:
    """Load a lexicon document from a string."""
    resolved_format = _normalize_document_format(document_format)
    try:
        if resolved_format == "json":
            payload = json.loads(text)
        else:
            if yaml is not None:
                payload = yaml.safe_load(text)
            else:
                payload = _load_basic_yaml(text)
    except AgentLexiconLoadError:
        raise
    except Exception as exc:  # noqa: BLE001 - parser errors differ between JSON/YAML backends
        location = f" from {source_path}" if source_path is not None else ""
        raise AgentLexiconLoadError(f"failed to parse {resolved_format} lexicon{location}: {exc}") from exc

    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise AgentLexiconLoadError("lexicon document root must be a mapping")
    return lexicon_from_dict(payload, source_path=source_path)


def lexicon_from_dict(payload: Mapping[str, Any], *, source_path: str | Path | None = None) -> Lexicon:
    """Build a validated :class:`Lexicon` from a mapping."""
    if not isinstance(payload, Mapping):
        raise AgentLexiconLoadError("lexicon payload must be a mapping")

    version = payload.get("version", 1)
    if str(version) != "1":
        raise AgentLexiconLoadError(f"unsupported lexicon version: {version!r}")

    metadata = _mapping(payload.get("metadata", {}), field_name="metadata")
    scopes = tuple(_parse_scope(item, index=index) for index, item in enumerate(_list(payload.get("scopes", []), field_name="scopes")))
    scope_ids = _ensure_unique((scope.id for scope in scopes), field_name="scope id")

    terms = tuple(_parse_term(item, index=index) for index, item in enumerate(_list(payload.get("terms", []), field_name="terms")))
    term_ids = _ensure_unique((term.id for term in terms), field_name="term id")

    proposals = tuple(
        _parse_proposal(item, index=index) for index, item in enumerate(_list(payload.get("proposals", []), field_name="proposals"))
    )
    _ensure_unique((proposal.id for proposal in proposals), field_name="proposal id")

    try:
        lexicon = Lexicon(
            version=str(version),
            scopes=scopes,
            terms=terms,
            proposals=proposals,
            metadata=metadata,
        )
    except AgentLexiconModelError as exc:
        raise AgentLexiconLoadError(str(exc)) from exc

    _validate_scope_references(lexicon, known_scope_ids=scope_ids)
    _validate_term_references(lexicon, known_term_ids=term_ids)
    _validate_deprecated_replacements(lexicon)
    _validate_alias_collisions(lexicon)

    return lexicon


def _load_basic_yaml(text: str) -> Any:
    """Parse a small YAML subset used by Agent Lexicon examples.

    PyYAML is used when available. This fallback keeps the package usable in
    minimal environments and supports mappings, nested lists, scalar values, and
    inline lists such as ``[billing, api]``.
    """
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if raw_line[:indent].replace(" ", ""):
            raise AgentLexiconLoadError("YAML indentation must use spaces")
        lines.append((indent, raw_line.strip()))
    if not lines:
        return {}

    value, next_index = _parse_basic_yaml_block(lines, 0, lines[0][0])
    if next_index != len(lines):
        raise AgentLexiconLoadError("failed to parse complete YAML document")
    return value


def _parse_basic_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, current_text = lines[index]
    if current_indent < indent:
        return {}, index
    if current_indent != indent:
        raise AgentLexiconLoadError("unexpected YAML indentation")
    if current_text.startswith("- "):
        return _parse_basic_yaml_list(lines, index, indent)
    return _parse_basic_yaml_mapping(lines, index, indent)


def _parse_basic_yaml_mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent:
            raise AgentLexiconLoadError("unexpected YAML indentation in mapping")
        if text.startswith("- "):
            break
        key, value_text = _split_basic_yaml_key_value(text)
        index += 1
        if value_text == "":
            if index < len(lines) and lines[index][0] > line_indent:
                value, index = _parse_basic_yaml_block(lines, index, lines[index][0])
            else:
                value = None
        else:
            value = _parse_basic_yaml_scalar(value_text)
        result[key] = value
    return result, index


def _parse_basic_yaml_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent or not text.startswith("- "):
            break
        item_text = text[2:].strip()
        index += 1
        if item_text == "":
            if index < len(lines) and lines[index][0] > line_indent:
                item, index = _parse_basic_yaml_block(lines, index, lines[index][0])
            else:
                item = None
        elif _looks_like_basic_yaml_key_value(item_text):
            key, value_text = _split_basic_yaml_key_value(item_text)
            item = {key: _parse_basic_yaml_scalar(value_text) if value_text else None}
            if index < len(lines) and lines[index][0] > line_indent:
                nested, index = _parse_basic_yaml_block(lines, index, lines[index][0])
                if isinstance(nested, dict):
                    item.update(nested)
                else:
                    raise AgentLexiconLoadError("list item mapping cannot be followed by a nested list")
        else:
            item = _parse_basic_yaml_scalar(item_text)
            if index < len(lines) and lines[index][0] > line_indent:
                raise AgentLexiconLoadError("scalar YAML list item cannot have nested content")
        result.append(item)
    return result, index


def _looks_like_basic_yaml_key_value(text: str) -> bool:
    if ":" not in text:
        return False
    key, _separator, _value = text.partition(":")
    return bool(key.strip()) and " " not in key.strip()


def _split_basic_yaml_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise AgentLexiconLoadError(f"expected YAML key/value pair: {text!r}")
    key, _separator, value = text.partition(":")
    key = key.strip()
    if not key:
        raise AgentLexiconLoadError("YAML mapping key must not be empty")
    return key, value.strip()


def _parse_basic_yaml_scalar(text: str) -> Any:
    value = text.strip()
    if value == "":
        return ""
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_basic_yaml_scalar(item.strip()) for item in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _resolve_document_format(path: Path, *, document_format: str | None) -> str:
    if document_format is not None:
        return _normalize_document_format(document_format)
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_FORMATS:
        raise AgentLexiconLoadError(
            f"unsupported lexicon file extension: {path.suffix or '<none>'}; expected .json, .yaml, or .yml"
        )
    return "yaml" if suffix == "yml" else suffix


def _normalize_document_format(document_format: str) -> str:
    normalized = document_format.strip().lower().lstrip(".")
    if normalized == "yml":
        normalized = "yaml"
    if normalized not in {"json", "yaml"}:
        raise AgentLexiconLoadError(f"unsupported lexicon format: {document_format!r}")
    return normalized


def _parse_scope(payload: Any, *, index: int) -> Scope:
    item = _mapping(payload, field_name=f"scopes[{index}]")
    try:
        return Scope(
            id=_required(item, "id", field_name=f"scopes[{index}].id"),
            label=item.get("label"),
            description=item.get("description"),
            parents=tuple(_list(item.get("parents", []), field_name=f"scopes[{index}].parents")),
            metadata=_mapping(item.get("metadata", {}), field_name=f"scopes[{index}].metadata"),
        )
    except AgentLexiconModelError as exc:
        raise AgentLexiconLoadError(str(exc)) from exc


def _parse_term(payload: Any, *, index: int) -> Term:
    item = _mapping(payload, field_name=f"terms[{index}]")
    term_id = _required(item, "id", field_name=f"terms[{index}].id")
    try:
        return Term(
            id=term_id,
            canonical=_required(item, "canonical", field_name=f"terms[{index}].canonical"),
            description=item.get("description"),
            aliases=tuple(_parse_alias(alias_payload, term_id=term_id, index=alias_index) for alias_index, alias_payload in enumerate(_list(item.get("aliases", []), field_name=f"terms[{index}].aliases"))),
            scopes=tuple(_list(item.get("scopes", []), field_name=f"terms[{index}].scopes")),
            tags=tuple(_list(item.get("tags", []), field_name=f"terms[{index}].tags")),
            tools=tuple(_list(item.get("tools", []), field_name=f"terms[{index}].tools")),
            deprecated=bool(item.get("deprecated", False)),
            evidence=tuple(_parse_evidence(evidence_payload, field_name=f"terms[{index}].evidence[{evidence_index}]") for evidence_index, evidence_payload in enumerate(_list(item.get("evidence", []), field_name=f"terms[{index}].evidence"))),
            metadata=_mapping(item.get("metadata", {}), field_name=f"terms[{index}].metadata"),
        )
    except AgentLexiconModelError as exc:
        raise AgentLexiconLoadError(str(exc)) from exc


def _parse_alias(payload: Any, *, term_id: str, index: int) -> Alias:
    if isinstance(payload, str):
        item: Mapping[str, Any] = {"surface": payload}
    else:
        item = _mapping(payload, field_name=f"alias[{index}]")
    alias_term_id = str(item.get("term_id", term_id))
    try:
        return Alias(
            surface=_required(item, "surface", field_name=f"alias[{index}].surface"),
            term_id=alias_term_id,
            scopes=tuple(_list(item.get("scopes", []), field_name=f"alias[{index}].scopes")),
            case_sensitive=bool(item.get("case_sensitive", False)),
            deprecated=bool(item.get("deprecated", False)),
            metadata=_mapping(item.get("metadata", {}), field_name=f"alias[{index}].metadata"),
        )
    except AgentLexiconModelError as exc:
        raise AgentLexiconLoadError(str(exc)) from exc


def _parse_evidence(payload: Any, *, field_name: str) -> EvidenceSpan:
    item = _mapping(payload, field_name=field_name)
    try:
        return EvidenceSpan(
            source_path=_required(item, "source_path", field_name=f"{field_name}.source_path"),
            snippet=_required(item, "snippet", field_name=f"{field_name}.snippet"),
            kind=item.get("kind", "context"),
            start_line=item.get("start_line"),
            end_line=item.get("end_line"),
            source_id=item.get("source_id"),
            metadata=_mapping(item.get("metadata", {}), field_name=f"{field_name}.metadata"),
        )
    except AgentLexiconModelError as exc:
        raise AgentLexiconLoadError(str(exc)) from exc


def _parse_proposal(payload: Any, *, index: int) -> ProposalCandidate:
    item = _mapping(payload, field_name=f"proposals[{index}]")
    try:
        return ProposalCandidate(
            id=_required(item, "id", field_name=f"proposals[{index}].id"),
            kind=_required(item, "kind", field_name=f"proposals[{index}].kind"),
            surface=_required(item, "surface", field_name=f"proposals[{index}].surface"),
            status=item.get("status", "pending"),
            candidate_term_id=item.get("candidate_term_id"),
            target_term_id=item.get("target_term_id"),
            confidence=item.get("confidence"),
            risk=item.get("risk", "medium"),
            scopes=tuple(_list(item.get("scopes", []), field_name=f"proposals[{index}].scopes")),
            evidence=tuple(_parse_evidence(evidence_payload, field_name=f"proposals[{index}].evidence[{evidence_index}]") for evidence_index, evidence_payload in enumerate(_list(item.get("evidence", []), field_name=f"proposals[{index}].evidence"))),
            rationale=item.get("rationale"),
            metadata=_mapping(item.get("metadata", {}), field_name=f"proposals[{index}].metadata"),
        )
    except AgentLexiconModelError as exc:
        raise AgentLexiconLoadError(str(exc)) from exc


def _validate_scope_references(lexicon: Lexicon, *, known_scope_ids: set[str]) -> None:
    for scope in lexicon.scopes:
        for parent in scope.parents:
            if parent not in known_scope_ids:
                raise AgentLexiconLoadError(f"scope {scope.id!r} references unknown parent scope {parent!r}")
    for term in lexicon.terms:
        _check_scope_ids(term.scopes, known_scope_ids=known_scope_ids, owner=f"term {term.id!r}")
        for alias in term.aliases:
            _check_scope_ids(alias.scopes, known_scope_ids=known_scope_ids, owner=f"alias {alias.surface!r}")
    for proposal in lexicon.proposals:
        _check_scope_ids(proposal.scopes, known_scope_ids=known_scope_ids, owner=f"proposal {proposal.id!r}")


def _validate_term_references(lexicon: Lexicon, *, known_term_ids: set[str]) -> None:
    for proposal in lexicon.proposals:
        for reference_field, reference_value in (
            ("candidate_term_id", proposal.candidate_term_id),
            ("target_term_id", proposal.target_term_id),
        ):
            if reference_value is not None and reference_value not in known_term_ids:
                raise AgentLexiconLoadError(
                    f"proposal {proposal.id!r} references unknown {reference_field} {reference_value!r}"
                )


def _validate_deprecated_replacements(lexicon: Lexicon) -> None:
    terms_by_id = {term.id: term for term in lexicon.terms}
    for term in lexicon.terms:
        replacement_id = _deprecated_replacement_id(term)
        if replacement_id is None:
            continue
        if not term.deprecated:
            raise AgentLexiconLoadError(
                f"term {term.id!r} declares replacement metadata but is not deprecated"
            )
        if replacement_id == term.id:
            raise AgentLexiconLoadError(f"deprecated term {term.id!r} cannot replace itself")
        replacement = terms_by_id.get(replacement_id)
        if replacement is None:
            raise AgentLexiconLoadError(
                f"deprecated term {term.id!r} references unknown replacement term {replacement_id!r}"
            )
        if replacement.deprecated:
            raise AgentLexiconLoadError(
                f"deprecated term {term.id!r} replacement {replacement_id!r} is also deprecated"
            )


def _validate_alias_collisions(lexicon: Lexicon) -> None:
    seen: dict[tuple[str, tuple[str, ...], bool], str] = {}
    for term in lexicon.terms:
        if term.deprecated:
            continue
        key = (term.canonical.casefold(), tuple(term.scopes), False)
        existing_term_id = seen.get(key)
        if existing_term_id is not None and existing_term_id != term.id:
            raise AgentLexiconLoadError(
                f"surface {term.canonical!r} maps to both {existing_term_id!r} and {term.id!r} in scopes {tuple(term.scopes)!r}"
            )
        seen[key] = term.id
        for alias in term.aliases:
            if alias.deprecated:
                continue
            key = (alias.normalized_surface(), tuple(alias.scopes), alias.case_sensitive)
            existing_term_id = seen.get(key)
            if existing_term_id is not None and existing_term_id != term.id:
                raise AgentLexiconLoadError(
                    f"alias {alias.surface!r} maps to both {existing_term_id!r} and {term.id!r} in scopes {tuple(alias.scopes)!r}"
                )
            seen[key] = term.id


def _deprecated_replacement_id(term: Term) -> str | None:
    for key in ("replacement_term_id", "replaced_by", "canonical_replacement_term_id"):
        value = term.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _check_scope_ids(scope_ids: Iterable[str], *, known_scope_ids: set[str], owner: str) -> None:
    for scope_id in scope_ids:
        if scope_id not in known_scope_ids:
            raise AgentLexiconLoadError(f"{owner} references unknown scope {scope_id!r}")


def _ensure_unique(values: Iterable[str], *, field_name: str) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise AgentLexiconLoadError(f"duplicate {field_name}: {value!r}")
        seen.add(value)
    return seen


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentLexiconLoadError(f"{field_name} must be a mapping")
    return value


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentLexiconLoadError(f"{field_name} must be a list")
    return value


def _required(payload: Mapping[str, Any], key: str, *, field_name: str) -> Any:
    if key not in payload:
        raise AgentLexiconLoadError(f"missing required field: {field_name}")
    return payload[key]


__all__ = [
    "AgentLexiconLoadError",
    "SUPPORTED_FORMATS",
    "lexicon_from_dict",
    "load_lexicon",
    "loads_lexicon",
]
