
import socket
import time
import threading
from sys import exit

from protocol.packet import *
from net.lobby_peer import *
from protocol.payload.payload import *
from protocol.payload.payloadtypes import *
from protocol.timings import *
from protocol.configs import *


class Lobby():
    def __init__(self, name, peer_host):
        self.threads = []
        self.hostname = name
        self.session_id = None

        self.roster = []

        self.peer_host = peer_host
        self.roster_lock = threading.Lock()

    # Lobby loop
    def start(self, room_code):

        self.session_id = gen_session_id(room_code)
        self.lobby_peer_id = gen_lobby_peer()

        host_peer = LobbyPeer(self.lobby_peer_id, self.peer_host.host_to_peer())
        host_peer.set_host(True)
        self.add_roster(host_peer)

        searching = threading.Event()
        searching.set()

        broadcast_thread = threading.Thread(target=self.broadcast_lobby, args=(searching,))
        listen_thread = threading.Thread(target=self.listen_for_messages, args=(searching,))
        culling_thread = threading.Thread(target=self.cull_inactive, args=(searching,))

        self.threads.append(broadcast_thread)
        self.threads.append(listen_thread)
        self.threads.append(culling_thread)
      
        for t in self.threads:
            t.start()

        try:
            time.sleep(T_LOBBY_TIMEOUT)
        except KeyboardInterrupt:
            searching.clear()
            self.leave_lobby()
            for t in self.threads:
                t.join(timeout=2)
            return None, None

        searching.clear()

        print("Ending Lobby Phase")
        self.session_id = None

        with self.roster_lock:
            if MIN <= len(self.roster) <= MAX:
                return self.roster, self.session_id
            else:
                return None, None

    # Broadcast to everyone in lobby
    def broadcast_lobby(self, searching):
        while searching.is_set():
            try:
                p = LobbyPresence(
                    lobby_peer_id = self.lobby_peer_id,
                    display_name = self.hostname,
                    host = HOST_ADDRESS,
                    port = self.peer_host.game_socket.getsockname()[1]
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
    def listen_for_messages(self, searching):
        while searching.is_set():
            try:
                data, addr = self.peer_host.receive_message(self.peer_host.lobby_socket)
                if data is None or addr is None:
                    continue

                if data.session_id != self.session_id:
                    continue

                if data.payload.get("lobby_peer_id") == self.lobby_peer_id:
                    continue

                match data.type:
                    case PayloadType.LOBBY_PRESENCE.value:
                        if data.payload["display_name"] != self.hostname:
                            peer = LobbyPeer(
                                data.payload["lobby_peer_id"],
                                (
                                data.payload["display_name"],
                                data.payload["host"], data.payload["port"])
                            )
                            peer.set_host(False)
                            self.add_roster(peer)

                    case PayloadType.LOBBY_LEAVE.value:
                        lobby_peer_id = data.payload["lobby_peer_id"]
                        if lobby_peer_id != self.lobby_peer_id:
                            self.remove_roster(lobby_peer_id)

                    case PayloadType.LOBBY_START_VOTE.value:
                        pass

                    case PayloadType.LOBBY_START_ACK.value:
                        pass

            except socket.timeout:
                continue

    def cull_inactive(self, searching):
        while searching.is_set():
            with self.roster_lock:
                for peer in self.roster:
                    if peer.check_lifetime():
                        self.roster.remove(peer)

    def add_roster(self, new_peer):
        with self.roster_lock:
            for peer in self.roster:
                if peer.lobby_peer_id == new_peer.lobby_peer_id:
                    peer.refresh_lifetime()
                    return False

            if len(self.roster) >= MAX:
                return False

            self.roster.append(new_peer)
            self.roster.sort(key=lambda peer: peer.display_name.lower())

            print("Adding: ")
            print("[ ", end="")
            for peer in self.roster:
                peer.print_name()
            print("] ", end="")

            print(f"{len(self.roster)}/{MAX}")

            return True

    def remove_roster(self, lobby_peer_id):
        with self.roster_lock:
            for peer in self.roster:
                if peer.lobby_peer_id == lobby_peer_id:
                    if self.roster.remove(peer):
                        print("Removing: ")
                        print("[ ", end="")
                        for peer in self.roster:
                            peer.print_name()
                        print("] ", end="")

                        print(f"{len(self.roster)}/{MAX}")

            return True

    def leave_lobby(self):
        try:
            leave = LobbyLeave(
                lobby_peer_id = self.lobby_peer_id,
            )
            
            packet = Packet(
                ver=1,
                type=PayloadType.LOBBY_LEAVE.value,
                flags=0,
                reserved=0,
                session_id=self.session_id,
                sender_peer_id=0xFFFF,
                payload_length=0,
                seq_num=0,
                ack_num=0,
                payload=leave
            )

            # broadcasts
            for _ in range(5):
                self.peer_host.send_message(("<broadcast>", LOBBY_PORT), packet, self.peer_host.lobby_socket)
                time.sleep(0.05)
            
        except Exception:
            pass

    def start_vote(self):
        pass

    def ack_start_vote(self):
        pass