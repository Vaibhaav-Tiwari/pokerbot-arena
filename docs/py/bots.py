"""Opponent pool approximating what a field of 60-minute bots looks like."""
import importlib.util
import os
import random

from cards import evaluate, category, PAIR
from preflop import TABLE, hand_key


def passive(s):
    return ("check",) if s["to_call"] == 0 else ("fold",)


def pct(s):
    return TABLE[hand_key(*s["hole"])]["pct"]


class RandomBot:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def get_action(self, s):
        r = self.rng.random()
        if s["to_call"] > 0 and r < 0.3:
            return ("fold",)
        if s["can_raise"] and r > 0.85:
            return ("raise", self.rng.randint(s["min_raise_to"], s["max_raise_to"]))
        return ("call",)


class ManiacBot:
    """Shoves wide preflop, fires pot-sized bets postflop."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def get_action(self, s):
        if s["street"] == 0:
            if pct(s) <= 0.40 or self.rng.random() < 0.08:
                return ("raise", s["max_raise_to"])
            return passive(s)
        if self.rng.random() < 0.6 and s["can_raise"]:
            return ("raise", s["current_bet"] + s["pot"])
        return ("call",)


class StationBot:
    """Calls everything, never raises."""

    def get_action(self, s):
        return ("call",)


class RockBot:
    """Top ~6% only; continues postflop with a pair or better."""

    def get_action(self, s):
        if s["street"] == 0:
            if pct(s) <= 0.06:
                return ("raise", s["current_bet"] * 3 if s["stack"] > 15 * s["big_blind"] else s["max_raise_to"])
            return passive(s)
        if category(evaluate(list(s["hole"]) + s["board"])) >= PAIR:
            return ("raise", s["bet"] + s["pot"] // 2) if s["to_call"] == 0 else ("call",)
        return passive(s)


# ---------- Exact copies of Deep-ML's house bots (pokerbot_sdk/house_bots.py) ----------
def chen_strength(hole):
    """pokerbot_sdk.preflop_strength: Chen formula / 20, clipped to 0..1."""
    a, b = sorted(hole, key=lambda c: c >> 2, reverse=True)
    ra, rb = a >> 2, b >> 2
    score = {12: 10, 11: 8, 10: 7, 9: 6}.get(ra, (ra + 2) / 2)
    if ra == rb:
        score = max(score * 2, 5)
    if (a & 3) == (b & 3):
        score += 2
    gap = ra - rb - 1
    if ra != rb:
        score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
        if gap <= 1 and ra < 10:
            score += 1
    return max(0.0, min(1.0, score / 20))


def mc_equity(hole, board, opponents, samples, rng):
    """pokerbot_sdk.equity: Monte Carlo pot share vs `opponents` random hands."""
    known = set(hole) | set(board)
    rest = [c for c in range(52) if c not in known]
    need = 5 - len(board)
    draw = need + 2 * opponents
    total = 0.0
    for _ in range(samples):
        cards = rng.sample(rest, draw)
        full = list(board) + cards[:need]
        mine = evaluate(list(hole) + full)
        best, ties = 0, 0
        for k in range(opponents):
            v = evaluate(cards[need + 2 * k: need + 2 * k + 2] + full)
            if v > best:
                best, ties = v, 0
            if v == mine:
                ties += 1
        if mine > best:
            total += 1.0
        elif mine == best:
            total += 1.0 / (ties + 1)
    return total / samples


class HouseFold:
    def get_action(self, s):
        return passive(s)


class HouseCall:
    def get_action(self, s):
        return ("call",)


class HouseRaise:
    """Maniac: pot-sized raise whenever it can."""
    def get_action(self, s):
        if s["can_raise"]:
            return ("raise", s["current_bet"] + s["pot"])
        return ("call",)


class HouseRandom:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def get_action(self, s):
        roll = self.rng.random()
        if roll < 0.15 and s["to_call"]:
            return ("fold",)
        if roll < 0.75 or not s["can_raise"]:
            return ("call",)
        lo, hi = s["min_raise_to"], s["max_raise_to"]
        return ("raise", self.rng.randint(lo, max(lo, min(hi, lo + s["pot"]))))


class HouseTight:
    """Tight-aggressive baseline: Chen preflop chart, then 120-sample equity vs pot odds."""
    def __init__(self, seed=None, samples=120):
        self.rng = random.Random(seed)
        self.samples = samples

    def get_action(self, s):
        bb = s["big_blind"]
        if s["street"] == 0:
            st = chen_strength(s["hole"])
            if st >= 0.55 and s["can_raise"]:
                return ("raise", max(s["min_raise_to"], 3 * s["current_bet"]))
            if st >= 0.4 or s["to_call"] == 0:
                return ("call",) if (s["to_call"] <= 4 * bb or st >= 0.55) else ("fold",)
            return ("fold",)
        opp = max(1, s["active_in_hand"] - 1)
        eq = mc_equity(list(s["hole"]), s["board"], opp, self.samples, self.rng)
        if eq > 0.7 and s["can_raise"]:
            return ("raise", s["current_bet"] + (2 * s["pot"]) // 3)
        if s["to_call"] == 0:
            return ("check",)
        odds = s["to_call"] / (s["pot"] + s["to_call"])
        return ("call",) if eq > odds + 0.05 else ("fold",)


class CrashBot:
    """Simulates a broken or too-slow entry (engine auto check/folds it)."""

    def get_action(self, s):
        raise RuntimeError("bug")


FIELD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "field")
_field_classes = {}


def field_bot(name):
    """Bots written independently by AI agents under the contest rules (field/<name>.py)."""
    if name not in _field_classes:
        spec = importlib.util.spec_from_file_location("field_" + name, os.path.join(FIELD, name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _field_classes[name] = mod.Bot
    return _field_classes[name]()


AI_BOTS = ["specialist", "equity", "aggro", "learned"]


def make_bot(kind, seed=None):
    if kind in AI_BOTS:
        return field_bot(kind)
    from mybot_v1 import MyBot
    if kind == "random":
        return RandomBot(seed)
    if kind == "maniac":
        return ManiacBot(seed)
    if kind == "station":
        return StationBot()
    if kind == "rock":
        return RockBot()
    if kind == "h_tight":
        return HouseTight(seed)
    if kind == "h_call":
        return HouseCall()
    if kind == "h_random":
        return HouseRandom(seed)
    if kind == "h_raise":
        return HouseRaise()
    if kind == "h_fold":
        return HouseFold()
    if kind == "crash":
        return CrashBot()
    if kind == "tag":
        return MyBot(seed=seed)  # default-parameter copy of the v1 bot = a competent opponent
    if kind == "nit":
        return MyBot({"open_pct": 0.06, "open_late_bonus": 0.08, "push_base": 0.06,
                      "call_allin_pct": 0.04, "cbet": 0.3}, seed=seed)
    if kind == "lag":
        return MyBot({"open_pct": 0.3, "open_late_bonus": 0.3, "push_base": 0.25,
                      "call_allin_pct": 0.15, "cbet": 0.9, "call_margin": -0.05}, seed=seed)
    raise ValueError(kind)


# Sampled with replacement to fill the 7 opponent seats. Weight them toward what you expect.
POOLS = {
    "default": ["tag", "tag", "tag", "maniac", "station", "rock", "random", "crash"],
    "tight": ["tag", "tag", "nit", "nit", "rock", "rock", "tag", "lag"],
    "loose": ["lag", "lag", "maniac", "station", "station", "random", "tag", "crash"],
    "strong": ["tag", "tag", "tag", "nit", "lag", "lag", "tag", "rock"],
    # Deep-ML house bots (from play_local's seat list): tight, call, random, raise, tight, fold, random
    # exact house bots in pokerbot_sdk's DEFAULT_OPPONENTS order
    "house": ["h_tight", "h_call", "h_random", "h_raise", "h_tight", "h_fold", "h_random"],
    # AI-written contest bots: the closest stand-in for the real field
    "ai": ["specialist", "equity", "aggro", "learned"],
    "contest": ["specialist", "equity", "aggro", "learned", "specialist", "equity", "aggro", "learned",
                "tag", "lag", "nit", "station", "crash"],
}
POOL = POOLS["default"]
