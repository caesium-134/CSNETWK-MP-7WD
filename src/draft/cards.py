"""Card database loader (Member 3). Reads Card_Database_7WD.xlsx into plain Python objects."""
import os
import re
from dataclasses import dataclass, field
import openpyxl

RES_NAMES = {"Wood", "Ore", "Clay", "Stone", "Glass", "Papyrus", "Textile"}


def parse_cost(text):
    """'2 Wood, 1 Glass' -> {'coins': 0, 'res': {'WOOD': 2, 'GLASS': 1}}.
    'Free' -> all zero. '1 coin' -> coins=1."""
    cost = {"coins": 0, "res": {}}
    text = str(text).strip()
    if text.lower() == "free":
        return cost
    for part in text.split(","):
        qty, name = part.strip().split(" ", 1)
        if name.lower().startswith("coin"):
            cost["coins"] += int(qty)
        else:
            cost["res"][name.upper()] = int(qty)
    return cost


def _chain(text):
    text = str(text or "").strip()
    return None if text in ("", "—", "None") else [t.strip() for t in text.split(",")]


@dataclass
class Card:
    name: str            # this is the card_id used on the wire (copy byte for byte)
    color: str
    age: int             # first Age the card appears in (guilds are 3)
    cost: dict
    effect: str          # raw text, parse what you need from it
    chain_from: list     # None or list of card names
    chain_to: list
    # deck copies per Age and player count: {age: {3: n, 4: n, ...}}
    # (Glassworks, Loom, Press appear in both Age I and Age II, same card_id)
    counts: dict = field(default_factory=dict)


@dataclass
class WonderStage:
    index: int
    cost: dict
    effect: str


@dataclass
class WonderBoard:
    name: str
    side: str
    start_resource: str
    stages: list


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Card_Database_7WD.xlsx")


def load_cards(path=DB_PATH):
    wb = openpyxl.load_workbook(path, data_only=True)
    cards = {}
    for age, sheet in [(1, "Age I Cards"), (2, "Age II Cards"), (3, "Age III Cards")]:
        for r in wb[sheet].iter_rows(min_row=5, values_only=True):
            if not r[0]:
                continue
            counts = {p: int(r[6 + i] or 0) for i, p in enumerate([3, 4, 5, 6, 7])}
            if r[0] in cards:   # same card, listed again in a later Age
                cards[r[0]].counts[age] = counts
            else:
                cards[r[0]] = Card(r[0], r[1], age, parse_cost(r[2]), r[3],
                                   _chain(r[4]), _chain(r[5]), {age: counts})
    for r in wb["Guild Cards (Age III)"].iter_rows(min_row=5, values_only=True):
        if r[0] and r[1] == "Purple":
            cards[r[0]] = Card(r[0], "Purple", 3, parse_cost(r[2]), r[3], None, None, {})
    return cards


def load_wonders(path=DB_PATH):
    wb = openpyxl.load_workbook(path, data_only=True)
    boards = {}
    for r in wb["Wonder Boards"].iter_rows(min_row=5, values_only=True):
        if not r[0] or r[3] is None:
            continue
        key = (r[0], r[1])
        if key not in boards:
            boards[key] = WonderBoard(r[0], r[1], r[2], [])
        boards[key].stages.append(WonderStage(int(r[3]), parse_cost(r[4]), r[5]))
    return boards


if __name__ == "__main__":
    c = load_cards()
    w = load_wonders()
    print(len(c), "cards,", len(w), "board sides")
    print(c["Arena"])
