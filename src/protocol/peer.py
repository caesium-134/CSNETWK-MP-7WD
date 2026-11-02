import socket
import time
import threading
from protocol import verbose

from sys import exit

from protocol.datatypes import *
from protocol.packet import *
from protocol.payload import *
from protocol.timings import *
from protocol.lobby import *
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
        self.lobby = Lobby(name, self)

        self.lobby_socket = None
        self.game_socket = None

    def start(self):
        try:
            self.lobby_socket = self.init_socket(LOBBY_PORT)
            self.lobby.start()
        except OSError:
            print("Error err")
        finally:
            if self.lobby_socket:
                self.lobby_socket.close()
        

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
