"""Train v4's postflop policy weights with an evolution strategy.

  python3 train_v4.py --gens 60

Each generation: 8 antithetic pairs (W + sigma*eps, W - sigma*eps) play the SAME tables (120 per
candidate across the ai / contest / strong pools). Fitness = average points. Ranks -> gradient -> Adam.
Every 5 generations the current weights are scored on 600 fixed validation tables; the best is saved.
"""
import argparse
import json
import math
import os
import random
import statistics
from multiprocessing import Pool

import mybot4
from run import simulate

POOLS = ["ai", "contest", "strong"]


def tables(seeds, params):
    return [(seed, params, 0.02, POOLS[i % len(POOLS)], "mybot4") for i, seed in enumerate(seeds)]


def centered_ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for rank, i in enumerate(order):
        r[i] = rank / (len(xs) - 1) - 0.5
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=60)
    ap.add_argument("--pairs", type=int, default=8)
    ap.add_argument("--tables", type=int, default=120)
    ap.add_argument("--sigma", type=float, default=0.3)
    ap.add_argument("--lr", type=float, default=0.06)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    base = mybot4.load_tuned()
    theta = list(base.pop("W"))
    n = len(theta)
    m, v = [0.0] * n, [0.0] * n
    val_seeds = list(range(900_000, 900_600))

    def params_with(w):
        p = dict(base)
        p["W"] = w
        return p

    with Pool(max(1, os.cpu_count() - 2)) as workers:
        def val_score(w):
            return statistics.mean(r[0] for r in workers.map(simulate, tables(val_seeds, params_with(w)), chunksize=8))

        best_w, best_val = list(theta), val_score(theta)
        print(f"gen   0  validation {best_val:.3f}  (starting weights)", flush=True)
        for gen in range(1, args.gens + 1):
            seeds = [rng.randrange(10**9) for _ in range(args.tables)]
            eps = [[rng.gauss(0, 1) for _ in range(n)] for _ in range(args.pairs)]
            cands = []
            for e in eps:
                cands.append([t + args.sigma * x for t, x in zip(theta, e)])
                cands.append([t - args.sigma * x for t, x in zip(theta, e)])
            jobs = [job for c in cands for job in tables(seeds, params_with(c))]
            res = workers.map(simulate, jobs, chunksize=8)
            fit = [statistics.mean(r[0] for r in res[i * args.tables:(i + 1) * args.tables]) for i in range(len(cands))]
            ranks = centered_ranks(fit)
            grad = [0.0] * n
            for k, e in enumerate(eps):
                d = ranks[2 * k] - ranks[2 * k + 1]
                for j in range(n):
                    grad[j] += d * e[j] / (args.pairs * args.sigma)
            for j in range(n):  # Adam ascent
                m[j] = 0.9 * m[j] + 0.1 * grad[j]
                v[j] = 0.999 * v[j] + 0.001 * grad[j] ** 2
                mh, vh = m[j] / (1 - 0.9 ** gen), v[j] / (1 - 0.999 ** gen)
                theta[j] += args.lr * mh / (math.sqrt(vh) + 1e-8)
            line = f"gen {gen:3d}  candidates mean {statistics.mean(fit):.3f} best {max(fit):.3f}"
            if gen % 5 == 0:
                vs = val_score(theta)
                line += f"  validation {vs:.3f}"
                if vs > best_val:
                    best_w, best_val = list(theta), vs
                    out = params_with([round(x, 4) for x in best_w])
                    with open(mybot4.TUNED_PATH, "w") as f:
                        json.dump(out, f, indent=1)
                    line += "  saved"
            print(line, flush=True)
    print(f"best validation {best_val:.3f}")
    print("weights (raise / continue / size):")
    for i, name in enumerate(mybot4.FEATURES):
        print(f"  {name:16s} {best_w[i]:7.2f} {best_w[mybot4.NF + i]:7.2f} {best_w[2 * mybot4.NF + i]:7.2f}")


if __name__ == "__main__":
    main()
