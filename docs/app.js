"use strict";
// ---------- worker RPC ----------
const worker = new Worker("worker.js");
const pending = new Map();
let rpcId = 0;
const bootEl = document.getElementById("boot");
const bootText = document.getElementById("bootText");
worker.onmessage = (e) => {
  const m = e.data;
  if (m.type === "progress") { bootText.textContent = m.text; return; }
  if (m.type === "ready") {
    bootEl.classList.add("ok");
    bootText.textContent = "Python engine ready. Deal a tournament or sit down at a table.";
    document.querySelectorAll("#wStart, #pStart").forEach((b) => (b.disabled = false));
    return;
  }
  if (m.type === "fatal") { bootEl.classList.add("err"); bootText.textContent = "Could not start the engine: " + m.text; return; }
  const p = pending.get(m.id);
  if (!p) return;
  pending.delete(m.id);
  m.ok ? p.resolve(m.data) : p.reject(new Error(m.error));
};
function rpc(cmd, args) {
  return new Promise((resolve, reject) => {
    const id = ++rpcId;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, cmd, args });
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- tabs ----------
document.querySelectorAll("nav.tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", b === btn));
    document.querySelectorAll("section.tab").forEach((s) => (s.hidden = s.id !== btn.dataset.tab));
    try { localStorage.setItem("tab", btn.dataset.tab); } catch (_) {}
  });
});
try { const t = localStorage.getItem("tab"); if (t) document.querySelector(`nav.tabs button[data-tab="${t}"]`)?.click(); } catch (_) {}

// ---------- cards ----------
const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };
function cardEl(c, hidden) {
  const d = document.createElement("div");
  if (hidden || !c) { d.className = "card back"; return d; }
  const r = c[0] === "T" ? "10" : c[0];
  d.className = "card" + ("hd".includes(c[1]) ? " red" : "");
  d.innerHTML = `<b>${r}</b><small>${SUIT[c[1]]}</small>`;
  d.setAttribute("aria-label", r + SUIT[c[1]]);
  return d;
}
const fmt = (n) => Number(n).toLocaleString("en-US");

