"""Learned-policy pokerbot.

Linear policy (3 heads: raise / continue / sizing) over hand-crafted features,
trained with an antithetic evolution strategy by self-play of whole sit-and-go
tournaments against simple opponents (see field/learned_train.py).
Weights are frozen below; no file reads at runtime.
"""
import math
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from cards import evaluate  # noqa: E402

# Preflop equity vs one random hand, index = hi*13+lo (suited if hi>lo stored at [hi][lo],
# offsuit at [lo][hi]; pairs on diagonal). Filled by learned_train.py.
PRE_EQ = [0.5003, 0.3338, 0.3393, 0.3412, 0.3518, 0.3395, 0.359, 0.3957, 0.4257, 0.4535, 0.4535, 0.4975, 0.544, 0.3612, 0.541, 0.3688, 0.3547, 0.3607, 0.38, 0.3762, 0.4, 0.4332, 0.453, 0.4793, 0.5165, 0.5468, 0.3657, 0.3895, 0.5767, 0.3743, 0.3753, 0.4045, 0.4, 0.4032, 0.43, 0.4682, 0.474, 0.5195, 0.5668, 0.3907, 0.3903, 0.4127, 0.6052, 0.3958, 0.4123, 0.4038, 0.4392, 0.44, 0.4743, 0.5018, 0.5445, 0.5847, 0.3762, 0.399, 0.4217, 0.4207, 0.6343, 0.4375, 0.437, 0.448, 0.4592, 0.4578, 0.5023, 0.5343, 0.5752, 0.387, 0.4047, 0.4023, 0.4382, 0.4588, 0.6463, 0.4625, 0.4675, 0.4918, 0.5007, 0.5217, 0.5663, 0.5765, 0.4197, 0.4005, 0.426, 0.43, 0.4607, 0.5012, 0.6942, 0.472, 0.4997, 0.5165, 0.5312, 0.5757, 0.6038, 0.436, 0.4317, 0.4495, 0.4525, 0.4657, 0.4945, 0.5018, 0.7118, 0.51, 0.5172, 0.5425, 0.5715, 0.5905, 0.451, 0.4617, 0.4567, 0.4792, 0.4767, 0.5152, 0.5048, 0.5407, 0.7603, 0.5487, 0.5778, 0.5888, 0.6153, 0.4937, 0.4737, 0.4905, 0.5053, 0.511, 0.5377, 0.536, 0.5692, 0.5838, 0.7758, 0.5732, 0.6047, 0.636, 0.5103, 0.503, 0.5182, 0.5267, 0.5328, 0.5412, 0.5772, 0.566, 0.6022, 0.6112, 0.7995, 0.6118, 0.652, 0.5235, 0.5303, 0.5677, 0.5532, 0.5668, 0.5603, 0.5897, 0.6088, 0.6085, 0.6302, 0.619, 0.8298, 0.6517, 0.5772, 0.5877, 0.5807, 0.6002, 0.5792, 0.6112, 0.6123, 0.6302, 0.6482, 0.6408, 0.6758, 0.6852, 0.8477]

N_FEAT = 12
# weights: [raise head (N_FEAT), continue head (N_FEAT), size head (N_FEAT)]
W = [-0.1527, 0.2007, 2.0116, -0.1602, -0.9839, 0.1134, 0.0916, -0.9937, 0.4898, -0.0346, -0.4815, -0.5739, -0.3751, -0.2339, 0.8626, -1.3026, -0.1675, 0.229, 0.1638, 0.0257, 0.0737, 0.0921, 0.6494, -0.4659, -1.7095, -0.4966, 0.5903, 0.2151, -0.9555, -0.5237, 0.0285, 0.1219, -0.155, -0.5531, -0.0895, -0.4545]

_SAMPLES = 12


def _pre_index(c1, c2):
    r1, r2 = c1 >> 2, c2 >> 2
    hi, lo = (r1, r2) if r1 >= r2 else (r2, r1)
    if (c1 & 3) == (c2 & 3) and hi != lo:
        return hi * 13 + lo
    return lo * 13 + hi


