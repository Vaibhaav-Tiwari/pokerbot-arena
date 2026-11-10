"""Math / equity bot.

Import time: Monte Carlo preflop equity of all 169 hand classes vs a random hand, giving a strength
ranking (combo list sorted best-first). Then equity of each class vs top-X% ranges.
Decision time: postflop budgeted Monte Carlo vs opponents' estimated ranges (sampled from the
ranked combo list according to each opponent's observed preflop tendencies), then EV-style
thresholds against pot odds for calls and bet sizing.
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cards import evaluate  # noqa: E402

_rng = random.Random(12345)


def _cls(a, b):
    ra, rb = a >> 2, b >> 2
    hi, lo = (ra, rb) if ra >= rb else (rb, ra)
    if (a & 3) == (b & 3) and hi != lo:
        return hi * 13 + lo  # suited: hi >= lo slot
    return lo * 13 + hi      # offsuit / pairs (pairs: hi==lo, same slot)


def _build_tables():
    t0 = time.perf_counter()
    reps = {}
    combos_by_cls = {}
    for a in range(52):
        for b in range(a + 1, 52):
            c = _cls(a, b)
            combos_by_cls.setdefault(c, []).append((a, b))
    for c, lst in combos_by_cls.items():
        reps[c] = lst[0]
    deck = list(range(52))
    ev = evaluate
    smp = _rng.sample
    # equity vs random hand
    eq_rand = {}
    n1 = 140
    for c, (a, b) in reps.items():
        rest = [x for x in deck if x != a and x != b]
        w = 0.0
        for _ in range(n1):
            s = smp(rest, 7)
            bd = s[2:]
            h = ev([a, b] + bd)
            o = ev(s[:2] + bd)
            w += 1.0 if h > o else (0.5 if h == o else 0.0)
        eq_rand[c] = w / n1
    order = sorted(eq_rand, key=lambda c: -eq_rand[c])
    combos = []
    pct = {}
    for c in order:
        combos.extend(combos_by_cls[c])
        pct[c] = len(combos) / 1326.0  # cumulative fraction incl. this class
    # equity vs top-X ranges
    fracs = (0.08, 0.2, 0.4)
    eq_vs = {}
    n2 = 70
    for f in fracs:
        top = combos[:max(1, int(1326 * f))]
        d = {}
        for c, (a, b) in reps.items():
            rest = [x for x in deck if x != a and x != b]
            w = 0.0
            k = 0
            while k < n2:
                o1, o2 = top[_rng.randrange(len(top))]
                if o1 == a or o1 == b or o2 == a or o2 == b:
                    continue
                k += 1
                bd = smp([x for x in rest if x != o1 and x != o2], 5) if k % 8 == 0 else None
                if bd is None:
                    bd = []
                    while len(bd) < 5:
                        x = rest[_rng.randrange(50)]
                        if x != o1 and x != o2 and x not in bd:
                            bd.append(x)
                h = ev([a, b] + bd)
                o = ev([o1, o2] + bd)
                w += 1.0 if h > o else (0.5 if h == o else 0.0)
            d[c] = w / n2
        eq_vs[f] = d
    eq_vs[1.0] = eq_rand
    return eq_rand, pct, combos, eq_vs, time.perf_counter() - t0


EQ_RAND, PCT, COMBOS, EQ_VS, BUILD_TIME = _build_tables()
NCOMBOS = len(COMBOS)


def pre_eq_vs(c, frac):
    """Interpolated preflop HU equity of class c vs top-frac range."""
    keys = (0.08, 0.2, 0.4, 1.0)
    if frac <= keys[0]:
        return EQ_VS[keys[0]][c]
    for i in range(3):
        lo, hi = keys[i], keys[i + 1]
        if frac <= hi:
            t = (frac - lo) / (hi - lo)
            return EQ_VS[lo][c] * (1 - t) + EQ_VS[hi][c] * t
    return EQ_RAND[c]


class Bot:
    def __init__(self):
        self.rng = random.Random(7)
        # per-seat stats: [hands, vpip, pfr, postflop aggressive acts, postflop passive acts]
        self.stats = {}
        self._cache = {}
        self._cache_hand = -1

    # ------------------------------------------------------------ opponent model
    def _st(self, s):
        st = self.stats.get(s)
        if st is None:
            st = [0, 0, 0, 0, 0]
            self.stats[s] = st
        return st

    def on_hand_end(self, info):
        try:
            hist = info.get("history", [])
            seen = set()
            vp = set()
            pr = set()
            for street, s, kind, _bt, _ai in hist:
                seen.add(s)
                if street == 0:
                    if kind in ("call", "raise"):
                        vp.add(s)
                    if kind == "raise":
                        pr.add(s)
                else:
                    st = self._st(s)
                    if kind == "raise":
                        st[3] += 1
                    elif kind in ("call", "check"):
                        st[4] += 1
            for s in seen:
                st = self._st(s)
                st[0] += 1
                if s in vp:
                    st[1] += 1
                if s in pr:
                    st[2] += 1
        except Exception:
            pass

    def _vpip(self, s):
        st = self.stats.get(s, [0, 0, 0, 0, 0])
        return (st[1] + 3.0) / (st[0] + 10.0)

    def _pfr(self, s):
        st = self.stats.get(s, [0, 0, 0, 0, 0])
        return (st[2] + 1.5) / (st[0] + 10.0)

    def _aggr(self, s):
        st = self.stats.get(s, [0, 0, 0, 0, 0])
        return (st[3] + 2.0) / (st[3] + st[4] + 6.0)

    def _range_frac(self, s, hist):
        """Estimated top-fraction of hands seat s holds, from its preflop action this hand."""
        raised = 0
        called = False
        for street, t, kind, _bt, _ai in hist:
            if street == 0 and t == s:
                if kind == "raise":
                    raised += 1
                elif kind == "call":
                    called = True
        if raised:
            f = min(1.0, max(0.05, self._pfr(s) * 1.1))
            if raised > 1:
                f *= 0.5
            return max(0.04, f)
        if called:
            return min(1.0, max(0.15, self._vpip(s) * 1.1))
        return 1.0

    # ------------------------------------------------------------ equity MC
    def _mc_equity(self, hole, board, opps, hist, deadline):
        known = set(hole) | set(board)
        deck = [x for x in range(52) if x not in known]
        nd = len(deck)
        need = 5 - len(board)
        fr = [max(1, int(NCOMBOS * self._range_frac(s, hist))) for s in opps]
        rr = self.rng.random
        ev = evaluate
        hl = list(hole)
        wins = 0.0
        n = 0
        tc = time.perf_counter
        while n < 400:
            if (n & 1) == 0 and tc() > deadline:
                break
            used = set(known)
            ohands = []
            ok = True
            for f in fr:
                for _ in range(12):
                    o1, o2 = COMBOS[int(rr() * f)]
                    if o1 not in used and o2 not in used:
                        break
                else:
                    o1 = o2 = -1
                    while True:
                        o1 = deck[int(rr() * nd)]
                        o2 = deck[int(rr() * nd)]
                        if o1 != o2 and o1 not in used and o2 not in used:
                            break
                used.add(o1)
                used.add(o2)
                ohands.append((o1, o2))
            if not ok:
                continue
            bd = list(board)
            while len(bd) < len(board) + need:
                x = deck[int(rr() * nd)]
                if x not in used:
                    used.add(x)
                    bd.append(x)
            h = ev(hl + bd)
            best = 0
            ties = 0
            lose = False
            for o1, o2 in ohands:
                v = ev([o1, o2] + bd)
                if v > h:
                    lose = True
                    break
                if v == h:
                    ties += 1
            if not lose:
                wins += 1.0 / (ties + 1)
            n += 1
        if n == 0:
            return None
        return wins / n, n

    # ------------------------------------------------------------ main
    def get_action(self, state):
        t0 = time.perf_counter()
        to_call = state.get("to_call", 0)
        try:
            act = self._decide(state, t0 + 0.0002)
            return self._legal(act, state)
        except Exception:
            return ("check",) if to_call == 0 else ("fold",)

    def _legal(self, act, state):
        to_call = state["to_call"]
        k = act[0]
        if k == "raise":
            if not state["can_raise"]:
                return ("call",) if to_call > 0 else ("check",)
            amt = int(act[1])
            amt = max(state["min_raise_to"], min(amt, state["max_raise_to"]))
            return ("raise", amt)
        if k == "call":
            return ("call",) if to_call > 0 else ("check",)
        if k == "fold":
            return ("fold",) if to_call > 0 else ("check",)
        return ("check",) if to_call == 0 else ("fold",)

    def _raise_to(self, state, total):
        # if raise would commit most of our stack, just shove
        mx = state["max_raise_to"]
        if total >= 0.6 * mx:
            total = mx
        return ("raise", int(total))

    def _decide(self, state, deadline):
        seat = state["seat"]
        hole = state["hole"]
        board = state["board"]
        pot = state["pot"]
        to_call = state["to_call"]
        bb = state["big_blind"]
        stack = state["stack"]
        hist = state["history"]
        folded = set(state["folded"])
        stacks = state["stacks"]
        opps = [s for s in stacks if s != seat and s not in folded]
        nopp = len(opps)
        eff = stack + state["bet"]
        if opps:
            eff = min(eff, max(stacks[s] for s in opps))
        pot_odds = to_call / float(pot + to_call) if to_call > 0 else 0.0

        if state["street"] == 0:
            return self._preflop(state, opps, eff, pot_odds)

        key = (state["hand_number"], state["street"], tuple(opps))
        res = self._cache.get(key) if self._cache_hand == state["hand_number"] else None
        if res is None:
            res = self._mc_equity(hole, board, opps[:4] if len(opps) > 4 else opps, hist, deadline)
            if self._cache_hand != state["hand_number"]:
                self._cache = {}
                self._cache_hand = state["hand_number"]
            self._cache[key] = res
        if res is None:
            return ("check",) if to_call == 0 else ("fold",)
        eq, _n = res
        if len(opps) > 4:
            eq *= 0.85
        # discount for postflop aggression from opponents this hand (range is tighter than preflop says)
        cur_street = state["street"]
        disc = 0.0
        for street, s, kind, _bt, _ai in hist:
            if street >= 1 and s != seat and kind == "raise":
                a = self._aggr(s)
                d = 0.05 * (1.0 - a) + 0.01
                if street == cur_street:
                    d *= 1.3
                disc += d
        eq = max(0.0, eq - min(disc, 0.3))

        spr = eff / max(1.0, pot)
        street = cur_street
        if to_call == 0:
            loose = sum(self._vpip(o) for o in opps) / max(1, nopp)
            vthr = 0.62 if nopp > 1 else (0.5 if loose > 0.45 else 0.54)
            if eq > vthr:
                frac = 0.75 if eq < 0.75 else 1.0
                if loose > 0.45 and eq > 0.7:
                    frac = 1.0
                if spr < 1.5 and eq > 0.6:
                    return self._raise_to(state, state["max_raise_to"])
                amt = max(bb, int(pot * frac))
                return self._raise_to(state, state["bet"] + amt)
            # occasional stab heads-up on flop/turn
            if nopp == 1 and street <= 2 and eq > 0.3 and self.rng.random() < 0.45:
                opp = opps[0]
                if self._vpip(opp) < 0.3:
                    return self._raise_to(state, state["bet"] + max(bb, int(pot * 0.5)))
            return ("check",)

        # facing a bet
        need = pot_odds + 0.02 + (0.02 if street < 3 else 0.0)
        if eq > 0.75 or (eq > 0.62 and nopp == 1 and eq > need + 0.2):
            if state["can_raise"]:
                tgt = state["current_bet"] + max(to_call, int((pot + to_call) * 0.8))
                if spr < 2.5 or eq > 0.85:
                    tgt = state["max_raise_to"]
                return self._raise_to(state, tgt)
            return ("call",)
        if eq >= need:
            return ("call",)
        return ("fold",)

    def _preflop(self, state, opps, eff, pot_odds):
        hole = state["hole"]
        c = _cls(hole[0], hole[1])
        p = PCT[c]  # 0 = best
        bb = state["big_blind"]
        to_call = state["to_call"]
        cur = state["current_bet"]
        hist = state["history"]
        seat = state["seat"]
        behind = state["players_behind"]
        nopp = len(opps)
        eff_bb = eff / float(bb)
        stack_bb = (state["stack"] + state["bet"]) / float(bb)

        raisers = [s for (st, s, k, _b, _a) in hist if st == 0 and k == "raise" and s != seat]
        nlimp = sum(1 for (st, s, k, _b, _a) in hist if st == 0 and k == "call" and s != seat)

        # short-stack push/fold
        if stack_bb <= 12:
            if not raisers:
                n_after = max(1, behind)
                thr = 0.10 + 0.25 * (12 - stack_bb) / 12.0 + 0.04 * (6 - min(6, n_after))
                if p <= thr:
                    return ("raise", state["max_raise_to"])
                if to_call == 0:
                    return ("check",)
                return ("fold",)
            # facing raise: call/shove with equity vs their range
            rf = min(self._range_frac(s, hist) for s in raisers)
            e = pre_eq_vs(c, rf)
            if len(raisers) > 1 or nopp > 2:
                e -= 0.06
            need = (state["max_raise_to"] - state["bet"]) / float(state["pot"] + state["max_raise_to"] - state["bet"])
            if e > max(need, 0.4) + 0.02:
                return ("raise", state["max_raise_to"])
            if e > pot_odds + 0.03 and to_call <= 0.35 * state["stack"]:
                return ("call",)
            return ("check",) if to_call == 0 else ("fold",)

        if not raisers:
            # unopened or limped pot
            late = behind <= 2
            thr = 0.24 if late else (0.17 if behind <= 4 else 0.13)
            thr += 0.02 * nlimp * 0  # keep tight with limpers
            if p <= thr:
                size = int(bb * (3 + nlimp))
                return ("raise", size)
            if to_call == 0:
                return ("check",)
            # cheap complete / limp with decent hands
            if to_call <= bb * 0.5 and p <= 0.5:
                return ("call",)
            if nlimp >= 2 and to_call <= bb and p <= 0.30:
                return ("call",)
            return ("fold",)

        # facing a raise
        rf = min(self._range_frac(s, hist) for s in raisers)
        e = pre_eq_vs(c, rf)
        multi = nopp - 1
        e_adj = e - 0.05 * multi - (0.04 if len(raisers) > 1 else 0.0)
        commit = to_call / float(max(1, state["stack"]))
        # 3-bet / shove for value
        if e_adj > 0.62 or p <= 0.03:
            if state["can_raise"]:
                tgt = cur * 3 + to_call
                if tgt > 0.4 * (state["stack"] + state["bet"]) or eff_bb < 30:
                    tgt = state["max_raise_to"]
                return self._raise_to(state, tgt)
            return ("call",)
        # implied odds for pairs/suited when deep and cheap
        implied = 0.0
        if commit < 0.08 and eff_bb > 25:
            r1, r2 = hole[0] >> 2, hole[1] >> 2
            if r1 == r2:
                implied = 0.08
            elif (hole[0] & 3) == (hole[1] & 3) and abs(r1 - r2) <= 2:
                implied = 0.04
        if e_adj + implied > pot_odds + 0.04 and (commit < 0.35 or e_adj > 0.5):
            return ("call",)
        return ("fold",)
