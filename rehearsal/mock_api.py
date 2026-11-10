"""A made-up contest API, deliberately unlike our engine, for rehearsing contest day.

Raw state the 'contest' sends:
  {"hand_id": "h-17", "you": "p3", "hole_cards": ["10H", "AS"], "community_cards": ["2C", "KD", "9S"],
   "pot": 340, "big_blind": 40, "small_blind": 20, "dealer": "p1",
   "players": [{"id": "p0", "chips": 1800, "bet_this_round": 40, "status": "active"|"folded"|"all_in"}, ...],
   "acting_order": ["p4", "p5", ...],        # remaining seats to act after you this round
   "call_amount": 40, "min_raise_by": 40, "max_raise_by": 1760}
Expected reply: {"action": "fold"|"check"|"call"|"raise", "amount": <raise BY this much over the call>}
No action history is provided.
"""
RANK_TXT = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUIT_TXT = "CDHS"


def card_txt(c):
    return RANK_TXT[c >> 2] + SUIT_TXT[c & 3]


def to_raw(s):
    pid = lambda t: f"p{t}"
    players = []
    for t, total in s["stacks"].items():
        status = "folded" if t in s["folded"] else "all_in" if t in s["allin"] else "active"
        players.append({"id": pid(t), "chips": total - s["bets"][t], "bet_this_round": s["bets"][t], "status": status})
    return {
        "hand_id": f"h-{s['hand_number']}", "you": pid(s["seat"]),
        "hole_cards": [card_txt(c) for c in s["hole"]], "community_cards": [card_txt(c) for c in s["board"]],
        "pot": s["pot"], "big_blind": s["big_blind"], "small_blind": s["small_blind"], "dealer": pid(s["button"]),
        "players": players, "acting_order": [f"x{i}" for i in range(s["players_behind"])],
        "call_amount": s["to_call"], "min_raise_by": s["min_raise_to"] - s["current_bet"],
        "max_raise_by": s["max_raise_to"] - s["current_bet"], "_players_left": s["players_left"],
    }


def from_reply(reply, s):
    """Contest reply -> our engine's action."""
    a = reply.get("action")
    if a == "raise":
        return ("raise", s["current_bet"] + int(reply.get("amount", 0)))
    return (a,)
