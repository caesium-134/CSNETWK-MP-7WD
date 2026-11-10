# maggo
import time
import threading
from protocol.configs import *
from protocol.timings import *

from protocol.packet import *
from protocol.payload.payload import *
from protocol.payload.payloadtypes import *

class Chat:
    def __init__(self, peer_host, peers):
        self.threads = []

        self.peer_host = peer_host
        self.peers = peers

    # chat loop
    def start(self, room_code):
        with self.roster_lock:
            self.roster = [self.hostname]
            self.peer_addrs = {}

        searching = threading.Event()
        searching.set()

        broadcast_thread = threading.Thread(target=self.broadcast_lobby, args=(searching,))
        listen_thread = threading.Thread(target=self.listen_for_messages, args=(searching,))

        self.threads.append(broadcast_thread)
        self.threads.append(listen_thread)
      
        for t in self.threads:
            t.start()

        try:
            time.sleep(T_LOBBY_TIMEOUT)
        except KeyboardInterrupt:
            searching.clear()
            self.leave_lobby()
            return None, None

        searching.clear()

    def stop(self):
        for t in self.threads:
            t.join(timeout=2)


    def direct_message(self, string):
        try:
            p = GroupMessage(
                text=string
            )

            packet = Packet(
                ver = 1,
                type = PayloadType.GROUP_MESSAGE.value,
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
            pass

    # Broadcast to everyone in lobby
    def group_message(self, string):
        try:
            p = GroupMessage(
                text=string
            )

            packet = Packet(
                ver = 1,
                type = PayloadType.GROUP_MESSAGE.value,
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
            pass


    # Listens for incoming messages in lobby phase
    def listen_for_messages(self, searching):
        with self.roster_lock:
            print(f"{self.roster} | {len(self.roster)}/{MAX}")

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
                    case PayloadType.DIRECT_MESSAGE.value:
                        display_name = data.payload["display_name"]
                        if display_name != self.hostname:
                            self.add_roster(display_name)

                    case PayloadType.GROUP_MESSAGE.value:
                        display_name = data.payload["display_name"]
                        if display_name != self.hostname:
                            self.remove_roster(display_name)

            except socket.timeout:
                continue