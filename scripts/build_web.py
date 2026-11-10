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
print(f"copied {len(FILES)} files to docs/py/")
