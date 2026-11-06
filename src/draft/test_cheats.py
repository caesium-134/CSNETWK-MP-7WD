"""Checks that cheating is caught. Run: python3 test_cheats.py"""
import random
from cards import load_cards, load_wonders
from turn import DraftPeer

db, w = load_cards(), load_wonders()
boards = [w[("Gizah", "A")], w[("Babylon", "A")], w[("Rhodos", "A")]]
a, b, c = (DraftPeer(s, 3, boards, db, rng=random.Random(s)) for s in range(3))

# 1) reveal that doesn't match the commitment -> COMMITMENT_MISMATCH
a.hand = ["Stone Pit", "Loom", "Tavern", "Altar", "Barracks", "Baths", "Theater"]
a.choice = ("Stone Pit", "BUILD")
a.age, a.turn, a.nonce = 1, 1, "ab" * 32
import draft
commit = {"age": 1, "turn": 1, "commitment": draft.make_commitment("Stone Pit", "BUILD", a.nonce)}
b.age, b.turn = 1, 1
b.on_card_commit(0, commit)
lie = {"age": 1, "turn": 1, "action": "BUILD", "nonce": a.nonce, "card_id": "Lumber Yard"}
print("changed card after commit ->", b.on_card_reveal(0, lie))

# 2) free build with no chain and no resources -> PROTOCOL_VIOLATION
fake = {"action": "BUILD", "age": 1, "turn": 2, "card_id": "Aqueduct",
        "cost_paid": {"coins": 0, "resources_bought": []}}
print("fake free Aqueduct ->", b.on_action_announce(0, fake))

# 3) legit free card and legit chain are accepted
ok1 = {"action": "BUILD", "age": 1, "turn": 3, "card_id": "Lumber Yard",
       "cost_paid": {"coins": 0, "resources_bought": []}}
print("Lumber Yard (printed Free) ->", b.on_action_announce(0, ok1))
