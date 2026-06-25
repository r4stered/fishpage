"""Assemble what a catalog card shows: resolve each Item's display Classifiers from the AI
enrichment and any human overrides, deriving Provenance on read.

Provenance is *derived*, never stored beside the value: an override present makes a value ``manual``
and authoritative, otherwise the AI read is ``ai-generated``. A value the model could not judge
(``unknown``) or has not produced yet simply does not appear, so an un-enriched Item degrades to a
clean card rather than a row of empty badges.
"""

from dataclasses import dataclass
from enum import StrEnum

from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.models import ImageRecord, Item, Provenance


@dataclass(frozen=True)
class ClassifierView:
    """One Item's resolved Classifier as a card shows it: a value plus where it came from."""

    key: str
    label: str
    value: str
    provenance: Provenance


@dataclass(frozen=True)
class Card:
    """Everything one catalog card draws from: the Item, its image (or ``None``), and its resolved
    display Classifiers. The single bundle the grid template iterates — no parallel side-maps."""

    item: Item
    image: ImageRecord | None
    classifiers: list[ClassifierView]

    @property
    def values(self) -> dict[str, str]:
        """The resolved value per Classifier key — what a badge shows and an override select
        preselects. Absent keys are un-enriched or honest gaps with no value in force."""
        return {view.key: view.value for view in self.classifiers}


@dataclass(frozen=True)
class ClassifierSpec:
    """One curated Classifier: the enrichment attribute it reads, its label, and its vocabulary."""

    key: str
    label: str
    enum: type[StrEnum]

    @property
    def choices(self) -> tuple[str, ...]:
        """The values offered as filter chips and manual overrides — the vocabulary minus the
        ``unknown`` honesty hatch, which is a model gap to fill, never a value a human selects."""
        return tuple(m.value for m in self.enum if m.value != "unknown")


# The fixed, curated Classifier vocabulary surfaced on a card, in display order. Extending it is a
# deliberate change — a new enum plus a migration — not an open-ended runtime registry.
CLASSIFIERS: tuple[ClassifierSpec, ...] = (
    ClassifierSpec("difficulty", "Difficulty", Difficulty),
    ClassifierSpec("temperament", "Temperament", Temperament),
    ClassifierSpec("plant_safe", "Plant safe", PlantSafe),
)


def classifier_spec(key: str) -> ClassifierSpec | None:
    """The spec for one Classifier key, or ``None`` when the key is outside the vocabulary."""
    return next((spec for spec in CLASSIFIERS if spec.key == key), None)


def resolve_classifiers(
    enrichment: EnrichmentResult | None,
    overrides: dict[str, str],
) -> list[ClassifierView]:
    """Resolve one Item's display Classifiers, deriving Provenance on read.

    A ``manual`` override wins on its attribute; absent one, the AI read stands as ``ai-generated``.
    """
    views: list[ClassifierView] = []
    for spec in CLASSIFIERS:
        key, label = spec.key, spec.label
        if key in overrides:
            views.append(ClassifierView(key, label, overrides[key], Provenance.MANUAL))
        elif enrichment is not None:
            classifier = getattr(enrichment, key)
            # unknown is the model's honest "I can't judge this", not a value to badge — skip it so
            # an un-readable attribute degrades to nothing rather than an actionable-looking guess.
            if classifier is not type(classifier).UNKNOWN:
                views.append(ClassifierView(key, label, classifier.value, Provenance.AI_GENERATED))
    return views


def build_cards(
    items: list[Item],
    *,
    enrichments: dict[str, EnrichmentResult],
    images: dict[str, ImageRecord],
    overrides: dict[str, dict[str, str]],
) -> list[Card]:
    """Assemble one :class:`Card` per Item, in order, joining the batch-read enrichment, image, and
    override maps. An Item missing from a map degrades cleanly — no image, no badges."""
    return [
        Card(
            item=item,
            image=images.get(item.sku),
            classifiers=resolve_classifiers(enrichments.get(item.sku), overrides.get(item.sku, {})),
        )
        for item in items
    ]


def filter_cards_by_classifiers(cards: list[Card], selected: dict[str, set[str]]) -> list[Card]:
    """Keep cards matching every active Classifier facet (AND across keys), where a key matches if
    the card's *resolved* value is any of that key's selected values (OR within a key).

    Filtering on the resolved value is what makes a manual override authoritative here too: a human
    correction is what the chip matches, not the AI read underneath it.
    """
    active = {key: values for key, values in selected.items() if values}
    if not active:
        return cards
    kept = []
    for card in cards:
        resolved = {view.key: view.value for view in card.classifiers}
        if all(resolved.get(key) in values for key, values in active.items()):
            kept.append(card)
    return kept
