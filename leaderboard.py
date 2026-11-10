"""Leaderboard: your bot vs a pool, with every seat's average points.

  python3 leaderboard.py --pool ai --n 1000 [--bot mybot] [--untuned]
"""
import argparse
import collections
import importlib
import os
import statistics
from multiprocessing import Pool

from run import build_table
from engine import Tournament


def play(job):
    seed, params, pool, bot = job
    bots, names, hero = build_table(seed, params, pool, bot)
    if os.environ.get("INFO_LEVEL", "full") != "full":  # simulate a sparser API (see degraded.py)
        from degraded import Degraded
        bots[hero] = Degraded(bots[hero], os.environ["INFO_LEVEL"])
    limits = [0.001 if i == hero else 0.02 for i in range(len(bots))]
    r = Tournament(bots, names, seed=seed, time_limit=limits).run()
    out = []
    for i, name in enumerate(names):
        kind = "YOUR BOT" if i == hero else name.rstrip("0123456789")
        out.append((kind, r["points"][i], r["place"][i]))
    return out, r["timeouts"][hero]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ai")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=700_000)
    ap.add_argument("--bot", default="mybot")
    ap.add_argument("--untuned", action="store_true")
    args = ap.parse_args()
    params = None if args.untuned else importlib.import_module(args.bot).load_tuned()
    jobs = [(s, params, args.pool, args.bot) for s in range(args.seed, args.seed + args.n)]
    with Pool(max(1, os.cpu_count() - 2)) as w:
        res = w.map(play, jobs, chunksize=8)
    pts, places = collections.defaultdict(list), collections.defaultdict(list)
    for seats, _ in res:
        for kind, p, pl in seats:
            pts[kind].append(p)
            places[kind].append(pl)
    print(f"{args.bot}{'' if args.untuned else ' (tuned)'} [{os.environ.get('INFO_LEVEL', 'full')} info] vs '{args.pool}' pool, {args.n} tables   "
          f"(your timeouts: {sum(t for _, t in res)})")
    print(f"{'bot':12s} {'avg pts':>8s} {'±':>5s} {'top-3':>6s} {'1st':>5s} {'seats':>6s}")
    for kind in sorted(pts, key=lambda k: -statistics.mean(pts[k])):
        p = pts[kind]
        se = statistics.stdev(p) / len(p) ** 0.5 if len(p) > 1 else 0
        print(f"{kind:12s} {statistics.mean(p):8.2f} {se:5.2f} {sum(x <= 3 for x in places[kind]) / len(p):6.0%} "
              f"{sum(x == 1 for x in places[kind]) / len(p):5.0%} {len(p):6d}")


if __name__ == "__main__":
    main()
