"""Parameterized sit-and-go bot (v2).

v1 + two upgrades:
  1. Opponent profiles per seat (VPIP, PFR, shove rate, fold-to-raise, postflop fold rate).
     Used to read a raiser's range, steal from players who fold too much, and stop bluffing
     calling stations.
  2. Real ICM. All-in calls compare expected *points* of calling vs folding, using the
     shover's estimated range. Everything else scales by an "ICM risk premium": how much
     more than 50% equity a stack-for-stack flip needs right now.
Every decision is lookups plus a few O(n^2) loops, so it stays far below the 1 ms limit.
"""
import gc
import json
import os
import random

from cards import evaluate, straight_high, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE
from preflop import TABLE, hand_key, load_range_equity, equity_vs_range

RANGE_EQ = load_range_equity()
PRIZES = [10, 5, 2]
# Card pair -> hand key / strength percentile, so a decision never builds strings.
KEY = [[hand_key(a, b) if a != b else None for b in range(52)] for a in range(52)]
PCT = [[TABLE[KEY[a][b]]["pct"] if a != b else 1.0 for b in range(52)] for a in range(52)]

DEFAULT_PARAMS = {
    "open_pct": 0.12,         # unopened raise range, earliest position (fraction of hands)
    "open_late_bonus": 0.18,  # extra range when last to act
    "open_size": 2.5,         # open raise in big blinds (+1 per limper)
    "threebet_pct": 0.025,    # re-raise range vs a typical raiser
    "call_raise_pct": 0.06,   # flat-call range vs a typical raiser
    "push_bb": 12.0,          # at or below this many big blinds: push/fold only
    "push_base": 0.10,        # push range at push_bb from early position
    "push_exp": 1.0,          # how fast the push range widens as the stack shrinks
    "push_late_mult": 2.5,    # push range multiplier when last to act
    "bet_eq": 0.55,           # postflop: bet for value above this strength
    "raise_eq": 0.82,         # postflop: raise above this strength
    "call_margin": 0.03,      # postflop: need this much over pot odds to call
    "value_frac": 0.66,       # value bet size as a fraction of the pot
    "cbet": 0.55,             # heads-up flop continuation-bet frequency
    # opponent modeling
    "min_samples": 15,        # hands observed before trusting a seat's stats
    "default_range": 0.15,    # assumed shove/raise range for unknown players
    "range_mult": 1.3,        # observed raise frequency -> range width
    "ref_pfr": 0.12,          # raise frequency treated as "normal"
    "steal_widen": 1.0,       # widen opens/pushes when players behind fold too much
    "station_value": 0.08,    # bet thinner for value vs players who rarely fold
    # ICM
    "icm_k": 1.5,             # shrink ranges by (1 - icm_k * premium)
    "icm_post_k": 1.0,        # postflop: add premium * commitment to the required equity
    "icm_call_margin": 0.0,   # all-in call needs this many expected points over folding
}

BOUNDS = {
    "open_pct": (0.04, 0.35), "open_late_bonus": (0.0, 0.5), "open_size": (2.0, 4.0),
    "threebet_pct": (0.005, 0.1), "call_raise_pct": (0.01, 0.2), "push_bb": (6.0, 45.0),
    "push_base": (0.03, 0.4), "push_exp": (0.3, 2.0), "push_late_mult": (1.0, 5.0),
    "bet_eq": (0.3, 0.85), "raise_eq": (0.6, 0.98), "call_margin": (-0.1, 0.2),
    "value_frac": (0.3, 1.2), "cbet": (0.0, 1.0),
    "min_samples": (8, 40), "default_range": (0.05, 0.4), "range_mult": (0.8, 2.5),
    "ref_pfr": (0.05, 0.3), "steal_widen": (0.0, 4.0), "station_value": (0.0, 0.2),
    "icm_k": (0.0, 5.0), "icm_post_k": (0.0, 3.0), "icm_call_margin": (-0.3, 0.3),
}

TUNED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_params_v2.json")


