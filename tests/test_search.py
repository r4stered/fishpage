from decimal import Decimal

from fishpage.models import Item
from fishpage.search import match_names

ANGEL_KOI = Item("120093", "M", "Angelfish Koi", Decimal("12.99"), None, 8)
BARB = Item("170011", "-", "Barb Cherry", Decimal("3.99"), None, 40)

EXACT_KOI = Item("120094", "M", "Angel Koi", Decimal("14.99"), None, 5)
SMOKEY_KOI = Item("120095", "L", "Angelfish Koi Smokey", Decimal("16.99"), None, 3)


def test_partial_tokens_match_across_words():
    # "angel koi" finds "Angelfish Koi": each query token matches a name word it only
    # partially spells, in any order.
    assert match_names([ANGEL_KOI, BARB], "angel koi") == [ANGEL_KOI]


def test_results_are_ranked_by_relevance():
    # All three pass the filter for "angel koi"; the closer the whole name is to the
    # query, the higher it ranks. Input order (Smokey, Angelfish, exact) is overridden.
    ranked = match_names([SMOKEY_KOI, ANGEL_KOI, EXACT_KOI], "angel koi")

    assert ranked == [EXACT_KOI, SMOKEY_KOI, ANGEL_KOI]


def test_blank_term_does_not_filter():
    # An empty search box is no search at all: every Item passes through, order intact.
    assert match_names([ANGEL_KOI, BARB], "   ") == [ANGEL_KOI, BARB]


def test_unrelated_term_matches_nothing():
    # A query that resembles no name returns nothing, rather than the closest Item.
    assert match_names([ANGEL_KOI, BARB], "tetra") == []


def test_every_query_token_must_match():
    # All tokens must land: "barb" alone matches, but "barb koi" does not, since the
    # Barb's name carries no "koi" word.
    assert match_names([ANGEL_KOI, BARB], "barb") == [BARB]
    assert match_names([ANGEL_KOI, BARB], "barb koi") == []
