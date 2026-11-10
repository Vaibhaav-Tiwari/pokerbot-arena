"""python3 field/check_bot.py field/<bot>.py  -> runs the bot on 40 tables vs simple opponents."""
import importlib.util
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import Tournament  # noqa: E402
from bots import RandomBot, ManiacBot, StationBot, RockBot  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "Bot", None) or next(v for k, v in vars(mod).items() if isinstance(v, type) and hasattr(v, "get_action"))
    return cls


if __name__ == "__main__":
    Bot = load(sys.argv[1])
    pts, errs, tos, think = [], 0, 0, []
    for seed in range(40):
        rng = random.Random(seed)
        hero = rng.randrange(8)
        makers = [lambda: RandomBot(seed), lambda: ManiacBot(seed), StationBot, RockBot]
        bots = [Bot() if i == hero else rng.choice(makers)() for i in range(8)]
        r = Tournament(bots, seed=seed).run()
        pts.append(r["points"][hero]); errs += r["errors"][hero]; tos += r["timeouts"][hero]; think += r["think"][hero]
    think.sort()
    print(f"avg points {statistics.mean(pts):.2f} (average seat 2.125) | errors {errs} | timeouts {tos} | "
          f"think median {think[len(think)//2]*1e6:.0f}us max {think[-1]*1e6:.0f}us over {len(think)} decisions")
