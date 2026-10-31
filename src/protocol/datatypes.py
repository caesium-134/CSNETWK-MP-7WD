
# magoo
# harmonize with the other modules upon fork

###################
#   DATA TYPES    #
###################

from enum import Enum

# Action for payload
class Action(Enum):
    BUILD = 1
    WONDER_STAGE = 2
    DISCARD = 3

class Resource(Enum):
    WOOD = 1
    ORE = 2
    CLAY = 3
    STONE = 4
    GLASS = 5
    PAPYRUS = 6
    TEXTILE = 7

#card_id: str <name of card>
#seat: int

class ErrorCode(Enum):
    COMMITMENT_MISMATCH= 1
    RING_MISMATCH= 2
    AGE_MISMATCH= 3
    SCORE_MISMATCH= 4
    MALFORMED_MESSAGE= 5
    OUT_OF_PHASE= 6
    TIMEOUT= 7

class DisconnectReason(Enum):
    USER_QUIT= 1
    QUORUM_LOST= 2
    PROTOCOL_VIOLATION= 3
    TIMEOUT= 4

#incident_id: int 64bit value
#lobby_id: int 128bit value
#lobby_peer_id 128bit value

#0x01 ~ 0x19 (p.s. convert to bits/1 byte)
class PayloadType(Enum):
    LOBBY_PRESENCE = 1
    LOBBY_LEAVE= 2
    LOBBY_START_VOTE= 3
    LOBBY_START_ACK= 4
    HELLO= 5
    HELLO_ACK= 6
    SEED_COMMIT= 7
    SEED_REVEAL= 8
    RING_CONFIRM= 9
    CARD_COMMIT= 10
    CARD_REVEAL= 11
    ACTION_ANNOUNCE= 12
    HAND_PASS= 13
    RESOURCE_QUERY= 14
    RESOURCE_OFFER= 15
    RESOURCE_BUY= 16
    AGE_END= 17
    SCORE_REPORT= 18
    GAME_END= 19
    ERROR= 20
    PING= 21
    PONG= 22
    DISCONNECT= 23
    DIRECT_MESSAGE= 24
    GROUP_MESSAGE= 25