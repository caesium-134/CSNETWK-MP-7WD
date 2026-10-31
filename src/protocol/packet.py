
# magoo
# udp packet record (sec. 5)

import hashlib # for sha-256
from dataclasses import dataclass
from protocol.payload import *

# to do think how to split ze hand_pass (handle multiple packets)

def gen_session_id(code: str) -> bytes:
    h = hashlib.new("SHA256")
    h.update(code.encode())
    return h.digest()[:16]

def compare_session_id(user_code:str, received_code:str) -> bool:
    h = hashlib.new("SHA256")
    h.update(user_code.encode())
    return h[:16] == received_code


@dataclass
class Packet():
    #L1
    ver: int # 1
    type: int # 0x01 - 0x19 (from payload)
    flags: int # bit 0 = ACK, bit 1 = RT, bit 2-7 RESERVED = 00000
    reserved: int # 0

    #L2
    session_id: bytes # 128 BIT

    #L3
    sender_peer_id: int # 0xFFFF init
    payload_length: int

    #L4-L6
    seq_num: int # starts 1, monotonically inc
    ack_num: int # ack bytee
    payload: dict # convert to dict BEFORE INSERT

def serialize_packet(packet: Packet) -> bytes:
    payload_bytes = convert_to_json(packet.payload).encode("utf-8")
    packet.payload_length = len(payload_bytes)

    header =  bytes([0b00000001])
    header += packet.type.to_bytes(1, 'big')
    header += packet.flags.to_bytes(1, 'big')
    header += bytes([0b00000000])

    header += packet.session_id

    header += packet.sender_peer_id.to_bytes(2, 'big')
    header += packet.payload_length.to_bytes(2, 'big')

    header += packet.seq_num.to_bytes(4, 'big')
    header += packet.ack_num.to_bytes(4, 'big')
    
    return bytes(header) + payload_bytes

# serialize/deserialize a packet
def deserialize_packet(recv_message: bytes) -> Packet:

    ver = recv_message[0]
    msg_type = recv_message[1]
    flags = recv_message[2]
    reserved = recv_message[3]

    session_id = recv_message[4:20]
    sender_peer_id = int.from_bytes(recv_message[20:22], "big")
    payload_length = int.from_bytes(recv_message[22:24], "big")
    seq_num = int.from_bytes(recv_message[24:28], "big")
    ack_num = int.from_bytes(recv_message[28:32], "big")

    payload_bytes = recv_message[32:32 + payload_length]
    payload_dict = convert_from_json(payload_bytes.decode("utf-8"))

    payload_obj = payload_dict

    return Packet(
        ver=ver,
        type=msg_type,
        flags=flags,
        reserved=reserved,
        session_id=session_id,
        sender_peer_id=sender_peer_id,
        payload_length=payload_length,
        seq_num=seq_num,
        ack_num=ack_num,
        payload=payload_obj,
    )