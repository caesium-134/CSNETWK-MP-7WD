# magoo
# Payload Types/Records as defined by Json Payloads
# Records of Payload types and instantiator

# Refer to (Sec 8)

from dataclasses import dataclass

import json
import random
import protocol.datatypes as datatypes
from typing import List 

###################
#    Payloads     #
###################

# Wrapper function of json to convert into sendable json obj
def convert_to_json(payload) -> any:
  return json.dumps(payload)

# Wrapper function of json to convert into receivable json obj
def convert_from_json(payload: str) -> any:
  return (json.loads(payload))

#Lobby-Peer ID gen 2^7
def gen_lobby_peer() -> int :
  return random.randint(1, 128)

# Base payload record
@dataclass
class Payload:
  pass

###################
#      Lobby      #
###################

# Re-sent every T_LOBBY_ANNOUNCE_INTERVAL (Timings.py) for as long as this peer is seeking.
@dataclass
class LobbyPresence(Payload):
  lobby_peer_id : str
  display_name : str
  host : str
  port : int

# Sent once, best-effort, when a peer stops seeking; 
# staleness (T_LOBBY_STALE) is the fallback if this is lost.
@dataclass
class LobbyLeave(Payload):
  lobby_peer_id : str

# taken after it has been stable for T_LOBBY_QUIET — or, 
# once T_LOBBY_TIMEOUT has elapsed with at least min_players present, 
# taken immediately as a forced final proposal
@dataclass
class LobbyStartVote(Payload):
  participants: List[str]

# Sent only by a peer whose own roster exactly matches the participants 
# list of a received LOBBY_START_VOTE for the same lobby_id
@dataclass
class LobbyStartAck(Payload):
  p_hash: List[str]

# HELLO's only role is to be the first reliable Payload on a channel
@dataclass
class Hello(Payload):
  pass

# Empty payload, sent once a peer has itself received the matching HELLO.
@dataclass
class HelloACK(Payload):
  pass

###################
#      Init       #
###################

@dataclass
class SeedCommit(Payload):
  commitment: str

# Receivers MUST verify SHA256(nonce) == commitment
@dataclass
class SeedReveal(Payload):
  nonce: str

# The seat and board this peer computed for itself from the shared seed; 
# compared by every receiver against its own independent computation.
@dataclass
class RingCofirm(Payload):
  seat: int
  wonder_board: str
  wonder_side: str

###################
#      Game       #
###################

@dataclass
class CardCommit(Payload):
  age: int
  turn: int
  commitment: str

# card_id is omitted for action == WONDER_STAGE/DISCARD
@dataclass
class CardReveal(Payload):
  age: int
  turn: int
  action: str
  nonce: str

# BUILD
@dataclass
class CardRevealBuild(CardReveal):
  card_id: str

# Base ActionAnnounce
@dataclass
class ActionAnnounce(Payload):
  age: int
  turn: int
  action: str

# ACTION == BUILD (and its sublists)
@dataclass
class ResourcesBought():
  from_seat: int
  resource: str
  qty: int

@dataclass
class CostPaid():
  coins: int
  resources_bought: List[ResourcesBought]

@dataclass
class BuildActionAnnounce(ActionAnnounce):
  cost_paid : CostPaid

# ACTION == WONDER_STAGE
@dataclass
class WonderActionAnnounce(ActionAnnounce):
  stage_index: int

# ACTION == DISCARD
@dataclass
class DiscardActionAnnounce(ActionAnnounce):
  pass

# The sender's remaining hand after this turn's action
@dataclass
class HandPass(Payload):
  age: int
  turn: int
  cards: List[str]

# Res Query
@dataclass
class ResourceQuery(Payload):
  resource: str
  qty: int

# Offering Peer
@dataclass
class ResourceOffer(Payload):
  resource: str
  qty: int
  price_per_unit: int

# Buying Peer
@dataclass
class ResourceBuy(Payload):
  resource: str
  qty: int
  coins: int

# This peer's own total shield count for the Age just completed
@dataclass
class AgeEnd(Payload):
  age: int
  shields: int

# total MUST equal the sum of the other seven fields; 
# a receiver that gets a mismatched sum treats it the same as a cross-check disagreement
@dataclass
class ScoreReport(Payload):
  military: int
  treasure: int
  wonder: int
  civilian: int
  scientific: int
  commercial: int
  guilds: int
  total: int

# Game End
@dataclass
class PlayerScore():
  seat: int
  total: int
  coins: int

@dataclass
class GameEnd(Payload):
  winner_seat: int
  final_score: List[PlayerScore]

###################
#      Utils      #
###################

# Broadcast so every peer can independently log the report and, where a procedure calls for it
@dataclass
class Error(Payload):
  code: int
  offending_peer: int
  detail: str

# Special Clase if TIMEOUT
@dataclass
class TimeoutError(Error):
  incident_id: int

# RTT stuff
@dataclass
class Ping(Payload):
  sent_at_ms: int

@dataclass
class PONG(Payload):
  echo_sent_at_ms: int

# Best-effort: a peer disconnecting voluntarily SHOULD send this, 
# but its absence is exactly what RETRY_MAX / T_TURN_TIMEOUT (Timeout.py)
@dataclass
class Disconnect(Payload):
  reason: str

###################
#     Social      #
###################

# Free-text chat addressed to one recipient peer
@dataclass
class DirectMessage(Payload):
  text: str

# Free-text chat broadcast to every other peer
@dataclass
class GroupMessage(Payload):
  text: str
