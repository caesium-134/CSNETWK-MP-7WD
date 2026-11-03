import functools  # keeps the name of a decorated handler intact
import queue      # thread-safe hand-over of PING verdicts from probe threads to the game loop
import secrets    # generates the random 64-bit incident_id
import threading  # probe threads, so waiting for a PONG never blocks the game loop
import time       # monotonic clock for the SCORE_REPORT barrier timeout
from typing import Dict, Set

# ---- Message type bytes (RFC Section 8.1) ----
AGE_END, SCORE_REPORT, GAME_END, ERROR, DISCONNECT = 0x11, 0x12, 0x13, 0x14, 0x17

# ---- Constants from the RFC (Section 10 wants timers configurable, so they live here) ----
T_SCORE_TIMEOUT = 10.0                               # seconds to wait for every SCORE_REPORT (Section 10)
TURN_MISS_LIMIT = 2                                  # consecutive missed CARD_COMMIT barriers before a peer is dropped (11.3)
INCIDENT_ID_LEN = 16                                 # incident_id is a 64-bit value, hex-encoded = 16 chars (8.2)
AGE_VICTORY_VP = {1: 1, 2: 3, 3: 5}                  # victory token value per Age (7.10); a defeat is always -1
SCORE_FIELDS = ["military", "treasury", "wonder", "civilian", "scientific", "commercial", "guilds"]  # the seven categories (7.11)
DISCONNECTING_CODES = {"COMMITMENT_MISMATCH", "RING_MISMATCH", "AGE_MISMATCH", "SCORE_MISMATCH"}  # we verify these ourselves from broadcast data, so the offender is dropped
ERROR_CODES = DISCONNECTING_CODES | {"MALFORMED_MESSAGE", "OUT_OF_PHASE", "TIMEOUT"}  # every error_code the RFC allows (8.2)


