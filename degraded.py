"""How well does the bot play if the contest API gives it less information than our simulator?

  python3 degraded.py --n 1500

Each level rebuilds the bot's state through adapter.build_state from a subset of the real state:
  full        everything (baseline)
  no_history  no action history and no end-of-hand callback -> no opponent tracking
  no_stacks   also no opponents' stack sizes -> ICM and stack depth use neutral guesses
  minimal     also no position info (players_behind) -> assumes the middle seat
"""
import argparse
import os
import statistics
from multiprocessing import Pool

import mybot
from adapter import build_state
from engine import Tournament
from run import build_table

LEVELS = ["full", "no_history", "no_stacks", "minimal"]


class Degraded:
    def __init__(self, bot, level):
        self.bot, self.level = bot, level

    def get_action(self, s):
        lvl = LEVELS.index(self.level)
        kw = dict(hole=s["hole"], board=s["board"], pot=s["pot"], to_call=s["to_call"], stack=s["stack"],
                  big_blind=s["big_blind"], small_blind=s["small_blind"], seat=s["seat"], my_bet=s["bet"],
                  current_bet=s["current_bet"], min_raise_to=s["min_raise_to"], max_raise_to=s["max_raise_to"],
                  players_left=s["players_left"], active_in_hand=s["active_in_hand"], hand_number=s["hand_number"])
        if lvl < 3:
            kw["players_behind"] = s["players_behind"]
        if lvl < 2:
            kw.update(stacks=s["stacks"], bets=s["bets"], folded=s["folded"], allin=s["allin"])
        if lvl < 1:
            kw["history"] = s["history"]
        return self.bot.get_action(build_state(**kw))

    def on_hand_end(self, info):
        if self.level == "full":
            self.bot.on_hand_end(info)


def play(job):
    seed, level, pool = job
    params = mybot.load_tuned()
    bots, names, hero = build_table(seed, params, pool, "mybot")
    inner = bots[hero]
    bots[hero] = Degraded(inner, level)
    limits = [0.02] * len(bots)
    r = Tournament(bots, names, seed=seed, time_limit=limits).run()
    return r["points"][hero], inner.errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--pool", default="ai")
    args = ap.parse_args()
    seeds = range(600_000, 600_000 + args.n)
    with Pool(max(1, os.cpu_count() - 2)) as w:
        for level in LEVELS:
            res = w.map(play, [(s, level, args.pool) for s in seeds], chunksize=8)
            pts = [r[0] for r in res]
            print(f"{level:11s} avg points {statistics.mean(pts):.2f} ± {statistics.stdev(pts) / len(pts) ** .5:.2f}"
                  f"   hidden errors {sum(r[1] for r in res)}", flush=True)


if __name__ == "__main__":
    main()
