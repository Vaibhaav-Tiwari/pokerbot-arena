"""Train field/learned.py's linear policy with an antithetic evolution strategy.

Fitness = average tournament points (10/5/2 + ICM at cap) of the hero seat over many
full 8-max sit-and-gos against RandomBot/ManiacBot/StationBot/RockBot (+ some copies of the
current mean policy for self-play). Common random numbers across each antithetic pair.
Writes PRE_EQ and W constants back into field/learned.py.

    python3 field/learned_train.py [generations] [tables_per_eval]
"""
import multiprocessing as mp
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from cards import evaluate  # noqa: E402
from engine import Tournament  # noqa: E402
from bots import RandomBot, ManiacBot, StationBot, RockBot  # noqa: E402
import learned  # noqa: E402

N = learned.N_FEAT


def preflop_table(samples=3000, seed=1):
    rng = random.Random(seed)
    eq = [0.0] * 169
    for hi in range(13):
        for lo in range(13):
            # [hi][lo] with hi>lo = suited; [lo][hi] = offsuit; diagonal = pair
            r1, r2 = max(hi, lo), min(hi, lo)
            suited = hi > lo
            c1 = r1 * 4
            c2 = r2 * 4 + (0 if suited else 1)
            deck = [c for c in range(52) if c not in (c1, c2)]
            w = 0.0
            for _ in range(samples):
                d = rng.sample(deck, 7)
                b = d[2:]
                a = evaluate([c1, c2] + b)
                o = evaluate(d[:2] + b)
                w += 1.0 if a > o else 0.5 if a == o else 0.0
            eq[hi * 13 + lo] = round(w / samples, 4)
    return eq


def init_weights():
    w = [0.0] * (3 * N)
    # raise head: a = hsn - 0.55
    w[0], w[2] = -0.55, 1.0
    # continue head: c = hsn - odds - 0.05
    w[N + 0], w[N + 2], w[N + 3] = -0.05, 1.0, -1.0
    # size head: ~1 pot
    w[2 * N + 0] = -1.0
    return w


def play(args):
    weights, ref, seed = args
    rng = random.Random(seed)
    hero = rng.randrange(8)
    makers = [lambda: RandomBot(seed), lambda: ManiacBot(seed), StationBot, RockBot]
    selfplay = rng.random() < 0.25
    bots = []
    for i in range(8):
        if i == hero:
            bots.append(learned.Bot(weights, seed=seed))
        elif selfplay and rng.random() < 0.3:
            bots.append(learned.Bot(ref, seed=seed + i))
        else:
            bots.append(rng.choice(makers)())
    r = Tournament(bots, seed=seed, time_limit=0.05).run()
    return r["points"][hero]


def evaluate_many(pool, weights, ref, seeds):
    return sum(pool.map(play, [(weights, ref, s) for s in seeds], chunksize=8)) / len(seeds)


def write_back(pre, w):
    path = os.path.join(HERE, "learned.py")
    src = open(path).read()
    src = re.sub(r"^PRE_EQ = .*$", "PRE_EQ = " + repr(pre), src, flags=re.M)
    src = re.sub(r"^W = .*$", "W = " + repr([round(x, 4) for x in w]), src, flags=re.M)
    open(path, "w").write(src)


def main():
    gens = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    tables = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    pop, sigma, lr = 8, 0.15, 0.08
    t0 = time.time()
    pre = learned.PRE_EQ or preflop_table()
    learned.PRE_EQ = pre
    w = list(learned.W) if learned.W else init_weights()
    rng = random.Random(12345)
    with mp.Pool(initializer=_init_pool, initargs=(pre,)) as pool:
        best_w, best_f = list(w), -1
        for g in range(gens):
            seeds = [rng.randrange(10 ** 6, 10 ** 9) for _ in range(tables)]
            eps_list, scores = [], []
            for _ in range(pop):
                eps = [rng.gauss(0, 1) for _ in w]
                wp = [x + sigma * e for x, e in zip(w, eps)]
                wm = [x - sigma * e for x, e in zip(w, eps)]
                fp = evaluate_many(pool, wp, w, seeds)
                fm = evaluate_many(pool, wm, w, seeds)
                eps_list.append(eps)
                scores.append(fp - fm)
            # normalized antithetic gradient
            sd = (sum(s * s for s in scores) / len(scores)) ** 0.5 or 1.0
            grad = [sum(s / sd * e[i] for s, e in zip(scores, eps_list)) / pop for i in range(len(w))]
            w = [x + lr * gi for x, gi in zip(w, grad)]
            if g % 5 == 4 or g == gens - 1:
                vs = [rng.randrange(10 ** 6, 10 ** 9) for _ in range(600)]
                f = evaluate_many(pool, w, w, vs)
                fb = evaluate_many(pool, best_w, w, vs)
                if f >= fb:
                    best_w, best_f = list(w), f
                print(f"gen {g+1} val {f:.3f} (best-so-far {fb:.3f}) t={time.time()-t0:.0f}s", flush=True)
                write_back(pre, best_w)
            else:
                print(f"gen {g+1} t={time.time()-t0:.0f}s", flush=True)
    write_back(pre, best_w)


def _init_pool(pre):
    learned.PRE_EQ = pre


if __name__ == "__main__":
    main()