// ---------- table ----------
function createTable(root, logEl, standEl) {
  const T = { seats: [], board: [], pot: 0, button: -1, hand: 0, revealAll: true, you: -1, hero: -1, snapshots: [] };
  let felt, center, boardEl, potEl, infoEl;

  T.setup = (names, anchor, opts) => {
    Object.assign(T, opts);
    root.innerHTML = "";
    felt = Object.assign(document.createElement("div"), { className: "felt" });
    center = Object.assign(document.createElement("div"), { className: "center" });
    boardEl = Object.assign(document.createElement("div"), { className: "board" });
    potEl = Object.assign(document.createElement("div"), { className: "pot", textContent: "Pot 0" });
    infoEl = Object.assign(document.createElement("div"), { className: "handinfo" });
    center.append(boardEl, potEl, infoEl);
    root.append(felt, center);
    const n = names.length;
    T.seats = names.map((name, i) => {
      const a = ((90 + ((i - anchor) * 360) / n) * Math.PI) / 180;
      const el = document.createElement("div");
      el.className = "seat" + (i === T.hero ? " hero" : "") + (i === T.you ? " you" : "");
      el.style.left = 50 + 43 * Math.cos(a) + "%";
      el.style.top = 50 + 41 * Math.sin(a) + "%";
      el.innerHTML = `<div class="cards"></div><div class="plate"><div class="nm"></div><div class="sk"></div></div><div class="act"></div><div class="hn"></div>`;
      el.querySelector(".nm").textContent = name;
      const bet = document.createElement("div");
      bet.className = "bet";
      bet.style.left = 50 + 27 * Math.cos(a) + "%";
      bet.style.top = 48 + 22 * Math.sin(a) + "%";
      root.append(el, bet);
      return { name, el, betEl: bet, stack: 2000, bet: 0, cards: [], folded: false, busted: false, place: 0, act: "", hn: "" };
    });
    T.board = []; T.pot = 0; T.snapshots = [names.map(() => 2000)];
    logEl.innerHTML = "";
    T.render();
  };

  T.render = () => {
    T.seats.forEach((s, i) => {
      const cardsEl = s.el.querySelector(".cards");
      cardsEl.innerHTML = "";
      if (!s.busted && s.cards.length) {
        const show = T.revealAll || i === T.you || s.shown;
        s.cards.forEach((c) => cardsEl.append(cardEl(c, !show)));
      }
      s.el.querySelector(".sk").textContent = s.busted ? (s.place ? `out · ${ordinal(s.place)}` : "out") : fmt(s.stack);
      s.el.querySelector(".act").textContent = s.act;
      s.el.querySelector(".hn").textContent = s.hn;
      s.el.classList.toggle("folded", s.folded && !s.busted);
      s.el.classList.toggle("busted", s.busted);
      s.betEl.textContent = s.bet > 0 ? fmt(s.bet) : "";
      const plate = s.el.querySelector(".plate");
      plate.querySelector(".dealer")?.remove();
      if (i === T.button) plate.insertAdjacentHTML("beforeend", '<div class="dealer" title="Dealer button">D</div>');
    });
    boardEl.innerHTML = "";
    T.board.forEach((c) => boardEl.append(cardEl(c)));
    potEl.textContent = "Pot " + fmt(T.pot);
    infoEl.textContent = T.hand ? `Hand ${T.hand} · blinds ${T.sb}/${T.bb}` : "";
    renderStand();
  };

  function renderStand() {
    if (!standEl) return;
    const rows = T.seats.map((s, i) => ({ s, i })).sort((a, b) =>
      (a.s.busted - b.s.busted) || (a.s.busted ? a.s.place - b.s.place : b.s.stack - a.s.stack));
    standEl.innerHTML = "<tr><th>Player</th><th class='n'>Chips</th></tr>" + rows.map(({ s, i }) =>
      `<tr class="${i === T.hero || i === T.you ? "me" : ""}"><td>${esc(s.name)}</td><td class="n">${s.busted ? ordinal(s.place) : fmt(s.stack)}</td></tr>`).join("");
  }

  function log(text, cls) {
    const d = document.createElement("div");
    if (cls) d.className = cls;
    d.textContent = text;
    logEl.append(d);
    if (logEl.children.length > 600) logEl.firstChild.remove();
    logEl.scrollTop = logEl.scrollHeight;
  }

  T.apply = (ev, quiet) => {
    const S = T.seats;
    switch (ev.t) {
      case "hand":
        T.hand = ev.n + 1; T.sb = ev.sb; T.bb = ev.bb; T.button = ev.button; T.board = []; T.pot = 0; T.streetMax = 0;
        S.forEach((s, i) => {
          const k = String(i);
          s.bet = 0; s.act = ""; s.hn = ""; s.folded = false; s.shown = false;
          if (k in ev.stacks) { s.stack = ev.stacks[k]; s.cards = ev.hole[k]; } else { s.cards = []; s.busted = true; }
        });
        if (!quiet) log(`Hand ${T.hand} · blinds ${ev.sb}/${ev.bb}`, "h");
        break;
      case "act": {
        const s = S[ev.seat];
        s.bet = ev.to; s.stack = ev.stack; T.pot = ev.pot;
        const label = { sb: "SB", bb: "BB", fold: "Fold", check: "Check", call: "Call",
          raise: ev.allin ? "All-in" : T.streetMax > 0 ? "Raise to" : "Bet" }[ev.kind];
        T.streetMax = Math.max(T.streetMax, ev.to);
        s.act = ev.kind === "raise" || ev.kind === "call" ? `${label} ${fmt(ev.to)}` : label;
        if (ev.kind === "fold") s.folded = true;
        if (!quiet && ev.kind !== "sb" && ev.kind !== "bb") log(`${s.name}: ${s.act.toLowerCase()}`);
        break;
      }
      case "board":
        T.board = ev.cards; T.streetMax = 0;
        S.forEach((s) => { s.bet = 0; if (!s.folded) s.act = ""; });
        if (!quiet) log(`${["", "Flop", "Turn", "River"][ev.street]}: ${ev.cards.join(" ")}`);
        break;
      case "show":
        Object.entries(ev.hands).forEach(([k, h]) => { const s = S[+k]; s.cards = h.cards; s.shown = true; s.hn = h.name; });
        if (!quiet) Object.entries(ev.hands).forEach(([k, h]) => log(`${S[+k].name} ${+k === T.you ? "show" : "shows"} ${h.cards.join(" ")} (${h.name})`));
        break;
      case "win":
        Object.entries(ev.stacks).forEach(([k, v]) => (S[+k].stack = v));
        S.forEach((s) => (s.bet = 0));
        T.pot = 0;
        ev.pots.forEach((p) => {
          const who = p.winners.map((w) => S[w].name).join(" & ");
          const plural = p.winners.length > 1 || (p.winners.length === 1 && p.winners[0] === T.you);
          if (!quiet) log(`${who} win${plural ? "" : "s"} ${fmt(p.amount)}`, "w");
          if (!quiet) p.winners.forEach((w) => { const pl = S[w].el.querySelector(".plate"); pl.classList.remove("win-flash"); void pl.offsetWidth; pl.classList.add("win-flash"); });
        });
        T.snapshots.push(S.map((s) => (s.busted ? 0 : s.stack)));
        break;
      case "bust":
        S[ev.seat].busted = true; S[ev.seat].place = ev.place; S[ev.seat].stack = 0;
        if (!quiet) log(`${S[ev.seat].name} is out in ${ordinal(ev.place)} place`, "b");
        break;
      case "end":
        ev.places.forEach((p, i) => { S[i].place = p; });
        ev.stacks.forEach((v, i) => { S[i].stack = v; if (v > 0) S[i].busted = false; });
        break;
    }
  };
  return T;
}
const ordinal = (n) => n + ({ 1: "st", 2: "nd", 3: "rd" }[n % 100 > 10 && n % 100 < 14 ? 0 : n % 10] || "th");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function drawGraph(svg, T) {
  const snaps = T.snapshots, n = snaps.length, W = 300, H = 170, max = 2000 * T.seats.length;
  if (n < 2) { svg.innerHTML = ""; return; }
  const x = (i) => (i / (n - 1)) * (W - 30) + 28, y = (v) => H - 14 - (v / max) * (H - 24);
  const colors = ["#6fb6ff", "#c79bff", "#ff8f8f", "#8fe3c0", "#ffcf70", "#a3b3ff", "#f39bd0", "#b9d36a"];
  let g = `<line x1="28" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="#24302a"/><text x="0" y="${y(max) + 4}">${max / 1000}k</text><text x="0" y="${y(0)}">0</text>`;
  T.seats.forEach((s, i) => {
    const me = i === T.hero || i === T.you;
    const pts = snaps.map((sn, k) => `${x(k).toFixed(1)},${y(sn[i]).toFixed(1)}`).join(" ");
    g += `<polyline fill="none" stroke="${me ? "#e2b54b" : colors[i % 8]}" stroke-width="${me ? 2.6 : 1}" stroke-opacity="${me ? 1 : 0.55}" points="${pts}"/>`;
  });
  svg.innerHTML = g;
}

