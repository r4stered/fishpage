"""Fuzzy name search over the catalog: narrow Items to approximate name matches."""

from rapidfuzz import fuzz

from fishpage.models import Item

# A query token matches a name word at or above this partial-ratio score. A short token
# that prefixes a longer word ("angel" in "Angelfish") scores 100, so partial spellings
# match while unrelated words fall well below.
_TOKEN_MATCH_CUTOFF = 80.0


def match_names(items: list[Item], term: str) -> list[Item]:
    """Keep Items whose name fuzzily matches every token of ``term``, ranked by relevance.

    The search is order-independent and partial-word: each whitespace token of ``term``
    must fuzzily match some word of the Item's name, so ``"angel koi"`` finds
    ``"Angelfish Koi"``. Survivors come back best-match-first — the closer the whole name
    is to the query, the higher it ranks; Items tied on relevance keep their input order.

    A blank ``term`` is no search at all: every Item passes through, unranked.
    """
    tokens = term.split()
    if not tokens:
        return list(items)
    survivors = [item for item in items if _matches_all(item.name, tokens)]
    return sorted(survivors, key=lambda item: _relevance(item.name, term), reverse=True)


def _relevance(name: str, term: str) -> float:
    # Score the whole name against the whole query, so a name that matches the query more
    # completely outranks one that only matches it partially.
    return fuzz.WRatio(term.lower(), name.lower())


def _matches_all(name: str, query_tokens: list[str]) -> bool:
    name_words = name.lower().split()
    return all(
        any(fuzz.partial_ratio(token.lower(), word) >= _TOKEN_MATCH_CUTOFF for word in name_words)
        for token in query_tokens
    )
