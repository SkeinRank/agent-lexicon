from __future__ import annotations

from pathlib import Path

import pytest

from agent_lexicon import AgentLexiconLoadError, Lexicon, load_lexicon, loads_lexicon


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_load_yaml_lexicon_example() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    assert isinstance(lexicon, Lexicon)
    assert lexicon.version == "1"
    assert len(lexicon.scopes) == 2
    assert len(lexicon.terms) == 2
    assert len(lexicon.proposals) == 1
    assert lexicon.get_term("billing.credit_limit") is not None
    assert lexicon.get_scope("billing") is not None
    assert lexicon.terms[0].aliases[0].surface == "customer cap"
    assert lexicon.terms[0].evidence[0].location() == "docs/billing.md:12"


def test_load_json_lexicon_example() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.json")

    assert len(lexicon.scopes) == 1
    assert len(lexicon.terms) == 1
    assert lexicon.terms[0].aliases[0].surface == "customer cap"
    assert lexicon.terms[0].aliases[0].term_id == "billing.credit_limit"


def test_loads_lexicon_accepts_yaml_text() -> None:
    lexicon = loads_lexicon(
        """
        version: 1
        scopes:
          - id: docs
        terms:
          - id: docs.snapshot
            canonical: snapshot
            scopes: [docs]
        """,
        document_format="yaml",
    )

    assert lexicon.get_term("docs.snapshot") is not None


def test_lexicon_from_file_classmethod() -> None:
    lexicon = Lexicon.from_file(EXAMPLES_DIR / "lexicon.yaml")

    assert lexicon.metadata["name"] == "Customer limits demo"


def test_rejects_duplicate_term_ids() -> None:
    with pytest.raises(AgentLexiconLoadError, match="duplicate term id"):
        loads_lexicon(
            """
            version: 1
            terms:
              - id: docs.snapshot
                canonical: snapshot
              - id: docs.snapshot
                canonical: dictionary snapshot
            """,
            document_format="yaml",
        )


def test_rejects_unknown_scope_reference() -> None:
    with pytest.raises(AgentLexiconLoadError, match="unknown scope"):
        loads_lexicon(
            """
            version: 1
            scopes:
              - id: billing
            terms:
              - id: api.rate_limit
                canonical: rate limit
                scopes: [api]
            """,
            document_format="yaml",
        )


def test_rejects_alias_collision_in_same_scope() -> None:
    with pytest.raises(AgentLexiconLoadError, match="maps to both"):
        loads_lexicon(
            """
            version: 1
            scopes:
              - id: billing
            terms:
              - id: billing.credit_limit
                canonical: credit limit
                scopes: [billing]
                aliases:
                  - surface: limit
                    scopes: [billing]
              - id: billing.refund_limit
                canonical: refund limit
                scopes: [billing]
                aliases:
                  - surface: limit
                    scopes: [billing]
            """,
            document_format="yaml",
        )


def test_rejects_proposal_unknown_candidate_term() -> None:
    with pytest.raises(AgentLexiconLoadError, match="unknown candidate_term_id"):
        loads_lexicon(
            """
            version: 1
            proposals:
              - id: proposal.unknown
                kind: alias_candidate
                surface: customer cap
                candidate_term_id: billing.credit_limit
            """,
            document_format="yaml",
        )


def test_yaml_fallback_parser_supports_package_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_lexicon.core.loader as loader

    monkeypatch.setattr(loader, "yaml", None)
    lexicon = loader.load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    assert len(lexicon.scopes) == 2
    assert lexicon.get_term("api.rate_limit") is not None
    assert lexicon.proposals[0].candidate_term_id == "billing.credit_limit"
