# ======================= Deep-ML pokerbot_sdk glue =======================
from pokerbot_sdk import Bot, Action

_STREETS = {"preflop": 0, "pre-flop": 0, "pre": 0, "flop": 1, "turn": 2, "river": 3}
_KINDS = {"fold": "fold", "check": "check", "call": "call", "raise": "raise", "bet": "raise",
          "raise_to": "raise", "all_in": "raise", "allin": "raise", "all-in": "raise", "shove": "raise",
          "small_blind": "sb", "sb": "sb", "big_blind": "bb", "bb": "bb", "post": "bb", "blind": "bb"}


def _get(obj, names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        v = getattr(obj, n, None)
        if v is not None:
            return v() if callable(v) and n in ("seat", "me") else v
    return default


def _as_map(x):
    if x is None:
        return None
    if isinstance(x, dict):
        return {int(k): v for k, v in x.items()}
    return {i: v for i, v in enumerate(x)}


def _street(x, board_len):
    if isinstance(x, int):
        return x
    if isinstance(x, str) and x.lower() in _STREETS:
        return _STREETS[x.lower()]
    return {0: 0, 3: 1, 4: 2, 5: 3}.get(board_len, 0)


def _history(raw_hist, stacks_behind):
    out = []
    for h in raw_hist or []:
        flag = False
        if isinstance(h, dict):
            st, seat, act, amt = (h.get("street"), h.get("seat", h.get("player")), h.get("action", h.get("kind")),
                                  h.get("amount", h.get("to", 0)))
            flag = bool(h.get("all_in", False))
        elif isinstance(h, (tuple, list)):
            st, seat, act, amt = (list(h) + [0, 0, 0, 0])[:4]
        else:
            st, seat, act, amt = (_get(h, ["street"]), _get(h, ["seat", "player"]), _get(h, ["action", "kind"]),
                                  _get(h, ["amount", "to"], 0))
        act = str(act).lower()
        kind = _KINDS.get(act, act)
        try:
            seat = int(seat)
        except Exception:
            continue
        allin = flag or act in ("all_in", "allin", "all-in", "shove") or (kind in ("raise", "call") and stacks_behind.get(seat, 1) == 0)
        out.append((_street(st, -1) if not isinstance(st, int) else st, seat, kind, amt or 0, allin))
    return out


def _seat_order(alive, button):
    alive = sorted(alive)
    if button is None or not alive:
        return alive
    after = [s for s in alive if s > button] + [s for s in alive if s <= button]
    return after  # left of the button first, button last


def _infer_seat(behind, bets, hist, street, button):
    """Whose turn is it? Walk the betting order from the last action (or the blinds/button)."""
    ring = sorted(t for t in behind if behind[t] + bets.get(t, 0) > 0 or any(h[1] == t for h in hist))
    if not ring:
        return None
    folded = {h[1] for h in hist if h[2] == "fold"}
    allin = {t for t in ring if behind.get(t, 0) == 0}
    blinds = [h[1] for h in hist if h[0] == 0 and h[2] in ("sb", "bb")]
    acts = [h for h in hist if h[0] == street and h[2] not in ("sb", "bb")]
    if acts:
        last = acts[-1][1]
    elif street == 0 and blinds:
        last = blinds[-1]
    else:
        last = button if isinstance(button, int) else ring[-1]
    after = [t for t in ring if t > last] + [t for t in ring if t <= last]
    for t in after:
        if t not in folded and t not in allin:
            return t
    return None


class MyBot(Bot):
    def __init__(self, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
        except Exception:
            pass
        self.core = CoreBot(load_tuned(), seed=0)
        self.hand_no = -1
        self._hole = None
        self._last_hist = []
        self._seat = None

    def act(self, state):
        try:
            return self._act(state)
        except Exception:
            self.core.errors += 1
            return Action.check() if not _get(state, ["to_call"], 0) else Action.fold()

    def _act(self, state):
        hole = parse_cards(_get(state, ["hole"]))
        board = parse_cards(_get(state, ["board"], []))
        key = tuple(sorted(hole))
        hn = _get(state, ["hand_number", "hand", "hand_id", "hand_no"])
        if key != self._hole:
            if self._hole is not None and self._last_hist:
                self.core.on_hand_end({"hand_number": self.hand_no, "history": self._last_hist, "shown": {}, "board": [], "stacks": {}})
                self._last_hist = []
            self._hole = key
            self.hand_no += 1
        hand_number = hn if isinstance(hn, int) else self.hand_no

        to_call = int(_get(state, ["to_call"], 0))
        pot = int(_get(state, ["pot"], 0))
        bb = int(_get(state, ["big_blind"], 20))
        sb = int(_get(state, ["small_blind"], bb // 2))
        behind = _as_map(_get(state, ["stacks"]))
        bets = _as_map(_get(state, ["bets"]))
        seat = _get(state, ["seat", "me", "my_seat", "player", "player_id", "you", "hero", "position", "index"])
        seat = int(seat) if isinstance(seat, int) else None
        my_stack = _get(state, ["stack", "my_stack", "chips"])
        street = _street(_get(state, ["street"]), len(board))
        button = _get(state, ["button", "dealer", "button_seat", "dealer_seat"])
        if behind is None:
            behind = {seat or 0: int(my_stack or 0)}
        if bets is None:
            bets = {t: 0 for t in behind}
        hist = _history(_get(state, ["history", "actions"], []), behind)
        if seat is None:
            seat = _infer_seat(behind, bets, hist, street, button)
        if seat is None:
            seat = 0
        my_bet = int(bets.get(seat, 0))
        stack = int(my_stack) if isinstance(my_stack, int) else int(behind.get(seat, 0))
        current = _get(state, ["current_bet"])
        current = int(current) if current is not None else my_bet + to_call
        min_r = _get(state, ["min_raise"])
        max_r = _get(state, ["max_raise"])
        self._last_hist = hist

        folded = {h[1] for h in hist if h[2] == "fold"}
        fl = _get(state, ["folded"])
        if isinstance(fl, (list, tuple)):
            folded |= {i for i, f in enumerate(fl) if f is True}
        in_hand = {h[1] for h in hist} | {seat}
        alive = [t for t in behind if behind[t] + bets.get(t, 0) > 0 or t in in_hand]
        folded = sorted(t for t in folded if t in alive and t != seat)
        allin = sorted(t for t in alive if behind.get(t, 0) == 0 and t not in folded and t != seat)
        stacks = {t: int(behind.get(t, 0)) + int(bets.get(t, 0)) for t in alive}
        stacks[seat] = stack + my_bet
        opp = _get(state, ["opponents"])
        active = (int(opp) + 1) if isinstance(opp, int) else len([t for t in alive if t not in folded])
        players_behind = None
        if isinstance(button, int) and alive:
            order = _seat_order(alive, button)
            if street == 0:
                first = 2 % len(order) if len(order) > 2 else (len(order) - 1)
                order = order[first:] + order[:first]
            if seat in order:
                pos = order.index(seat)
                players_behind = sum(1 for t in order[pos + 1:] if t not in folded and t not in allin)

        s = build_state(
            hole=hole, board=board, pot=pot, to_call=to_call, stack=stack, big_blind=bb, small_blind=sb,
            seat=seat, my_bet=my_bet, current_bet=current,
            min_raise_to=int(min_r) if min_r is not None else None,
            max_raise_to=int(max_r) if max_r is not None else None,
            stacks=stacks, bets={t: int(bets.get(t, 0)) for t in stacks}, folded=folded, allin=allin,
            history=hist, players_left=len(alive), active_in_hand=active, players_behind=players_behind,
            hand_number=hand_number, button=button, street=street)
        a = self.core.get_action(s)
        legal = [str(x).lower() for x in (_get(state, ["legal"], []) or [])]
        kind = a[0]
        if kind == "raise":
            to = int(a[1])
            if (legal and "raise" not in legal and "all_in" not in legal) or stack <= to_call:
                kind = "call"
            elif to >= s["max_raise_to"]:
                return Action.all_in()
            else:
                return Action.raise_to(max(s["min_raise_to"], min(to, s["max_raise_to"])))
        if kind == "call":
            if to_call == 0:
                return Action.check()
            return Action.call()
        if kind == "check":
            return Action.check() if to_call == 0 else Action.fold()
        return Action.check() if to_call == 0 else Action.fold()

    def on_hand_end(self, result):
        try:
            hist = _get(result, ["actions", "history"])
            behind = _as_map(_get(result, ["stacks"])) or {}
            h = _history(hist, behind) if hist else self._last_hist
            shown = {}
            self.core.on_hand_end({"hand_number": self.hand_no, "history": h, "shown": shown, "board": [], "stacks": behind})
            self._last_hist = []
            self._hole = None
        except Exception:
            pass
