"""Draft turn helpers (Member 3): dealing, hand rotation, commit/reveal, chain check."""
import hashlib
import os
import random


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---- Commit / reveal (RFC 7.8 steps 1 and 3) ----
# NOTE: the RFC writes SHA256(card_id || action || nonce) with no separator.
# We use plain string concatenation, nonce as 64 lowercase hex chars.
# Agree on this with your group (and any group you test with).

def make_nonce() -> str:
    return os.urandom(32).hex()


def make_commitment(card_id: str, action: str, nonce: str) -> str:
    return sha256_hex(card_id + action + nonce)


def verify_reveal(commitment: str, action: str, nonce: str, card_id=None) -> bool:
    """For BUILD the reveal carries card_id so we can fully check.
    For WONDER_STAGE / DISCARD card_id is hidden, so we can't check it
    (only optional voluntary disclosure later)."""
    if card_id is None:
        return True   # nothing to verify without card_id; see RFC 7.8 step 3
    return make_commitment(card_id, action, nonce) == commitment


# ---- Dealing (RFC 7.7) ----

def build_age_deck(cards, age, k, guild_names=None, rng=None):
    """Return a shuffled list of card names for this Age and player count k.
    For age 3 pass the (k+2) guild names derived from the shared seed.
    rng must be the seeded PRNG so every peer shuffles identically."""
    rng = rng or random.Random()
    deck = []
    for c in cards.values():
        if c.color != "Purple":
            deck += [c.name] * c.counts.get(age, {}).get(k, 0)
    if age == 3 and guild_names:
        deck += list(guild_names)
    assert len(deck) == 7 * k, f"deck has {len(deck)} cards, expected {7 * k}"
    rng.shuffle(deck)
    return deck


def deal_hands(deck, k):
    """Slice the deck into k hands of 7. Seat i takes hand i."""
    return [deck[i * 7:(i + 1) * 7] for i in range(k)]


def pass_direction(age):
    """Ages I and III pass left, Age II passes right (RFC section 3)."""
    return "LEFT" if age in (1, 3) else "RIGHT"


# ---- Chaining check (RFC 7.8 step 5) ----

def chain_ok(card, built_names):
    """A zero-cost BUILD is only legal if one of card.chain_from was built earlier."""
    return bool(card.chain_from) and any(n in built_names for n in card.chain_from)


if __name__ == "__main__":
    n = make_nonce()
    com = make_commitment("Clay Pit", "BUILD", n)
    print("commit ok:", verify_reveal(com, "BUILD", n, "Clay Pit"))
    print("tampered:", verify_reveal(com, "BUILD", n, "Stone Pit"))
