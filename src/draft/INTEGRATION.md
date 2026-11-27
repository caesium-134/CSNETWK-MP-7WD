# Draft engine integration guide (Zed)

Files: `cards.py`, `draft.py`, `economy.py`, `stats.py`, `state.py`, `turn.py`.
Offline tests: `python simulate.py`, `python test_cheats.py`, `python test_state.py`.

## One object per peer
```python
state = GameState(my_seat, ring, min_players=3)      # ring = seats in seating order (Natt's ceremony)
hooks = ManagerHooks(disconnect_mgr, scoring_mgr)    # Nori's two managers (DisconnectManager first)
peer  = DraftPeer(my_seat, k, boards, db, state=state, hooks=hooks)
```
Everyone shares the SAME `state.live_peers` set. Nori's code removes a seat from it when that
peer is disconnected. The draft engine then skips that seat for hand passing and barriers.

## What the engine fills in: `state.log[seat]`
`shields` (cumulative), `t`, `c`, `g`, `w` (wildcards), `wonder`, `civilian`, `commercial`, `guilds`,
and `coins` for MY seat only (others are None, wonder-stage purchases are private).
Updated after every ACTION_ANNOUNCE. Nori owns `mil_tokens` and `defeats`
(Strategists Guild needs `defeats`). `stats.science_score(t, c, g, w)` gives the science total.

## Hooks the engine calls (Nori)
| When | Call |
|---|---|
| after every CARD_COMMIT barrier (including when nobody is missing) | `on_turn_barrier(missing_seats)` |
| after turn 6 of each Age (`peer.end_age()`) | `start_age_end(age)` |
| when a trade fails (`peer.on_trade_timeout(seat)`) | `initiate_timeout_consensus(seat)` |
`tick()` on the managers is called by whoever owns the main loop, not the draft engine.

## Calls from the network layer (Mirai) and trading (Natt)
- incoming types 0x0A to 0x0D: `on_card_commit`, `on_card_reveal`, `on_action_announce`, `on_hand_pass`
  (all take `(sender_seat, payload_dict)`; header peer ID must be converted to a seat first)
- main loop, every ~100 ms: `peer.poll_barrier()` returns None while waiting, then the set of seats
  that missed the commit barrier. When it returns, call `make_reveal()` and broadcast it.
- resolve: `buys = peer.prepare_resolve()`, Natt runs RESOURCE_QUERY/OFFER/BUY for each,
  then `peer.commit_resolve()` returns the ACTION_ANNOUNCE payload to broadcast.
  If no neighbor answers: `peer.on_trade_timeout(neighbor_seat)` then `peer.commit_resolve()`.
- seller side: `peer.offer_for(buyer_seat, resource)` gives qty_available and price_per_unit;
  `peer.on_resource_buy(payload)` credits coins.
