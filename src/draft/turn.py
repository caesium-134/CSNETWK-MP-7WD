"""Draft Turn state machine for ONE peer (Member 3), RFC 7.8 / 7.9.
It never touches a socket: it takes incoming payloads and returns outgoing payloads
(plain dicts matching RFC 8.3), so you can plug Member 1's network in later.

One turn, in order:
  begin_turn()        -> CARD_COMMIT payload        (broadcast)
  on_card_commit()    <- everyone's commits
  make_reveal()       -> CARD_REVEAL payload        (broadcast, after ALL commits are in)
  on_card_reveal()    <- everyone's reveals, verified against the commitments
  resolve()           -> ACTION_ANNOUNCE payload + list of RESOURCE_BUYs to send
  on_action_announce()<- everyone's announcements (updates the public log)
  make_hand_pass()    -> (destination seat, HAND_PASS payload)
  on_hand_pass()      <- your next hand from the other neighbor
"""
import random
from draft import make_nonce, make_commitment, verify_reveal, pass_direction, chain_ok
from economy import (Tableau, legal_actions, plan_payment, card_plan,
                     immediate_coins, unit_price)


def default_chooser(peer, options):
    """Random legal move, weighted toward building. Replace with your own UI/strategy."""
    weights = {"BUILD": 4, "WONDER_STAGE": 2, "DISCARD": 1}
    pick = peer.rng.choices(options, weights=[weights[o[1]] for o in options])[0]
    return pick[0], pick[1]


