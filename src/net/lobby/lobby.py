
import select
import socket
import sys
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

        # T_LOBBY_QUIET
        self.roster_timer = time.monotonic()
        self.can_vote = False

        self.start_requested = threading.Event()
        self.p_ack_cntr = 0

    # Lobby loop
    def start(self, room_code):
        print("Entering Lobby Phase")

        self.session_id = gen_session_id(room_code)
        self.lobby_peer_id = gen_lobby_peer()
        lobby_start_time = time.monotonic() 

        host_peer = LobbyPeer(self.lobby_peer_id, self.peer_host.host_to_peer())
        host_peer.set_host(True)
        self.add_roster(host_peer)

        searching = threading.Event()
        searching.set()

        self.threads = [
        threading.Thread(target=self.broadcast_lobby, args=(searching,), daemon=True),
        threading.Thread(target=self.listen_for_messages, args=(searching,), daemon=True),
        threading.Thread(target=self.cull_inactive, args=(searching,), daemon=True),
        threading.Thread(target=self.input_thread, args=(searching,), daemon=True)
        ]

        for t in self.threads:
            t.start()

        try:
            while searching.is_set():
                if self.start_requested.is_set():
                    self.allow_vote()
                    self.start_requested.clear()

                if time.monotonic() - lobby_start_time > T_LOBBY_TIMEOUT:
                    print("Lobby duration expired. Closing lobby...")
                    searching.clear()
                    break
                    
                if not searching.wait(timeout=1.0): 
                    pass

        except KeyboardInterrupt:
            print("\nLobby interrupted by user")
        finally:
            searching.clear()

        print("Ending Lobby Phase")

        with self.roster_lock:
            if MIN <= len(self.roster) <= MAX:
                return self.session_id, self.peer_host, self.roster
            else:
                return None, None, None
            
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

                match data.type:
                    case PayloadType.LOBBY_PRESENCE.value:
                        peer = LobbyPeer(
                            data.payload["lobby_peer_id"],
                            (data.payload["display_name"],
                            data.payload["host"], data.payload["port"])
                        )
                        peer.set_host(False)
                        self.add_roster(peer)

                    case PayloadType.LOBBY_LEAVE.value:
                        lobby_peer_id = data.payload["lobby_peer_id"]
                        self.remove_roster(lobby_peer_id)

                    case PayloadType.LOBBY_START_VOTE.value:
                        participants = data.payload["participants"]

                        ids = [peer.lobby_peer_id for peer in self.roster]

                        if all(p in ids for p in participants):
                            self.ack_start_vote()

                    case PayloadType.LOBBY_START_ACK.value:
                        p_hash = data.payload["p_hash"]

                        p_list = []

                        for peer in self.roster:
                            p_list.append(peer.lobby_peer_id)

                        if compare_participant_hash(p_list, p_hash):
                            self.p_ack_cntr = self.p_ack_cntr + 1
                            if self.p_ack_cntr >= len(self.roster) and len(self.roster) >= MIN:
                                searching.clear()

            except socket.timeout:
                continue

    # Thread to remove inactive peers
    def cull_inactive(self, searching):
        while searching.is_set():
            with self.roster_lock:
                for peer in self.roster:
                    if peer.check_lifetime():
                        self.roster.remove(peer)
                        self.reset_roster_timeout()

    def reset_roster_timeout(self):
        self.roster_timer = time.monotonic()

    def add_roster(self, new_peer):
        with self.roster_lock:
            for peer in self.roster:
                if peer.lobby_peer_id == new_peer.lobby_peer_id:
                    peer.refresh_lifetime()
                    return False

            if len(self.roster) >= MAX:
                return False

            self.roster.append(new_peer)
            self.reset_roster_timeout()
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
                    self.roster.remove(peer)
                    self.reset_roster_timeout()
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
            for _ in range(3):
                self.peer_host.send_message(("<broadcast>", LOBBY_PORT), packet, self.peer_host.lobby_socket)
                time.sleep(0.05)
            
        except Exception:
            pass

    def allow_vote(self):
        with self.roster_lock:
            if len(self.roster) >= 3:
                print("Starting game...")
                self.p_ack_cntr = 0 

                participants = []

                for peer in self.roster:
                    participants.append(peer.lobby_peer_id)

                p = LobbyStartVote(
                    participants=participants
                )

                packet = Packet(
                    ver = 1,
                    type = PayloadType.LOBBY_START_VOTE.value,
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

            else:
                print("What the hell you wrote.")


    def ack_start_vote(self):
        with self.roster_lock:
            p_list = []

            for peer in self.roster:
                p_list.append(str(peer.lobby_peer_id))

            p = LobbyStartAck(
                p_hash=gen_participant_hash(p_list)
            )

            packet = Packet(
                ver = 1,
                type = PayloadType.LOBBY_START_ACK.value,
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

    def input_thread(self, searching):
        print("Type 'start' to begin: ")
        while searching.is_set():
            if time.monotonic() - self.roster_timer > T_LOBBY_TIMEOUT:
                try:
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        vote = sys.stdin.readline().strip()
                        
                        if vote == "start":
                            self.start_requested.set()
                        elif vote != "":
                            print("Invalid command.")
                            
                except EOFError:
                    break
                except Exception as e:
                    print(f"Input error: {e}")
                    break