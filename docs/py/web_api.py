"""Browser entry points (run inside Pyodide by docs/worker.js). Everything returns JSON strings.

watch(seed, field)          one full sit-and-go with the bot vs a field, as a replayable event log
Game(seed, mode)            an interactive game with a human seat: start() then act(kind, amount)
"""
import json
import os
import random

import mybot
from bots import make_bot
from engine import Tournament

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "params", "final_entry.json")) as f:
    FINAL_PARAMS = {k: v for k, v in json.load(f).items() if k in mybot.DEFAULT_PARAMS}

BOT_NAME = "VT-Bot"
HOUSE = [("h_tight", "Tight"), ("h_call", "Caller"), ("h_random", "Random"), ("h_raise", "Maniac"),
         ("h_tight", "Tight II"), ("h_fold", "Folder"), ("h_random", "Random II")]
AI = [("specialist", "Specialist"), ("equity", "Equity"), ("aggro", "Aggro"), ("learned", "RL-Learned"),
      ("specialist", "Specialist II"), ("equity", "Equity II"), ("learned", "RL-Learned II")]
FIELDS = {"house": HOUSE, "ai": AI}


def _hero(seed):
    return mybot.MyBot(dict(FINAL_PARAMS), seed=seed)


def _table(seed, field, hero_seat):
    opponents = list(FIELDS[field])
    bots, names = [], []
    for i in range(8):
        if i == hero_seat:
            bots.append(_hero(seed))
            names.append(BOT_NAME)
        else:
            kind, name = opponents.pop(0)
            bots.append(make_bot(kind, seed * 31 + i))
            names.append(name)
    return bots, names


def watch(seed, field="house"):
    seed = int(seed)
    hero = random.Random(seed).randrange(8)
    bots, names = _table(seed, field, hero)
    t = Tournament(bots, names, seed=seed, time_limit=10.0, record=True)
    res = t.run()
    return json.dumps({"names": names, "hero": hero, "events": t.events,
                       "points": [round(p, 2) for p in res["points"]], "places": res["place"],
                       "hands": res["hands"]})


class Human:
    is_human = True


class Game:
    """You vs the bot: mode 'hu' (heads-up) or 'table' (you, the bot and six house bots)."""

    def __init__(self, seed, mode="hu"):
        self.seed = int(seed)
        rng = random.Random(self.seed)
        if mode == "hu":
            bots, names = [Human(), _hero(self.seed)], ["You", BOT_NAME]
        else:
            others = [make_bot(k, self.seed * 31 + i) for i, (k, _) in enumerate(HOUSE[:6])]
            bots = [Human(), _hero(self.seed)] + others
            names = ["You", BOT_NAME] + [n for _, n in HOUSE[:6]]
            order = list(range(len(bots)))
            rng.shuffle(order)
            bots, names = [bots[i] for i in order], [names[i] for i in order]
        self.you = names.index("You")
        self.names = names
        self.t = Tournament(bots, names, seed=self.seed, time_limit=10.0, record=True)
        self.gen = self.t.steps()
        self.cursor = 0
        self.started = False
        self.state = None

    def _advance(self, action):
        out = {"names": self.names, "you": self.you}
        try:
            if not self.started:
                self.started = True
                req = next(self.gen)
            else:
                req = self.gen.send(action)
            s = req[1]
            self.state = s
            out["decide"] = {k: s[k] for k in ("to_call", "pot", "stack", "bet", "current_bet", "min_raise_to",
                                               "max_raise_to", "can_raise", "big_blind", "street")}
        except StopIteration as done:
            res = done.value
            out["done"] = True
            out["points"] = [round(p, 2) for p in res["points"]]
            out["places"] = res["place"]
        out["events"] = self.t.events[self.cursor:]
        self.cursor = len(self.t.events)
        return json.dumps(out)

    def start(self):
        return self._advance(None)

    def act(self, kind, amount=0):
        if kind == "raise":
            return self._advance(("raise", int(amount)))
        return self._advance((kind,))