// ---------- watch ----------
(() => {
  const T = createTable(document.getElementById("wTable"), document.getElementById("wLog"), document.getElementById("wStand"));
  const $ = (id) => document.getElementById(id);
  const startB = $("wStart"), pauseB = $("wPause"), skipB = $("wSkip"), endB = $("wEnd"), result = $("wResult"), graph = $("wGraph");
  let events = [], idx = 0, paused = false, timer = null, run = 0, data = null;

  function finish() {
    clearTimeout(timer);
    T.render(); drawGraph(graph, T);
    const h = data.hero, place = data.places[h];
    result.hidden = false;
    result.innerHTML = `<b>VT-Bot finished ${ordinal(place)}</b> and scored <b>${data.points[h]} points</b> after ${data.hands} hands (seed ${$("wSeed").value}). ` +
      `Points: 10 / 5 / 2 for 1st / 2nd / 3rd; tables still running at hand 200 are split by chip count (ICM).`;
    pauseB.disabled = skipB.disabled = endB.disabled = true;
    startB.disabled = false;
  }
  function step(myRun) {
    if (myRun !== run || paused) return;
    if (idx >= events.length) return finish();
    const ev = events[idx++];
    T.apply(ev);
    T.render();
    if (ev.t === "win") drawGraph(graph, T);
    const base = +$("wSpeed").value;
    const d = ev.t === "act" && (ev.kind === "sb" || ev.kind === "bb") ? base * 0.2 : ev.t === "win" ? base * 3 : ev.t === "hand" ? base * 0.6 : base;
    timer = setTimeout(() => step(myRun), d);
  }
  startB.addEventListener("click", async () => {
    startB.disabled = true; result.hidden = true; run++;
    const myRun = run;
    $("wLog").textContent = "Dealing… (a full tournament takes a moment to simulate)";
    try {
      data = await rpc("watch", { seed: +$("wSeed").value || 0, field: $("wField").value });
    } catch (e) { $("wLog").textContent = "Error: " + e.message; startB.disabled = false; return; }
    if (myRun !== run) return;
    T.setup(data.names, data.hero, { hero: data.hero, you: -1, revealAll: true });
    events = data.events; idx = 0; paused = false;
    pauseB.disabled = skipB.disabled = endB.disabled = false; pauseB.textContent = "Pause";
    step(myRun);
  });
  pauseB.addEventListener("click", () => { paused = !paused; pauseB.textContent = paused ? "Resume" : "Pause"; if (!paused) step(run); });
  skipB.addEventListener("click", () => {
    clearTimeout(timer);
    while (idx < events.length && !(events[idx].t === "hand" && idx > 0 && events[idx - 1].t !== "hand")) { T.apply(events[idx++], true); }
    T.render(); drawGraph(graph, T);
    if (!paused) step(run);
  });
  endB.addEventListener("click", () => {
    clearTimeout(timer);
    while (idx < events.length) T.apply(events[idx++], true);
    finish();
  });
  $("wSeed").addEventListener("change", () => {});
})();

