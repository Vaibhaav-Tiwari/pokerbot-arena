"""8-player no-limit hold'em sit-and-go engine mirroring the contest rules (as far as they're published).

- 2,000 starting chips, blinds rise every 25 hands, 200-hand cap
- top three score 10/5/2; if the cap is hit, remaining players get ICM equity
- a bot that errors or thinks longer than `time_limit` checks (or folds facing a bet)

Bot interface:  bot.get_action(state) -> ('fold',) | ('check',) | ('call',) | ('raise', total_bet_to)
                bot.on_hand_end(info)   (optional, untimed)
The real contest API will differ; keep your strategy behind a thin adapter.
"""
import json
import os
import random
import time

from cards import card_str, cards_str, evaluate, CATEGORY_NAMES, category

# pokerbot_sdk.DEFAULT_BLINDS (read from the SDK). On contest day, set the real structure without
# editing code, e.g.:  BLINDS='[[25,50],[50,100],[100,200]]' ANTE_FRAC=0.1 HANDS_PER_LEVEL=25 python3 leaderboard.py
BLINDS = [(10, 20), (20, 40), (30, 60), (50, 100), (75, 150), (100, 200), (150, 300), (200, 400)]  # real DEFAULT_BLINDS
if os.environ.get("BLINDS"):
    BLINDS = [tuple(x) for x in json.loads(os.environ["BLINDS"])]
ANTE_FRAC = float(os.environ.get("ANTE_FRAC", "0"))  # ante per player as a fraction of the big blind
HANDS_PER_LEVEL = int(os.environ.get("HANDS_PER_LEVEL", "25"))
START_STACK = int(os.environ.get("START_STACK", "2000"))
PRIZES = [10, 5, 2]


def icm(stacks, prizes):
    """Malmuth-Harville expected prize per player."""
    out = [0.0] * len(stacks)

    def rec(left, total, place, prob):
        if place >= len(prizes) or total <= 0:
            return
        for i in left:
            p = prob * stacks[i] / total
            out[i] += p * prizes[place]
            rec([j for j in left if j != i], total - stacks[i], place + 1, p)

    rec(list(range(len(stacks))), sum(stacks), 0, 1.0)
    return out


