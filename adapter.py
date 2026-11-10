"""Adapter toolkit: turn whatever the contest API gives us into the full `state` dict MyBot expects,
and turn MyBot's answer back into the contest's action format. Standard library only; it is copied
into submission/bot.py by build_submission.py.

On contest day you write ONE small function, `to_state(raw)`, that pulls fields out of their format
and calls `build_state(...)` with whatever is available. Everything not provided gets a safe default.
"""

RANKS = "23456789TJQKA"
SUITS = "cdhs"
_SUIT_ALIASES = {"c": 0, "d": 1, "h": 2, "s": 3, "♣": 0, "♦": 1, "♥": 2, "♠": 3,
                 "clubs": 0, "diamonds": 1, "hearts": 2, "spades": 3}
_RANK_ALIASES = {"10": 8, "t": 8, "j": 9, "q": 10, "k": 11, "a": 12, "1": 12}


def parse_rank(r):
    """'A', 'a', '10', 'T', 14, 12 (0-based) ... -> 0..12 (0 = '2', 12 = 'A')."""
    if isinstance(r, int):
        if 2 <= r <= 14:
            return r - 2          # 2..14 convention (most common)
        raise ValueError(f"ambiguous int rank {r}: convert it yourself")
    r = str(r).strip().lower()
    if r in _RANK_ALIASES:
        return _RANK_ALIASES[r]
    if r.isdigit() and 2 <= int(r) <= 9:
        return int(r) - 2
    raise ValueError(f"unknown rank {r!r}")


def parse_suit(x):
    x = str(x).strip().lower()
    if x in _SUIT_ALIASES:
        return _SUIT_ALIASES[x]
    if x[:1] in _SUIT_ALIASES:
        return _SUIT_ALIASES[x[:1]]
    raise ValueError(f"unknown suit {x!r}")


def parse_card(c, int_scheme="rank_major"):
    """Card in almost any format -> our int (rank * 4 + suit).

    Strings: 'Ah', 'AH', 'ah', '10h', 'Th', 'A♥', 'Ace of Hearts'.
    Tuples/lists: ('A', 'h'), (14, 'h'), ('h', 'A') is NOT supported (rank first).
    Dicts: {'rank': 'A', 'suit': 'h'}.
    Ints: int_scheme='rank_major' (rank*4+suit, ours), 'suit_major' (suit*13+rank).
    """
    if isinstance(c, int):
        if int_scheme == "rank_major":
            return c
        if int_scheme == "suit_major":
            return (c % 13) * 4 + c // 13
        raise ValueError("unknown int_scheme")
    if isinstance(c, dict):
        return parse_rank(c.get("rank", c.get("value"))) * 4 + parse_suit(c["suit"])
    if isinstance(c, (tuple, list)):
        return parse_rank(c[0]) * 4 + parse_suit(c[1])
    s = str(c).strip()
    if " of " in s.lower():  # 'Ace of Hearts'
        r, _, su = s.lower().partition(" of ")
        names = {"ace": "a", "king": "k", "queen": "q", "jack": "j", "ten": "10"}
        return parse_rank(names.get(r, r)) * 4 + parse_suit(su)
    return parse_rank(s[:-1]) * 4 + parse_suit(s[-1])


def parse_cards(cs, int_scheme="rank_major"):
    if cs is None:
        return []
    if isinstance(cs, str):  # 'AhKd' or 'Ah Kd' or 'Ah,Kd'
        cs = cs.replace(",", " ").split() if (" " in cs or "," in cs) else [cs[i:i + 2] for i in range(0, len(cs), 2)]
    return [parse_card(c, int_scheme) for c in cs]


