"""Copy the Python engine + bot into docs/py/ so GitHub Pages can serve it to Pyodide.

  python3 scripts/build_web.py
"""
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "py")
FILES = ["cards.py", "preflop.py", "preflop_table.json", "range_equity.json", "engine.py", "mybot.py",
         "best_params.json", "mybot_v1.py", "best_params_v1.json", "bots.py", "web_api.py",
         "params/final_entry.json", "field/specialist.py", "field/equity.py", "field/aggro.py",
         "field/learned.py"]

if os.path.exists(OUT):
    shutil.rmtree(OUT)
for f in FILES:
    dst = os.path.join(OUT, f)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(os.path.join(ROOT, f), dst)
with open(os.path.join(OUT, "manifest.json"), "w") as fh:
    json.dump(FILES, fh)
# One bundle so the browser fetches every file in a single request, in parallel with the runtime.
bundle = {}
for f in FILES:
    with open(os.path.join(ROOT, f)) as fh:
        bundle[f] = fh.read()
with open(os.path.join(OUT, "bundle.json"), "w") as fh:
    json.dump(bundle, fh, separators=(",", ":"))
print(f"copied {len(FILES)} files to docs/py/ (+ bundle.json)")

# Pre-recorded tournaments: the Watch tab plays these instantly while Pyodide is still loading.
# They come from the same web_api.watch() the browser runs, so a seed gives the same game either way.
REPLAYS = os.path.join(ROOT, "docs", "replays")
SEEDS = range(0, 25)
if os.path.exists(REPLAYS):
    shutil.rmtree(REPLAYS)
os.makedirs(REPLAYS)
import sys
sys.path.insert(0, OUT)
cwd = os.getcwd()
os.chdir(OUT)
import web_api  # noqa: E402
for field in web_api.FIELDS:
    for seed in SEEDS:
        with open(os.path.join(REPLAYS, f"{field}-{seed}.json"), "w") as fh:
            fh.write(web_api.watch(seed, field))
os.chdir(cwd)
with open(os.path.join(REPLAYS, "index.json"), "w") as fh:
    json.dump({"fields": list(web_api.FIELDS), "seeds": list(SEEDS)}, fh)
print(f"wrote {len(web_api.FIELDS) * len(SEEDS)} replays to docs/replays/")
