"""Derive an Item's Derived Category from its SKU prefix block.

The supplier groups Items into blocks identified by the leading two digits of the SKU,
and each block is a single category. The category is read from that block; the leading
word of the Item's name is a cross-check, not an override, so a misspelled or unusual
name (``Cichid``, ``Catifsh``) still lands in its block's category.
"""

_MONSTER_ODDBALL = "Monster/Oddball"

_CATEGORY_BY_BLOCK = {
    "11": _MONSTER_ODDBALL,
    "12": "Angelfish",
    "13": "Discus",
    "14": _MONSTER_ODDBALL,
    "15": "Eel",
    "16": "Goby",
    "17": "Barb",
    "18": "Danio",
    "19": "Rasbora",
    "20": "Betta",
    "21": "Cichlid",
    "22": "Cichlid",
    "23": "Catfish",
    "24": "Cory",
    "25": "Shark",
    "26": "Frog",
    "27": "Crustacean",
    "28": "Shrimp",
    "29": "Snail",
    "30": "GloFish",
    "31": "Goldfish",
    "32": "Gourami",
    "33": "Molly",
    "34": "Guppy",
    "35": "Killifish",
    "36": "Feeder",
    "37": "Koi",
    "38": "Loach",
    "40": "Ram",
    "41": "Platy",
    "42": "Pleco",
    "43": "Rainbowfish",
    "44": "Puffer",
    "45": _MONSTER_ODDBALL,
    "46": "Apistogramma",
    "47": "Swordtail",
    "48": "Tetra",
    "75": "Plant",
}


def derive_category(sku: str, name: str) -> str:
    block = _CATEGORY_BY_BLOCK.get(sku[:2])
    if block is not None:
        return block
    leading_word = name.split()
    return leading_word[0] if leading_word else _MONSTER_ODDBALL
