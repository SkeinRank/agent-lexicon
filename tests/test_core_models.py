from __future__ import annotations

import unittest

from agent_lexicon import (
    AgentLexiconModelError,
    Alias,
    EvidenceKind,
    EvidenceSpan,
    Lexicon,
    ProposalCandidate,
    ProposalKind,
    RiskLevel,
    Scope,
    Term,
)


class CoreModelTests(unittest.TestCase):
    def test_scope_normalizes_fields(self) -> None:
        scope = Scope(
            id=" billing ",
            label=" Billing ",
            description=" Payment and credit terminology ",
            parents=["business"],
        )

        self.assertEqual(scope.id, "billing")
        self.assertEqual(scope.label, "Billing")
        self.assertEqual(scope.parents, ("business",))
        self.assertEqual(scope.to_dict()["parents"], ["business"])

    def test_term_surfaces_include_canonical_and_aliases(self) -> None:
        alias = Alias(surface="customer cap", term_id="billing.credit_limit", scopes=("billing",))
        deprecated_alias = Alias(
            surface="old customer threshold",
            term_id="billing.credit_limit",
            deprecated=True,
        )
        evidence = EvidenceSpan(
            source_path="docs/billing.md",
            start_line=12,
            end_line=14,
            snippet="The customer cap controls the maximum account exposure.",
            kind=EvidenceKind.POSITIVE,
        )
        term = Term(
            id="billing.credit_limit",
            canonical="credit limit",
            description="Maximum allowed customer credit exposure.",
            aliases=(alias, deprecated_alias),
            scopes=("billing",),
            tags=("customer", "risk"),
            evidence=(evidence,),
        )

        self.assertEqual(
            term.surfaces(),
            ("credit limit", "customer cap", "old customer threshold"),
        )
        self.assertEqual(term.surfaces(include_deprecated=False), ("credit limit", "customer cap"))
        self.assertEqual(evidence.location(), "docs/billing.md:12-14")
        self.assertEqual(term.to_dict()["aliases"][0]["surface"], "customer cap")

    def test_lexicon_container_serializes_terms_and_scopes(self) -> None:
        scope = Scope(id="billing")
        term = Term(id="billing.credit_limit", canonical="credit limit", scopes=("billing",))
        lexicon = Lexicon(version="1", scopes=(scope,), terms=(term,), metadata={"name": "demo"})

        self.assertEqual(lexicon.get_scope("billing"), scope)
        self.assertEqual(lexicon.get_term("billing.credit_limit"), term)
        self.assertEqual(lexicon.to_dict()["terms"][0]["id"], "billing.credit_limit")

    def test_alias_term_id_must_match_owner(self) -> None:
        alias = Alias(surface="rate limit", term_id="api.rate_limit")

        with self.assertRaises(AgentLexiconModelError):
            Term(id="billing.credit_limit", canonical="credit limit", aliases=(alias,))

    def test_evidence_line_range_is_validated(self) -> None:
        with self.assertRaises(AgentLexiconModelError):
            EvidenceSpan(
                source_path="docs/api.md",
                start_line=10,
                end_line=3,
                snippet="rate limit controls requests per minute",
            )

    def test_proposal_candidate_serializes_review_fields(self) -> None:
        evidence = EvidenceSpan(
            source_path="docs/billing.md",
            start_line=42,
            snippet="Customer cap is the credit limit for an account.",
            kind="positive",
        )
        proposal = ProposalCandidate(
            id="proposal.customer-cap.alias",
            kind=ProposalKind.ALIAS_CANDIDATE,
            surface="customer cap",
            candidate_term_id="billing.credit_limit",
            confidence=0.78,
            risk=RiskLevel.MEDIUM,
            scopes=("billing",),
            evidence=(evidence,),
            rationale="The phrase appears near credit exposure language.",
        )

        payload = proposal.to_dict()
        self.assertTrue(proposal.needs_human_review())
        self.assertEqual(payload["kind"], "alias_candidate")
        self.assertEqual(payload["risk"], "medium")
        self.assertEqual(payload["evidence"][0]["kind"], "positive")

    def test_low_risk_proposal_does_not_require_human_review_by_default(self) -> None:
        proposal = ProposalCandidate(
            id="proposal.low-risk",
            kind="term_candidate",
            surface="context space",
            confidence=0.9,
            risk="low",
        )

        self.assertFalse(proposal.needs_human_review())

    def test_proposal_confidence_is_bounded(self) -> None:
        with self.assertRaises(AgentLexiconModelError):
            ProposalCandidate(
                id="proposal.invalid-confidence",
                kind=ProposalKind.TERM_CANDIDATE,
                surface="context space",
                confidence=1.5,
            )


if __name__ == "__main__":
    unittest.main()