// ---------- play ----------
(() => {
  const $ = (id) => document.getElementById(id);
  const T = createTable($("pTable"), $("pLog"), $("pStand"));
  const startB = $("pStart"), status = $("pStatus"), result = $("pResult");
  const fold = $("aFold"), call = $("aCall"), raise = $("aRaise"), slider = $("aSlider");
  const presets = [...document.querySelectorAll("#pBar .presets .btn")];
  let gid = null, decide = null, busy = false;

  function setControls(on) {
    [fold, call, raise, slider, ...presets].forEach((b) => (b.disabled = !on));
    if (on && decide && !decide.can_raise) [raise, slider, ...presets].forEach((b) => (b.disabled = true));
  }
  function updateRaiseLabel() {
    const v = +slider.value;
    raise.textContent = v >= decide.max_raise_to ? `All-in ${fmt(v)}` : `${decide.current_bet > 0 ? "Raise to" : "Bet"} ${fmt(v)}`;
  }
  slider.addEventListener("input", updateRaiseLabel);
  presets.forEach((b) => b.addEventListener("click", () => {
    const d = decide;
    let v = b.dataset.frac === "all" ? d.max_raise_to : Math.round(d.current_bet + +b.dataset.frac * (d.pot + d.to_call));
    slider.value = Math.max(d.min_raise_to, Math.min(d.max_raise_to, v));
    updateRaiseLabel();
  }));

  async function animate(events) {
    for (const ev of events) {
      T.apply(ev); T.render();
      const yours = ev.t === "act" && ev.seat === T.you;
      const blind = ev.t === "act" && (ev.kind === "sb" || ev.kind === "bb");
      await sleep(yours ? 60 : blind ? 120 : ev.t === "hand" ? 300 : ev.t === "win" ? 1100 : ev.t === "show" ? 900 : 420);
    }
  }
  async function handle(resp) {
    await animate(resp.events);
    if (resp.done) {
      const place = resp.places[T.you], pts = resp.points[T.you];
      result.hidden = false;
      result.innerHTML = place === 1 ? `<b>You won the table!</b> 1st place, ${pts} points. VT-Bot finished ${ordinal(resp.places[T.hero])}.`
        : `<b>You finished ${ordinal(place)}</b> (${pts} points). VT-Bot finished ${ordinal(resp.places[T.hero])}. Sit down again to try another deal.`;
      status.textContent = "Game over.";
      startB.disabled = false;
      return;
    }
    decide = resp.decide;
    const d = decide;
    call.textContent = d.to_call ? (d.to_call >= d.stack ? `Call all-in ${fmt(d.stack)}` : `Call ${fmt(d.to_call)}`) : "Check";
    fold.textContent = "Fold";
    slider.min = d.min_raise_to; slider.max = d.max_raise_to; slider.step = Math.max(1, Math.floor(d.big_blind / 2)); slider.value = d.min_raise_to;
    updateRaiseLabel();
    status.textContent = d.to_call ? `Your move: ${fmt(d.to_call)} to call into a pot of ${fmt(d.pot)}.` : `Your move: check or bet. Pot ${fmt(d.pot)}.`;
    T.seats[T.you].el.classList.add("acting");
    setControls(true);
    busy = false;
  }
  async function send(kind, amount) {
    if (busy) return;
    busy = true; setControls(false);
    T.seats[T.you].el.classList.remove("acting");
    status.textContent = "VT-Bot and the table are thinking…";
    try { await handle(await rpc("act", { gid, kind, amount })); } catch (e) { status.textContent = "Error: " + e.message; }
  }
  fold.addEventListener("click", () => send(decide.to_call ? "fold" : "check"));
  call.addEventListener("click", () => send(decide.to_call ? "call" : "check"));
  raise.addEventListener("click", () => send("raise", +slider.value));
  document.addEventListener("keydown", (e) => {
    if ($("play").hidden || fold.disabled || ["INPUT", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "f") fold.click(); else if (e.key === "c") call.click(); else if (e.key === "r") raise.click();
  });

  startB.addEventListener("click", async () => {
    startB.disabled = true; result.hidden = true; setControls(false);
    status.textContent = "Shuffling…";
    try {
      const resp = await rpc("newGame", { seed: +$("pSeed").value || 0, mode: $("pMode").value });
      gid = resp.gid;
      const hero = resp.names.indexOf("VT-Bot");
      T.setup(resp.names, resp.you, { you: resp.you, hero, revealAll: false });
      await handle(resp);
    } catch (e) { status.textContent = "Error: " + e.message; startB.disabled = false; }
  });
  setControls(false);
})();

// ---------- lab ----------
(() => {
  const bars = (rows, max) => `<div class="bars">${rows.map(([n, v, cls]) =>
    `<div class="barrow"><span>${n}</span><div><div class="bar ${cls || ""}" style="width:${Math.max(2, (v / max) * 100)}%"></div></div><span class="v">${v.toFixed(2)}</span></div>`).join("")}</div>`;
  const root = document.getElementById("labRoot");
  root.innerHTML = `
  <div>
    <h2>The story in one chart</h2>
    <p>Average points per table against four contest bots written independently by AI agents (2,000 tables each). An average seat earns 2.125.</p>
    ${bars([["v2 tuned on weak practice bots", 1.26, ""], ["v2 untuned", 1.95, ""], ["v2 re-tuned on the AI field", 2.30, ""], ["v3 (final strategy)", 2.37, "me"], ["Best AI bot (RL-trained)", 2.67, "top"]], 2.8)}
    <div class="callout" style="margin-top:12px"><p><b>Overfitting was the biggest lesson.</b> Tuned against simple practice bots, the bot scored 5+ points there but came last against realistic opponents. Those practice bots knocked each other out so fast that a bot that only checks and folds scored 3.83 against them.</p></div>
  </div>
  <div class="grid2">
    <div class="panel"><h3>Leaderboard vs the AI-written field</h3><div class="scroll"><table class="std">
      <tr><th>Bot</th><th class="n">Pts / table</th><th class="n">Top 3</th><th class="n">1st</th></tr>
      <tr><td>RL-trained (evolution strategy)</td><td class="n">2.67</td><td class="n">45%</td><td class="n">18%</td></tr>
      <tr class="me"><td>VT-Bot (this project)</td><td class="n">2.37</td><td class="n">45%</td><td class="n">10%</td></tr>
      <tr><td>Tournament specialist</td><td class="n">2.25</td><td class="n">38%</td><td class="n">13%</td></tr>
      <tr><td>Monte Carlo equity</td><td class="n">1.92</td><td class="n">32%</td><td class="n">13%</td></tr>
      <tr><td>Loose-aggressive exploiter</td><td class="n">1.51</td><td class="n">30%</td><td class="n">8%</td></tr></table></div></div>
    <div class="panel"><h3>Against Deep-ML's house bots (exact copies)</h3><div class="scroll"><table class="std">
      <tr><th>Version</th><th class="n">Pts / table</th><th class="n">Top 3</th><th class="n">1st</th></tr>
      <tr><td>Balanced (submitted)</td><td class="n">5.68</td><td class="n">77%</td><td class="n">37%</td></tr>
      <tr><td>Aggressive</td><td class="n">6.08</td><td class="n">75%</td><td class="n">53%</td></tr>
      <tr class="me"><td>Final: balanced, aggressive from hand 100</td><td class="n">6.05</td><td class="n">77%</td><td class="n">51%</td></tr></table></div>
      <p style="margin-top:8px;font-size:13px">1,500 tables each. The final version kept the balanced bot's survival rate and won outright far more often.</p></div>
  </div>
  <div class="grid2">
    <div class="panel"><h3>Speed: 3× faster, identical decisions</h3><table class="std">
      <tr><td>Median decision</td><td class="n">11.3 → 3.3 µs</td></tr>
      <tr><td>Slowest decision</td><td class="n">177 → 96 µs</td></tr>
      <tr><td>Decisions changed (of 137,972)</td><td class="n">0</td></tr>
      <tr><td>Contest limit</td><td class="n">1,000 µs</td></tr></table>
      <p style="margin-top:8px;font-size:13px">ICM math is skipped whenever it cannot change the answer (bounds on how far it can shrink a range), plus a faster O(n²) ICM and card-to-strength lookup tables.</p></div>
    <div class="panel"><h3>What the API exposes matters</h3><table class="std">
      <tr><th>Information available</th><th class="n">Pts / table</th></tr>
      <tr><td>Everything</td><td class="n">2.32</td></tr><tr><td>No action history</td><td class="n">1.78</td></tr>
      <tr><td>No opponent stacks</td><td class="n">1.53</td></tr><tr><td>No position either</td><td class="n">1.56</td></tr></table>
      <p style="margin-top:8px;font-size:13px">Testing this before the contest found a bug that would have silently folded 27,000 decisions when history was missing.</p></div>
  </div>
  <div class="panel">
    <h3>ICM calculator: why points are not chips</h3>
    <p>Only the top three of eight score (10 / 5 / 2). Enter stacks to see each player's expected points (Malmuth-Harville ICM). Big stacks are worth less than their chip share, short stacks more, which is why the bot tightens near the money.</p>
    <div class="icm-in">${[7000, 5000, 3000, 1000].map((v, i) => `<label style="display:grid;gap:4px;font-size:12px;color:var(--muted)">Player ${"ABCD"[i]}<input type="number" class="icmv" value="${v}" min="0" step="100"></label>`).join("")}</div>
    <div id="icmOut"></div>
  </div>`;
  const prizes = [10, 5, 2];
  function icm(stacks) {
    const out = stacks.map(() => 0), alive = stacks.map((s, i) => i).filter((i) => stacks[i] > 0), pz = prizes.slice(0, alive.length);
    (function rec(left, total, place, prob) {
      if (place >= pz.length || total <= 0) return;
      for (const i of left) { const p = (prob * stacks[i]) / total; out[i] += p * pz[place]; rec(left.filter((j) => j !== i), total - stacks[i], place + 1, p); }
    })(alive, alive.reduce((a, i) => a + stacks[i], 0), 0, 1);
    return out;
  }
  function drawIcm() {
    const st = [...document.querySelectorAll(".icmv")].map((e) => Math.max(0, +e.value || 0));
    const tot = st.reduce((a, b) => a + b, 0) || 1, pts = icm(st);
    document.getElementById("icmOut").innerHTML = `<table class="std"><tr><th>Player</th><th class="n">Chip share</th><th class="n">Expected points</th><th class="n">Point share</th></tr>` +
      st.map((s, i) => `<tr><td>${"ABCD"[i]}</td><td class="n">${((s / tot) * 100).toFixed(0)}%</td><td class="n">${pts[i].toFixed(2)}</td><td class="n">${((pts[i] / 17) * 100).toFixed(0)}%</td></tr>`).join("") + "</table>";
  }
  document.querySelectorAll(".icmv").forEach((e) => e.addEventListener("input", drawIcm));
  drawIcm();
})();
