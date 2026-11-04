import time  # monotonic clock for the SCORE_REPORT barrier timeout
from typing import Dict

from protocol.disconnect_errors import is_int, json_object   # shared payload check and malformed-message decorator

# ---- Message type bytes (RFC Section 8.1) ----
AGE_END, SCORE_REPORT, GAME_END = 0x11, 0x12, 0x13

# ---- Constants from the RFC (Section 10 wants timers configurable, so they live here) ----
T_SCORE_TIMEOUT = 10.0                               # seconds to wait for every SCORE_REPORT (Section 10)
AGE_VICTORY_VP = {1: 1, 2: 3, 3: 5}                  # victory token value per Age (7.10); a defeat is always -1
SCORE_FIELDS = ["military", "treasury", "wonder", "civilian", "scientific", "commercial", "guilds"]  # the seven categories (7.11)


class ScoringManager:
    """
    Military resolution and end-of-game scoring consensus (RFC Sections 7.10 and 7.11). Everything is keyed by Seat.

    Threading: call every method from ONE thread (the game loop).

    Message handlers, both called as handler(sender_seat, payload_dict):
      handle_age_end, handle_score_report
    Called by the other modules:
      start_age_end(age)    draft engine, after Turn 6 of an Age
      tick()                game loop, every ~100 ms
    Needs from the other modules:
      network.broadcast(type, payload)      sends to every peer
      disconnects                           the DisconnectManager of this peer (errors, drops, neighbors)
      state.my_seat, state.live_peers (set)
      state.log[seat] -> dict with shields (cumulative total), coins, mil_tokens, t, c, g, w, wonder, civilian, commercial, guilds
    """

    def __init__(self, network, state, disconnects):
        self.network = network                           # transport provided by the socket layer
        self.state = state                               # shared game state / Public Log
        self.disconnects = disconnects                   # ERROR / drop handling lives in the other file
        self.broadcast_error = disconnects.broadcast_error   # also used by the json_object decorator
        disconnects.on_drop.append(self._recheck)        # when a peer is dropped, the barriers below may be complete
        self.pending_age = None                          # Age whose AGE_END we broadcast but have not resolved yet
        self.age_shields: Dict[int, Dict[int, int]] = {} # age -> {seat: verified shield total}, the snapshot used for the comparisons
        self.scoring_started = False                     # True once Age III is resolved and our SCORE_REPORT is sent
        self.score_deadline = 0.0                        # monotonic time when the SCORE_REPORT barrier expires
        self.pending_reports: Dict[int, dict] = {}       # SCORE_REPORTs held back until our own scoring has started
        self.score_reports: Dict[int, dict] = {}         # SCORE_REPORTs that passed the cross-check
        self.game_ended = False                          # guarantees GAME_END is sent exactly once

    def _recheck(self):
        """Runs after a peer was dropped: the smaller live set may complete the AGE_END or SCORE_REPORT barrier."""
        self._try_resolve_age()
        self._try_conclude()

    def _matches(self, sender: int, code: str, expected, claimed) -> bool:
        """True if a peer's claim equals our own derivation; otherwise broadcasts the mismatch ERROR, which also drops that peer."""
        if claimed == expected:                          # the claim agrees with the Public Log
            return True
        self.broadcast_error(code, sender, f"expected {expected}, got {claimed}")   # AGE_MISMATCH / SCORE_MISMATCH (11.2)
        return False

    # =================================================================
    # MILITARY RESOLUTION (Section 7.10)
    # =================================================================
    def start_age_end(self, age: int):
        """Called by the draft engine after Turn 6 of an Age: broadcasts our AGE_END and starts waiting for everyone else's."""
        if self.disconnects.aborted:                     # nothing to do once the session is dead
            return
        shields = self.state.log[self.state.my_seat]["shields"]   # our own shield total from the Public Log
        self.pending_age = age                           # this Age is now waiting on its barrier
        self.age_shields.setdefault(age, {})[self.state.my_seat] = shields   # our entry of the snapshot
        self.network.broadcast(AGE_END, {"age": age, "shields": shields})   # tell everyone (0x11)
        self._try_resolve_age()                          # peers' AGE_ENDs may already be in

    @json_object                                         # non-object payload -> MALFORMED_MESSAGE
    def handle_age_end(self, sender: int, payload: dict):
        """Called when AGE_END (0x11) arrives. The sender's own turns arrived before it (per-channel order), so our log of it is current."""
        if sender not in self.state.live_peers:          # ignore peers already dropped
            return
        age, shields = payload.get("age"), payload.get("shields")   # the two fields of the message
        if not (is_int(age) and age in AGE_VICTORY_VP and is_int(shields)):   # age must be 1-3, shields an integer
            self.broadcast_error("MALFORMED_MESSAGE", sender, "AGE_END needs an age of 1-3 and an integer shields")
            return
        if self._matches(sender, "AGE_MISMATCH", self.state.log[sender]["shields"], shields):   # cross-check with our own count
            self.age_shields.setdefault(age, {})[sender] = shields   # keep the verified value even if the log moves on later
            self._try_resolve_age()                      # maybe that was the last one we waited for

    def _try_resolve_age(self):
        """Resolves military once we sent ours and every live peer's AGE_END passed the check."""
        age = self.pending_age                           # None means we are not waiting on an Age
        if self.disconnects.aborted or age is None or not self.state.live_peers <= self.age_shields[age].keys():   # dead session or someone still missing
            return
        shields = self.age_shields[age]                  # the verified snapshot, identical on every peer
        for seat in self.state.live_peers:               # every live peer gets its tokens
            tokens = 0                                   # net victory/defeat points for this seat
            for neighbor in self.disconnects.get_neighbors(seat):   # compare against left and right neighbor
                if shields[seat] > shields[neighbor]:
                    tokens += AGE_VICTORY_VP[age]        # win: +1 / +3 / +5
                elif shields[seat] < shields[neighbor]:
                    tokens -= 1                          # defeat: -1 (a tie gives nothing)
            self.state.log[seat]["mil_tokens"] += tokens # append to the Public Log
        self.pending_age = None                          # this Age is done
        if age == 3:                                     # Age III was the last military resolution, so our Public Log is final
            self._start_scoring()                        # and scoring can begin

    # =================================================================
    # END-OF-GAME SCORING (Section 7.11)
    # =================================================================
    @staticmethod
    def compute_scientific_score(t: int, c: int, g: int, wildcards: int) -> int:
        """Tries every split of the wildcards over (t, c, g) and returns the best t²+c²+g²+7·min(t,c,g)."""
        best = 0                                         # best total found so far
        for wt in range(wildcards + 1):                  # wildcards given to tablets
            for wc in range(wildcards + 1 - wt):         # wildcards given to compasses
                a, b, d = t + wt, c + wc, g + wildcards - wt - wc   # the rest go to gears
                best = max(best, a * a + b * b + d * d + 7 * min(a, b, d))
        return best

    def compute_score(self, seat: int) -> dict:
        """All seven scoring categories plus total, derived only from the Public Log."""
        e = self.state.log[seat]                         # that peer's entry in the Public Log
        score = {
            "military": e["mil_tokens"],                 # tokens accumulated over the 3 Ages
            "treasury": e["coins"] // 3,                 # 1 VP per 3 coins, rounded down
            "wonder": e["wonder"],                       # VP printed on built wonder stages
            "civilian": e["civilian"],                   # blue cards
            "scientific": self.compute_scientific_score(e["t"], e["c"], e["g"], e["w"]),   # green cards
            "commercial": e["commercial"],               # yellow cards
            "guilds": e["guilds"],                       # purple cards
        }
        score["total"] = sum(score[f] for f in SCORE_FIELDS)   # total = sum of the seven categories
        return score

    def _start_scoring(self):
        """Starts the SCORE_REPORT barrier. Runs by itself when Age III is resolved."""
        self.scoring_started = True                      # from now on held-back reports can be checked
        self.score_deadline = time.monotonic() + T_SCORE_TIMEOUT   # start the barrier clock
        self.network.broadcast(SCORE_REPORT, self.compute_score(self.state.my_seat))   # our own report (0x12)
        self._verify_reports()                           # reports that arrived early are checked now

    @json_object                                         # non-object payload -> MALFORMED_MESSAGE
    def handle_score_report(self, sender: int, payload: dict):
        """Called when SCORE_REPORT (0x12) arrives. Held back until our own scoring has started, then cross-checked."""
        if self.game_ended or sender not in self.state.live_peers:   # too late, or sender already dropped
            return
        self.pending_reports[sender] = payload           # keep the newest report from that sender
        self._verify_reports()                           # checks it right away when we are ready

    def _verify_reports(self):
        """Cross-checks held-back reports against our own derivation, then tries to conclude."""
        if not self.scoring_started:                     # our military tokens are not final yet, so checking now could blame an honest peer
            return
        for sender in list(self.pending_reports):        # copy: the dict shrinks while we loop
            report = self.pending_reports.pop(sender)    # every report is checked exactly once
            if sender not in self.state.live_peers:      # dropped in the meantime
                continue
            expected = self.compute_score(sender)        # what the Public Log says this peer must report
            claimed = {k: report.get(k) for k in expected}   # only the eight known fields; extras are ignored (8.3)
            if self._matches(sender, "SCORE_MISMATCH", expected, claimed):
                self.score_reports[sender] = claimed     # verified
        self._try_conclude()                             # maybe that was the last report

    def _try_conclude(self):
        """Broadcasts GAME_END exactly once, when every live peer's report has been verified."""
        if self.game_ended or self.disconnects.aborted or not self.scoring_started:   # already done, dead session, or not scoring yet
            return
        if not self.disconnects.others <= self.score_reports.keys():   # someone's report is still missing
            return
        self.game_ended = True                           # set first so GAME_END can never fire twice
        standings = [{"seat": s, "total": self.compute_score(s)["total"], "coins": self.state.log[s]["coins"]}
                     for s in self.state.live_peers]     # one row per live peer (dropped peers are excluded)
        standings.sort(key=lambda x: (-x["total"], -x["coins"], x["seat"]))   # best score, then most coins (RFC); seat only so every peer picks the same winner on a full tie
        self.network.broadcast(GAME_END, {"winner_seat": standings[0]["seat"], "final_scores": standings})   # 0x13

    def tick(self):
        """Call every ~100 ms from the game loop: enforces the SCORE_REPORT timeout (7.11)."""
        if self.scoring_started and not self.game_ended and time.monotonic() >= self.score_deadline:   # barrier expired
            for seat in sorted(self.disconnects.others - self.score_reports.keys()):   # peers that never sent a valid report
                self.disconnects.mark_peer_disconnected(seat, "TIMEOUT")   # single miss (11.3); dropping the last laggard also concludes the game
