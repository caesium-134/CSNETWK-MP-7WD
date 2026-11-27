"""Production, trade prices, affordability (Member 3).
Everything here works only from PUBLIC info (tableaux) plus the buyer's own coins,
so every peer can compute the same answer (RFC 7.9)."""
import re

RAW = {"WOOD", "ORE", "STONE", "CLAY"}
FREE = {"coins": 0, "res": {}}


def parse_production(effect):
    """'Produces 2 Clay' -> (('CLAY',), 2, sellable)
    'Produces 1 of: Ore, Clay (...)' -> (('ORE','CLAY'), 1, sellable)
    Returns None if the effect is not a production effect."""
    sellable = "cannot be sold" not in effect
    m = re.match(r"Produces (\d+) of: ([A-Za-z, ]+?) \(", effect)
    if m:
        opts = tuple(x.strip().upper() for x in m.group(2).split(","))
        return (opts, int(m.group(1)), sellable)
    m = re.match(r"Produces (\d+) ([A-Za-z]+)$", effect)
    if m:
        return ((m.group(2).upper(),), int(m.group(1)), sellable)
    return None


class Tableau:
    """One peer's public board: wonder board, built cards, stages done, coins.
    Coins are only meaningful for your OWN tableau (RFC 12.2)."""

    def __init__(self, seat, board, db, coins=0):
        self.seat, self.board, self.db = seat, board, db
        self.built, self.stages, self.coins = [], 0, coins

    def copy(self):
        t = Tableau(self.seat, self.board, self.db, self.coins)
        t.built, t.stages = list(self.built), self.stages
        return t

    def providers(self, for_sale=False):
        """List of (options, qty, sellable). A provider gives up to qty units of ONE option."""
        out = [((self.board.start_resource.upper(),), 1, True)]
        for name in self.built:
            c = self.db[name]
            if for_sale and c.color not in ("Brown", "Grey"):
                continue          # neighbors can only buy brown/grey production
            p = parse_production(c.effect)
            if p:
                out.append(p)
        for st in self.board.stages[:self.stages]:
            p = parse_production(st.effect)
            if p:
                out.append(p)
        return [p for p in out if p[2]] if for_sale else out

    def max_available(self, resource):
        """How many units of `resource` this peer could sell (for RESOURCE_OFFER)."""
        return sum(q for opts, q, _ in self.providers(for_sale=True) if resource in opts)

    def count_color(self, color):
        return sum(1 for n in self.built if self.db[n].color.lower() == color.lower())


def unit_price(buyer, resource, direction):
    """Price per unit the BUYER pays. direction = 'LEFT' or 'RIGHT' neighbor (RFC 7.9).
    Depends only on the buyer's own board."""
    names = set(buyer.built)
    raw = resource in RAW
    if raw and direction == "LEFT" and "West Trading Post" in names:
        return 1
    if raw and direction == "RIGHT" and "East Trading Post" in names:
        return 1
    if not raw and "Marketplace" in names:
        return 1
    if raw and buyer.board.name == "Olympia" and buyer.board.side == "B" and buyer.stages >= 1:
        return 1
    return 2


def plan_payment(cost, me, left, right, own_only=False):
    """Cheapest way for `me` to pay `cost`, or None if impossible.
    Returns {'coins': bank coins, 'resources_bought': [{from_seat, resource, qty}], 'spend': total coins}.
    Own production is free; neighbors' sellable production costs unit_price per unit."""
    need = dict(cost["res"])
    if not need:
        return {"coins": cost["coins"], "resources_bought": [], "spend": cost["coins"]} \
            if me.coins >= cost["coins"] else None

    order = sorted(need)
    provs = [(o, q, None, None) for o, q, _ in me.providers()]
    for tab, d in (() if own_only else ((left, "LEFT"), (right, "RIGHT"))):
        provs += [(o, q, tab.seat, d) for o, q, _ in tab.providers(for_sale=True)]

    memo = {}

    def best(i, rem):
        if not any(rem):
            return (0, [])
        if i == len(provs):
            return None
        if (i, rem) in memo:
            return memo[(i, rem)]
        opts, qty, seat, d = provs[i]
        result = best(i + 1, rem)                       # skip this provider
        for r in opts:
            if r not in need:
                continue
            idx = order.index(r)
            for take in range(1, min(qty, rem[idx]) + 1):
                new = list(rem)
                new[idx] -= take
                sub = best(i + 1, tuple(new))
                if sub is None:
                    continue
                price = 0 if seat is None else take * unit_price(me, r, d)
                bought = sub[1] + ([(seat, r, take)] if seat is not None else [])
                cand = (sub[0] + price, bought)
                if result is None or cand[0] < result[0]:
                    result = cand
        memo[(i, rem)] = result
        return result

    res = best(0, tuple(need[r] for r in order))
    if res is None:
        return None
    spend = cost["coins"] + res[0]
    if spend > me.coins:
        return None
    merged = {}
    for seat, r, q in res[1]:
        merged[(seat, r)] = merged.get((seat, r), 0) + q
    bought = [{"from_seat": s, "resource": r, "qty": q} for (s, r), q in merged.items()]
    return {"coins": cost["coins"], "resources_bought": bought, "spend": spend}


def card_plan(card, me, left, right):
    """Plan for BUILDING `card`: chain = free, printed Free = free, else pay."""
    if card.name in me.built:
        return None                                     # can't build the same card twice
    if card.chain_from and any(n in me.built for n in card.chain_from):
        return {"coins": 0, "resources_bought": [], "spend": 0}
    return plan_payment(card.cost, me, left, right)


def legal_actions(hand, me, left, right, db):
    """Everything this peer may commit to this turn: [(card_name, action, plan)].
    Checked BEFORE CARD_COMMIT because an unaffordable BUILD is a violation (RFC 7.9)."""
    out = []
    for name in hand:
        p = card_plan(db[name], me, left, right)
        if p is not None:
            out.append((name, "BUILD", p))
        if me.stages < len(me.board.stages):
            sp = plan_payment(me.board.stages[me.stages].cost, me, left, right)
            if sp is not None:
                out.append((name, "WONDER_STAGE", sp))
        out.append((name, "DISCARD", None))
    return out


def immediate_coins(effect, me, left, right, db):
    """Coins gained the moment a card or wonder stage is completed.
    `me` must already include the new card/stage (Lighthouse counts itself)."""
    total = 0
    m = re.search(r"Gain (\d+) coins? immediately", effect)
    if m:
        total += int(m.group(1))
    m = re.search(r"Gain (\d+) coins?(?: and \d+ Victory Points?)? for each (built Wonder stage|(\w+) card)", effect)
    if m:
        boards = [me]
        if "left neighbor" in effect:
            boards.append(left)
        if "right neighbor" in effect:
            boards.append(right)
        count = sum(b.stages if m.group(2).startswith("built Wonder") else b.count_color(m.group(3))
                    for b in boards)
        total += int(m.group(1)) * count
    return total
