"""Parameterized sit-and-go bot: preflop ranges, push/fold when short, pot-odds postflop,
ICM tightening near the bubble, and a simple maniac detector. Every decision is a few
lookups and arithmetic, so it stays far below the 1 ms limit."""
import json
import os
import random

from cards import (evaluate, straight_high, HIGH, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE)
from preflop import TABLE, hand_key

DEFAULT_PARAMS = {
    "open_pct": 0.12,         # unopened raise range, earliest position (fraction of hands)
    "open_late_bonus": 0.18,  # extra range when last to act
    "open_size": 2.5,         # open raise in big blinds (+1 per limper)
    "threebet_pct": 0.025,    # re-raise range vs a normal raise
    "call_raise_pct": 0.06,   # flat-call range vs a normal raise
    "push_bb": 12.0,          # at or below this many big blinds: push/fold only
    "push_base": 0.10,        # push range at push_bb from early position
    "push_exp": 1.0,          # how fast the push range widens as the stack shrinks
    "push_late_mult": 2.5,    # push range multiplier when last to act
    "call_allin_pct": 0.07,   # call range vs a shove at even pot odds
    "bubble_tighten": 0.65,   # multiply calling ranges by this near the money / cap
    "maniac_widen": 2.2,      # multiply calling ranges vs detected maniacs
    "maniac_rate": 0.15,      # preflop-shove frequency that marks a maniac
    "bet_eq": 0.55,           # postflop: bet for value above this strength
    "raise_eq": 0.82,         # postflop: raise above this strength
    "call_margin": 0.03,      # postflop: need this much over pot odds to call
    "icm_margin": 0.06,       # extra margin when ICM pressure applies
    "value_frac": 0.66,       # value bet size as a fraction of the pot
    "cbet": 0.55,             # heads-up flop continuation-bet frequency
}

BOUNDS = {
    "open_pct": (0.04, 0.35), "open_late_bonus": (0.0, 0.5), "open_size": (2.0, 4.0),
    "threebet_pct": (0.005, 0.1), "call_raise_pct": (0.01, 0.2), "push_bb": (6.0, 20.0),
    "push_base": (0.03, 0.4), "push_exp": (0.3, 2.0), "push_late_mult": (1.0, 5.0),
    "call_allin_pct": (0.02, 0.3), "bubble_tighten": (0.3, 1.0), "maniac_widen": (1.0, 5.0),
    "maniac_rate": (0.05, 0.4), "bet_eq": (0.3, 0.85), "raise_eq": (0.6, 0.98),
    "call_margin": (-0.1, 0.2), "icm_margin": (0.0, 0.2), "value_frac": (0.3, 1.2), "cbet": (0.0, 1.0),
}

TUNED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_params_v1.json")


def load_tuned():
    if os.path.exists(TUNED_PATH):
        with open(TUNED_PATH) as f:
            return json.load(f)
    return None


