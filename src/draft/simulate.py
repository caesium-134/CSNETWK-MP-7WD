"""Plays full games offline (no sockets) to test the draft engine. Run: python3 simulate.py"""
import random
from cards import load_cards, load_wonders
from draft import build_age_deck, deal_hands
from state import GameState
from turn import DraftPeer


class Recorder:
    """Stands in for Nori's managers and records every hook call."""
    def __init__(self):
        self.age_ends, self.barriers, self.timeouts = [], [], []

    def start_age_end(self, age): self.age_ends.append(age)
    def on_turn_barrier(self, missing): self.barriers.append(set(missing))
    def initiate_timeout_consensus(self, seat): self.timeouts.append(seat)


def run(k, seed, verbose=False, drop=None):
    """drop=(age, turn, seat): that seat disconnects right before that turn begins."""
    db, wonders = load_cards(), load_wonders()
    rng = random.Random(seed)
    names = rng.sample(sorted({n for n, _ in wonders}), k)
    boards = [wonders[(n, "A")] for n in names]
    hooks = [Recorder() for _ in range(k)]
    peers = [DraftPeer(s, k, boards, db, rng=random.Random(seed * 100 + s),
                       state=GameState(s, range(k)), hooks=hooks[s]) for s in range(k)]
    guilds = rng.sample([n for n, c in db.items() if c.color == "Purple"], k + 2)

    def live():
        return [p for p in peers if p.seat in p.state.live_peers]

    for age in (1, 2, 3):
        deck = build_age_deck(db, age, k, guilds if age == 3 else None, rng)
        for p, h in zip(peers, deal_hands(deck, k)):
            p.hand = h
        for turn in range(1, 7):
            if drop and drop[:2] == (age, turn):          # Nori's code removes it from live_peers
                for p in peers:
                    p.state.live_peers.discard(drop[2])
            lp = live()
            for p in lp:
                p._out = p.begin_turn(age, turn)
            for p in lp:                                  # broadcast commits
                for q in lp:
                    assert q.on_card_commit(p.seat, p._out) is None
            for q in lp:                                  # barrier closes with nobody missing
                assert q.poll_barrier() == set()
                assert q.poll_barrier() is None           # reported exactly once
            for p in lp:                                  # broadcast reveals, everybody verifies
                r = p.make_reveal()
                for q in lp:
                    assert q.on_card_reveal(p.seat, r) is None
            assert all(q.reveals_complete() for q in lp)
            anns = []
            for p in lp:                                  # resolve + trade
                ann, buys = p.resolve()
                for seller, buy in buys:
                    offer = peers[seller].offer_for(p.seat, buy["resource"])
                    assert offer["price_per_unit"] * buy["qty"] == buy["coins"], "price mismatch"
                    assert offer["qty_available"] >= buy["qty"]
                    peers[seller].on_resource_buy(buy)
                anns.append((p.seat, ann))
            for sender, ann in anns:                      # broadcast announcements
                for q in lp:
                    assert q.on_action_announce(sender, ann) is None, ann
            passes = [(p.seat,) + p.make_hand_pass() for p in lp]
            for src, dest, payload in passes:
                if turn == 6:
                    assert payload["cards"] == []
                assert peers[dest].on_hand_pass(src, payload) is None
        for p in live():
            assert p.end_age()["age"] == age
    # every live peer must hold the same public log AND the same computed stats
    lp = live()
    for p in lp[1:]:
        for s in range(k):
            assert p.tab[s].built == lp[0].tab[s].built and p.tab[s].stages == lp[0].tab[s].stages
            a, b = p.state.log[s], lp[0].state.log[s]
            for f in ("shields", "t", "c", "g", "w", "wonder", "civilian", "commercial", "guilds"):
                assert getattr(a, f) == getattr(b, f), (s, f)
    for p in lp:                                          # hooks were called as Nori expects
        h = hooks[p.seat]
        assert h.age_ends == [1, 2, 3] and len(h.barriers) == 18 and not any(h.barriers)
    if verbose:
        for p in lp:
            r = p.state.log[p.seat]
            print(f"seat {p.seat} {p.me.board.name:<13} built={len(p.me.built):>2} stages={p.me.stages} "
                  f"coins={p.me.coins:>2} shields={r.shields} sci={r.t}/{r.c}/{r.g}+{r.w}w "
                  f"vp(w/c/com/g)={r.wonder}/{r.civilian}/{r.commercial}/{r.guilds}")
    return peers


if __name__ == "__main__":
    for k in range(3, 8):
        for seed in range(1, 21):
            run(k, seed)
        print(f"{k} players: 20 full games OK")
    for seed in range(1, 11):
        run(5, seed, drop=(2, 3, 1))                      # seat 1 disconnects in Age II turn 3
    print("5 players, one disconnects mid-game: 10 games OK")
    print("--- sample game (5 players) ---")
    run(5, 42, verbose=True)
