"""Aggressive exploiter: loose-aggressive bot with per-opponent tracking."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cards import evaluate  # noqa: E402

_HIGH = {12: 10.0, 11: 8.0, 10: 7.0, 9: 6.0}


def chen(c1, c2):
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 < r2:
        r1, r2 = r2, r1
    s = _HIGH.get(r1, (r1 + 2) / 2.0)
    if r1 == r2:
        return max(5.0, s * 2)
    if (c1 & 3) == (c2 & 3):
        s += 2
    gap = r1 - r2 - 1
    s -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and r1 < 10:
        s += 1
    return s


class Stats(object):
    __slots__ = ("hands", "vpip", "pfr", "faced", "folds", "acts", "aggr",
                 "sd", "sd_weak")

    def __init__(self):
        self.hands = 0
        self.vpip = 0
        self.pfr = 0
        self.faced = 0   # times facing a bet
        self.folds = 0   # folded facing a bet
        self.acts = 0    # voluntary actions
        self.aggr = 0    # raises among those
        self.sd = 0
        self.sd_weak = 0

    def vp(self):
        return (self.vpip + 1.0) / (self.hands + 3.0)

    def pf(self):
        return (self.pfr + 0.5) / (self.hands + 4.0)

    def fold_rate(self):
        return (self.folds + 2.0) / (self.faced + 5.0)

    def aggression(self):
        return (self.aggr + 1.0) / (self.acts + 4.0)


class Bot(object):
    def __init__(self):
        self.st = {}
        self.pf_aggr = False
        self.last_hand = -1

    def S(self, seat):
        s = self.st.get(seat)
        if s is None:
            s = self.st[seat] = Stats()
        return s

    # ------------------------------------------------------------------ stats
    def on_hand_end(self, info):
        try:
            hist = info["history"]
            seen = set()
            vp, pr = set(), set()
            cur = {}
            bets = {}
            street = -1
            for (stt, seat, kind, to, allin) in hist:
                if stt != street:
                    street = stt
                    bets = {}
                    cur_bet = 0
                seen.add(seat)
                if kind in ("sb", "bb"):
                    bets[seat] = to
                    cur_bet = max(cur_bet, to)
                    continue
                s = self.S(seat)
                facing = cur_bet > bets.get(seat, 0)
                if facing:
                    s.faced += 1
                    if kind == "fold":
                        s.folds += 1
                if kind in ("call", "raise", "check"):
                    s.acts += 1
                    if kind == "raise":
                        s.aggr += 1
                if stt == 0 and kind in ("call", "raise"):
                    vp.add(seat)
                    if kind == "raise":
                        pr.add(seat)
                if kind in ("call", "raise"):
                    bets[seat] = to
                    if to > cur_bet:
                        cur_bet = to
            for seat in seen:
                s = self.S(seat)
                s.hands += 1
                if seat in vp:
                    s.vpip += 1
                if seat in pr:
                    s.pfr += 1
            board = info.get("board") or []
            shown = info.get("shown") or {}
            if len(shown) > 1 and len(board) == 5:
                for seat, h in shown.items():
                    s = self.S(seat)
                    s.sd += 1
                    v = evaluate(list(h) + list(board))
                    if (v >> 20) <= 1:
                        s.sd_weak += 1
        except Exception:
            pass

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _raise(state, total):
        lo, hi = state["min_raise_to"], state["max_raise_to"]
        total = int(total)
        if total < lo:
            total = lo
        if total > hi:
            total = hi
        # if it leaves us with little behind, just jam
        if hi - total < 0.35 * total:
            total = hi
        return ("raise", total)

    @staticmethod
    def _pass(state):
        return ("check",) if state["to_call"] == 0 else ("fold",)

    def get_action(self, state):
        try:
            if state["street"] == 0:
                return self.preflop(state)
            return self.postflop(state)
        except Exception:
            return self._pass(state)

    # ------------------------------------------------------------- preflop
    def preflop(self, state):
        me = state["seat"]
        h = state["hole"]
        bb = state["big_blind"]
        to_call = state["to_call"]
        stack = state["stack"]
        my_total = stack + state["bet"]
        hist = state["history"]
        if state["hand_number"] != self.last_hand:
            self.last_hand = state["hand_number"]
            self.pf_aggr = False
        c = chen(h[0], h[1])
        stacks = state["stacks"]
        folded = state["folded"]
        others = [stacks[s] for s in stacks if s != me and s not in folded]
        eff = min(my_total, max(others) if others else my_total)
        eff_bb = eff / float(bb)
        raises = [x for x in hist if x[0] == 0 and x[2] == "raise"]
        limpers = sum(1 for x in hist if x[0] == 0 and x[2] == "call")
        behind = state["players_behind"]
        nplayers = state["active_in_hand"]
        pl = state["players_left"]
        can_raise = state["can_raise"]

        # tightness of players yet to act (for steals)
        acted = set(x[1] for x in hist if x[2] not in ("sb", "bb"))
        rest = [s for s in stacks if s != me and s not in folded and s not in acted]
        fold_beh = sum(self.S(s).fold_rate() for s in rest) / len(rest) if rest else 0.5
        loose_beh = sum(self.S(s).vp() for s in rest) / len(rest) if rest else 0.3

        short_adj = 0.0
        if pl <= 4:
            short_adj = -1.0  # loosen up when table is short-handed
        if pl <= 2:
            short_adj = -2.5

        if not raises:
            # unopened (maybe limpers)
            thresh = 5.0 + 0.55 * behind + short_adj + 0.6 * limpers
            thresh -= (fold_beh - 0.5) * 4.0
            thresh += (loose_beh - 0.35) * 3.0
            if eff_bb <= 13:
                jam = thresh + (0.5 if eff_bb > 8 else -0.5)
                if c >= jam and can_raise:
                    self.pf_aggr = True
                    return self._raise(state, state["max_raise_to"])
                return ("check",) if to_call == 0 else ("fold",)
            if c >= thresh and can_raise:
                self.pf_aggr = True
                size = bb * (2.5 + limpers) + (bb if behind >= 4 else 0)
                return self._raise(state, size)
            if to_call == 0:
                return ("check",)
            # cheap limp/complete
            if to_call <= bb * 0.5 + 1 and c >= 5:
                return ("call",)
            if limpers >= 2 and c >= 6.5 and to_call <= bb:
                return ("call",)
            return ("fold",)

        # facing raise(s)
        last = raises[-1]
        raiser = last[1]
        rs = self.S(raiser)
        loose = rs.pf()  # raise frequency
        nr = len(raises)
        cost_frac = to_call / float(max(1, stack))
        jammed = any(x[4] for x in raises)

        if nr == 1:
            if loose > 0.35:
                t3, tc = 8.5, 7.0
            elif loose < 0.12:
                t3, tc = 12.0, 9.0
            else:
                t3, tc = 10.0, 8.0
            t3 += short_adj * 0.5
            tc += short_adj * 0.5
            if behind == 0 and nplayers <= 3:
                tc -= 0.5
        else:
            if loose > 0.35:
                t3, tc = 11.0, 9.5
            else:
                t3, tc = 14.0, 11.0
        if cost_frac > 0.4 or jammed:
            # basically all-in decision
            need = tc + 1.0
            if loose > 0.35:
                need -= 1.5
            if pl <= 3:
                need -= 1.0
            if c >= need:
                return ("call",) if not can_raise or jammed else self._raise(state, state["max_raise_to"])
            return ("fold",)
        if c >= t3 and can_raise:
            self.pf_aggr = True
            if eff_bb <= 25:
                return self._raise(state, state["max_raise_to"])
            size = state["current_bet"] * 3 + limpers * bb
            return self._raise(state, size)
        # re-steal vs loose late opener
        if (nr == 1 and loose > 0.28 and rs.fold_rate() > 0.4 and c >= tc - 1.5
                and can_raise and limpers == 0 and eff_bb < 40 and eff_bb > 10):
            self.pf_aggr = True
            return self._raise(state, state["max_raise_to"] if eff_bb < 22
                               else state["current_bet"] * 3)
        odds = to_call / float(state["pot"] + to_call)
        if c >= tc and (cost_frac < 0.25 or c >= tc + 2):
            return ("call",)
        if odds < 0.2 and c >= tc - 2 and cost_frac < 0.1:
            return ("call",)
        if to_call == 0:
            return ("check",)
        return ("fold",)

    # ------------------------------------------------------------ postflop
    def strength(self, hole, board):
        """Heuristic hand strength 0..1 plus draw equity."""
        allc = list(hole) + board
        v = evaluate(allc)
        cat = v >> 20
        bv = evaluate(board)
        bcat = bv >> 20
        h1, h2 = hole[0] >> 2, hole[1] >> 2
        branks = sorted((c >> 2 for c in board), reverse=True)
        top = branks[0]
        # board texture
        suits = [0, 0, 0, 0]
        for cc in board:
            suits[cc & 3] += 1
        maxs = max(suits)
        bmask = 0
        for r in branks:
            bmask |= 1 << r
        paired_board = len(set(branks)) < len(branks)

        if cat >= 4 and cat > bcat:
            s = 0.9 + 0.02 * (cat - 4)
            if cat == 4 and maxs >= 3:
                s = 0.8
            if cat == 5:
                # flush: is our flush card high?
                fs = max(range(4), key=lambda k: suits[k])
                hr = max([c >> 2 for c in hole if (c & 3) == fs] or [0])
                s = 0.86 + 0.01 * hr
                if paired_board:
                    s -= 0.08
            return min(s, 0.99), 0.0
        if cat >= 4:  # board plays
            return 0.3, 0.0
        s = 0.1
        if cat == 3:  # trips/set
            if h1 == h2:
                s = 0.9
            else:
                s = 0.78 if bcat < 3 else 0.25
            if maxs >= 3 and len(board) >= 4:
                s -= 0.1
        elif cat == 2:
            # two pair: both hole cards used?
            if h1 != h2 and h1 in branks and h2 in branks:
                s = 0.8 if not paired_board else 0.6
            elif h1 == h2 and h1 > top:
                s = 0.72
            elif paired_board and (h1 in branks or h2 in branks):
                s = 0.6
            else:
                s = 0.35
        elif cat == 1:
            if h1 == h2:
                if h1 > top:
                    s = 0.7 + 0.015 * h1
                elif h1 > branks[1] if len(branks) > 1 else False:
                    s = 0.45
                else:
                    s = 0.3
            elif top in (h1, h2):
                kick = h2 if h1 == top else h1
                s = 0.58 + 0.012 * kick + 0.006 * top
            elif len(branks) > 1 and branks[1] in (h1, h2):
                s = 0.42
            elif h1 in branks or h2 in branks:
                s = 0.33
            else:
                s = 0.15 + 0.01 * max(h1, h2)  # board pair, we have high card
        else:
            s = 0.05 + 0.012 * max(h1, h2)
            if h1 > top and h2 > top:
                s += 0.07
        if maxs >= 4 and cat < 5:
            s -= 0.12
        elif maxs == 3 and len(board) >= 4 and cat < 5:
            s -= 0.05
        # draws
        draw = 0.0
        if len(board) < 5:
            for k in range(4):
                if suits[k] == 3 or suits[k] == 4:
                    pass
            for k in range(4):
                n = suits[k] + sum(1 for c in hole if (c & 3) == k)
                if n == 4 and any((c & 3) == k for c in hole):
                    draw = max(draw, 0.18 if len(board) == 3 else 0.1)
            mask = bmask | (1 << h1) | (1 << h2)
            m = (mask << 1) | ((mask >> 12) & 1)
            for lo in range(0, 10):
                w = (m >> lo) & 0x1F
                cnt = bin(w).count("1")
                if cnt == 4:
                    # contains a hole card?
                    hm = ((1 << h1) | (1 << h2)) << 1
                    if (w << lo) & hm:
                        inner = (w & 1) and (w & 16)
                        d = 0.08 if inner else 0.16
                        if len(board) == 4:
                            d *= 0.55
                        draw = max(draw, d) if draw < 0.17 else draw + 0.06
                        break
        return max(0.0, min(s, 0.99)), draw

    def postflop(self, state):
        me = state["seat"]
        hole = state["hole"]
        board = state["board"]
        pot = state["pot"]
        to_call = state["to_call"]
        stack = state["stack"]
        street = state["street"]
        bb = state["big_blind"]
        hist = state["history"]
        can_raise = state["can_raise"]
        folded = state["folded"]
        stacks = state["stacks"]
        opps = [s for s in stacks if s != me and s not in folded]
        nopp = len(opps)
        s, draw = self.strength(hole, board)
        if state["hand_number"] != self.last_hand:
            self.pf_aggr = False

        fold_r = sum(self.S(o).fold_rate() for o in opps) / max(1, nopp)
        sd_weak = sum((self.S(o).sd_weak + 1.0) / (self.S(o).sd + 3.0) for o in opps) / max(1, nopp)
        station = fold_r < 0.3
        eff = min(stack + state["bet"], max(stacks[o] for o in opps) if opps else stack)
        spr = eff / float(max(1, pot))

        # multiway discount
        if nopp >= 2:
            s -= 0.05 * (nopp - 1)

        if to_call == 0:
            if not can_raise:
                return ("check",)
            # value bet
            vthresh = 0.55 if station else 0.62
            if nopp >= 3:
                vthresh += 0.08
            if s >= vthresh:
                frac = 0.75 if s < 0.85 else 0.9
                if station:
                    frac += 0.15
                if spr < 1.5 and s >= 0.6:
                    return self._raise(state, state["max_raise_to"])
                return self._raise(state, state["bet"] + frac * pot)
            # semi-bluff draws
            if draw >= 0.14 and street < 3 and nopp <= 2:
                return self._raise(state, state["bet"] + 0.65 * pot)
            # c-bet / bluff vs folders
            bluff_ok = (not station) and nopp <= 2 and fold_r > 0.4
            if bluff_ok:
                if self.pf_aggr and street == 1:
                    return self._raise(state, state["bet"] + 0.55 * pot)
                if self.pf_aggr and street == 2 and fold_r > 0.5 and (s + draw) > 0.2:
                    return self._raise(state, state["bet"] + 0.6 * pot)
                # stab when checked to in position-ish
                if state["players_behind"] == 0 and nopp == 1 and fold_r > 0.5 and \
                        ((hash((state["hand_number"], street)) & 3) == 0):
                    return self._raise(state, state["bet"] + 0.5 * pot)
            return ("check",)

        # facing a bet
        bettor = None
        for x in reversed(hist):
            if x[0] == street and x[2] == "raise":
                bettor = x[1]
                break
        bs = self.S(bettor) if bettor is not None else None
        agg = bs.aggression() if bs else 0.3
        odds = to_call / float(pot + to_call)
        # maniacs bet everything: their bets mean less
        adj = 0.0
        if agg > 0.4:
            adj = 0.15
        elif agg > 0.25:
            adj = 0.05
        elif agg < 0.12:
            adj = -0.1
        adj += (sd_weak - 0.3) * 0.2
        bet_size = to_call / float(max(1, pot - to_call))
        eff_s = s + adj - 0.08 * max(0.0, bet_size - 0.7)
        if street == 3:
            draw = 0.0

        # raise for value
        if can_raise and eff_s >= 0.82:
            if spr < 2.5 or s >= 0.9:
                return self._raise(state, state["max_raise_to"])
            return self._raise(state, state["current_bet"] * 2.5 + 0.5 * pot)
        commit = to_call >= 0.5 * stack
        need = odds + 0.15
        if commit:
            need = max(need, 0.55)
        if eff_s >= need or (eff_s + draw >= odds + 0.05 and not commit):
            return ("call",)
        if commit and draw >= 0.15 and eff_s + draw >= 0.5 and street == 1:
            return ("call",)
        # semi-bluff raise vs folders
        if (can_raise and draw >= 0.15 and street == 1 and bs and bs.fold_rate() > 0.5
                and nopp == 1 and to_call < 0.25 * stack):
            return self._raise(state, state["current_bet"] * 3)
        return ("fold",)
