"""Draft Turn state machine for ONE peer (Zed), RFC 7.8 / 7.9.
It never touches a socket: it takes incoming payloads and returns outgoing ones (plain dicts
matching RFC 8.3), so Mirai's network layer can plug in later.

One turn, in order:
  begin_turn()          -> CARD_COMMIT payload            (broadcast)
  on_card_commit()      <- everyone's commits
  poll_barrier()        call every ~100 ms; returns None while waiting, then the set of
                        seats that missed the barrier (empty set = everyone committed).
                        This is where Nori's on_turn_barrier(missing) is called.
  make_reveal()         -> CARD_REVEAL payload            (broadcast, after the barrier)
  on_card_reveal()      <- everyone's reveals, verified against their commitments
  prepare_resolve()     -> list of RESOURCE_BUYs Natt's trading code must perform
  commit_resolve()      -> ACTION_ANNOUNCE payload        (broadcast)
     (if a trade times out: on_trade_timeout(seat) first, then commit_resolve() = DISCARD)
  on_action_announce()  <- everyone's announcements (updates tableaux + state.log)
  make_hand_pass()      -> (destination seat, HAND_PASS payload)
  on_hand_pass()        <- your next hand
After turn 6:  end_age() -> calls Nori's start_age_end(age), returns the AGE_END payload.
"""
import random
import time
from draft import make_nonce, make_commitment, verify_reveal, pass_direction, chain_ok
from economy import (Tableau, legal_actions, plan_payment, card_plan,
                     immediate_coins, unit_price)
from state import GameState, NullHooks

T_TURN_TIMEOUT = 8.0      # seconds, RFC section 10


def default_chooser(peer, options):
    """Random legal move, weighted toward building. Replace with your own UI/strategy."""
    weights = {"BUILD": 4, "WONDER_STAGE": 2, "DISCARD": 1}
    pick = peer.rng.choices(options, weights=[weights[o[1]] for o in options])[0]
    return pick[0], pick[1]


