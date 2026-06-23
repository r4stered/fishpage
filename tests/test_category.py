from pathlib import Path

from fishpage.category import derive_category
from fishpage.parser import parse_stocklist

FIXTURE = Path(__file__).parent / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"


def test_homogeneous_block_maps_to_its_category():
    # Block 12 is entirely Angelfish; the leading name word confirms it.
    assert derive_category("120091", "Angelfish Full Black") == "Angelfish"


def test_other_homogeneous_blocks_map_to_their_categories():
    assert derive_category("130011", "Discus Blue Diamond") == "Discus"
    assert derive_category("150013", "Eel Fire") == "Eel"
    assert derive_category("170011", "Barb Cherry") == "Barb"


def test_clean_block_wins_over_a_misleading_leading_name_word():
    # The leading name word is a cross-check, never an override. A typo in the name
    # ("Cichid") still lands in its block's category...
    assert derive_category("220011", "Cichid Electric Yellow") == "Cichlid"
    # ...and a name whose leading word happens to name a *different* category does not
    # drag the Item out of its block.
    assert derive_category("170011", "Tetra Misfiled Barb") == "Barb"


def test_invertebrate_amphibian_and_plant_blocks():
    # Not livestock fish, but each is still a single supplier block with its own category.
    assert derive_category("280011", "Shrimp Cherry Red") == "Shrimp"
    assert derive_category("290011", "Snail Mystery Gold") == "Snail"
    assert derive_category("270012", "Crab Gold Fiddler Freshwater") == "Crustacean"
    assert derive_category("260012", "African Dwarf Frog") == "Frog"
    assert derive_category("757141", "Micro Sword") == "Plant"


def test_unknown_block_falls_back_to_the_leading_name_word():
    # A block we have not curated yet still yields a usable category from the name's
    # leading word rather than failing.
    assert derive_category("990001", "Wobbegong Spotted") == "Wobbegong"


def test_unknown_block_with_empty_name_falls_back_without_crashing():
    # A data anomaly — uncurated SKU prefix and no name to read a leading word from.
    # There is no signal to categorize on, so it lands in the catch-all rather than
    # raising.
    assert derive_category("990001", "") == "Monster/Oddball"
    assert derive_category("990001", "   ") == "Monster/Oddball"


def test_heterogeneous_blocks_are_monster_oddball():
    # Blocks 11, 14 and 45 each mix unrelated oddballs under one block, so no single
    # leading name word names the block — they all collapse to Monster/Oddball.
    assert derive_category("110042", "Bichir Ornate") == "Monster/Oddball"
    assert derive_category("140092", "Datnoid Gold Tiger") == "Monster/Oddball"
    assert derive_category("450011", "Arowana Silver") == "Monster/Oddball"


def test_every_item_in_the_stocklist_gets_a_curated_category():
    items = parse_stocklist(FIXTURE)

    categories = {derive_category(item.sku, item.name) for item in items}

    # Every Item resolves to a non-empty category...
    assert all(derive_category(item.sku, item.name) for item in items)
    # ...and the catalog's category vocabulary is exactly the curated set — no item
    # falls through to a noisy name-word category. This is the dropdown's contents.
    assert categories == {
        "Angelfish",
        "Apistogramma",
        "Barb",
        "Betta",
        "Catfish",
        "Cichlid",
        "Cory",
        "Crustacean",
        "Danio",
        "Discus",
        "Eel",
        "Feeder",
        "Frog",
        "GloFish",
        "Goby",
        "Goldfish",
        "Gourami",
        "Guppy",
        "Killifish",
        "Koi",
        "Loach",
        "Molly",
        "Monster/Oddball",
        "Plant",
        "Platy",
        "Pleco",
        "Puffer",
        "Rainbowfish",
        "Ram",
        "Rasbora",
        "Shark",
        "Shrimp",
        "Snail",
        "Swordtail",
        "Tetra",
    }
