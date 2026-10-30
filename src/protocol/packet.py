
# magoo
# udp packet record (sec. 5)

from dataclasses import dataclass
from protocol.payload import *

# to do think how to split ze hand_pass (handle multiple packets)
# serialize/deserialize a packet

@dataclass
class Packet():
    #L1
    ver: int # 1
    type: int # 0x01 - 0x19 (from payload)
    flags: int # bit 0 = ACK, bit 1 = RT, bit 2-7 RESERVED = 00000
    reserved: int # 0

    #L2
    session_id: int # 128 BIT str

    #L3
    sender_peer_id: int # 0xFFFF init
    payload_length: int

    #L4-L6
    seq_num: int # starts 1, monotonically inc
    ack_num: int # ack bytee
    payload: Payload # 1200 bits WINDOW

def serialize_packet(packet: Packet) -> bytes:
    header =  bytes([0b00000001])
    header += packet.type.to_bytes(1, 'big')
    header += packet.flags.to_bytes(1, 'big')
    header += bytes([0b00000000])

    header += packet.session_id.encode('utf-8')

    header += packet.sender_peer_id.to_bytes(2, 'big')
    header += packet.payload_length.to_bytes(2, 'big')

    header += packet.seq_num.to_bytes(4, 'big')
    header += packet.ack_num.to_bytes(4, 'big')

    header += packet.payload

    return header

def deserialize_packet(recv_message : bytes) -> Packet:
    ver = recv_message[0]
    msg_type = recv_message[1]
    flags = recv_message[2]
    reserved = recv_message[3]
    
    session_id = int.from_bytes(recv_message[4:8], 'big')
    
    sender_peer_id = int.from_bytes(recv_message[8:10], 'big')
    payload_length = int.from_bytes(recv_message[10:12], 'big')
    
    seq_num = int.from_bytes(recv_message[12:16], 'big')
    ack_num = int.from_bytes(recv_message[16:20], 'big')
    
    payload_bytes = recv_message[20:]
    payload_json = payload_bytes.decode()
    payload = json.loads(payload_json)
    
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
        payload=payload
    )