# magoo
# main entrypoint that enables verbose mode on/off, doesn't do anything
# unless you define verbose stuff elsewhere ¯\_(ツ)_/¯

import argparse

from protocol.peer import Peer_Host
from protocol.verbose import set_verbose 

# Initialize main for verbose mode
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--v', action='store_true')
    args = parser.parse_args()

    set_verbose(args.v) 
    
    if args.v:
        print("\nVerbose Mode activated\n")

    print("Starting up 7WD.")
    print("Enter host name.")
    name = input()
    peer_host = Peer_Host(name)
    peer_host.start()