def _draws(hole, board):
    cards = list(hole) + list(board)
    suits = [0, 0, 0, 0]
    for c in cards:
        suits[c & 3] += 1
    fd = 0.0
    for s in range(4):
        if suits[s] == 4 and ((hole[0] & 3) == s or (hole[1] & 3) == s):
            fd = 1.0
    sd = 0.0
    m = 0
    for c in cards:
        m |= 1 << ((c >> 2) + 1)
    if m & (1 << 13):  # ace also plays low
        m |= 1
    for lo in range(0, 10):
        w = (m >> lo) & 0x1F
        if bin(w).count("1") == 4:
            sd = 1.0
            break
    return fd, sd


def _hand_strength(hole, board, rng):
    """Fraction of sampled opponent hands we beat on the current board."""
    known = set(hole) | set(board)
    deck = [c for c in range(52) if c not in known]
    mine = evaluate(list(hole) + list(board))
    b = list(board)
    win = 0.0
    for _ in range(_SAMPLES):
        o1, o2 = rng.sample(deck, 2)
        v = evaluate(b + [o1, o2])
        if mine > v:
            win += 1.0
        elif mine == v:
            win += 0.5
    return win / _SAMPLES


def features(s, rng):
    hole = s["hole"]
    board = s["board"]
    street = s["street"]
    bb = max(1, s["big_blind"])
    aih = s["active_in_hand"]
    nopp = max(1, (aih if isinstance(aih, int) else len(aih)) - 1)
    if street == 0:
        hs = PRE_EQ[_pre_index(hole[0], hole[1])]
        fd = sd = 0.0
    else:
        hs = _hand_strength(hole, board, rng)
        fd, sd = _draws(hole, board) if street < 3 else (0.0, 0.0)
    hsn = hs ** nopp  # crude multiway adjustment
    to_call = s["to_call"]
    pot = s["pot"]
    odds = to_call / float(pot + to_call) if to_call > 0 else 0.0
    eff = s["stack"] + s["bet"]
    stack_bb = eff / float(bb)
    pb = s["players_behind"]
    nbehind = pb if isinstance(pb, int) else len(pb)
    raised = 0.0
    for h in s["history"]:
        if h[0] == street and h[2] == "raise":
            raised += 1.0
    return [
        1.0,
        hs,
        hsn,
        odds,
        math.log(1.0 + stack_bb) / 4.0,
        nopp / 7.0,
        nbehind / 7.0,
        min(raised, 3.0) / 3.0,
        1.0 if street == 0 else 0.0,
        street / 3.0,
        fd + sd,
        to_call / float(eff) if eff > 0 else 1.0,
    ]


class Bot:
    def __init__(self, weights=None, seed=None):
        self.w = list(weights) if weights is not None else list(W)
        self.rng = random.Random(seed)

    def act(self, s):
        to_call = s["to_call"]
        f = features(s, self.rng)
        w = self.w
        n = N_FEAT
        a = 0.0
        c = 0.0
        z = 0.0
        for i in range(n):
            fi = f[i]
            a += w[i] * fi
            c += w[n + i] * fi
            z += w[2 * n + i] * fi
        if s["can_raise"] and a > 0:
            frac = 0.25 + 2.75 / (1.0 + math.exp(-z))  # raise between 0.25 and 3 pots
            pot_after_call = s["pot"] + to_call
            target = s["current_bet"] + frac * pot_after_call
            if z > 3.0:
                target = s["max_raise_to"]
            amt = int(max(s["min_raise_to"], min(s["max_raise_to"], target)))
            # if the raise commits most of our stack, just jam
            if amt > 0.6 * (s["stack"] + s["bet"]):
                amt = s["max_raise_to"]
            return ("raise", amt)
        if to_call == 0:
            return ("check",)
        if c > 0:
            return ("call",)
        return ("fold",)

    def get_action(self, state):
        try:
            return self.act(state)
        except Exception:
            return ("check",) if state.get("to_call", 0) == 0 else ("fold",)

    def on_hand_end(self, info):
        pass
