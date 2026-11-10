"""v4: v3's preflop, push/fold and ICM all-in logic, plus a LEARNED postflop policy.

Postflop, a linear model over hand-made features picks the action:
  raise head    > 0  -> bet / raise
  continue head > 0  -> call (when facing a bet); otherwise fold
  size head          -> bet size, 0.25..1.5 x pot (sigmoid)
Weights W are trained by train_v4.py (evolution strategy, self-play against the AI field).
Hand strength comes from a small Monte Carlo (12 run-outs vs one random hand), cached per street.
"""
import json
import math
import os

from cards import evaluate
import mybot
from mybot import MyBot as V3

FEATURES = ["bias", "mc_eq", "mc_eq^n", "rule_strength", "draw", "pot_odds", "to_call/stack",
            "log_stack_bb", "n_opp", "raises_street", "street", "behind", "opp_fold_to_bet",
            "station", "icm_premium"]
NF = len(FEATURES)

# Starting weights: a hand-written policy roughly like v3 (bet/raise strong hands, call on pot odds).
W0 = [0.0] * (3 * NF)
_ix = {f: i for i, f in enumerate(FEATURES)}
W0[_ix["bias"]] = -3.5
W0[_ix["mc_eq^n"]] = 6.0                      # raise head
W0[NF + _ix["bias"]] = 0.2
W0[NF + _ix["mc_eq^n"]] = 4.0
W0[NF + _ix["pot_odds"]] = -5.0               # continue head
W0[2 * NF + _ix["bias"]] = -0.5               # size head

DEFAULT_PARAMS = dict(mybot.DEFAULT_PARAMS)
DEFAULT_PARAMS["mc_samples"] = 12
BOUNDS = dict(mybot.BOUNDS)
TUNED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_params_v4.json")


def load_tuned():
    """Saved v4 params; until v4 is trained, v3's tuned settings plus the starting weights."""
    for path in (TUNED_PATH, mybot.TUNED_PATH):
        if os.path.exists(path):
            with open(path) as f:
                saved = json.load(f)
            out = {k: v for k, v in saved.items() if k in DEFAULT_PARAMS or k == "W"}
            out.setdefault("W", list(W0))
            return out
    return {"W": list(W0)}


class MyBot(V3):
    def __init__(self, params=None, seed=None):
        params = dict(params or {})
        self.W = list(params.pop("W", W0))
        params.setdefault("mc_samples", DEFAULT_PARAMS["mc_samples"])
        super().__init__(params, seed)
        self._mc = {}

    def get_action(self, s):
        if s["hand_number"] != self._hand:
            self._mc.clear()
        return super().get_action(s)

    def mc_equity(self, hole, board):
        key = tuple(board)
        hit = self._mc.get(key)
        if hit is not None:
            return hit
        known = set(hole) | set(board)
        rest = [c for c in range(52) if c not in known]
        need = 5 - len(board)
        n = int(self.p["mc_samples"])
        w = 0.0
        sample = self.rng.sample
        for _ in range(n):
            d = sample(rest, 2 + need)
            b = board + d[2:]
            me = evaluate(hole + b)
            op = evaluate(d[:2] + b)
            w += 1.0 if me > op else 0.5 if me == op else 0.0
        self._mc[key] = eq = w / n
        return eq

    def features(self, s):
        hole, board = list(s["hole"]), s["board"]
        me = s["seat"]
        n_opp = max(1, s["active_in_hand"] - 1)
        eq = self.mc_equity(hole, board)
        rule = self.made_strength(hole, board)
        draw = self.draw_equity(hole, board, s["street"])
        pot, to_call = s["pot"], s["to_call"]
        opps = [t for t in s["stacks"] if t != me and t not in s["folded"]]
        ftb = sum(self.fold_to_bet(t) for t in opps) / len(opps) if opps else 0.5
        station = 1.0 if n_opp == 1 and opps and self.is_station(opps[0]) else 0.0
        raises = sum(1 for st, _, k, _a, _ai in s["history"] if st == s["street"] and k == "raise")
        eff = min(s["stacks"][me], max((v for t, v in s["stacks"].items() if t != me), default=1))
        prem = self.icm_premium(s, self.last_raiser(s)) if to_call else self.icm_premium(s)
        return [1.0, eq, eq ** n_opp, rule, draw,
                to_call / (pot + to_call) if to_call else 0.0,
                min(1.0, to_call / max(1, s["stack"])),
                math.log(max(1.0, eff / s["big_blind"])) / math.log(100),
                n_opp / 7, min(raises, 3) / 3, s["street"] / 3,
                s["players_behind"] / 7, ftb, station, prem]

    def postflop(self, s):
        x = self.features(s)
        W = self.W
        raise_v = sum(w * f for w, f in zip(W[:NF], x))
        cont_v = sum(w * f for w, f in zip(W[NF:2 * NF], x))
        size_v = sum(w * f for w, f in zip(W[2 * NF:], x))
        frac = 0.25 + 1.25 / (1 + math.exp(-max(-30.0, min(30.0, size_v))))
        pot, to_call = s["pot"], s["to_call"]
        if raise_v > 0 and s["can_raise"]:
            if to_call == 0:
                return self.raise_to(s, s["bet"] + frac * pot)
            return self.raise_to(s, s["current_bet"] + max(s["big_blind"], frac * (pot + to_call)))
        if to_call == 0:
            return ("check",)
        return ("call",) if cont_v > 0 else ("fold",)