class DraftPeer:
    def __init__(self, seat, k, boards, db, chooser=None, rng=None):
        self.seat, self.k, self.db = seat, k, db
        self.left, self.right = (seat - 1) % k, (seat + 1) % k     # RFC 7.6 seating
        self.tab = {s: Tableau(s, boards[s], db, coins=3 if s == seat else 0) for s in range(k)}
        self.chooser = chooser or default_chooser
        self.rng = rng or random.Random()
        self.hand, self.age, self.turn = [], 0, 0
        self.commits, self.reveals, self.log = {}, {}, []

    @property
    def me(self):
        return self.tab[self.seat]

    # ---- 1. COMMIT ----
    def begin_turn(self, age, turn):
        self.age, self.turn = age, turn
        # Snapshot of the public state at turn start: all peers decide and price
        # from the same view even if announcements arrive in different orders.
        self.snap = {s: t.copy() for s, t in self.tab.items()}
        options = legal_actions(self.hand, self.snap[self.seat], self.snap[self.left],
                                self.snap[self.right], self.db)
        self.choice = self.chooser(self, options)          # (card_id, action)
        self.nonce = make_nonce()
        self.commits, self.reveals = {}, {}
        return {"age": age, "turn": turn,
                "commitment": make_commitment(self.choice[0], self.choice[1], self.nonce)}

    def on_card_commit(self, sender, p):
        if (p["age"], p["turn"]) != (self.age, self.turn):
            return "OUT_OF_PHASE"
        self.commits[sender] = p["commitment"]
        return None

    def commits_complete(self, live=None):
        return len(self.commits) == (live or self.k)

    # ---- 2. REVEAL ----
    def make_reveal(self):
        card, action = self.choice
        p = {"age": self.age, "turn": self.turn, "action": action, "nonce": self.nonce}
        if action == "BUILD":
            p["card_id"] = card             # hidden for WONDER_STAGE / DISCARD (RFC 7.8 step 3)
        return p

    def on_card_reveal(self, sender, p):
        if sender not in self.commits:
            return "OUT_OF_PHASE"
        if not verify_reveal(self.commits[sender], p["action"], p["nonce"], p.get("card_id")):
            return "COMMITMENT_MISMATCH"
        self.reveals[sender] = p
        return None

    # ---- 3. RESOLVE (private) ----
    def resolve(self):
        """Returns (ACTION_ANNOUNCE payload, [(seller_seat, RESOURCE_BUY payload), ...])."""
        card, action = self.choice
        me, L, R = (self.snap[self.seat], self.snap[self.left], self.snap[self.right])
        self.hand.remove(card)
        base = {"action": action, "age": self.age, "turn": self.turn}
        plan = None
        if action == "BUILD":
            plan = card_plan(self.db[card], me, L, R)
            if plan is None:                      # unaffordable now: RFC 7.9 says fall back to DISCARD
                action, base["action"] = "DISCARD", "DISCARD"
        elif action == "WONDER_STAGE":
            plan = plan_payment(me.board.stages[me.stages].cost, me, L, R)

        buys, gain = [], 0
        if plan:
            for b in plan["resources_bought"]:
                d = "LEFT" if b["from_seat"] == self.left else "RIGHT"
                coins = unit_price(me, b["resource"], d) * b["qty"]
                buys.append((b["from_seat"], {"resource": b["resource"], "qty": b["qty"], "coins": coins}))
            self.me.coins -= plan["spend"]
        if action == "BUILD":
            after = me.copy()
            after.built.append(card)
            gain = immediate_coins(self.db[card].effect, after, L, R, self.db)
            base.update(card_id=card, cost_paid={"coins": plan["coins"],
                                                 "resources_bought": plan["resources_bought"]})
        elif action == "WONDER_STAGE":
            after = me.copy()
            after.stages += 1
            gain = immediate_coins(me.board.stages[me.stages].effect, after, L, R, self.db)
            base["stage_index"] = me.stages + 1
        else:
            gain = 3                                        # discard = +3 coins
        self.me.coins += gain
        return base, buys

    # Seller side of trading (RFC 7.9): the SELLER computes the price from the BUYER's public board.
    def offer_for(self, buyer_seat, resource):
        d = "LEFT" if self.tab[buyer_seat].seat == (self.seat + 1) % self.k else "RIGHT"
        # buyer's left neighbor is me  <=>  buyer = my right neighbor
        return {"resource": resource,
                "qty_available": self.snap[self.seat].max_available(resource),
                "price_per_unit": unit_price(self.snap[buyer_seat], resource, d)}

    def on_resource_buy(self, p):
        self.me.coins += p["coins"]

    # ---- 4. ANNOUNCE (public log) ----
    def on_action_announce(self, sender, p):
        """Update the public log; flag illegal zero-cost builds (RFC 7.8 step 5)."""
        t = self.tab[sender]
        if p["action"] == "BUILD":
            card = self.db[p["card_id"]]
            cp = p["cost_paid"]
            zero = cp["coins"] == 0 and not cp["resources_bought"]
            printed_free = not card.cost["coins"] and not card.cost["res"]
            # NOTE: the RFC text says every zero-cost BUILD needs chain_from, but a card can ALSO be
            # zero-cost_paid because (a) its printed cost is "Free" (Lumber Yard, Loom...) or (b) the
            # builder paid it entirely from its OWN production. Put this deviation in your README.
            if zero and not printed_free and not chain_ok(card, set(t.built)):
                rich = t.copy()
                rich.coins = 10 ** 6
                own_covers = card.cost["coins"] == 0 and \
                    plan_payment(card.cost, rich, None, None, own_only=True) is not None
                if not own_covers:          # also not payable from its own production -> cheating
                    return "PROTOCOL_VIOLATION"
            t.built.append(p["card_id"])
        elif p["action"] == "WONDER_STAGE":
            t.stages += 1
        self.log.append((sender, p))
        return None

    # ---- 5. PASS ----
    def make_hand_pass(self):
        dest = self.left if pass_direction(self.age) == "LEFT" else self.right
        cards = list(self.hand) if self.turn < 6 else []    # turn 6: last card is silently set aside
        self.hand = []
        return dest, {"age": self.age, "turn": self.turn, "cards": cards}

    def on_hand_pass(self, sender, p):
        expect = self.right if pass_direction(self.age) == "LEFT" else self.left
        if sender != expect or (p["age"], p["turn"]) != (self.age, self.turn):
            return "OUT_OF_PHASE"
        self.hand = list(p["cards"])
        return None
