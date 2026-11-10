"""Local stand-in for Deep-ML's pokerbot_sdk, built from the documented API, running on our engine."""
import os, sys, statistics
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from cards import card_str  # noqa

NAMES = ["preflop", "flop", "turn", "river"]
EXPOSE_SEAT = os.environ.get("SDK_SEAT", "1") == "1"   # test both: with and without a seat attribute


class Bot:
    def act(self, state):
        raise NotImplementedError


class Action:
    @staticmethod
    def fold(): return ("fold",)
    @staticmethod
    def check(): return ("check",)
    @staticmethod
    def call(): return ("call",)
    @staticmethod
    def raise_to(total): return ("raise", int(total))
    @staticmethod
    def all_in(): return ("allin",)


class State(dict):
    """Like the real SDK: a dict whose keys are also readable as attributes."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __init__(self, s):
        super().__init__()
        object.__setattr__(self, "_s", s)
        self.hole = [card_str(c).replace("T", "T") for c in s["hole"]]
        self.board = [card_str(c) for c in s["board"]]
        self.street = NAMES[s["street"]]
        self.to_call = s["to_call"]; self.pot = s["pot"]
        self.min_raise = s["min_raise_to"]; self.max_raise = s["max_raise_to"]
        self.current_bet = s["current_bet"]
        n = max(s["stacks"]) + 1
        self.stacks = [s["stacks"].get(i, 0) - s["bets"].get(i, 0) for i in range(8)]
        self.bets = [s["bets"].get(i, 0) for i in range(8)]
        self.big_blind = s["big_blind"]; self.small_blind = s["small_blind"]
        self.level = 0
        self.stack_m = (s["stack"] + s["bet"]) / (s["big_blind"] + s["small_blind"])
        self.legal = ["fold", "call"] + (["raise"] if s["can_raise"] else []) if s["to_call"] else ["check"] + (["raise"] if s["can_raise"] else [])
        self.opponents = s["active_in_hand"] - 1
        self.history = [{"street": NAMES[st], "seat": seat, "action": {"sb": "blind", "bb": "blind"}.get(k, k), "amount": amt, "all_in": ai}
                        for st, seat, k, amt, ai in s["history"]]
        self.folded = [(i in s["folded"]) or (s["stacks"].get(i, 0) == 0) for i in range(8)]
        self.button = s["button"]
        if EXPOSE_SEAT:
            self.seat = s["seat"]
            self.button = s["button"]

    def __setattr__(self, k, v):
        self[k] = v

    def pot_odds(self):
        return self.to_call / (self.pot + self.to_call) if self.to_call else 0.0


class Result(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v

    def __init__(self, info):
        super().__init__()
        self.stacks = [info["stacks"].get(i, 0) for i in range(8)]
        self.shown = {k: [card_str(c) for c in v] for k, v in info["shown"].items()}
        self.actions = [{"street": NAMES[st], "seat": seat, "action": {"sb": "blind", "bb": "blind"}.get(k, k), "amount": amt, "all_in": ai} for st, seat, k, amt, ai in info["history"]]
        self.deltas = None


class Seat:
    def __init__(self, bot): self.bot = bot
    def get_action(self, s):
        a = self.bot.act(State(s))
        if a[0] == "allin":
            return ("raise", s["max_raise_to"])
        return a
    def on_hand_end(self, info):
        cb = getattr(self.bot, "on_hand_end", None)
        if cb: cb(Result(info))


def play_local(bot_factory, tables=20, pool="ai", seed0=777_000):
    from engine import Tournament
    from run import build_table
    pts, think, errs = [], [], 0
    for seed in range(seed0, seed0 + tables):
        bots, names, hero = build_table(seed, None, pool, "mybot")
        b = bot_factory()
        bots[hero] = Seat(b)
        r = Tournament(bots, names, seed=seed, time_limit=[0.001 if i == hero else 0.02 for i in range(8)]).run()
        pts.append(r["points"][hero]); think += r["think"][hero]; errs += r["errors"][hero] + b.core.errors
    think.sort()
    return statistics.mean(pts), errs, think
