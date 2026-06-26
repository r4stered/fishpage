"""The catalog assembly layer: resolve each Item's display Classifiers from the AI enrichment
and any human overrides, deriving Provenance on read.

These exercise the pure resolve-on-read core — no database, no rendering — so the rule "a manual
override wins and is marked manual, else the AI value is marked ai-generated, else nothing shows"
is pinned independently of how a card later draws it.
"""

from dataclasses import replace
from decimal import Decimal

from fishpage.catalog import build_cards, filter_cards_by_classifiers, resolve_classifiers
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.models import ImageRecord, Item, Provenance

AN_ENRICHMENT = EnrichmentResult(
    scientific_name="Polypterus ornatipinnis",
    common_name="Ornate Bichir",
    difficulty=Difficulty.INTERMEDIATE,
    temperament=Temperament.SEMI_AGGRESSIVE,
    plant_safe=PlantSafe.SAFE,
)


def _by_key(views):
    return {view.key: view for view in views}


def test_a_human_override_wins_and_is_marked_manual():
    views = _by_key(resolve_classifiers(AN_ENRICHMENT, {"difficulty": "beginner"}))

    # The override outranks the AI read on its own attribute and carries manual Provenance, so the
    # card can show a buyer this value is a human fact, not a best-effort guess.
    assert views["difficulty"].value == "beginner"
    assert views["difficulty"].provenance is Provenance.MANUAL


def test_an_unoverridden_ai_value_is_marked_ai_generated():
    views = _by_key(resolve_classifiers(AN_ENRICHMENT, {}))

    # With no human correction the AI read stands, flagged ai-generated so a buyer reads it as a
    # best-effort guess rather than a confirmed fact.
    assert views["temperament"].value == "semi_aggressive"
    assert views["temperament"].provenance is Provenance.AI_GENERATED
    assert views["plant_safe"].value == "safe"
    assert views["plant_safe"].provenance is Provenance.AI_GENERATED


def test_an_un_enriched_item_shows_no_classifiers():
    # A SKU the drainer has not reached yet has no enrichment row; it must degrade to a clean card,
    # not a row of empty badges. No override either — nothing to show.
    assert resolve_classifiers(None, {}) == []


def test_an_unknown_ai_value_is_dropped_but_the_honest_ones_remain():
    honest_gap = EnrichmentResult(
        scientific_name=None,
        common_name=None,
        difficulty=Difficulty.UNKNOWN,
        temperament=Temperament.PEACEFUL,
        plant_safe=PlantSafe.UNKNOWN,
    )

    views = _by_key(resolve_classifiers(honest_gap, {}))

    # unknown is the honesty hatch — the model's "I can't judge this" — so it is not a badge a buyer
    # could act on. The attributes the model *could* read still show.
    assert "difficulty" not in views
    assert "plant_safe" not in views
    assert views["temperament"].value == "peaceful"


def test_a_manual_override_can_resolve_an_unknown_ai_value():
    honest_gap = EnrichmentResult(
        scientific_name=None,
        common_name=None,
        difficulty=Difficulty.UNKNOWN,
        temperament=Temperament.UNKNOWN,
        plant_safe=PlantSafe.UNKNOWN,
    )

    views = _by_key(resolve_classifiers(honest_gap, {"difficulty": "advanced"}))

    # A human filling the gap the model left is exactly the override path: the value shows as manual
    # even though the AI value underneath was unknown.
    assert views["difficulty"].value == "advanced"
    assert views["difficulty"].provenance is Provenance.MANUAL


ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish", Decimal("5.99"), Decimal("4.99"), 30)


def test_build_cards_assembles_item_image_and_resolved_classifiers_in_order():
    image = ImageRecord("img/110042.webp", None, "A. Photographer", None, Provenance.WIKIMEDIA)
    cards = build_cards(
        [ORNATE_M, LEAF],
        enrichments={"110042": AN_ENRICHMENT},
        images={"110042": image},
        overrides={"110042": {"difficulty": "beginner"}},
    )

    # One card per Item in the given order; each carries its Item, its image record (or None), and
    # its resolved Classifiers — the single bundle the grid template draws from.
    assert [card.item.sku for card in cards] == ["110042", "110092"]
    ornate, leaf = cards
    assert ornate.image is image
    assert _by_key(ornate.classifiers)["difficulty"].provenance is Provenance.MANUAL
    # The un-enriched, image-less Item degrades cleanly: no image, no badges.
    assert leaf.image is None
    assert leaf.classifiers == []


def test_build_cards_marks_new_this_week_against_the_latest_stocklist_date():
    from datetime import date

    brand_new = replace(ORNATE_M, first_seen=date(2026, 6, 26), last_seen=date(2026, 6, 26))
    returning = replace(LEAF, first_seen=date(2026, 6, 19), last_seen=date(2026, 6, 26))
    cards = build_cards(
        [brand_new, returning],
        enrichments={},
        images={},
        overrides={},
        latest_date=date(2026, 6, 26),
    )

    # The first-ever sighting in the latest Stocklist carries the badge; the returner does not.
    assert [card.new_this_week for card in cards] == [True, False]


def _cards_for_filtering():
    beginner_peaceful = EnrichmentResult(
        None, None, Difficulty.BEGINNER, Temperament.PEACEFUL, PlantSafe.SAFE
    )
    advanced_aggressive = EnrichmentResult(
        None, None, Difficulty.ADVANCED, Temperament.AGGRESSIVE, PlantSafe.UNSAFE
    )
    return build_cards(
        [ORNATE_M, LEAF],
        enrichments={"110042": beginner_peaceful, "110092": advanced_aggressive},
        images={},
        overrides={},
    )


def test_filtering_by_a_classifier_value_keeps_only_matching_cards():
    cards = filter_cards_by_classifiers(_cards_for_filtering(), {"difficulty": {"beginner"}})

    # The chip keeps only cards whose resolved difficulty is beginner; the advanced Item drops out.
    assert [card.item.sku for card in cards] == ["110042"]


def test_filtering_across_classifiers_is_conjunctive():
    cards = filter_cards_by_classifiers(
        _cards_for_filtering(), {"difficulty": {"beginner"}, "temperament": {"aggressive"}}
    )

    # A card must satisfy every active Classifier facet: no Item is both beginner and aggressive.
    assert cards == []


def test_filtering_within_one_classifier_is_disjunctive():
    cards = filter_cards_by_classifiers(
        _cards_for_filtering(), {"difficulty": {"beginner", "advanced"}}
    )

    # Two chips on the same Classifier widen the match — either value qualifies.
    assert {card.item.sku for card in cards} == {"110042", "110092"}


def test_filtering_on_a_manual_override_uses_the_resolved_value():
    cards = build_cards(
        [ORNATE_M],
        enrichments={
            "110042": EnrichmentResult(
                None, None, Difficulty.ADVANCED, Temperament.UNKNOWN, PlantSafe.UNKNOWN
            )
        },
        images={},
        overrides={"110042": {"difficulty": "beginner"}},
    )

    # The buyer's correction is what filtering sees: a manual "beginner" matches the beginner chip
    # even though the AI underneath read "advanced".
    assert filter_cards_by_classifiers(cards, {"difficulty": {"beginner"}})
    assert filter_cards_by_classifiers(cards, {"difficulty": {"advanced"}}) == []
