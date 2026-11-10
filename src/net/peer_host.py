import socket
import time
import threading
from protocol import verbose

from sys import exit

from protocol.packet import *
from protocol.payload import *
from protocol.timings import *
from protocol.configs import * 


#to-do:
# implement udpee / mesh
# implement system to host mesh
#   every peer connects to everyone else
# implement directed/broadcast message
#   sending message/receiving
# implement fallback/
# make udp more stable/reliable

# get self
# get a peer id
# get all list of peers

class Peer_Host():
    def __init__(self, name):
        self.name = name
        self.channels = {}  
        self.lobby_socket = self.open_lobby()
        self.game_socket = self.open_game()

        self.peers = []

    def open_lobby(self):
        self.lobby_socket = self.init_socket(LOBBY_PORT)
        return self.lobby_socket

    def close_lobby(self):
        if self.lobby_socket:
            self.lobby_socket.close()

        # Reset to null
        self.lobby_socket = None

    def open_game(self):
        self.game_socket = self.init_socket(GAME_PORT)
        return self.game_socket

    def close_game(self):
        if self.game_socket:
            self.game_socket.close()

        # Reset to null
        self.game_socket = None
        
    def send_message(self, address, message, socket):
        '''Sends a message to other peers on a socket'''
        try:
            socket.sendto(serialize_packet(message), address)
            verbose.log_sent(self.name, address, message) # prints log if verbose mode is on
        except OSError:
            print("Error sending message")


    def receive_message(self, socket): 
        '''Receives a message on a socket from another peer'''
        try:
            message, addr = socket.recvfrom(4096)
            if message != None and addr != None:
                message = deserialize_packet(message)
                verbose.log_received(self.name, addr, message) 
                return message, addr
            else:
                return None, None
        except socket.timeout:
            return None, None
        except OSError:
            print("Error receiving message")
            return None, None

    def init_socket(self, port):
        '''Initialize a socket given a port'''
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", port))
            return sock
        except OSError:
            print("Can't init socket")

    def host_to_peer(self):
        return (self.name, self.game_socket.getsockname()[0], self.game_socket.getsockname()[1])