# natt
# implementation of verbose mode 

import time

VERBOSE = False # verbose mode is off by default 

def set_verbose(value: bool):
    global VERBOSE 
    VERBOSE = value 

def is_verbose() -> bool:
    return VERBOSE 

# returns current local time as a string 
def _ts() -> str: 
    return time.strftime("%H:%M:%S")

# e.g. [14:32:07] [John] SENT -> ('127.0.0.0', 5000): Hello 
def log_sent(peer_name, dest, message): 
    if VERBOSE: 
        print(f"[{_ts()}] [{peer_name}] SENT -> {dest}: {message}") 

def log_received(peer_name, source, message):
    if VERBOSE: 
        print(f"[{_ts()}] [{peer_name}] RECEIVED <- {source}: {message}") 
