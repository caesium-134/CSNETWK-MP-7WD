import functools  # keeps the name of a decorated handler intact
import queue      # thread-safe hand-over of PING verdicts from probe threads to the game loop
import secrets    # generates the random 64-bit incident_id
import threading  # probe threads, so waiting for a PONG never blocks the game loop
from typing import Dict, Set

# ---- Message type bytes (RFC Section 8.1) ----
ERROR, DISCONNECT = 0x14, 0x17

# ---- Constants from the RFC (Section 10 wants timers configurable, so they live here) ----
TURN_MISS_LIMIT = 2                                  # consecutive missed CARD_COMMIT barriers before a peer is dropped (11.3)
INCIDENT_ID_LEN = 16                                 # incident_id is a 64-bit value, hex-encoded = 16 chars (8.2)
DISCONNECTING_CODES = {"COMMITMENT_MISMATCH", "RING_MISMATCH", "AGE_MISMATCH", "SCORE_MISMATCH"}  # we verify these ourselves from broadcast data, so the offender is dropped
ERROR_CODES = DISCONNECTING_CODES | {"MALFORMED_MESSAGE", "OUT_OF_PHASE", "TIMEOUT"}  # every error_code the RFC allows (8.2)


def is_int(value) -> bool:
    """True for a real JSON integer (bool is excluded because Python treats True as 1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def json_object(handler):
    """Decorator for message handlers: a payload that is not a JSON object is answered with MALFORMED_MESSAGE instead of a crash (11.2)."""
    @functools.wraps(handler)                        # keep the handler's name for the error detail
    def wrapper(self, sender, payload):
        if isinstance(payload, dict):                # normal case
            return handler(self, sender, payload)    # hand over to the real handler
        self.broadcast_error("MALFORMED_MESSAGE", sender, f"{handler.__name__} needs a JSON object")
    return wrapper


class DisconnectManager:
    """
    ERROR handling and peer disconnection consensus (RFC Sections 7.9, 10, 11.2, 11.3). Everything is keyed by Seat.

    Threading: call every method from ONE thread (the game loop). Only the PING probes run on worker threads, and they
    hand their verdict back through a queue that tick() applies, so no locks are needed.

    Message handlers, all called as handler(sender_seat, payload_dict):
      handle_received_error, handle_disconnect
    Called by the other modules:
      broadcast_error(code, seat, detail)   any module that detects a protocol violation
      initiate_timeout_consensus(seat)      trading / channel code, when a private exchange with a peer timed out
      on_turn_barrier(missing_seats)        draft engine, after every CARD_COMMIT barrier
      mark_peer_disconnected(seat, reason)  seed / join barriers (single-miss drops)
      get_neighbors(seat)                   draft engine, to pass hands to the next LIVE seat
      quit_session()                        the user quits
      tick()                                game loop, every ~100 ms
    Needs from the other modules:
      network.broadcast(type, payload)      sends to every peer
      network.ping(seat) -> bool            sends PING, waits for PONG (up to T_RESOURCE_TIMEOUT), True if it came back
      state.my_seat, state.live_peers (set), state.min_players, state.ring (seats in seating order, never edited)
    """

    def __init__(self, network, state):
        self.network = network                           # transport provided by the socket layer
        self.state = state                               # shared game state / Public Log
        self.aborted = False                             # True once quorum is lost or we quit
        self.turn_misses: Dict[int, int] = {}            # seat -> consecutive missed turn barriers
        self.incidents: Dict[str, dict] = {}             # incident_id -> {"offender": seat, "reporters": set of seats}
        self.dismissed: Set[str] = set()                 # incident_ids that our own PING test refuted
        self.probe_results = queue.Queue()               # (incident_id, answered) coming back from the probe threads
        self.on_drop = []                                # functions to run after a peer is dropped (the scoring module re-checks its barriers)

    @property
    def others(self) -> Set[int]:
        """Every live peer except us, i.e. the peers each barrier waits on."""
        return self.state.live_peers - {self.state.my_seat}

    def get_neighbors(self, seat: int):
        """(left, right) of a seat in the ring, skipping disconnected seats (the ring is never recomputed, 11.3)."""
        ring = [s for s in self.state.ring if s in self.state.live_peers]   # live seats in seating order
        i = ring.index(seat)                             # this seat's position
        return ring[(i - 1) % len(ring)], ring[(i + 1) % len(ring)]

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

    @json_object                                         # non-object payload -> MALFORMED_MESSAGE
    def handle_received_error(self, sender: int, payload: dict):
        """Processes a received ERROR (0x14): logs it and, for a TIMEOUT incident, starts our own PING test of the offender."""
        if sender not in self.state.live_peers:          # a dropped peer no longer takes part in anything
            return
        code, offender, incident_id = payload.get("code"), payload.get("offending_peer"), payload.get("incident_id")   # the fields we use
        bad_id = incident_id is not None and not (isinstance(incident_id, str) and len(incident_id) == INCIDENT_ID_LEN)   # optional, but 16 chars when present
        if not isinstance(code, str) or code not in ERROR_CODES or not is_int(offender) or bad_id:   # unknown code or bad fields
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
        if answered:                                     # offender answered: our channel to it is fine
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
        for seat in sorted(self.others):                 # same order on every peer
            if seat not in missing:                      # committed in time
                self.turn_misses[seat] = 0               # streak broken
                continue
            self.turn_misses[seat] = self.turn_misses.get(seat, 0) + 1   # one more consecutive miss
            if self.turn_misses[seat] >= TURN_MISS_LIMIT:   # second miss in a row
                self.mark_peer_disconnected(seat, "TIMEOUT")

    @json_object                                         # non-object payload -> MALFORMED_MESSAGE
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
        for recheck in self.on_drop:                     # the smaller live set may satisfy the AGE_END or SCORE_REPORT barrier
            recheck()
        self._check_incidents()                          # ...or an incident waiting for corroboration

    def tick(self):
        """Call every ~100 ms from the game loop: applies finished PING probes."""
        while not self.probe_results.empty():            # tick is the only consumer, so empty() is safe here
            self._finish_probe(*self.probe_results.get_nowait())   # (incident_id, answered)
