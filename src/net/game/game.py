from protocol.payload.payloadtypes import *

class Game:
    def __init__(self, roster, session_id):
        self.roster = roster
        self.session_id = session_id

    def run():
        pass

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
                    case PayloadType.CARD_COMMIT.value:
                        display_name = data.payload["display_name"]
                        if display_name != self.hostname:
                            self.add_roster(display_name)

                    case PayloadType.CARD_REVEAL.value:
                        display_name = data.payload["display_name"]
                        if display_name != self.hostname:
                            self.remove_roster(display_name)

                    case PayloadType.ACTION_ANNOUNCE.value:
                        pass

                    case PayloadType.HAND_PASS.value:
                        pass

                    case PayloadType.RESOURCE_QUERY.value:
                        pass

                    case PayloadType.RESOURCE_OFFER.value:
                        pass

                    case PayloadType.RESOURCE_BUY.value:
                        pass

                    case PayloadType.RESOURCE_BUY.value:
                        pass

            except socket.timeout:
                continue