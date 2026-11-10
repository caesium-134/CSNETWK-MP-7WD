
#magoo

#peer class contain the peers in a lobby, including the user

import time
from protocol.timings import *

class LobbyPeer:
    def __init__(self, id, tuple):
        self.lobby_peer_id = id
        self.display_name = tuple[0]
        self.addr = tuple[1]
        self.port = tuple[2]
        
        self.lifetime = time.monotonic()
        self.is_host = False

    def set_host(self, bool):
        self.is_host = bool

    def print_name(self):
        print(f"{self.display_name}", end=" ")

    def refresh_lifetime(self):
        self.lifetime = time.monotonic()

    # T LOBBY STALE
    def check_lifetime(self):
        if self.is_host:
            return False
        else:
            if time.monotonic() - self.lifetime > T_LOBBY_STALE:
                return True
            else:
                return False