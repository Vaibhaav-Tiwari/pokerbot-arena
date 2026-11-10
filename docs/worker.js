// Runs the real Python engine + bot (docs/py/*) inside Pyodide, off the main thread.
importScripts("https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js");

const APP = "/home/pyodide/app";
let py = null;
let games = new Map();
let nextGame = 1;

async function boot() {
  postMessage({ type: "progress", text: "Loading Python runtime…" });
  py = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/" });
  const files = await (await fetch("py/manifest.json")).json();
  postMessage({ type: "progress", text: `Loading the bot (${files.length} files)…` });
  for (const f of files) {
    const text = await (await fetch("py/" + f)).text();
    const dir = (APP + "/" + f).split("/").slice(0, -1).join("/");
    py.FS.mkdirTree(dir);
    py.FS.writeFile(APP + "/" + f, text);
  }
  py.runPython(`import sys, os\nsys.path.insert(0, "${APP}")\nos.chdir("${APP}")\nimport web_api`);
  postMessage({ type: "ready" });
}

const ready = boot().catch((e) => postMessage({ type: "fatal", text: String(e) }));

onmessage = async (msg) => {
  await ready;
  const { id, cmd, args } = msg.data;
  try {
    let out;
    const api = py.globals.get("web_api");
    if (cmd === "watch") {
      out = api.watch(args.seed, args.field);
    } else if (cmd === "newGame") {
      const g = api.Game(args.seed, args.mode);
      const gid = nextGame++;
      games.set(gid, g);
      out = JSON.stringify({ gid, ...JSON.parse(g.start()) });
    } else if (cmd === "act") {
      const g = games.get(args.gid);
      out = g.act(args.kind, args.amount || 0);
    }
    postMessage({ id, ok: true, data: JSON.parse(out) });
  } catch (e) {
    postMessage({ id, ok: false, error: String(e) });
  }
};
