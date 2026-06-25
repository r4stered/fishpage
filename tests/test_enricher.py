"""The Enrichment spine: one constrained-schema Claude call, validated into a result.

These tests never touch the network. They exercise the result shape, the honesty guardrail
that keeps a fabricated value out of the result, and the dependency-injected enricher driven
by a fake client.
"""

import pytest

from fishpage.config import load_settings
from fishpage.enricher import (
    ClaudeEnricher,
    Difficulty,
    Enricher,
    EnrichmentResult,
    PlantSafe,
    Temperament,
    build_tool,
    parse_enrichment,
    select_enricher,
)


class FakeEnricher:
    """A canned :class:`Enricher` for tests that inject the dependency, never the network."""

    def __init__(self, result: EnrichmentResult):
        self._result = result

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        return self._result


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name: str, payload: dict):
        self.name = name
        self.input = payload


class _Response:
    def __init__(self, *content):
        self.content = list(content)


class FakeClient:
    """A stand-in for ``anthropic.Anthropic`` that records the request and returns a canned call.

    The whole test suite drives the enricher through this — no key, no network.
    """

    def __init__(self, payload: dict):
        self._payload = payload
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs) -> _Response:
        self.calls.append(kwargs)
        return _Response(_ToolUseBlock("record_enrichment", self._payload))


def test_classifiers_support_unknown_and_species_supports_none():
    # The honesty guardrail's two escape values: an unmappable name leaves the species null, and
    # any care attribute the model can't judge confidently comes back ``unknown``.
    result = EnrichmentResult(
        scientific_name=None,
        common_name=None,
        difficulty=Difficulty.UNKNOWN,
        temperament=Temperament.UNKNOWN,
        plant_safe=PlantSafe.UNKNOWN,
    )

    assert result.scientific_name is None
    assert result.common_name is None
    assert result.difficulty is Difficulty.UNKNOWN
    assert result.temperament is Temperament.UNKNOWN
    assert result.plant_safe is PlantSafe.UNKNOWN


def test_out_of_vocabulary_classifier_values_are_impossible_by_construction():
    # A value outside the curated vocabulary cannot be represented at all — there is no
    # ``Difficulty`` member for it, so a fabricated grade can never become a result.
    with pytest.raises(ValueError):
        Difficulty("expert")
    with pytest.raises(ValueError):
        Temperament("docile")
    with pytest.raises(ValueError):
        PlantSafe("maybe")


def test_parse_maps_a_well_formed_payload_to_a_result():
    result = parse_enrichment(
        {
            "scientific_name": "Pterophyllum scalare",
            "common_name": "Freshwater Angelfish",
            "difficulty": "intermediate",
            "temperament": "semi_aggressive",
            "plant_safe": "safe",
        }
    )

    assert result == EnrichmentResult(
        scientific_name="Pterophyllum scalare",
        common_name="Freshwater Angelfish",
        difficulty=Difficulty.INTERMEDIATE,
        temperament=Temperament.SEMI_AGGRESSIVE,
        plant_safe=PlantSafe.SAFE,
    )


def test_parse_coerces_an_out_of_vocabulary_classifier_to_unknown():
    # Defense in depth behind the constrained schema: even if a value outside the vocabulary
    # arrives, it resolves to an honest ``unknown`` rather than reaching the result fabricated.
    result = parse_enrichment(
        {
            "scientific_name": "Betta splendens",
            "common_name": "Betta",
            "difficulty": "beginner",
            "temperament": "feisty",
            "plant_safe": "safe",
        }
    )

    assert result.temperament is Temperament.UNKNOWN


def test_parse_treats_a_missing_classifier_as_unknown():
    result = parse_enrichment({"scientific_name": "Betta splendens", "common_name": "Betta"})

    assert result.difficulty is Difficulty.UNKNOWN
    assert result.temperament is Temperament.UNKNOWN
    assert result.plant_safe is PlantSafe.UNKNOWN


def test_parse_leaves_an_unmappable_species_null():
    result = parse_enrichment(
        {
            "scientific_name": None,
            "common_name": None,
            "difficulty": "unknown",
            "temperament": "unknown",
            "plant_safe": "unknown",
        }
    )

    assert result.scientific_name is None
    assert result.common_name is None


