"""Tournament specialist: push/fold by stack depth & position, ICM-aware calls, TAG deep play."""
import gc
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cards import evaluate  # noqa: E402

PAIR, TWO_PAIR, TRIPS, STRAIGHT = 1, 2, 3, 4


# ---------------- preflop hand ranking (Chen-style score, hardcoded) ----------------
def _chen(hi, lo, suited):
    hv = {12: 10.0, 11: 8.0, 10: 7.0, 9: 6.0}.get(hi, (hi + 2) / 2.0)
    if hi == lo:
        return max(5.0, hv * 2) + (0.5 if hi >= 10 else 0)
    s = hv + (2.0 if suited else 0.0)
    gap = hi - lo - 1
    s -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and hi < 10:
        s += 1
    if hi == 12:  # ace-x plays better than raw Chen suggests in short-handed SNG
        s += 0.5 + lo * 0.08
    return s


def _build():
    hands = []
    for hi in range(13):
        for lo in range(hi + 1):
            if hi == lo:
                hands.append((_chen(hi, lo, False), hi + lo, 6, (hi, lo, False)))
            else:
                hands.append((_chen(hi, lo, True), hi + lo, 4, (hi, lo, True)))
                hands.append((_chen(hi, lo, False), hi + lo, 12, (hi, lo, False)))
    hands.sort(key=lambda x: (-x[0], -x[1]))
    pct, cum = {}, 0
    for sc, _, combos, key in hands:
        pct[key] = (cum + combos / 2.0) / 1326.0  # percentile: 0 = best
        cum += combos
    return pct


PCT = _build()


def eq_rand(p):
    """Approx all-in equity vs a random hand from percentile p."""
    return 0.85 - 0.55 * (p ** 0.7)


def eq_vs_range(p, r):
    """Approx equity of percentile-p hand vs an opponent range of top r."""
    return max(0.12, min(0.88, 0.5 + 1.05 * (eq_rand(p) - eq_rand(r * 0.5))))


# Nash-ish shove widths at 10bb by number of players left to act behind.
SHOVE10 = {0: 1.0, 1: 0.62, 2: 0.42, 3: 0.31, 4: 0.24, 5: 0.19, 6: 0.15, 7: 0.13}
OPEN = {0: 0.6, 1: 0.42, 2: 0.32, 3: 0.24, 4: 0.18, 5: 0.15, 6: 0.12, 7: 0.10}


