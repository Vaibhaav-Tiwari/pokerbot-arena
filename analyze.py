"""Compare playing styles of every bot at your tables.

  python3 analyze.py --pool ai --n 300

Per bot: VPIP (% hands it puts money in voluntarily), PFR (% it raises preflop), shove % (preflop
all-ins), AF (postflop raises per call), WTSD (% of hands reaching showdown), W$SD (% of showdowns won),
and where its chips come from: hands won without a showdown vs at showdown.
"""
import argparse
import collections
import importlib
import os
from multiprocessing import Pool

from engine import Tournament
from run import build_table


def play(job):
    seed, params, pool, bot = job
    bots, names, hero = build_table(seed, params, pool, bot)
    kinds = ["YOUR BOT" if i == hero else n.rstrip("0123456789") for i, n in enumerate(names)]
    stats = collections.defaultdict(collections.Counter)
    prev = {}
    orig = getattr(bots[hero], "on_hand_end", None)

    def observe(info):
        if orig:
            orig(info)
        hist = info["history"]
        seats = {seat for _, seat, *_ in hist}
        post_seen = set()
        for street, seat, kind, _a, allin in hist:
            c = stats[kinds[seat]]
            if street == 0 and kind in ("call", "raise"):
                c["_vpip_" + str(seat)] = 1
            if street == 0 and kind == "raise":
                c["_pfr_" + str(seat)] = 1
                if allin:
                    c["_shove_" + str(seat)] = 1
            if street > 0:
                post_seen.add(seat)
                if kind == "raise":
                    c["post_raise"] += 1
                elif kind == "call":
                    c["post_call"] += 1
        for seat in seats:
            c = stats[kinds[seat]]
            c["hands"] += 1
            for tag in ("vpip", "pfr", "shove"):
                if c.pop(f"_{tag}_{seat}", 0):
                    c[tag] += 1
            before = prev.get(seat)
            after = info["stacks"].get(seat)
            if before is not None and after is not None:
                delta = after - before
                if info["shown"] and seat in info["shown"]:
                    c["wtsd"] += 1
                    c["sd_chips"] += delta
                    c["wsd"] += delta > 0
                elif delta > 0:
                    c["nosd_chips"] += delta
                else:
                    c["fold_chips"] += delta
        prev.clear()
        prev.update(info["stacks"])

    bots[hero].on_hand_end = observe
    Tournament(bots, names, seed=seed, time_limit=[0.02] * len(bots)).run()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ai")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--bot", default="mybot")
    args = ap.parse_args()
    params = importlib.import_module(args.bot).load_tuned()
    jobs = [(s, params, args.pool, args.bot) for s in range(800_000, 800_000 + args.n)]
    total = collections.defaultdict(collections.Counter)
    with Pool(max(1, os.cpu_count() - 2)) as w:
        for st in w.map(play, jobs, chunksize=4):
            for k, c in st.items():
                total[k].update(c)
    print(f"{'bot':11s} {'VPIP':>5s} {'PFR':>5s} {'shove':>6s} {'AF':>5s} {'WTSD':>5s} {'W$SD':>5s} "
          f"{'won no-SD':>10s} {'net SD':>8s} {'lost folding':>13s}   (chips per 100 hands)")
    for k, c in sorted(total.items(), key=lambda kv: kv[0] != "YOUR BOT"):
        h = max(1, c["hands"])
        print(f"{k:11s} {c['vpip'] / h:5.0%} {c['pfr'] / h:5.0%} {c['shove'] / h:6.1%} "
              f"{c['post_raise'] / max(1, c['post_call']):5.1f} {c['wtsd'] / h:5.0%} {c['wsd'] / max(1, c['wtsd']):5.0%} "
              f"{100 * c['nosd_chips'] / h:10.0f} {100 * c['sd_chips'] / h:8.0f} {100 * c['fold_chips'] / h:13.0f}")


if __name__ == "__main__":
    main()