class DraftPeer:
    def __init__(self, seat, k, boards, db, chooser=None, rng=None,
                 state=None, hooks=None, clock=time.monotonic):
        self.seat, self.k, self.db = seat, k, db
        self.state = state or GameState(seat, list(range(k)))
        self.hooks = hooks or NullHooks()
        self.clock = clock
        self.tab = {s: Tableau(s, boards[s], db, coins=3 if s == seat else 0)
                    for s in self.state.ring}
        self.chooser = chooser or default_chooser
        self.rng = rng or random.Random()
        self.hand, self.age, self.turn = [], 0, 0
        self.commits, self.reveals, self.log = {}, {}, []
        self.barrier_start, self.barrier_closed = None, True
        self._pending = None
        self._refresh()

    # neighbors always come from the shared state, so disconnected seats are skipped (RFC 11.3)
    @property
    def left(self):
        return self.state.left_of(self.seat)

    @property
    def right(self):
        return self.state.right_of(self.seat)

    @property
    def me(self):
        return self.tab[self.seat]

    def _refresh(self):
        """Keep state.log[seat] (shields, science, VP...) in sync with the public tableaux."""
        self.state.refresh(self.tab, self.db)
        self.state.log[self.seat].coins = self.me.coins

    # ---- 1. COMMIT ----
    def begin_turn(self, age, turn):
        self.age, self.turn = age, turn
        # Snapshot of the public state at turn start: every peer decides and prices from the
        # same view even if announcements arrive in different orders.
        self.snap = {s: t.copy() for s, t in self.tab.items()}
        options = legal_actions(self.hand, self.snap[self.seat], self.snap[self.left],
                                self.snap[self.right], self.db)
        self.choice = self.chooser(self, options)          # (card_id, action)
        self.nonce = make_nonce()
        self.commits, self.reveals = {}, {}
        self.barrier_start, self.barrier_closed = self.clock(), False
        return {"age": age, "turn": turn,
                "commitment": make_commitment(self.choice[0], self.choice[1], self.nonce)}

    def on_card_commit(self, sender, p):
        if (p["age"], p["turn"]) != (self.age, self.turn) or sender not in self.state.live_peers:
            return "OUT_OF_PHASE"
        self.commits[sender] = p["commitment"]
        return None

    def missing_commits(self):
        return self.state.live_peers - set(self.commits)

    def commits_complete(self):
        return not self.missing_commits()

    def poll_barrier(self, now=None):
        """The CARD_COMMIT barrier (RFC 7.8 step 2). Call every ~100 ms.
        Returns None while still waiting. Once everyone committed OR T_TURN_TIMEOUT passed it
        returns the set of seats that missed it (maybe empty), exactly once, after telling
        Nori's code via hooks.on_turn_barrier(missing)."""
        if self.barrier_closed:
            return None
        now = self.clock() if now is None else now
        missing = self.missing_commits()
        if missing and now - self.barrier_start < T_TURN_TIMEOUT:
            return None
        self.barrier_closed = True
        self.hooks.on_turn_barrier(set(missing))
        return set(missing)

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

    def reveals_complete(self):
        return (self.state.live_peers & set(self.commits)) <= set(self.reveals)

    # ---- 3. RESOLVE (private), split in two so a failed trade can be rolled back ----
    def prepare_resolve(self):
        """Works out what to pay. No side effects. Returns [(seller_seat, RESOURCE_BUY payload)].
        Natt's trading code does RESOURCE_QUERY / OFFER / BUY for each one."""
        card, action = self.choice
        me, L, R = (self.snap[self.seat], self.snap[self.left], self.snap[self.right])
        plan = None
        if action == "BUILD":
            plan = card_plan(self.db[card], me, L, R)
            if plan is None:                       # unaffordable now: fall back to DISCARD (7.9)
                action = "DISCARD"
        elif action == "WONDER_STAGE":
            plan = plan_payment(me.board.stages[me.stages].cost, me, L, R)
        self._pending = (card, action, plan)
        buys = []
        if plan:
            for b in plan["resources_bought"]:
                d = "LEFT" if b["from_seat"] == self.left else "RIGHT"
                coins = unit_price(me, b["resource"], d) * b["qty"]
                buys.append((b["from_seat"], {"resource": b["resource"], "qty": b["qty"],
                                              "coins": coins}))
        return buys

    def on_trade_timeout(self, neighbor_seat):
        """No neighbor supplied the resource in time (RFC 7.9): tell Nori's consensus code,
        then turn this turn into a DISCARD. Call commit_resolve() afterwards."""
        self.hooks.initiate_timeout_consensus(neighbor_seat)
        card, _, _ = self._pending
        self._pending = (card, "DISCARD", None)

    def commit_resolve(self):
        """Applies the turn (hand, coins, immediate gains). Returns the ACTION_ANNOUNCE payload."""
        card, action, plan = self._pending
        me, L, R = (self.snap[self.seat], self.snap[self.left], self.snap[self.right])
        self.hand.remove(card)
        base = {"action": action, "age": self.age, "turn": self.turn}
        if plan:
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
        self._refresh()
        return base

    def resolve(self):
        """Convenience for offline tests: prepare + commit. Returns (announce, buys)."""
        buys = self.prepare_resolve()
        return self.commit_resolve(), buys

    # Seller side of trading (RFC 7.9): the SELLER computes the price from the BUYER's public board.
    def offer_for(self, buyer_seat, resource):
        d = "LEFT" if self.state.right_of(self.seat) == buyer_seat else "RIGHT"
        # buyer's LEFT neighbor is me  <=>  buyer is my RIGHT neighbor
        return {"resource": resource,
                "qty_available": self.snap[self.seat].max_available(resource),
                "price_per_unit": unit_price(self.snap[buyer_seat], resource, d)}

    def on_resource_buy(self, p):
        self.me.coins += p["coins"]
        self._refresh()

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
        self._refresh()
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

    # ---- 6. END OF AGE ----
    def end_age(self):
        """Call after turn 6 of each Age. Tells Nori's scoring code, returns the AGE_END payload."""
        self._refresh()
        self.hooks.start_age_end(self.age)
        return {"age": self.age, "shields": self.state.log[self.seat].shields}
