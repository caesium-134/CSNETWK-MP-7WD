"""Tests for the shared state, stats, barrier and timeout hooks. Run: python3 test_state.py"""
import random
from cards import load_cards, load_wonders
from economy import Tableau
from state import GameState
from stats import tableau_stats, science_score
from turn import DraftPeer, T_TURN_TIMEOUT
from simulate import Recorder

db, w = load_cards(), load_wonders()

# ---- neighbors skip disconnected seats (RFC 11.3) ----
s = GameState(0, range(5))
assert (s.left_of(0), s.right_of(0)) == (4, 1)
s.live_peers.discard(1)
assert s.right_of(0) == 2 and s.left_of(2) == 0
s.live_peers -= {2, 3}
assert not s.have_quorum()
print("neighbor skipping + quorum: OK")

# ---- stats ----
me = Tableau(0, w[("Rhodos", "B")], db); L = Tableau(1, w[("Gizah", "A")], db); R = Tableau(2, w[("Gizah", "A")], db)
me.built = ["Barracks", "Archery Range", "Arsenal", "Apothecary", "Scriptorium", "Workshop",
            "Scientists Guild", "Altar", "Baths", "Workers Guild", "Haven", "Lumber Yard"]
me.stages = 2
L.built, R.built = ["Lumber Yard", "Stone Pit"], ["Clay Pit", "Loom"]
st = tableau_stats(me, L, R, db)
assert st == dict(shields=8, t=1, c=1, g=1, w=1, wonder=7, civilian=5, commercial=1, guilds=3), st
assert science_score(1, 1, 1, 1) == 13 and science_score(2, 0, 0, 0) == 4 and science_score(2, 2, 2) == 26
# Strategists Guild reads neighbors' defeat tokens
me.built = ["Strategists Guild"]
assert tableau_stats(me, L, R, db, defeats_of=lambda seat: 2)["guilds"] == 4
print("stats (shields, science, VP, guilds): OK")

# ---- barrier timeout calls Nori's hook with the missing seat ----
now = [0.0]
rec = Recorder()
boards = [w[("Gizah", "A")], w[("Babylon", "A")], w[("Rhodos", "A")]]
peers = [DraftPeer(i, 3, boards, db, rng=random.Random(i), clock=lambda: now[0],
                   hooks=rec if i == 0 else None) for i in range(3)]
for p in peers:
    p.hand = ["Stone Pit", "Loom", "Tavern", "Altar", "Barracks", "Baths", "Theater"]
commits = [p.begin_turn(1, 1) for p in peers]
peers[0].on_card_commit(0, commits[0]); peers[0].on_card_commit(1, commits[1])   # seat 2 never commits
assert peers[0].poll_barrier() is None                    # still waiting
now[0] = T_TURN_TIMEOUT + 0.1
assert peers[0].poll_barrier() == {2} and rec.barriers == [{2}]
assert peers[0].poll_barrier() is None
print("commit barrier timeout -> on_turn_barrier({2}): OK")

# ---- trade timeout: hook fires, turn becomes a DISCARD (+3 coins) ----
p = peers[0]
p.choice = ("Stone Pit", "BUILD")
p.snap = {s_: t.copy() for s_, t in p.tab.items()}
p.prepare_resolve()
coins_before = p.me.coins
p.on_trade_timeout(1)
ann = p.commit_resolve()
assert rec.timeouts == [1] and ann["action"] == "DISCARD" and "card_id" not in ann
assert p.me.coins == coins_before + 3 and "Stone Pit" not in p.hand
print("trade timeout -> initiate_timeout_consensus(1) + DISCARD: OK")