def load_tuned():
    if os.path.exists(TUNED_PATH):
        with open(TUNED_PATH) as f:
            saved = json.load(f)
        return {k: v for k, v in saved.items() if k in DEFAULT_PARAMS}
    return None


def icm_me(stacks, i):
    """Expected points for stacks[i] (Malmuth-Harville). O(n^2): cheap enough per decision."""
    s = stacks[i]
    if s <= 0:
        return 0.0
    others = [x for j, x in enumerate(stacks) if j != i and x > 0]
    S = float(s + sum(others))
    ev = PRIZES[0] * s / S
    if not others:
        return ev
    p2 = p3 = 0.0
    third = len(others) > 1
    for a, sa in enumerate(others):
        pa = sa / S
        rest = S - sa
        p2 += pa * s / rest
        if third:
            inner = 0.0
            for b, sb in enumerate(others):
                if b != a:
                    inner += sb / (rest - sb)
            p3 += pa / rest * inner
    ev += PRIZES[1] * p2
    if third:
        ev += PRIZES[2] * p3 * s
    return ev


# The tables above live for the whole game: move them out of the garbage collector's view so its
# pauses (which can land inside a timed decision) have less to scan.
gc.collect()
gc.freeze()


class Profile:
    __slots__ = ("hands", "vpip", "pfr", "shoves", "faced_raise", "fold_raise", "faced_bet", "fold_bet")

    def __init__(self):
        for k in self.__slots__:
            setattr(self, k, 0)


