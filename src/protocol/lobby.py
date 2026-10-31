
from dataclasses import asdict
import socket
import time
import threading
from sys import exit

from protocol.datatypes import *
from protocol.packet import *
from protocol.payload import *
from protocol.timings import *
from protocol.lobby import *
from protocol.configs import *

class Lobby():
    def __init__(self, name):
        self.threads = []
        self.roster = []
        self.hostname = name
        self.session_id = None

    def start(self):
        print("Enter room code:")
        self.session_id = gen_session_id(input())
        self.roster.append(self.hostname)

        print("Starting Lobby (3~7 Required)")

        searching = threading.Event()
        searching.set()
        broadcast_thread = threading.Thread(target=self.broadcast_lobby, args=(searching,))
        listen_thread = threading.Thread(target=self.listen_for_messages, args=(searching, self.roster))

        self.threads.append(broadcast_thread)
        self.threads.append(listen_thread)

        for t in self.threads:
            t.start()

        time.sleep(T_LOBBY_TIMEOUT)

        searching.clear()

        for t in self.threads:
            t.join()

        #TO-DO:
        #   further interaction between listener-announcer
        #   handle leaving players (w/)
        #   game socket saved somewhere else?
        #   forming session
    
    def broadcast_lobby(self, searching):
        lobby_peer_id = gen_lobby_peer()

        while searching.is_set():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(.5)  # ADD THIS LINE
                try:

                    sock.connect(("localhost", LOBBY_PORT))
                    p = LobbyPresence(
                        lobby_peer_id = lobby_peer_id, 
                        display_name = self.hostname, 
                        host = HOST_ADDRESS, 
                        port = sock.getsockname() #game_socket a.k.a. no idea how to implement as of yet
                        )

                    packet = Packet(
                    ver = 1,
                    type = PayloadType.LOBBY_PRESENCE.value,
                    flags=0,
                    reserved = 0,
                    session_id = self.session_id,
                    sender_peer_id= 0xFFFF,
                    payload_length=len(p.__str__()),
                    seq_num=0,
                    ack_num=0,
                    payload=asdict(p)
                    )

                    sock.sendto(serialize_packet(packet), ("localhost", 7777))
                    #print(packet)
                    time.sleep(T_LOBBY_ANNOUNCE_INTERVAL)
                except socket.timeout:
                    continue

    def listen_for_messages(self, searching, roster):
        prev_roster_len = len(roster)
        print(f"{roster} | {prev_roster_len}/8")

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(.5)  # ADD THIS LINE
            sock.bind(("", 7777))

            while searching.is_set() and prev_roster_len <= 8:
                try:
                    data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    continue

                data = deserialize_packet(data)

                if data.type == PayloadType.LOBBY_PRESENCE.value and data.session_id == self.session_id:
                    display_name = data.payload["display_name"]
                    if display_name not in roster:
                        roster.append(display_name)
                        prev_roster_len = len(roster)
                        print(f"{roster} | {prev_roster_len}/8")