def test_tool_schema_constrains_each_classifier_to_its_vocabulary():
    schema = build_tool()["input_schema"]
    properties = schema["properties"]

    assert properties["difficulty"]["enum"] == [m.value for m in Difficulty]
    assert properties["temperament"]["enum"] == [m.value for m in Temperament]
    assert properties["plant_safe"]["enum"] == [m.value for m in PlantSafe]
    # No room for the model to invent a key outside the curated payload.
    assert schema["additionalProperties"] is False


def test_tool_is_strict_and_carries_the_species_fields():
    tool = build_tool()

    assert tool["strict"] is True
    assert "scientific_name" in tool["input_schema"]["properties"]
    assert "common_name" in tool["input_schema"]["properties"]


def test_enrich_returns_the_parsed_result_from_the_forced_tool_call():
    client = FakeClient(
        {
            "scientific_name": "Polypterus ornatipinnis",
            "common_name": "Ornate Bichir",
            "difficulty": "intermediate",
            "temperament": "semi_aggressive",
            "plant_safe": "safe",
        }
    )

    result = ClaudeEnricher(client).enrich("Bichir Ornate", category="Monster/Oddball", size="M")

    assert result == EnrichmentResult(
        scientific_name="Polypterus ornatipinnis",
        common_name="Ornate Bichir",
        difficulty=Difficulty.INTERMEDIATE,
        temperament=Temperament.SEMI_AGGRESSIVE,
        plant_safe=PlantSafe.SAFE,
    )


def test_enrich_passes_the_trade_name_category_and_size_to_the_model():
    client = FakeClient(
        {
            "scientific_name": None,
            "common_name": None,
            "difficulty": "unknown",
            "temperament": "unknown",
            "plant_safe": "unknown",
        }
    )

    ClaudeEnricher(client).enrich("Bichir Ornate", category="Monster/Oddball", size="M")

    (call,) = client.calls
    sent = str(call["messages"])
    assert "Bichir Ornate" in sent
    assert "Monster/Oddball" in sent
    assert "M" in sent


def test_enrich_forces_the_constrained_tool_call():
    client = FakeClient(
        {
            "scientific_name": None,
            "common_name": None,
            "difficulty": "unknown",
            "temperament": "unknown",
            "plant_safe": "unknown",
        }
    )

    ClaudeEnricher(client).enrich("Leaf Fish", category="Monster/Oddball", size="-")

    (call,) = client.calls
    assert call["tool_choice"] == {"type": "tool", "name": "record_enrichment"}
    assert call["tools"] == [build_tool()]


def test_enrich_fails_loudly_when_the_model_does_not_call_the_tool():
    # The forced tool_choice makes this unreachable in practice, but if no tool call comes back
    # the enricher raises rather than inventing an empty result.
    class _EmptyClient:
        messages = None

        def create(self, **kwargs) -> _Response:
            return _Response()

    empty = _EmptyClient()
    empty.messages = empty

    with pytest.raises(ValueError):
        ClaudeEnricher(empty).enrich("Leaf Fish", category="Monster/Oddball", size="-")


def test_enrichment_is_off_by_default_so_no_credentials_are_needed():
    # With an empty environment — the `just run` / CI case — there is no enricher and no key.
    assert select_enricher(load_settings({})) is None


def test_enrichment_stays_off_unless_both_the_flag_and_a_key_are_present():
    assert select_enricher(load_settings({"ENRICHMENT_ENABLED": "1"})) is None
    assert select_enricher(load_settings({"ANTHROPIC_API_KEY": "sk-ant-test"})) is None


def test_enrichment_selects_a_claude_enricher_when_enabled_and_keyed():
    settings = load_settings({"ENRICHMENT_ENABLED": "1", "ANTHROPIC_API_KEY": "sk-ant-test"})

    assert isinstance(select_enricher(settings), ClaudeEnricher)


def test_a_fake_enricher_is_an_injectable_enricher():
    # The injection seam other slices depend on: anything shaped like an Enricher drops in for
    # the real one, so the rest of the system is exercised with no key and no network.
    canned = EnrichmentResult(
        scientific_name="Polypterus ornatipinnis",
        common_name="Ornate Bichir",
        difficulty=Difficulty.INTERMEDIATE,
        temperament=Temperament.SEMI_AGGRESSIVE,
        plant_safe=PlantSafe.SAFE,
    )
    fake = FakeEnricher(canned)

    assert isinstance(fake, Enricher)
    assert fake.enrich("Bichir Ornate", category="Monster/Oddball", size="M") == canned
