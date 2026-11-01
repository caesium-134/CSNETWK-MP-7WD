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
from protocol.verbose import * 

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
        self.lobby = Lobby(name)

    def start(self):
        self.lobby.start()

    def send_message(self, address, message):
        self.sock.sendto(message.encode(), address)
        verbose.log_sent(self.name, address, message) # prints log if verbose mode is on  

    def handle_incoming(self): 
        data, addr = self.sock.recvfrom(4096)
        message = data.decode() 
        verbose.log_received(self.name, addr, message) 
        # to-do: message processing
