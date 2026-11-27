"""Shared game state + hook plumbing (Zed). One GameState per peer, shared by every module.
Matches the interface Nori asked for: my_seat, live_peers, min_players, ring, log[seat]."""
from dataclasses import dataclass
from stats import tableau_stats


@dataclass
class SeatRecord:
    # ---- filled by the draft engine (Zed) from ACTION_ANNOUNCE ----
    shields: int = 0          # cumulative shields on this board right now
    coins: object = None      # exact for MY seat only; None for others (wonder-stage buys are private)
    t: int = 0                # Tablet symbols (green cards)
    c: int = 0                # Compass symbols
    g: int = 0                # Gear symbols
    w: int = 0                # wildcard symbols (Scientists Guild, Babylon A stage 2)
    wonder: int = 0           # VP printed on built wonder stages
    civilian: int = 0         # VP from blue cards
    commercial: int = 0       # VP from yellow cards (Arena, Chamber of Commerce, Haven, Lighthouse)
    guilds: int = 0           # VP from purple cards
    # ---- filled by the scoring code (Nori) ----
    mil_tokens: int = 0       # net military victory points (+1/+3/+5 wins, -1 losses)
    defeats: int = 0          # number of -1 tokens (needed by Strategists Guild)


class GameState:
    def __init__(self, my_seat, ring, min_players=3):
        self.my_seat = my_seat
        self.ring = list(ring)               # seats in seating order (RFC 7.6)
        self.live_peers = set(ring)          # SHARED: the disconnect code removes seats from this
        self.min_players = min_players
        self.log = {s: SeatRecord() for s in self.ring}
        self.copied_guild = {}               # seat -> guild chosen with Olympia B stage 3

    def have_quorum(self):
        return len(self.live_peers) >= self.min_players

    def _step(self, seat, d):
        i = self.ring.index(seat)
        for _ in range(len(self.ring)):
            i = (i + d) % len(self.ring)
            s = self.ring[i]
            if s in self.live_peers and s != seat:
                return s
        return seat

    def left_of(self, seat):                 # RFC 7.6: seat i-1, skipping disconnected peers (11.3)
        return self._step(seat, -1)

    def right_of(self, seat):                # seat i+1, skipping disconnected peers
        return self._step(seat, +1)

    def refresh(self, tabs, db):
        """Recompute every seat's record from the public tableaux. Does not touch
        mil_tokens / defeats (Nori's) or coins (set by the engine for my seat only)."""
        for s in self.ring:
            st = tableau_stats(tabs[s], tabs[self.left_of(s)], tabs[self.right_of(s)], db,
                               defeats_of=lambda x: self.log[x].defeats,
                               copied_guild=self.copied_guild.get(s))
            rec = self.log[s]
            for k, v in st.items():
                setattr(rec, k, v)


class NullHooks:
    """Do-nothing hooks so the draft engine runs without Nori's code."""
    def start_age_end(self, age): pass
    def on_turn_barrier(self, missing_seats): pass
    def initiate_timeout_consensus(self, seat): pass


class ManagerHooks:
    """Glue: hand this to DraftPeer(hooks=...) with Nori's two managers."""
    def __init__(self, disconnect, scoring):
        self.disconnect, self.scoring = disconnect, scoring

    def start_age_end(self, age):
        self.scoring.start_age_end(age)

    def on_turn_barrier(self, missing_seats):
        self.disconnect.on_turn_barrier(missing_seats)

    def initiate_timeout_consensus(self, seat):
        self.disconnect.initiate_timeout_consensus(seat)
