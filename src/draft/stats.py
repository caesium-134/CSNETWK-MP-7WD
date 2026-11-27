"""Turns a seat's built cards + wonder stages into the numbers the scoring code needs (Zed).
Pure functions: no network, no state. Everything is recomputed from the PUBLIC tableaux,
so every peer gets the same numbers (RFC 7.10 / 7.11)."""
import re

_VP = re.compile(r"(\d+) Victory Points? \(fixed")
_SHIELD = re.compile(r"(\d+) Military Shields?")
_SYMBOL = re.compile(r"Science symbol: (Tablet|Compass|Gear|player's choice)")
# 'Gain [N coins and] M Victory Point(s) for each <subject> on your ...'  (yellow + purple)
_FOR_EACH = re.compile(r"Gain (?:\d+ coins? and )?(\d+) Victory Points? for each (.+?) on your")


def science_score(t, c, g, w=0):
    """(t*t)+(c*c)+(g*g)+7*min(t,c,g), with the w wildcards assigned to maximise the total
    (RFC 7.11). Tries every assignment, the space is tiny."""
    best = 0
    for a in range(w + 1):
        for b in range(w - a + 1):
            tt, cc, gg = t + a, c + b, g + (w - a - b)
            best = max(best, tt * tt + cc * cc + gg * gg + 7 * min(tt, cc, gg))
    return best


def _count(subject, board, defeats):
    """How many of `subject` a board has. subject is e.g. 'Grey card', 'Brown, Grey, Purple card',
    'built Wonder stage', 'Military Defeat token'."""
    if subject.startswith("built Wonder stage"):
        return board.stages
    if subject.startswith("Military Defeat token"):
        return defeats
    colors = [c.strip() for c in subject.replace(" card", "").split(",")]
    return sum(board.count_color(c) for c in colors)


def _for_each_vp(effect, me, left, right, defeats_of):
    m = _FOR_EACH.search(effect)
    if not m:
        return 0
    boards = []
    if "left neighbor" in effect:
        boards.append(left)
    if "own board" in effect:
        boards.append(me)
    if "right neighbor" in effect:
        boards.append(right)
    return int(m.group(1)) * sum(_count(m.group(2), b, defeats_of(b.seat)) for b in boards)


def _symbols(effect, out):
    m = _SYMBOL.search(effect)
    if m:
        key = {"Tablet": "t", "Compass": "c", "Gear": "g"}.get(m.group(1), "w")
        out[key] += 1


def tableau_stats(me, left, right, db, defeats_of=lambda seat: 0, copied_guild=None):
    """Stats for ONE seat. me/left/right are Tableau objects (left/right = its live neighbors).
    defeats_of(seat) = number of -1 military defeat tokens that seat has (Strategists Guild).
    copied_guild = guild card name chosen with Olympia B stage 3 (optional)."""
    out = dict(shields=0, t=0, c=0, g=0, w=0, wonder=0, civilian=0, commercial=0, guilds=0)
    for name in me.built:
        card = db[name]
        e = card.effect
        if card.color == "Red":
            m = _SHIELD.search(e)
            out["shields"] += int(m.group(1)) if m else 0
        _symbols(e, out)                                   # green cards + Scientists Guild
        if card.color == "Blue":
            m = _VP.search(e)
            out["civilian"] += int(m.group(1)) if m else 0
        elif card.color == "Yellow":
            out["commercial"] += _for_each_vp(e, me, left, right, defeats_of)
        elif card.color == "Purple":
            out["guilds"] += _for_each_vp(e, me, left, right, defeats_of)
    for st in me.board.stages[:me.stages]:
        e = st.effect
        m = _SHIELD.search(e)
        out["shields"] += int(m.group(1)) if m else 0
        m = _VP.search(e)
        out["wonder"] += int(m.group(1)) if m else 0
        _symbols(e, out)                                   # Babylon A stage 2 = wildcard
    if copied_guild and me.board.name == "Olympia" and me.board.side == "B" and me.stages >= 3:
        out["guilds"] += _for_each_vp(db[copied_guild].effect, me, left, right, defeats_of)
    return out
