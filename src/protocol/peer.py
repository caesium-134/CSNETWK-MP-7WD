
import socket
import time
import threading
from sys import exit

from protocol.datatypes import *
from protocol.packet import *
from protocol.payload import *
from protocol.timings import *

#to-do:
# implement udpee / mesh
# implement system to host mesh
#   every peer connects to itself
# implement directed/broadcast message
#   sending message/receiving
# implement fallback/
# make udp more stable/reliable

# Configs
BROADCAST_ADDRESS = "255.255.255.255"
LOBBY_PORT = 7777
GAME_PORT = 7777 # game =/= lobby
BUFFER_SIZE = 1200 #(Sec 5)

# get self
# get a peer id
# get all list of peers

class Peer_Host():
    def __init__(self, hostname):
        self.threads = []
        self.hostname = hostname

    def start(self):

        print("join or create lobby?")
        choice = input()

        match choice:
            case "create":
                self.broadcast_lobby()
            case "join":
                thread = threading.Thread(target=self.listen_for_messages)
                self.threads.append(thread)
                thread.start()

    def listen_for_messages(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", 7777))

            while True:
                data, addr = sock.recvfrom(1024)
                data = deserialize_packet(data)
                print("received message: %s" % data)

    def send_message(self):
        pass

    def broadcast_lobby(self):
        session_id = input()

        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("localhost", LOBBY_PORT))
                p = convert_to_json(
                    LobbyPresence(
                        lobby_peer_id = 67676767, 
                        display_name = self.hostname, 
                        host = "localhost",
                        port = LOBBY_PORT)
                        ).encode('utf-8')

                packet = Packet(
                    ver = 1,
                    type = PayloadType.LOBBY_PRESENCE.value,
                    flags=0,
                    reserved = 0,
                    session_id = session_id,
                    sender_peer_id= 0xFFFF,
                    payload_length=len(p),
                    seq_num=0,
                    ack_num=0,
                    payload=p
                )

                sock.sendto(serialize_packet(packet), ("localhost", 7777))
                print(packet)
                time.sleep(T_LOBBY_ANNOUNCE_INTERVAL)