def _is_int(value) -> bool:
    """True for a real JSON integer (bool is excluded because Python treats True as 1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _json_object(handler):
    """Decorator for message handlers: a payload that is not a JSON object is answered with MALFORMED_MESSAGE instead of a crash (11.2)."""
    @functools.wraps(handler)                        # keep the handler's name for the error detail
    def wrapper(self, sender, payload):
        if isinstance(payload, dict):                # normal case
            return handler(self, sender, payload)    # hand over to the real handler
        self.broadcast_error("MALFORMED_MESSAGE", sender, f"{handler.__name__} needs a JSON object")
    return wrapper


class NoriManager:
    """
    Military resolution, scoring consensus, ERROR handling and disconnection consensus. Everything is keyed by Seat.

    Threading: call every method from ONE thread (the game loop). Only the PING probes run on worker threads, and they
    hand their verdict back through a queue that tick() applies, so no locks are needed.

    Message handlers, all called as handler(sender_seat, payload_dict):
      handle_age_end, handle_score_report, handle_received_error, handle_disconnect
    Called by the other modules:
      start_age_end(age)                    draft engine, after Turn 6 of an Age
      on_turn_barrier(missing_seats)        draft engine, after every CARD_COMMIT barrier
      initiate_timeout_consensus(seat)      trading / channel code, when a private exchange with a peer timed out
      broadcast_error(code, seat, detail)   any module that detects a protocol violation
      mark_peer_disconnected(seat, reason)  seed / join barriers (single-miss drops)
      quit_session()                        the user quits
      tick()                                game loop, every ~100 ms
    Needs from the other modules:
      network.broadcast(type, payload)      sends to every peer
      network.ping(seat) -> bool            sends PING, waits for PONG (up to T_RESOURCE_TIMEOUT), True if it came back
      state.my_seat, state.live_peers (set), state.min_players, state.ring (seats in seating order, never edited)
      state.log[seat] -> dict with shields (cumulative total), coins, mil_tokens, t, c, g, w, wonder, civilian, commercial, guilds
    """

    def __init__(self, network, state):
        self.network = network                           # transport provided by the socket layer
        self.state = state                               # shared game state / Public Log
        self.aborted = False                             # True once quorum is lost or we quit
        self.pending_age = None                          # Age whose AGE_END we broadcast but have not resolved yet
        self.age_shields: Dict[int, Dict[int, int]] = {} # age -> {seat: verified shield total}, the snapshot used for the comparisons
        self.scoring_started = False                     # True once Age III is resolved and our SCORE_REPORT is sent
        self.score_deadline = 0.0                        # monotonic time when the SCORE_REPORT barrier expires
        self.pending_reports: Dict[int, dict] = {}       # SCORE_REPORTs held back until our own scoring has started
        self.score_reports: Dict[int, dict] = {}         # SCORE_REPORTs that passed the cross-check
        self.game_ended = False                          # guarantees GAME_END is sent exactly once
        self.turn_misses: Dict[int, int] = {}            # seat -> consecutive missed turn barriers
        self.incidents: Dict[str, dict] = {}             # incident_id -> {"offender": seat, "reporters": set of seats}
        self.dismissed: Set[str] = set()                 # incident_ids that our own PING test refuted
        self.probe_results = queue.Queue()               # (incident_id, answered) coming back from the probe threads

    @property
    def _others(self) -> Set[int]:
        """Every live peer except us, i.e. the peers each barrier waits on."""
        return self.state.live_peers - {self.state.my_seat}

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
        if self.aborted:                                 # nothing to do once the session is dead
            return
        shields = self.state.log[self.state.my_seat]["shields"]   # our own shield total from the Public Log
        self.pending_age = age                           # this Age is now waiting on its barrier
        self.age_shields.setdefault(age, {})[self.state.my_seat] = shields   # our entry of the snapshot
        self.network.broadcast(AGE_END, {"age": age, "shields": shields})   # tell everyone (0x11)
        self._try_resolve_age()                          # peers' AGE_ENDs may already be in

    @_json_object                                        # non-object payload -> MALFORMED_MESSAGE
    def handle_age_end(self, sender: int, payload: dict):
        """Called when AGE_END (0x11) arrives. The sender's own turns arrived before it (per-channel order), so our log of it is current."""
        if sender not in self.state.live_peers:          # ignore peers already dropped
            return
        age, shields = payload.get("age"), payload.get("shields")   # the two fields of the message
        if not (_is_int(age) and age in AGE_VICTORY_VP and _is_int(shields)):   # age must be 1-3, shields an integer
            self.broadcast_error("MALFORMED_MESSAGE", sender, "AGE_END needs an age of 1-3 and an integer shields")
            return
        if self._matches(sender, "AGE_MISMATCH", self.state.log[sender]["shields"], shields):   # cross-check with our own count
            self.age_shields.setdefault(age, {})[sender] = shields   # keep the verified value even if the log moves on later
            self._try_resolve_age()                      # maybe that was the last one we waited for

    def _try_resolve_age(self):
        """Resolves military once we sent ours and every live peer's AGE_END passed the check."""
        age = self.pending_age                           # None means we are not waiting on an Age
        if self.aborted or age is None or not self.state.live_peers <= self.age_shields[age].keys():   # dead session or someone still missing
            return
        shields = self.age_shields[age]                  # the verified snapshot, identical on every peer
        for seat in self.state.live_peers:               # every live peer gets its tokens
            tokens = 0                                   # net victory/defeat points for this seat
            for neighbor in self.get_neighbors(seat):    # compare against left and right neighbor
                if shields[seat] > shields[neighbor]:
                    tokens += AGE_VICTORY_VP[age]        # win: +1 / +3 / +5
                elif shields[seat] < shields[neighbor]:
                    tokens -= 1                          # defeat: -1 (a tie gives nothing)
            self.state.log[seat]["mil_tokens"] += tokens # append to the Public Log
        self.pending_age = None                          # this Age is done
        if age == 3:                                     # Age III was the last military resolution, so our Public Log is final
            self._start_scoring()                        # and scoring can begin

    def get_neighbors(self, seat: int):
        """(left, right) of a seat in the ring, skipping disconnected seats (the ring is never recomputed, 11.3)."""
        ring = [s for s in self.state.ring if s in self.state.live_peers]   # live seats in seating order
        i = ring.index(seat)                             # this seat's position
        return ring[(i - 1) % len(ring)], ring[(i + 1) % len(ring)]

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

    @_json_object                                        # non-object payload -> MALFORMED_MESSAGE
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
        if self.game_ended or self.aborted or not self.scoring_started:   # already done, dead session, or not scoring yet
            return
        if not self._others <= self.score_reports.keys():   # someone's report is still missing
            return
        self.game_ended = True                           # set first so GAME_END can never fire twice
        standings = [{"seat": s, "total": self.compute_score(s)["total"], "coins": self.state.log[s]["coins"]}
                     for s in self.state.live_peers]     # one row per live peer (dropped peers are excluded)
        standings.sort(key=lambda x: (-x["total"], -x["coins"], x["seat"]))   # best score, then most coins (RFC); seat only so every peer picks the same winner on a full tie
        self.network.broadcast(GAME_END, {"winner_seat": standings[0]["seat"], "final_scores": standings})   # 0x13

    # =================================================================
    # ERRORS (Section 11.2)
    # =================================================================
    def broadcast_error(self, code: str, offending: int, detail: str, incident_id: str = None):
        """Broadcasts ERROR (0x14). Other modules call this too (COMMITMENT_MISMATCH, RING_MISMATCH, OUT_OF_PHASE, ...)."""
        payload = {"code": code, "offending_peer": offending, "detail": detail}   # RFC 0x14 fields
        if incident_id:                                  # only TIMEOUT incidents carry one
            payload["incident_id"] = incident_id
        self.network.broadcast(ERROR, payload)           # tell everyone
        if code in DISCONNECTING_CODES:                  # we detected it ourselves from broadcast data, no corroboration needed
            self.mark_peer_disconnected(offending, "PROTOCOL_VIOLATION")

    @_json_object                                        # non-object payload -> MALFORMED_MESSAGE
    def handle_received_error(self, sender: int, payload: dict):
        """Processes a received ERROR (0x14): logs it and, for a TIMEOUT incident, starts our own PING test of the offender."""
        if sender not in self.state.live_peers:          # a dropped peer no longer takes part in anything
            return
        code, offender, incident_id = payload.get("code"), payload.get("offending_peer"), payload.get("incident_id")   # the fields we use
        bad_id = incident_id is not None and not (isinstance(incident_id, str) and len(incident_id) == INCIDENT_ID_LEN)   # optional, but 16 chars when present
        if not isinstance(code, str) or code not in ERROR_CODES or not _is_int(offender) or bad_id:   # unknown code or bad fields
            self.broadcast_error("MALFORMED_MESSAGE", sender, "ERROR needs a valid code, an integer offending_peer and a 16-char incident_id")
            return
        print(f"[ERROR RECEIVED] from Seat {sender}: {code} about Seat {offender}: {payload.get('detail')}")   # always log it (11.2)
        if code != "TIMEOUT" or incident_id is None:     # other codes are only logged: we verified them independently
            return
        if offender == self.state.my_seat or offender not in self.state.live_peers or incident_id in self.dismissed:
            return                                       # about us, already gone, or already refuted by our PING
        incident = self.incidents.get(incident_id)       # do we already know this incident?
        if incident is None:                             # first report we see: remember the sender and test the offender ourselves
            self.incidents[incident_id] = {"offender": offender, "reporters": {sender}}
            threading.Thread(target=self._probe, args=(offender, incident_id), daemon=True).start()
        elif incident["offender"] == offender:           # same id AND same target, so it counts as corroboration
            incident["reporters"].add(sender)
            self._check_incidents()                      # maybe everyone has corroborated now

    # =================================================================
    # DISCONNECTION CONSENSUS (Sections 7.9 and 11.3)
    # =================================================================
    def initiate_timeout_consensus(self, offender: int):
        """Call when a private exchange with a peer timed out (RESOURCE_QUERY unanswered twice, or a channel hit RETRY_MAX)."""
        if offender in self.state.live_peers and not self.aborted:   # nothing to do for a peer that is already gone
            self._report_timeout(offender, secrets.token_hex(INCIDENT_ID_LEN // 2), "Private exchange timed out")   # fresh 64-bit incident_id

    def _report_timeout(self, offender: int, incident_id: str, detail: str):
        """Counts us as a reporter of an incident and broadcasts our TIMEOUT report with that same incident_id (7.9)."""
        incident = self.incidents.setdefault(incident_id, {"offender": offender, "reporters": set()})   # new for the initiator, existing for a corroborator
        incident["reporters"].add(self.state.my_seat)    # our own report counts
        print(f"[CONSENSUS] Reporting Seat {offender} on incident {incident_id}: {detail}")
        self.broadcast_error("TIMEOUT", offender, detail, incident_id)

    def _probe(self, offender: int, incident_id: str):
        """Worker-thread body: PING the offender (with one retry, 7.9) and hand the verdict to tick() through the queue."""
        answered = self.network.ping(offender) or self.network.ping(offender)   # the second PING only runs if the first got no PONG
        self.probe_results.put((incident_id, answered))  # applied later on the game-loop thread

    def _finish_probe(self, incident_id: str, answered: bool):
        """Applies a PING verdict: a PONG refutes the incident for us, silence makes us corroborate it."""
        if self.aborted:                                 # nothing to report once the session is dead
            return
        incident = self.incidents[incident_id]           # always present: it is created before its probe starts
        if answered:                                   # offender answered: our channel to it is fine
            print(f"[CONSENSUS] Seat {incident['offender']} answered our PING, incident {incident_id} refuted")
            self.dismissed.add(incident_id)              # never corroborate or count this incident
            del self.incidents[incident_id]
            return
        self._report_timeout(incident["offender"], incident_id, "No PONG from offender")   # echo the same incident_id
        self._check_incidents()                          # our report may complete the set

    def _check_incidents(self):
        """Drops an offender once EVERY other live peer has corroborated the same incident."""
        for incident in list(self.incidents.values()):   # copy: peers get dropped while we loop
            offender = incident["offender"]              # the suspected peer
            if not self.aborted and offender in self.state.live_peers and self.state.live_peers - {offender} <= incident["reporters"]:
                print(f"[CONSENSUS] Every live peer corroborated the incident on Seat {offender}")
                self.network.broadcast(DISCONNECT, {"reason": "TIMEOUT", "offending_peer": offender})   # consensus reached (0x17)
                self.mark_peer_disconnected(offender, "TIMEOUT_CONSENSUS")

    def on_turn_barrier(self, missing: Set[int]):
        """Draft engine calls this after every CARD_COMMIT barrier with the seats that missed it; two in a row = dropped (11.3)."""
        for seat in sorted(self._others):                # same order on every peer
            if seat not in missing:                      # committed in time
                self.turn_misses[seat] = 0               # streak broken
                continue
            self.turn_misses[seat] = self.turn_misses.get(seat, 0) + 1   # one more consecutive miss
            if self.turn_misses[seat] >= TURN_MISS_LIMIT:   # second miss in a row
                self.mark_peer_disconnected(seat, "TIMEOUT")

    @_json_object                                        # non-object payload -> MALFORMED_MESSAGE
    def handle_disconnect(self, sender: int, payload: dict):
        """Processes a received DISCONNECT (0x17): the sender is leaving. A consensus notice about someone else is ignored, we reach that conclusion ourselves."""
        if "offending_peer" not in payload:              # plain {reason}: the sender itself is going away
            self.mark_peer_disconnected(sender, str(payload.get("reason", "USER_QUIT")))

    def quit_session(self):
        """Voluntary quit requested by the user."""
        self.network.broadcast(DISCONNECT, {"reason": "USER_QUIT"})   # best-effort goodbye (0x17)
        self.aborted = True                              # stop all further work

    def mark_peer_disconnected(self, seat: int, reason: str):
        """Removes a peer from the live set (the ring is NOT recomputed) and aborts if quorum is lost. Seed/join barrier code can call it directly."""
        if self.aborted or seat == self.state.my_seat or seat not in self.state.live_peers:   # dead session, ourselves, or already gone
            return
        print(f"[DISCONNECT] Seat {seat} marked DISCONNECTED. Reason: {reason}")   # log the decision
        self.state.live_peers.discard(seat)              # remove from the live set
        if len(self.state.live_peers) < self.state.min_players:   # fewer than 3 left
            self.aborted = True                          # stop all further work
            self.network.broadcast(DISCONNECT, {"reason": "QUORUM_LOST"})
            return
        self._try_resolve_age()                          # the smaller live set may satisfy the AGE_END barrier
        self._try_conclude()                             # ...or the SCORE_REPORT barrier
        self._check_incidents()                          # ...or an incident waiting for corroboration

    def tick(self):
        """Call every ~100 ms from the game loop: applies finished PING probes, then enforces the SCORE_REPORT timeout (7.11)."""
        while not self.probe_results.empty():            # tick is the only consumer, so empty() is safe here
            self._finish_probe(*self.probe_results.get_nowait())   # (incident_id, answered)
        if self.scoring_started and not self.game_ended and time.monotonic() >= self.score_deadline:   # barrier expired
            for seat in sorted(self._others - self.score_reports.keys()):   # peers that never sent a valid report
                self.mark_peer_disconnected(seat, "TIMEOUT")   # single miss (11.3); dropping the last laggard also concludes the game