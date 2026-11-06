"""Plays full games offline (no sockets) to test the draft engine. Run: python3 simulate.py"""
import random
from cards import load_cards, load_wonders
from draft import build_age_deck, deal_hands
from turn import DraftPeer


def run(k, seed, verbose=False):
    db, wonders = load_cards(), load_wonders()
    rng = random.Random(seed)
    names = rng.sample(sorted({n for n, _ in wonders}), k)
    boards = [wonders[(n, "A")] for n in names]
    peers = [DraftPeer(s, k, boards, db, rng=random.Random(seed * 100 + s)) for s in range(k)]
    guilds = rng.sample([n for n, c in db.items() if c.color == "Purple"], k + 2)
    played = [0] * k
    for age in (1, 2, 3):
        deck = build_age_deck(db, age, k, guilds if age == 3 else None, rng)
        for p, h in zip(peers, deal_hands(deck, k)):
            p.hand = h
        for turn in range(1, 7):
            for p in peers:
                p._out = p.begin_turn(age, turn)
            for p in peers:                         # broadcast commits
                for q in peers:
                    assert q.on_card_commit(p.seat, p._out) is None
            assert all(q.commits_complete() for q in peers)
            for p in peers:                         # broadcast reveals, everybody verifies
                r = p.make_reveal()
                for q in peers:
                    assert q.on_card_reveal(p.seat, r) is None
            anns = []
            for p in peers:                         # resolve + trade
                ann, buys = p.resolve()
                for seller, buy in buys:
                    offer = peers[seller].offer_for(p.seat, buy["resource"])
                    assert offer["price_per_unit"] * buy["qty"] == buy["coins"], "price mismatch"
                    assert offer["qty_available"] >= buy["qty"]
                    peers[seller].on_resource_buy(buy)
                anns.append((p.seat, ann))
            for sender, ann in anns:                # broadcast announcements
                for q in peers:
                    assert q.on_action_announce(sender, ann) is None, ann
            passes = [(p.seat,) + p.make_hand_pass() for p in peers]
            for src, dest, payload in passes:
                if turn == 6:
                    assert payload["cards"] == []
                assert peers[dest].on_hand_pass(src, payload) is None
    # every peer must hold the same public log
    for p in peers[1:]:
        for s in range(k):
            assert p.tab[s].built == peers[0].tab[s].built and p.tab[s].stages == peers[0].tab[s].stages
    if verbose:
        for p in peers:
            t = p.me
            print(f"seat {p.seat} {t.board.name:<13} built={len(t.built):>2} stages={t.stages} coins={t.coins}")
    return peers


if __name__ == "__main__":
    for k in range(3, 8):
        for seed in range(1, 21):
            run(k, seed)
        print(f"{k} players: 20 full games OK")
    print("--- sample game (5 players) ---")
    run(5, 42, verbose=True)