def build_state(*, hole, board=(), pot, to_call, stack, big_blind, small_blind=None,
                seat=0, my_bet=0, current_bet=None, min_raise_to=None, max_raise_to=None,
                stacks=None, bets=None, folded=(), allin=(), history=None,
                players_left=None, active_in_hand=None, players_behind=None,
                hand_number=0, button=None, street=None, last_raise_size=None):
    """Full MyBot state from whatever is known. Only the first six arguments are required.

    stack      = MY chips behind (not counting my bet this street)
    my_bet     = my chips already in front of me this street
    stacks     = {seat: chips behind + this street's bet} for everyone dealt in (if known)
    history    = [(street, seat, kind, bet_to_after, is_allin)], kind in sb/bb/fold/check/call/raise
    """
    hole = list(hole)
    board = list(board)
    if street is None:
        street = {0: 0, 3: 1, 4: 2, 5: 3}[len(board)]
    if current_bet is None:
        current_bet = my_bet + to_call
    if max_raise_to is None:
        max_raise_to = my_bet + stack
    if min_raise_to is None:
        min_raise_to = current_bet + max(big_blind, last_raise_size or 0)
    min_raise_to = min(min_raise_to, max_raise_to)
    if stacks is None:
        # Unknown opponents: assume everyone has my stack (neutral for ICM and stack-depth logic).
        n = (players_left or active_in_hand or 2) - 1
        stacks = {seat: stack + my_bet}
        for k in range(1, max(1, n) + 1):
            stacks[seat + k] = stack + my_bet
    stacks = dict(stacks)
    stacks.setdefault(seat, stack + my_bet)
    if bets is None:
        bets = {t: 0 for t in stacks}
        bets[seat] = my_bet
        if current_bet > my_bet:
            # Someone made the bet I'm facing; credit it to an opponent so pot accounting stays right.
            opp = next((t for t in stacks if t != seat and t not in folded), None)
            if opp is not None:
                bets[opp] = current_bet
    bets = {t: bets.get(t, 0) for t in stacks}
    if players_left is None:
        players_left = len(stacks)
    if active_in_hand is None:
        active_in_hand = len(stacks) - len(folded)
    if players_behind is None:
        players_behind = max(0, (active_in_hand - 1) // 2)  # unknown position: assume the middle
    return {
        "seat": seat, "hole": tuple(hole), "board": board, "street": street,
        "pot": pot, "to_call": to_call, "stack": stack, "bet": my_bet,
        "current_bet": current_bet, "min_raise_to": min_raise_to, "max_raise_to": max_raise_to,
        "can_raise": stack > to_call, "small_blind": small_blind or big_blind // 2,
        "big_blind": big_blind, "button": button, "stacks": stacks, "bets": bets,
        "folded": sorted(folded), "allin": sorted(allin), "players_left": players_left,
        "active_in_hand": active_in_hand, "players_behind": players_behind,
        "hand_number": hand_number, "history": list(history or []),
    }


def convert_action(action, state, raise_mode="to"):
    """MyBot action -> (kind, amount) in the contest's convention.

    raise_mode: 'to'  = total bet this street (MyBot's own convention)
                'by'  = increase over the current highest bet
                'add' = chips to put in now (total minus what I already have in front of me)
    """
    kind = action[0]
    if kind != "raise":
        return kind, 0
    to = int(action[1])
    if raise_mode == "to":
        return "raise", to
    if raise_mode == "by":
        return "raise", to - state["current_bet"]
    if raise_mode == "add":
        return "raise", to - state["bet"]
    raise ValueError("raise_mode must be 'to', 'by' or 'add'")


class HandTracker:
    """Gives MyBot a hand counter and end-of-hand callbacks when the API only sends per-decision states.

    A new hand is detected when the hole cards change. If you can collect the hand's action history
    (in MyBot's (street, seat, kind, bet_to, is_allin) format), pass it to `end_hand` so opponent
    tracking works; without it the bot still plays, just without reads on opponents.
    """

    def __init__(self, bot):
        self.bot = bot
        self.hand_number = -1
        self._hole = None
        self.history = []

    def observe(self, hole):
        hole = tuple(sorted(hole))
        if hole != self._hole:
            if self._hole is not None:
                self.end_hand()
            self._hole = hole
            self.hand_number += 1
            self.history = []
        return self.hand_number

    def end_hand(self, history=None, shown=None, stacks=None):
        cb = getattr(self.bot, "on_hand_end", None)
        if cb:
            try:
                cb({"hand_number": self.hand_number, "history": history or self.history,
                    "shown": shown or {}, "board": [], "stacks": stacks or {}})
            except Exception:
                pass