class Tournament:
    def __init__(self, bots, names=None, seed=0, start_stack=None, max_hands=200,
                 hands_per_level=None, time_limit=0.001, verbose=False, record=False):
        start_stack = start_stack or START_STACK
        hands_per_level = hands_per_level or HANDS_PER_LEVEL
        self.bots = bots
        self.n = len(bots)
        self.names = names or [f"seat{i}" for i in range(self.n)]
        self.rng = random.Random(seed)
        self.stacks = [start_stack] * self.n
        self.total = start_stack * self.n
        self.max_hands = max_hands
        self.hpl = hands_per_level
        # one number for everyone, or a per-seat list (e.g. strict for your bot, lenient for opponents)
        self.time_limit = time_limit if isinstance(time_limit, list) else [time_limit] * self.n
        self.verbose = verbose
        self.timeouts = [0] * self.n
        self.errors = [0] * self.n
        self.think = [[] for _ in range(self.n)]
        self.record = record
        self.events = []  # filled when record=True: a replayable log of every hand (see emit)

    def emit(self, **ev):
        if self.record:
            self.events.append(ev)

    def log(self, msg):
        if self.verbose:
            print(msg)

    def next_alive(self, s):
        for k in range(1, self.n + 1):
            t = (s + k) % self.n
            if self.stacks[t] > 0:
                return t

    def run(self):
        """Play the whole tournament with bots only and return the results."""
        g = self.steps()
        try:
            next(g)
        except StopIteration as done:
            return done.value
        raise RuntimeError("a human seat needs steps(); use run() only with bots")

    def steps(self):
        """Generator version of run(): yields ("decide", state) whenever a seat whose bot has
        `is_human = True` must act; send it the action tuple. Returns the results dict."""
        n = self.n
        bust_order = []  # worst finisher first
        button = self.rng.randrange(n)
        hand_no = 0
        while hand_no < self.max_hands:
            alive = [i for i in range(n) if self.stacks[i] > 0]
            if len(alive) < 2:
                break
            sb, bb = BLINDS[min(hand_no // self.hpl, len(BLINDS) - 1)]
            if hand_no > 0:
                button = self.next_alive(button)
            before = self.stacks[:]
            yield from self.play_hand(hand_no, button, sb, bb, alive)
            assert sum(self.stacks) == self.total, "chip leak"
            busted = sorted((i for i in alive if self.stacks[i] == 0), key=lambda i: before[i])
            for i in busted:
                self.log(f"  ** {self.names[i]} busts in place {n - len(bust_order)}")
                self.emit(t="bust", seat=i, place=n - len(bust_order))
                bust_order.append(i)
            hand_no += 1

        points = [0.0] * n
        place = [0] * n
        for idx, i in enumerate(bust_order):
            place[i] = n - idx
            if place[i] <= len(PRIZES):
                points[i] = PRIZES[place[i] - 1]
        remaining = [i for i in range(n) if self.stacks[i] > 0]
        prizes = PRIZES[:len(remaining)]
        for i, eq in zip(remaining, icm([self.stacks[i] for i in remaining], prizes)):
            points[i] = eq
        for rank, i in enumerate(sorted(remaining, key=lambda i: -self.stacks[i])):
            place[i] = rank + 1
        self.emit(t="end", places=place, points=[round(x, 2) for x in points], stacks=self.stacks[:], hands=hand_no)
        return {"points": points, "place": place, "stacks": self.stacks[:], "hands": hand_no,
                "timeouts": self.timeouts, "errors": self.errors, "think": self.think}

    def play_hand(self, hand_no, button, sb, bb, alive):
        st = self.stacks
        deck = list(range(52))
        self.rng.shuffle(deck)
        i = alive.index(button)
        order = alive[i + 1:] + alive[:i + 1]  # left of button first, button last
        heads_up = len(order) == 2
        sb_seat, bb_seat = (button, order[0]) if heads_up else (order[0], order[1])
        hole = {s: (deck[2 * k], deck[2 * k + 1]) for k, s in enumerate(order)}
        board_all = deck[2 * len(order):2 * len(order) + 5]
        contrib = {s: 0 for s in order}
        bet = {s: 0 for s in order}
        folded, allin, history = set(), set(), []

        def post(s, amt):
            a = min(amt, st[s])
            st[s] -= a
            bet[s] += a
            contrib[s] += a
            if st[s] == 0:
                allin.add(s)

        ante = int(round(bb * ANTE_FRAC))
        if ante:
            for s in order:  # antes are dead money: in the pot, but not part of anyone's bet
                a = min(ante, st[s])
                st[s] -= a
                contrib[s] += a
                if st[s] == 0:
                    allin.add(s)
        self.emit(t="hand", n=hand_no, sb=sb, bb=bb, button=button,
                  stacks={s: st[s] for s in order}, hole={s: [card_str(c) for c in hole[s]] for s in order})
        post(sb_seat, sb)
        post(bb_seat, bb)
        history.append((0, sb_seat, "sb", bet[sb_seat], sb_seat in allin))
        history.append((0, bb_seat, "bb", bet[bb_seat], bb_seat in allin))
        for s_, k_ in ((sb_seat, "sb"), (bb_seat, "bb")):
            self.emit(t="act", seat=s_, kind=k_, to=bet[s_], allin=s_ in allin, pot=sum(contrib.values()), stack=st[s_])
        self.log(f"\n=== Hand {hand_no + 1}  blinds {sb}/{bb}  button {self.names[button]} ===")
        for s in order:
            self.log(f"  {self.names[s]:10s} {st[s] + bet[s]:6d}  [{cards_str(hole[s])}]")

        for street in range(4):
            if street > 0:
                for s in order:
                    bet[s] = 0
            if sum(1 for s in order if s not in folded) == 1:
                break
            board = board_all[:(0, 3, 4, 5)[street]]
            if street > 0:
                self.log(f"  -- {('preflop', 'flop', 'turn', 'river')[street]}: {cards_str(board)}")
                self.emit(t="board", street=street, cards=[card_str(c) for c in board])
            if street == 0:
                first = button if heads_up else order[2 % len(order)]
            else:
                first = order[0]
            k = order.index(first)
            sorder = order[k:] + order[:k]
            current = max(bet.values())
            min_inc = bb
            acted = set()
            active = [s for s in sorder if s not in folded and s not in allin]
            if not active or (len(active) == 1 and bet[active[0]] >= current):
                continue
            idx = 0
            while True:
                if sum(1 for s in sorder if s not in folded) == 1:
                    break
                need = {s for s in sorder if s not in folded and s not in allin
                        and (s not in acted or bet[s] < current)}
                if not need:
                    break
                s = sorder[idx % len(sorder)]
                idx += 1
                if s not in need:
                    continue
                pos = sorder.index(s)
                to_call = current - bet[s]
                max_to = bet[s] + st[s]
                state = {
                    "seat": s, "hole": hole[s], "board": list(board), "street": street,
                    "pot": sum(contrib.values()), "to_call": to_call, "stack": st[s], "bet": bet[s],
                    "current_bet": current, "min_raise_to": min(current + min_inc, max_to),
                    "max_raise_to": max_to, "can_raise": st[s] > to_call,
                    "small_blind": sb, "big_blind": bb, "button": button,
                    "stacks": {t: st[t] + bet[t] for t in order},  # chips behind incl. this street's bet
                    "bets": dict(bet), "folded": sorted(folded), "allin": sorted(allin),
                    "players_left": len(alive), "active_in_hand": len(order) - len(folded),
                    "players_behind": sum(1 for t in sorder[pos + 1:] if t not in folded and t not in allin),
                    "hand_number": hand_no, "history": list(history),
                }
                act = None
                if getattr(self.bots[s], "is_human", False):
                    act = yield ("decide", state)  # a person decides: no clock
                else:
                    t0 = time.perf_counter()
                    try:
                        act = self.bots[s].get_action(state)
                    except Exception:
                        self.errors[s] += 1
                    dt = time.perf_counter() - t0
                    self.think[s].append(dt)
                    if dt > self.time_limit[s]:
                        self.timeouts[s] += 1
                        act = None

                kind = act[0] if isinstance(act, (tuple, list)) and act else None
                if kind not in ("fold", "check", "call", "raise"):
                    kind = "check" if to_call == 0 else "fold"
                if kind == "raise" and not state["can_raise"]:
                    kind = "call"
                if kind == "check" and to_call > 0:
                    kind = "fold"
                if kind in ("call", "fold") and to_call == 0:
                    kind = "check"

                if kind == "raise":
                    try:
                        target = int(act[1])
                    except Exception:
                        target = state["min_raise_to"]
                    target = max(state["min_raise_to"], min(target, max_to))
                    post(s, target - bet[s])
                    if target - current >= min_inc:
                        min_inc = target - current
                        acted = set()
                    current = max(current, target)
                elif kind == "call":
                    post(s, to_call)
                elif kind == "fold":
                    folded.add(s)
                acted.add(s)
                history.append((street, s, kind, bet[s], s in allin))
                self.emit(t="act", seat=s, kind=kind, to=bet[s], allin=s in allin, pot=sum(contrib.values()), stack=st[s])
                self.log(f"  {self.names[s]:10s} {kind}{' to ' + str(bet[s]) if kind == 'raise' else ''}"
                         f"{' (all-in)' if s in allin and kind != 'fold' else ''}")

        live = [s for s in order if s not in folded]
        shown = {}
        pots_won = []
        if len(live) == 1:
            st[live[0]] += sum(contrib.values())
            pots_won.append({"amount": sum(contrib.values()), "winners": [live[0]]})
            self.log(f"  {self.names[live[0]]} wins {sum(contrib.values())}")
        else:
            vals = {s: evaluate(list(hole[s]) + board_all) for s in live}
            shown = {s: hole[s] for s in live}
            self.emit(t="show", hands={s: {"cards": [card_str(c) for c in hole[s]],
                                           "name": CATEGORY_NAMES[category(vals[s])]} for s in live})
            self.log(f"  board: {cards_str(board_all)}")
            for s in live:
                self.log(f"  {self.names[s]:10s} shows [{cards_str(hole[s])}] "
                         f"{CATEGORY_NAMES[category(vals[s])]}")
            prev = 0
            for lvl in sorted(set(c for c in contrib.values() if c > 0)):
                pot = sum(min(c, lvl) - min(c, prev) for c in contrib.values())
                elig = [s for s in live if contrib[s] >= lvl] or live
                best = max(vals[s] for s in elig)
                winners = [s for s in order if s in elig and vals[s] == best]
                share, rem = divmod(pot, len(winners))
                for w in winners:
                    st[w] += share
                st[winners[0]] += rem
                if pots_won and pots_won[-1]["winners"] == winners:
                    pots_won[-1]["amount"] += pot
                else:
                    pots_won.append({"amount": pot, "winners": winners})
                self.log(f"  pot {pot} -> {', '.join(self.names[w] for w in winners)}")
                prev = lvl

        self.emit(t="win", pots=pots_won, stacks={s: st[s] for s in order})
        info = {"hand_number": hand_no, "history": history, "shown": shown, "board": board_all,
                "stacks": {s: st[s] for s in order}}
        for s in order:
            cb = getattr(self.bots[s], "on_hand_end", None)
            if cb:
                try:
                    cb(info)
                except Exception:
                    self.errors[s] += 1
