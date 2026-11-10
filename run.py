"""Benchmark your bot against the opponent pool.

  python3 run.py                  # 400 sit-and-gos, summary
  python3 run.py --n 2000         # more tables, tighter estimate
  python3 run.py --watch          # print one tournament hand by hand
  python3 run.py --tuned          # use best_params.json from tune.py
  python3 run.py --pool tight     # opponent pool from bots.POOLS
  python3 run.py --bot mybot_v1   # benchmark another bot module (e.g. the frozen v1)
"""
import importlib
import argparse
import os
import random
import statistics
from multiprocessing import Pool

from bots import POOLS, make_bot
from engine import Tournament


def build_table(seed, params, pool="default", bot="mybot"):
    MyBot = importlib.import_module(bot).MyBot
    pool = POOLS[pool]
    rng = random.Random(seed)
    hero = rng.randrange(8)
    bots, names = [], []
    fixed = list(pool) if len(pool) == 7 and pool == POOLS.get("house") else None
    for i in range(8):
        if fixed is not None and i != hero:
            kind = fixed.pop(0)
            bots.append(make_bot(kind, seed * 31 + i))
            names.append(f"{kind}{i}")
            continue
        if i == hero:
            bots.append(MyBot(params, seed=seed))
            names.append("HERO")
        else:
            kind = rng.choice(pool)
            bots.append(make_bot(kind, seed * 31 + i))
            names.append(f"{kind}{i}")
    return bots, names, hero


def simulate(job):
    seed, params, time_limit, pool, bot = job
    bots, names, hero = build_table(seed, params, pool, bot)
    level = os.environ.get("INFO_LEVEL", "full")
    if level != "full":  # simulate an API that gives the bot less information (see degraded.py)
        from degraded import Degraded
        bots[hero] = Degraded(bots[hero], level)
    # Your bot gets the real limit; opponents get slack so a busy machine doesn't make them auto-fold.
    limits = [time_limit if i == hero else 0.02 for i in range(len(bots))]
    r = Tournament(bots, names, seed=seed, time_limit=limits).run()
    return r["points"][hero], r["place"][hero], r["timeouts"][hero], r["errors"][hero], r["think"][hero]


def evaluate_params(params, seeds, time_limit=0.001, workers=None, pool="default", bot="mybot"):
    jobs = [(s, params, time_limit, pool, bot) for s in seeds]
    if workers is None:
        with Pool(max(1, os.cpu_count() - 2)) as w:
            return w.map(simulate, jobs, chunksize=8)
    return workers.map(simulate, jobs, chunksize=8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=10_000)
    ap.add_argument("--tuned", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--pool", default="default", choices=sorted(POOLS))
    ap.add_argument("--bot", default="mybot")
    args = ap.parse_args()
    params = importlib.import_module(args.bot).load_tuned() if args.tuned else None

    if args.watch:
        bots, names, hero = build_table(args.seed, params, args.pool, args.bot)
        r = Tournament(bots, names, seed=args.seed, verbose=True).run()
        print("\nFinal:")
        for i in sorted(range(8), key=lambda i: r["place"][i]):
            print(f"  {r['place'][i]}. {names[i]:10s} stack {r['stacks'][i]:6d}  points {r['points'][i]:.2f}")
        return

    res = evaluate_params(params, range(args.seed, args.seed + args.n), pool=args.pool, bot=args.bot)
    pts = [r[0] for r in res]
    places = [r[1] for r in res]
    think = sorted(t for r in res for t in r[4])
    mean = statistics.mean(pts)
    se = statistics.stdev(pts) / len(pts) ** 0.5
    print(f"{args.bot}{' (tuned)' if args.tuned else ''} vs {args.pool} pool: {args.n} sit-and-gos | avg points {mean:.3f} ± {se:.3f}   (random seat ≈ {17 / 8:.3f})")
    print("finish places: " + "  ".join(f"{k}:{places.count(k) / len(places):.0%}" for k in range(1, 9)))
    print(f"top-3 rate {sum(p <= 3 for p in places) / len(places):.1%} | "
          f"hero timeouts {sum(r[2] for r in res)} errors {sum(r[3] for r in res)}")
    if think:
        print(f"think time: median {think[len(think) // 2] * 1e6:.0f}µs  "
              f"p99 {think[int(len(think) * 0.99)] * 1e6:.0f}µs  max {think[-1] * 1e6:.0f}µs")


if __name__ == "__main__":
    main()