class Bot:
    def __init__(self, *a, **k):
        self.rng = random.Random(1234)
        self.pf_aggr = False
        self.last_hand = -1

    def on_hand_end(self, info):
        # untimed: collect garbage here so GC pauses don't land inside timed decisions
        try:
            if info.get('hand_number', 0) % 5 == 0:
                gc.collect(1)
        except Exception:
            pass

    # ---------------- helpers ----------------
    def _raise(self, st, amt):
        if not st['can_raise']:
            return ('call',) if st['to_call'] > 0 else ('check',)
        amt = int(max(st['min_raise_to'], min(st['max_raise_to'], amt)))
        # if leaving less than ~25% of stack behind, just go all in
        if st['max_raise_to'] - amt < 0.25 * amt:
            amt = st['max_raise_to']
        return ('raise', amt)

    def _passive(self, st):
        return ('check',) if st['to_call'] == 0 else ('fold',)

    def _icm_premium(self, st, my_total):
        pl = st['players_left']
        n = len(st['stacks']) or 1
        avg = sum(st['stacks'].values()) / float(n)
        prem = 0.0
        if pl == 4:
            prem = 0.07
        elif pl == 5:
            prem = 0.04
        elif pl == 3:
            prem = 0.03
        if st['hand_number'] >= 175 and pl > 3:
            prem += 0.04
        if prem and my_total < 0.5 * avg:
            prem *= 0.4  # short stacks can't afford to wait
        elif prem and my_total > 1.6 * avg:
            prem *= 0.6  # big stack covers
        return prem

    def get_action(self, st):
        try:
            if st['hand_number'] != self.last_hand:
                self.last_hand = st['hand_number']
                self.pf_aggr = False
            if st['street'] == 0:
                a = self._preflop(st)
                if a[0] == 'raise':
                    self.pf_aggr = True
                return a
            return self._postflop(st)
        except Exception:
            try:
                return self._passive(st)
            except Exception:
                return ('fold',)

    # ---------------- preflop ----------------
    def _preflop(self, st):
        c1, c2 = st['hole']
        r1, r2 = c1 >> 2, c2 >> 2
        hi, lo = max(r1, r2), min(r1, r2)
        suited = (c1 & 3) == (c2 & 3) and hi != lo
        p = PCT[(hi, lo, suited)]
        bb = st['big_blind']
        me = st['seat']
        my_total = st['stack'] + st['bet']
        opp_stacks = [v for s, v in st['stacks'].items() if s != me and s not in st['folded']]
        eff = min(my_total, max(opp_stacks) if opp_stacks else my_total)
        eff_bb = eff / float(bb)
        behind = st['players_behind']
        to_call = st['to_call']
        prem = self._icm_premium(st, my_total)

        raises = [h for h in st['history'] if h[0] == 0 and h[2] == 'raise']
        n_raises = len(raises)
        limpers = sum(1 for h in st['history'] if h[0] == 0 and h[2] == 'call')

        if n_raises == 0:
            # unopened (maybe limped) pot
            if eff_bb <= 13:
                width = min(1.0, SHOVE10.get(behind, 0.12) * (10.0 / max(eff_bb, 1.5)) ** 0.75)
                width *= (1.0 - 2.5 * prem) * (0.85 if limpers else 1.0)
                if limpers:
                    width *= 0.7
                if p <= width:
                    return self._raise(st, st['max_raise_to'])
                if to_call == 0:
                    return ('check',)
                return ('fold',)
            width = OPEN.get(behind, 0.10) * (1.0 - 2.0 * prem)
            if limpers:
                width *= 0.75
            if p <= width:
                size = bb * (2.5 if eff_bb > 25 else 2.2) + bb * limpers
                if behind == 0 and to_call < bb:  # SB vs BB
                    size = bb * 3
                return self._raise(st, size)
            if to_call == 0:
                return ('check',)
            # cheap SB completion / limp behind with speculative hands
            if to_call <= bb * 0.5 and p <= 0.5 and limpers == 0 and eff_bb > 20:
                return ('call',)
            return ('fold',)

        # facing raise(s)
        last = raises[-1]
        raiser = last[1]
        raiser_allin = last[4]
        raise_to = last[3]
        raise_bb = raise_to / float(bb)
        # estimate raiser range: bigger / more raises => tighter
        if n_raises == 1:
            r = 0.30 if raise_bb <= 4 else 0.35 if raiser_allin else 0.25
        elif n_raises == 2:
            r = 0.12
        else:
            r = 0.06
        if raiser_allin and raise_bb > 30:
            r *= 0.8
        pot = st['pot']
        call_amt = min(to_call, st['stack'])
        # treat as all-in decision if call commits most of our stack or eff is short
        commit = call_amt >= 0.35 * my_total or eff_bb <= 15 or raiser_allin
        if commit:
            eq = eq_vs_range(p, r)
            # multiple players already in reduce our equity
            others_in = st['active_in_hand'] - 2 - behind
            if others_in > 0:
                eq *= 0.85 ** others_in
            need = call_amt / float(pot + call_amt) + prem
            if behind > 0:
                need += 0.02 * min(behind, 3)
            if p <= 0.025 or (eq > need + 0.05 and p <= 0.06):
                return self._raise(st, st['max_raise_to'])
            if eq >= need:
                # shove over rather than flat if we have fold equity & short
                if not raiser_allin and eff_bb <= 25 and st['can_raise']:
                    return self._raise(st, st['max_raise_to'])
                return ('call',)
            return self._passive(st)

        # deep-stacked: 3-bet value, flat good hands in position-ish, fold rest
        if n_raises == 1:
            if p <= 0.035 or (p <= 0.05 and raise_bb <= 3.5):
                return self._raise(st, raise_to * 3 + limpers * bb)
            if p <= 0.13 and call_amt <= 0.12 * my_total:
                return ('call',)
            # set-mining small pairs when cheap and deep
            if hi == lo and call_amt <= 0.07 * my_total:
                return ('call',)
            return self._passive(st)
        # facing 3-bet+
        if p <= 0.02:
            return self._raise(st, st['max_raise_to'])
        if p <= 0.045 and call_amt <= 0.3 * my_total:
            return ('call',)
        return self._passive(st)

    # ---------------- postflop ----------------
    def _strength(self, hole, board):
        cards = list(hole) + board
        v = evaluate(cards)
        cat = v >> 20
        bv = evaluate(board)
        bcat = bv >> 20
        hr = sorted([hole[0] >> 2, hole[1] >> 2], reverse=True)
        branks = sorted([c >> 2 for c in board], reverse=True)
        top = branks[0]
        s = 0.05
        if cat >= STRAIGHT and cat > bcat:
            s = 0.93
            if cat == STRAIGHT:
                # flush possible on board?
                sc = [0, 0, 0, 0]
                for c in board:
                    sc[c & 3] += 1
                if max(sc) >= 3:
                    s = 0.8
            elif cat == 5:  # flush: nut-ish check
                s = 0.9
        elif cat == TRIPS and bcat < TRIPS:
            s = 0.9 if hr[0] == hr[1] else 0.82
        elif cat == TWO_PAIR and bcat == 0:
            s = 0.84
        elif cat == TWO_PAIR and bcat == PAIR:
            # our pair + board pair
            if hr[0] == hr[1]:
                s = 0.72 if hr[0] > top else 0.5
            else:
                pr = (v >> 12) & 15  # second-ranked pair isn't trivial; approximate
                mine = [r for r in hr if r in branks]
                s = 0.66 if mine and mine[0] == top else 0.5
        elif cat == PAIR and bcat == 0:
            if hr[0] == hr[1]:
                if hr[0] > top:
                    s = 0.76 + 0.01 * hr[0]
                else:
                    above = sum(1 for r in set(branks) if r > hr[0])
                    s = 0.42 if above == 1 else 0.3
            else:
                pr = hr[0] if hr[0] in branks else hr[1]
                kick = hr[1] if pr == hr[0] else hr[0]
                uniq = sorted(set(branks), reverse=True)
                if pr == uniq[0]:
                    s = 0.58 + 0.012 * kick
                elif len(uniq) > 1 and pr == uniq[1]:
                    s = 0.44
                else:
                    s = 0.33
        elif cat >= PAIR:
            # board-made hand only (paired board etc.)
            s = 0.15 + (0.08 if hr[0] >= 11 else 0)
        else:
            s = 0.05 + (0.08 if hr[0] == 12 else 0.04 if hr[0] >= 10 else 0)
        # draws
        draw = 0
        if len(board) < 5:
            sc = [0, 0, 0, 0]
            for c in cards:
                sc[c & 3] += 1
            for su in range(4):
                if sc[su] >= 4 and ((hole[0] & 3) == su or (hole[1] & 3) == su) and cat < 5:
                    draw = max(draw, 9)
            if cat < STRAIGHT:
                mask = 0
                for c in cards:
                    mask |= 1 << (c >> 2)
                m = (mask << 1) | ((mask >> 12) & 1)
                for lo_ in range(0, 10):
                    w = (m >> lo_) & 0x1F
                    if bin(w).count('1') == 4:
                        if w in (0b01111, 0b11110) and 0 < lo_ < 9:
                            draw = max(draw, 8)
                        else:
                            draw = max(draw, 4)
        return s, draw

    def _postflop(self, st):
        hole = st['hole']
        board = st['board']
        s, draw = self._strength(hole, board)
        pot = st['pot']
        to_call = st['to_call']
        my_total = st['stack'] + st['bet']
        nopp = st['active_in_hand'] - 1
        street = st['street']
        cards_left = 5 - len(board)
        draw_eq = 0.0
        if draw and cards_left:
            draw_eq = min(0.6, draw * (0.02 if cards_left == 1 else 0.038))
        # multiway discount
        s_eff = s - 0.04 * max(0, nopp - 1)
        prem = self._icm_premium(st, my_total)

        if to_call == 0:
            if s_eff >= 0.7:
                return self._raise(st, st['bet'] + int(pot * 0.7))
            if s_eff >= 0.55:
                return self._raise(st, st['bet'] + int(pot * 0.55))
            if draw >= 8 and street < 3 and nopp <= 2 and self.rng.random() < 0.6:
                return self._raise(st, st['bet'] + int(pot * 0.55))
            if self.pf_aggr and street == 1 and nopp == 1 and self.rng.random() < 0.5:
                return self._raise(st, st['bet'] + int(pot * 0.4))
            return ('check',)

        # facing a bet
        call_amt = min(to_call, st['stack'])
        odds = call_amt / float(pot + call_amt)
        bet_frac = to_call / float(max(1, pot - to_call))  # bet relative to pot before it
        # map strength to equity vs a betting range; big bets are stronger
        eq = s_eff - 0.08 * max(0.0, bet_frac - 0.7)
        if street == 3:
            eq -= 0.04
        eq = max(eq, draw_eq if street < 3 else 0.0)
        need = odds + prem * (call_amt / float(max(1, my_total)))

        if s_eff >= 0.8:
            return self._raise(st, st['current_bet'] * 2 + pot // 2)
        if s_eff >= 0.68 and call_amt >= 0.4 * st['stack']:
            return self._raise(st, st['max_raise_to'])
        if eq >= need:
            return ('call',)
        return ('fold',)
