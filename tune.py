"""Self-play parameter search (a simple evolution strategy / hill climb).

  python3 tune.py --iters 40 --n 600

Uses the same table seeds for every candidate (common random numbers) so differences
are mostly skill rather than luck, then re-checks the winner on fresh seeds to catch overfitting.
"""
import argparse
import json
import math
import os
import random
import statistics
from multiprocessing import Pool

import importlib
from run import evaluate_params


# Tuning uses a loose time limit: the bot's speed is checked separately, and on a busy machine a
# strict 1 ms limit just adds random auto-folds that drown out the differences being measured.
TUNE_TIME_LIMIT = 0.02


OBJECTIVE = os.environ.get("OBJECTIVE", "points")  # "points" = average points, "firsts" = share of 1st places


def score(params, seeds, workers, pools, bot):
    res = [r for name in pools
           for r in evaluate_params(params, seeds, TUNE_TIME_LIMIT, workers=workers, pool=name, bot=bot)]
    if OBJECTIVE == "firsts":
        return statistics.mean(1.0 if r[1] == 1 and r[0] >= 9.99 else 0.0 for r in res) * 10
    return statistics.mean(r[0] for r in res)


def mutate(params, rng, sigma, BOUNDS, only=None):
    new = dict(params)
    keys = only or [k for k in params if k in BOUNDS]
    for k in rng.sample(keys, k=rng.randint(1, min(4, len(keys)))):
        lo, hi = BOUNDS[k]
        if lo <= 0:  # additive for params that can be zero or negative
            new[k] = params[k] + rng.gauss(0, sigma * (hi - lo) / 2)
        else:
            new[k] = params[k] * math.exp(rng.gauss(0, sigma))
        new[k] = min(hi, max(lo, new[k]))
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--n", type=int, default=600, help="tables per candidate")
    ap.add_argument("--sigma", type=float, default=0.25)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--pools", default="default", help="comma-separated pools from bots.POOLS")
    ap.add_argument("--bot", default="mybot")
    ap.add_argument("--out", default="", help="save to this file instead of the bot's default params file")
    ap.add_argument("--only", default="", help="comma-separated params to tune (default: all)")
    ap.add_argument("--seed", type=int, default=1, help="search seed; change it to explore other directions")
    args = ap.parse_args()
    m = importlib.import_module(args.bot)
    BOUNDS, DEFAULT_PARAMS, TUNED_PATH, load_tuned = m.BOUNDS, m.DEFAULT_PARAMS, m.TUNED_PATH, m.load_tuned
    if args.out:
        TUNED_PATH = os.path.abspath(args.out)

    rng = random.Random(args.seed)
    train = list(range(args.n))
    holdout = list(range(500_000, 500_000 + 2 * args.n))
    best = dict(DEFAULT_PARAMS)
    if args.resume:
        best.update(load_tuned() or {})  # settings added since the last save keep their defaults
    start = dict(best)

    pools = args.pools.split(",")
    with Pool(max(1, os.cpu_count() - 2)) as workers:
        best_score = score(best, train, workers, pools, args.bot)
        print(f"start: {best_score:.3f}")
        for it in range(args.iters):
            cand = mutate(best, rng, args.sigma, BOUNDS, [k for k in args.only.split(",") if k] or None)
            sc = score(cand, train, workers, pools, args.bot)
            changed = {k: round(v, 3) for k, v in cand.items() if v != best[k]}
            if sc > best_score:
                best, best_score = cand, sc
                print(f"[{it + 1:3d}] {sc:.3f}  accepted {changed}")
            else:
                print(f"[{it + 1:3d}] {sc:.3f}")

        base_h = score(start, holdout, workers, pools, args.bot)
        best_h = score(best, holdout, workers, pools, args.bot)
    print(f"\nholdout ({len(holdout)} tables): start {base_h:.3f}  tuned {best_h:.3f}")
    if best_h > base_h:
        with open(TUNED_PATH, "w") as f:
            json.dump(best, f, indent=1)
        print(f"saved -> {os.path.basename(TUNED_PATH)}")
    else:
        print("tuned params did not beat the starting point on holdout; not saved")


if __name__ == "__main__":
    main()
