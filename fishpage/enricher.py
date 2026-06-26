"""The Enrichment spine: one constrained-schema Claude call per Item, validated into a result.

Care Classifiers are hobbyist judgments, not biological facts, so they are AI-generated. A single
call takes an Item's trade name plus its Derived Category and Size and returns a species
(``scientific_name`` and ``common_name``) and the enum Classifiers in one validated payload.

The honesty guardrail is what makes that payload safe to filter on later. Every Classifier carries
an ``unknown`` member and the species names are nullable, so an unmappable name resolves to an
honest gap rather than a confident-but-wrong guess. Out-of-vocabulary Classifier values are
impossible by construction: there is simply no enum member for them.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from fishpage import observability
from fishpage.config import Settings

DEFAULT_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-haiku-4-5"


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    UNKNOWN = "unknown"


class Temperament(StrEnum):
    PEACEFUL = "peaceful"
    SEMI_AGGRESSIVE = "semi_aggressive"
    AGGRESSIVE = "aggressive"
    UNKNOWN = "unknown"


class PlantSafe(StrEnum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EnrichmentResult:
    """One Item's AI-read species and care Classifiers.

    The species names are nullable and every Classifier carries ``unknown``: an honest gap, never
    a fabricated value, is what reaches the result.
    """

    scientific_name: str | None
    common_name: str | None
    difficulty: Difficulty
    temperament: Temperament
    plant_safe: PlantSafe


_TOOL_NAME = "record_enrichment"


def build_tool() -> dict:
    """The strict tool the model must call, with each Classifier constrained to its vocabulary.

    Every Classifier's ``enum`` is the curated vocabulary including ``unknown``, and the species
    names accept ``null`` — so the constrained schema itself routes an honest gap rather than
    forcing a fabricated value.
    """
    return {
        "name": _TOOL_NAME,
        "description": (
            "Record the resolved species and care Classifiers for one aquarium-trade Item. "
            "Use unknown for any Classifier you cannot judge confidently, and null for the "
            "species names when the trade name does not map to a species with confidence. "
            "Never guess."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "scientific_name": {"type": ["string", "null"]},
                "common_name": {"type": ["string", "null"]},
                "difficulty": {"enum": [m.value for m in Difficulty]},
                "temperament": {"enum": [m.value for m in Temperament]},
                "plant_safe": {"enum": [m.value for m in PlantSafe]},
            },
            "required": [
                "scientific_name",
                "common_name",
                "difficulty",
                "temperament",
                "plant_safe",
            ],
            "additionalProperties": False,
        },
    }


def parse_enrichment(payload: dict) -> EnrichmentResult:
    """Validate a raw model payload into an :class:`EnrichmentResult`.

    Any Classifier value outside its vocabulary — or absent entirely — resolves to ``unknown``,
    so a fabricated grade can never reach the result even if the model returns one.
    """
    return EnrichmentResult(
        scientific_name=payload.get("scientific_name"),
        common_name=payload.get("common_name"),
        difficulty=_classifier(Difficulty, payload.get("difficulty")),
        temperament=_classifier(Temperament, payload.get("temperament")),
        plant_safe=_classifier(PlantSafe, payload.get("plant_safe")),
    )


def _classifier[C: StrEnum](enum: type[C], value: object) -> C:
    """Coerce a raw value into ``enum``, falling back to ``unknown``.

    Every Classifier enum carries an ``unknown`` member — the honesty hatch — so a value outside
    the vocabulary, or none at all, always has a safe home.
    """
    try:
        return enum(value)
    except ValueError:
        return enum("unknown")


@runtime_checkable
class Enricher(Protocol):
    """The injectable spine: resolve one Item's species and care Classifiers."""

    @property
    def model(self) -> str:
        """The model identifier the drainer tags each call's telemetry with."""
        ...

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult: ...


class _MessagesClient(Protocol):
    @property
    def messages(self) -> Any: ...


class ClaudeEnricher:
    """An :class:`Enricher` backed by one forced, constrained-schema Claude tool call.

    The client is injected so the parse-and-prompt logic is exercised by a fake with no network.
    """

    def __init__(self, client: _MessagesClient, *, model: str = DEFAULT_MODEL):
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            tools=[build_tool()],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _prompt(trade_name, category, size)}],
        )
        # Recorded before the parse so a response that is billed but omits the tool call still has
        # its spend counted rather than lost to the parse error below.
        observability.record_enrichment_tokens(
            response.usage.input_tokens,
            response.usage.output_tokens,
            model=self._model,
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                return parse_enrichment(block.input)
        raise ValueError("model did not call the enrichment tool")


def select_enricher(settings: Settings) -> Enricher | None:
    """The configured enricher, or ``None`` when Enrichment is off.

    Enrichment is opt-in and default-off: it takes both the flag and an Anthropic key, so
    ``just run`` and the test suite need no credentials and never construct a client.
    """
    if not (settings.enrichment_enabled and settings.anthropic_api_key):
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return ClaudeEnricher(client)


def _prompt(trade_name: str, category: str, size: str) -> str:
    return (
        "Identify the species and care Classifiers for this aquarium-trade Item.\n"
        f"Trade name: {trade_name}\n"
        f"Category: {category}\n"
        f"Size: {size}"
    )