class MyBot:
    def __init__(self, params=None, seed=None):
        self.p = dict(DEFAULT_PARAMS)
        if params:
            self.p.update(params)
        self.rng = random.Random(seed)
        self.prof = {}
        self._hand = -1      # hand number the caches below belong to
        self._icm = {}       # (stack totals, opponent) -> ICM premium
        self._board = {}     # board tuple -> evaluate(board)
        p = self.p
        # Widest range any unopened decision can use; anything weaker folds immediately.
        self._max_open = min(1.0, (p["open_pct"] + p["open_late_bonus"]) * (1 + max(0.0, p["steal_widen"]) * 0.45))

    # ---------- engine hooks ----------
    def get_action(self, s):
        try:
            if s["hand_number"] != self._hand:
                self._hand = s["hand_number"]
                self._icm.clear()
                self._board.clear()
            return self.preflop(s) if s["street"] == 0 else self.postflop(s)
        except Exception:
            return self.passive(s)

    def on_hand_end(self, info):
        gc.collect(0)  # untimed: clear young garbage now rather than mid-decision
        seen, vpip, pfr, shoves = set(), set(), set(), set()
        faced, folded = set(), set()
        raised = False
        post_faced, post_folded = {}, {}
        street_bet = {}
        for street, seat, kind, _amt, allin in info["history"]:
            seen.add(seat)
            if kind in ("sb", "bb"):
                continue
            if street == 0:
                if kind in ("call", "raise"):
                    vpip.add(seat)
                if kind == "raise":
                    pfr.add(seat)
                    if allin:
                        shoves.add(seat)
                if raised and seat not in faced:
                    faced.add(seat)
                    if kind == "fold":
                        folded.add(seat)
                if kind == "raise":
                    raised = True
            else:
                if street_bet.get(street):
                    post_faced[seat] = post_faced.get(seat, 0) + 1
                    post_folded[seat] = post_folded.get(seat, 0) + (kind == "fold")
                if kind == "raise":
                    street_bet[street] = True
        for seat in seen:
            pr = self.prof.get(seat)
            if pr is None:
                pr = self.prof[seat] = Profile()
            pr.hands += 1
            pr.vpip += seat in vpip
            pr.pfr += seat in pfr
            pr.shoves += seat in shoves
            pr.faced_raise += seat in faced
            pr.fold_raise += seat in folded
            pr.faced_bet += post_faced.get(seat, 0)
            pr.fold_bet += post_folded.get(seat, 0)

    # ---------- opponent reads ----------
    def known(self, seat):
        pr = self.prof.get(seat)
        return pr if pr and pr.hands >= self.p["min_samples"] else None

    def raise_range(self, seat, shove):
        """Estimated width (fraction of hands) of this seat's raising / shoving range."""
        pr = self.known(seat)
        if not pr:
            return self.p["default_range"]
        freq = (pr.shoves if shove and pr.shoves else pr.pfr) / pr.hands
        return min(1.0, max(0.02, freq * self.p["range_mult"]))

    def fold_to_raise(self, seat):
        pr = self.prof.get(seat)
        if not pr or pr.faced_raise < 6:
            return 0.5
        return pr.fold_raise / pr.faced_raise

    def is_station(self, seat):
        pr = self.prof.get(seat)
        return bool(pr) and pr.faced_bet >= 8 and pr.fold_bet / pr.faced_bet < 0.2

    # ---------- helpers ----------
    @staticmethod
    def passive(s):
        return ("check",) if s["to_call"] == 0 else ("fold",)

    @staticmethod
    def raise_to(s, target):
        if not s["can_raise"]:
            return ("call",)
        target = int(max(s["min_raise_to"], min(target, s["max_raise_to"])))
        if target >= 0.7 * s["max_raise_to"]:  # don't leave a sliver behind
            target = s["max_raise_to"]
        return ("raise", target)

    @staticmethod
    def last_raiser(s):
        for street, seat, kind, _a, _ai in reversed(s["history"]):
            if street == s["street"] and kind == "raise":
                return seat
        return None

    @staticmethod
    def seats_behind(s):
        acted = {seat for st, seat, k, _a, _ai in s["history"] if st == s["street"] and k not in ("sb", "bb")}
        out = set(s["folded"]) | set(s["allin"])
        return [t for t in s["stacks"] if t != s["seat"] and t not in acted and t not in out]

    def icm_premium(self, s, opp=None):
        """Extra equity (above 0.5) a stack-for-stack flip needs in ICM terms right now."""
        key = (tuple(s["stacks"].values()), opp)
        hit = self._icm.get(key)
        if hit is None:
            hit = self._icm[key] = self._icm_premium(s, opp)
        return hit

    def _icm_premium(self, s, opp):
        seats = list(s["stacks"])
        me = s["seat"]
        if opp is None:
            others = [t for t in seats if t != me]
            if not others:
                return 0.0
            opp = max(others, key=lambda t: s["stacks"][t])
        st = [s["stacks"][t] for t in seats]
        i, j = seats.index(me), seats.index(opp)
        eff = min(st[i], st[j])
        now = icm_me(st, i)
        win, lose = st[:], st[:]
        win[i] += eff
        win[j] -= eff
        lose[i] -= eff
        lose[j] += eff
        ev_win, ev_lose = icm_me(win, i), self.bust_points(s) if lose[i] == 0 else icm_me(lose, i)
        if ev_win <= ev_lose:
            return 0.0
        return max(0.0, (now - ev_lose) / (ev_win - ev_lose) - 0.5)

    @staticmethod
    def bust_points(s):
        place = s["players_left"]
        return PRIZES[place - 1] if place <= len(PRIZES) else 0.0

    def allin_call_ev(self, s, opp, eq):
        """Expected points of calling an all-in-sized bet minus expected points of folding."""
        seats = list(s["stacks"])
        me = s["seat"]
        i, j = seats.index(me), seats.index(opp)
        my_bet, opp_bet = s["bets"][me], s["bets"][opp]
        dead = s["pot"] - my_bet - opp_bet
        base = [s["stacks"][t] - s["bets"][t] for t in seats]  # everyone's chips minus this street's bets
        me_tot, opp_tot = s["stacks"][me], s["stacks"][opp]
        eff = min(me_tot, opp_tot)

        win, lose, fold = base[:], base[:], base[:]
        win[i] = me_tot + eff + dead
        win[j] = opp_tot - eff
        lose[i] = me_tot - eff
        lose[j] = opp_tot + eff + dead
        fold[i] = me_tot - my_bet
        fold[j] = opp_tot + my_bet + dead
        ev_lose = self.bust_points(s) if lose[i] == 0 else icm_me(lose, i)
        ev_call = eq * icm_me(win, i) + (1 - eq) * ev_lose
        ev_fold = self.bust_points(s) if fold[i] == 0 else icm_me(fold, i)
        return ev_call - ev_fold

    def _max_push(self, s):
        p = self.p
        mine = s["stacks"][s["seat"]]
        biggest_other = max((v for t, v in s["stacks"].items() if t != s["seat"]), default=mine)
        eff_bb = min(mine, biggest_other) / s["big_blind"]
        return (p["push_base"] * (p["push_bb"] / max(eff_bb, 1.0)) ** p["push_exp"]
                * p["push_late_mult"] * (1 + max(0.0, p["steal_widen"]) * 0.45))

    # ---------- preflop ----------
    def preflop(self, s):
        p = self.p
        bb = s["big_blind"]
        c1, c2 = s["hole"]
        pct = PCT[c1][c2]
        if s["current_bet"] <= bb and pct > self._max_open and pct > self._max_push(s):
            # Unopened and weaker than the widest open or push range we could use: fold before any other work.
            return self.passive(s)
        key = KEY[c1][c2]
        me = s["seat"]
        mine = s["stacks"][me]
        biggest_other = max((v for t, v in s["stacks"].items() if t != me), default=mine)
        eff_bb = min(mine, biggest_other) / bb
        late = 1.0 - s["players_behind"] / max(1, s["players_left"] - 1)
        to_call = s["to_call"]

        if s["current_bet"] <= bb:  # unopened (maybe limped)
            push = eff_bb <= p["push_bb"]
            if push:
                base = p["push_base"] * (p["push_bb"] / max(eff_bb, 1.0)) ** p["push_exp"]
                pos = 1 + (p["push_late_mult"] - 1) * late
            else:
                base, pos = p["open_pct"] + p["open_late_bonus"] * late, 1.0
            # Steal widening multiplies the range by 1..steal_max and ICM by 0.25..1, so most
            # hands are decided by these bounds without computing either (same decisions, less work).
            lo = base * pos * 0.25 * (1 - 1e-9)
            steal_max = 1 + max(0.0, p["steal_widen"]) * 0.45 * late
            if pct > base * pos * steal_max * (1 + 1e-9):
                return self.passive(s)
            if pct > lo:
                behind = self.seats_behind(s)
                ftr = sum(self.fold_to_raise(t) for t in behind) / len(behind) if behind else 1.0
                steal = 1 + p["steal_widen"] * max(0.0, ftr - 0.55) * late
                rng = base * (pos * steal) if push else base * steal
                if pct > rng:
                    return self.passive(s)
                if pct > rng * 0.25 and pct > rng * max(0.25, 1 - p["icm_k"] * self.icm_premium(s)):
                    return self.passive(s)
            if push:
                return self.raise_to(s, s["max_raise_to"])
            limpers = sum(1 for st, _, k, _a, _ai in s["history"] if st == 0 and k == "call")
            return self.raise_to(s, bb * (p["open_size"] + limpers))

        raiser = self.last_raiser(s)
        if to_call >= 0.4 * s["stack"] or eff_bb <= p["push_bb"]:
            # Committing decision: compare expected points of going all-in vs folding.
            shove = raiser is not None and raiser in s["allin"]
            width = self.raise_range(raiser, shove) if raiser is not None else p["default_range"]
            eq = equity_vs_range(key, width, RANGE_EQ)
            others_in = sum(1 for t in s["stacks"] if t not in (me, raiser) and s["bets"][t] > bb)
            eq = eq ** (1 + 0.8 * others_in)  # crude multiway discount
            if self.allin_call_ev(s, raiser, eq) > p["icm_call_margin"]:
                return self.raise_to(s, s["max_raise_to"])
            return self.passive(s)

        # Normal raise: scale ranges by how wide this raiser opens and by ICM pressure.
        width = self.raise_range(raiser, False) if raiser is not None else p["ref_pfr"]
        looseness = min(2.5, max(0.4, width / (p["ref_pfr"] * p["range_mult"])))
        if pct <= p["threebet_pct"] * looseness:
            return self.raise_to(s, s["current_bet"] * 3)
        call_rng = p["call_raise_pct"] * looseness
        if pct > call_rng:
            return self.passive(s)
        if pct <= call_rng * 0.25 or pct <= call_rng * max(0.25, 1 - p["icm_k"] * self.icm_premium(s, raiser)):
            return ("call",)
        return self.passive(s)

    # ---------- postflop ----------
    def made_strength(self, hole, board):
        v = evaluate(hole + board)
        cat = v >> 20
        bkey = tuple(board)
        bv = self._board.get(bkey)
        if bv is None:
            bv = self._board[bkey] = evaluate(board)
        bcat = bv >> 20
        if cat <= bcat:
            return 0.12  # playing the board
        branks = sorted((c >> 2 for c in board), reverse=True)
        hranks = [c >> 2 for c in hole]
        if cat == PAIR:
            pr = ((v >> 16) & 15) - 1
            if hranks[0] == hranks[1] and pr > branks[0]:
                return 0.72  # overpair
            if pr == branks[0]:
                kicker = max((r for r in hranks if r != pr), default=12)
                return 0.6 if kicker >= 9 else 0.52  # top pair
            return 0.42 if pr >= branks[1] else 0.3
        if cat == TWO_PAIR:
            return 0.5 if bcat == PAIR else 0.78
        if cat == TRIPS:
            return 0.75 if bcat == PAIR else 0.86
        return {STRAIGHT: 0.87, FLUSH: 0.91, FULL_HOUSE: 0.96}.get(cat, 0.99)

    @staticmethod
    def draw_equity(hole, board, street):
        if street == 3:
            return 0.0
        cards = hole + board
        outs = 0.0
        for su in range(4):
            if sum(1 for c in cards if c & 3 == su) == 4 and any(c & 3 == su for c in hole):
                outs += 9
        mask = 0
        for c in cards:
            mask |= 1 << (c >> 2)
        if straight_high(mask) < 0:
            ranks = sum(1 for r in range(13) if not mask >> r & 1 and straight_high(mask | 1 << r) >= 0)
            outs += min(8, 4 * ranks) * (0.7 if outs else 1.0)
        return min(0.6, outs * (0.04 if street == 1 else 0.02))

    def postflop(self, s):
        p = self.p
        hole, board = list(s["hole"]), s["board"]
        me = s["seat"]
        n_opp = max(1, s["active_in_hand"] - 1)
        made = self.made_strength(hole, board) ** (1 + 0.6 * (n_opp - 1))
        e = max(made, self.draw_equity(hole, board, s["street"]))
        pot, to_call = s["pot"], s["to_call"]
        opps = [t for t in s["stacks"] if t != me and t not in s["folded"]]
        vs_station = n_opp == 1 and self.is_station(opps[0])

        if to_call == 0:
            if e >= p["bet_eq"] - (p["station_value"] if vs_station else 0.0):
                return self.raise_to(s, s["bet"] + p["value_frac"] * pot)
            raised_pre = any(st == 0 and seat == me and k == "raise"
                             for st, seat, k, _a, _ai in s["history"])
            if (raised_pre and n_opp == 1 and not vs_station and s["street"] == 1
                    and self.rng.random() < p["cbet"]):
                return self.raise_to(s, s["bet"] + 0.5 * pot)
            return ("check",)

        odds = to_call / (pot + to_call)
        if e >= p["raise_eq"]:
            return self.raise_to(s, s["current_bet"] + max(s["big_blind"], p["value_frac"] * (pot + to_call)))
        commit = min(1.0, to_call / max(1, s["stack"]))
        base = odds + p["call_margin"]
        # The ICM premium is between 0 and 0.5, so skip computing it when it can't change the answer.
        if e < base - 1e-12:
            return ("fold",)
        if p["icm_post_k"] >= 0 and e >= base + p["icm_post_k"] * 0.5 * commit + 1e-12:
            return ("call",)
        need = base + p["icm_post_k"] * self.icm_premium(s, self.last_raiser(s)) * commit
        return ("call",) if e >= need else ("fold",)
