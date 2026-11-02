
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
    def __init__(self, name, peer_host):
        self.threads = []
        self.roster = []
        self.hostname = name
        self.session_id = None

        self.peer_host = peer_host

    # Lobby loop
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
    
    # Broadcast to everyone in lobby
    def broadcast_lobby(self, searching):
        lobby_peer_id = gen_lobby_peer()

        while searching.is_set():
            try:
                p = LobbyPresence(
                    lobby_peer_id = lobby_peer_id, 
                    display_name = self.hostname, 
                    host = HOST_ADDRESS, 
                    port = self.peer_host.lobby_socket.getsockname()[1] #game_socket a.k.a. no idea how to implement as of yet
                )

                packet = Packet(
                    ver = 1,
                    type = PayloadType.LOBBY_PRESENCE.value,
                    flags=0,
                    reserved = 0,
                    session_id = self.session_id,
                    sender_peer_id= 0xFFFF,
                    payload_length=0,
                    seq_num=0,
                    ack_num=0,
                    payload=p
                )

                self.peer_host.send_message(("<broadcast>", LOBBY_PORT), packet, self.peer_host.lobby_socket)
                time.sleep(T_LOBBY_ANNOUNCE_INTERVAL)

            except socket.timeout:
                continue

    # Listens for incoming messages in lobby phase
    def listen_for_messages(self, searching, roster):
        prev_roster_len = len(roster)
        print(f"{roster} | {prev_roster_len}/8")

        while searching.is_set() and prev_roster_len <= 8:
            try:
                data, addr = self.peer_host.receive_message(self.peer_host.lobby_socket)

                if data != None and addr != None:
                    if data.type == PayloadType.LOBBY_PRESENCE.value and data.session_id == self.session_id:
                        display_name = data.payload["display_name"]
                        if display_name not in roster:
                            roster.append(display_name)
                            prev_roster_len = len(roster)
                            print(f"{roster} | {prev_roster_len}/8")
            
            except socket.timeout:
                continue
