"""Preflop table for the 169 starting hands: equity vs one random hand and a percentile rank.

Run `python3 preflop.py` once to build preflop_table.json (~15s). The bot only looks it up.
"""
import json
import os
import random

from cards import RANKS, evaluate

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflop_table.json")


def hand_key(c1, c2):
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 < r2:
        r1, r2 = r2, r1
    if r1 == r2:
        return RANKS[r1] * 2
    return RANKS[r1] + RANKS[r2] + ("s" if (c1 & 3) == (c2 & 3) else "o")


def _representatives():
    reps = {}
    for a in range(13):
        for b in range(a + 1):
            if a == b:
                reps[RANKS[a] * 2] = (a * 4, a * 4 + 1, 6)
            else:
                reps[RANKS[a] + RANKS[b] + "s"] = (a * 4, b * 4, 4)
                reps[RANKS[a] + RANKS[b] + "o"] = (a * 4, b * 4 + 1, 12)
    return reps


def compute(sims=20000, seed=0):
    rng = random.Random(seed)
    table = {}
    reps = _representatives()
    for key, (c1, c2, combos) in reps.items():
        rest = [c for c in range(52) if c not in (c1, c2)]
        score = 0.0
        for _ in range(sims):
            draw = rng.sample(rest, 7)
            board = draw[2:]
            me = evaluate([c1, c2] + board)
            opp = evaluate(draw[:2] + board)
            score += 1.0 if me > opp else 0.5 if me == opp else 0.0
        table[key] = {"eq": score / sims, "combos": combos}

    cum = 0
    for key in sorted(table, key=lambda k: -table[k]["eq"]):
        cum += table[key]["combos"]
        table[key]["pct"] = cum / 1326.0  # fraction of all hands at least this strong
    return table


def load():
    if not os.path.exists(PATH):
        table = compute()
        with open(PATH, "w") as f:
            json.dump(table, f, indent=1)
        return table
    with open(PATH) as f:
        return json.load(f)


TABLE = load()

if __name__ == "__main__":
    top = sorted(TABLE, key=lambda k: TABLE[k]["pct"])
    for k in top[:15] + ["..."] + top[-5:]:
        if k == "...":
            print("...")
            continue
        print(f"{k:4s} eq={TABLE[k]['eq']:.3f} top {TABLE[k]['pct'] * 100:5.1f}%")


# ---------- equity vs a top-X% range (used for ICM all-in decisions) ----------
RANGE_GRID = [0.03, 0.06, 0.1, 0.15, 0.2, 0.3, 0.45, 0.65, 1.0]
RANGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "range_equity.json")


def _combos_by_strength():
    combos = [(a, b) for a in range(52) for b in range(a)]
    return sorted(combos, key=lambda c: TABLE[hand_key(*c)]["pct"])


def _range_row(args):
    key, sims = args
    c1, c2, _ = _representatives()[key]
    rng = random.Random(hash(key) & 0xFFFF)
    combos = _combos_by_strength()
    row = []
    for w in RANGE_GRID:
        rngcombos = [c for c in combos if TABLE[hand_key(*c)]["pct"] <= max(w, 0.005)
                     and c1 not in c and c2 not in c]
        score = 0.0
        for _ in range(sims):
            o = rngcombos[rng.randrange(len(rngcombos))]
            rest = [c for c in range(52) if c not in (c1, c2, o[0], o[1])]
            board = rng.sample(rest, 5)
            me = evaluate([c1, c2] + board)
            op = evaluate(list(o) + board)
            score += 1.0 if me > op else 0.5 if me == op else 0.0
        row.append(score / sims)
    return key, row


def compute_range_equity(sims=3000):
    from multiprocessing import Pool
    with Pool(max(1, os.cpu_count() - 2)) as pool:
        rows = pool.map(_range_row, [(k, sims) for k in _representatives()])
    return dict(rows)


def load_range_equity():
    if not os.path.exists(RANGE_PATH):
        data = compute_range_equity()
        with open(RANGE_PATH, "w") as f:
            json.dump(data, f)
        return data
    with open(RANGE_PATH) as f:
        return json.load(f)


def equity_vs_range(key, width, table):
    """Linear interpolation of my hand's equity vs the opponent's top-`width` range."""
    row = table[key]
    if width <= RANGE_GRID[0]:
        return row[0]
    for i in range(1, len(RANGE_GRID)):
        if width <= RANGE_GRID[i]:
            lo, hi = RANGE_GRID[i - 1], RANGE_GRID[i]
            t = (width - lo) / (hi - lo)
            return row[i - 1] * (1 - t) + row[i] * t
    return row[-1]
