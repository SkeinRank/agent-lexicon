from __future__ import annotations

from agent_lexicon.text import code_identifier_variants, normalized_fragment_surface, surface_fragments


def test_surface_fragments_split_code_identifiers() -> None:
    assert surface_fragments("PaymentCoreV2") == ("payment", "core", "v2")
    assert surface_fragments("partition_key") == ("partition", "key")
    assert surface_fragments("access-token") == ("access", "token")


def test_code_identifier_variants_from_natural_surface() -> None:
    assert code_identifier_variants("access token") == (
        "accessToken",
        "AccessToken",
        "access_token",
        "ACCESS_TOKEN",
        "access-token",
    )


def test_normalized_fragment_surface() -> None:
    assert normalized_fragment_surface("PaymentCore") == "payment core"