class MyBot:
    def __init__(self, params=None, seed=None):
        self.p = dict(DEFAULT_PARAMS)
        if params:
            self.p.update(params)
        self.rng = random.Random(seed)
        self.stats = {}  # seat -> [hands seen, preflop shoves]

    # ---------- engine hooks ----------
    def get_action(self, s):
        try:
            return self.preflop(s) if s["street"] == 0 else self.postflop(s)
        except Exception:
            return self.passive(s)

    def on_hand_end(self, info):
        seen, shoved = set(), set()
        for street, seat, kind, _amt, allin in info["history"]:
            seen.add(seat)
            if street == 0 and kind == "raise" and allin:
                shoved.add(seat)
        for seat in seen:
            st = self.stats.setdefault(seat, [0, 0])
            st[0] += 1
            st[1] += seat in shoved

    # ---------- helpers ----------
    @staticmethod
    def passive(s):
        return ("check",) if s["to_call"] == 0 else ("fold",)

    @staticmethod
    def raise_to(s, target):
        if not s["can_raise"]:
            return ("call",)
        target = int(max(s["min_raise_to"], min(target, s["max_raise_to"])))
        if target >= 0.7 * s["max_raise_to"]:  # don't leave a sliver behind
            target = s["max_raise_to"]
        return ("raise", target)

    def is_maniac(self, seat):
        st = self.stats.get(seat)
        return bool(st) and st[0] >= 12 and st[1] / st[0] > self.p["maniac_rate"]

    @staticmethod
    def last_raiser(s):
        for street, seat, kind, _a, _ai in reversed(s["history"]):
            if street == s["street"] and kind == "raise":
                return seat
        return None

    @staticmethod
    def icm_pressure(s):
        return s["players_left"] <= 4 or s["hand_number"] >= 175

    # ---------- preflop ----------
    def preflop(self, s):
        p = self.p
        bb = s["big_blind"]
        pct = TABLE[hand_key(*s["hole"])]["pct"]
        me = s["seat"]
        mine = s["stack"] + s["bet"]
        biggest_other = max((v for t, v in s["stacks"].items() if t != me), default=mine)
        eff_bb = min(mine, biggest_other) / bb
        late = 1.0 - s["players_behind"] / max(1, s["players_left"] - 1)
        icm = p["bubble_tighten"] if self.icm_pressure(s) else 1.0
        to_call = s["to_call"]

        if s["current_bet"] <= bb:  # unopened (maybe limped)
            if eff_bb <= p["push_bb"]:
                rng = p["push_base"] * (p["push_bb"] / max(eff_bb, 1.0)) ** p["push_exp"]
                rng *= 1 + (p["push_late_mult"] - 1) * late
                return self.raise_to(s, s["max_raise_to"]) if pct <= rng else self.passive(s)
            if pct <= p["open_pct"] + p["open_late_bonus"] * late:
                limpers = sum(1 for st, _, k, _a, _ai in s["history"] if st == 0 and k == "call")
                return self.raise_to(s, bb * (p["open_size"] + limpers))
            return self.passive(s)

        widen = p["maniac_widen"] if self.is_maniac(self.last_raiser(s)) else 1.0
        if to_call >= 0.4 * s["stack"] or eff_bb <= p["push_bb"]:
            # effectively an all-in decision; cheaper prices justify wider calls
            odds = to_call / (s["pot"] + to_call)
            rng = p["call_allin_pct"] * icm * widen * max(0.5, 0.45 / max(odds, 0.05))
            return self.raise_to(s, s["max_raise_to"]) if pct <= min(rng, 1.0) else self.passive(s)
        if pct <= p["threebet_pct"] * widen:
            return self.raise_to(s, s["current_bet"] * 3)
        if pct <= p["call_raise_pct"] * icm * widen:
            return ("call",)
        return self.passive(s)

    # ---------- postflop ----------
    @staticmethod
    def made_strength(hole, board):
        v = evaluate(hole + board)
        cat = v >> 20
        bcat = evaluate(board) >> 20
        if cat <= bcat:
            return 0.12  # playing the board
        branks = sorted((c >> 2 for c in board), reverse=True)
        hranks = [c >> 2 for c in hole]
        if cat == PAIR:
            pr = ((v >> 16) & 15) - 1
            if hranks[0] == hranks[1] and pr > branks[0]:
                return 0.72  # overpair
            if pr == branks[0]:
                kicker = max((r for r in hranks if r != pr), default=12)
                return 0.6 if kicker >= 9 else 0.52  # top pair
            return 0.42 if pr >= branks[1] else 0.3
        if cat == TWO_PAIR:
            return 0.5 if bcat == PAIR else 0.78
        if cat == TRIPS:
            return 0.75 if bcat == PAIR else 0.86
        return {STRAIGHT: 0.87, FLUSH: 0.91, FULL_HOUSE: 0.96}.get(cat, 0.99)

    @staticmethod
    def draw_equity(hole, board, street):
        if street == 3:
            return 0.0
        cards = hole + board
        outs = 0.0
        for su in range(4):
            if sum(1 for c in cards if c & 3 == su) == 4 and any(c & 3 == su for c in hole):
                outs += 9
        mask = 0
        for c in cards:
            mask |= 1 << (c >> 2)
        if straight_high(mask) < 0:
            ranks = sum(1 for r in range(13) if not mask >> r & 1 and straight_high(mask | 1 << r) >= 0)
            outs += min(8, 4 * ranks) * (0.7 if outs else 1.0)
        return min(0.6, outs * (0.04 if street == 1 else 0.02))

    def postflop(self, s):
        p = self.p
        hole, board = list(s["hole"]), s["board"]
        n_opp = max(1, s["active_in_hand"] - 1)
        made = self.made_strength(hole, board) ** (1 + 0.6 * (n_opp - 1))
        e = max(made, self.draw_equity(hole, board, s["street"]))
        pot, to_call = s["pot"], s["to_call"]

        if to_call == 0:
            if e >= p["bet_eq"]:
                return self.raise_to(s, s["bet"] + p["value_frac"] * pot)
            raised_pre = any(st == 0 and seat == s["seat"] and k == "raise"
                             for st, seat, k, _a, _ai in s["history"])
            if raised_pre and n_opp == 1 and s["street"] == 1 and self.rng.random() < p["cbet"]:
                return self.raise_to(s, s["bet"] + 0.5 * pot)
            return ("check",)

        odds = to_call / (pot + to_call)
        if e >= p["raise_eq"]:
            return self.raise_to(s, s["current_bet"] + max(s["big_blind"], p["value_frac"] * (pot + to_call)))
        need = odds + p["call_margin"] + (p["icm_margin"] if self.icm_pressure(s) else 0.0)
        return ("call",) if e >= need else ("fold",)
