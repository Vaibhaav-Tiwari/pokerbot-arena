"""Play tournaments where the hero is rehearsal/entry.py, talking only through the mock API."""
import importlib.util
import os
import statistics
import sys
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import Tournament  # noqa: E402
from run import build_table  # noqa: E402
import mock_api  # noqa: E402


class Client:
    """Our engine's seat -> mock API -> contestant's entry.py -> mock reply -> engine action."""
    def __init__(self):
        spec = importlib.util.spec_from_file_location("entry", os.path.join(os.path.dirname(__file__), "entry.py"))
        self.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.entry)  # fresh module (and bot) per table

    def get_action(self, s):
        return mock_api.from_reply(self.entry.get_action(mock_api.to_raw(s)), s)


def play(seed):
    bots, names, hero = build_table(seed, None, "ai", "mybot")
    client = Client()
    bots[hero] = client
    r = Tournament(bots, names, seed=seed, time_limit=[0.001 if i == hero else 0.02 for i in range(8)]).run()
    return r["points"][hero], client.entry._bot.errors, r["errors"][hero], r["think"][hero]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    with Pool(max(1, os.cpu_count() - 2)) as w:
        res = w.map(play, range(600_000, 600_000 + n), chunksize=8)
    t = sorted(x for r in res for x in r[3])
    print(f"{n} tables through the mock API: avg points {statistics.mean(r[0] for r in res):.2f} | "
          f"hidden bot errors {sum(r[1] for r in res)} | glue/engine errors {sum(r[2] for r in res)} | "
          f"think median {t[len(t)//2]*1e6:.0f}us max {t[-1]*1e6:.0f}us")
