"""Cards are ints 0..51: rank = c >> 2 (0='2' .. 12='A'), suit = c & 3."""

RANKS = "23456789TJQKA"
SUITS = "cdhs"

HIGH, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)
CATEGORY_NAMES = ["high card", "pair", "two pair", "trips", "straight", "flush",
                  "full house", "quads", "straight flush"]


def card_str(c):
    return RANKS[c >> 2] + SUITS[c & 3]


def cards_str(cs):
    return " ".join(card_str(c) for c in cs)


def parse(s):
    return RANKS.index(s[0].upper()) * 4 + SUITS.index(s[1].lower())


def straight_high(mask):
    """Highest rank of a straight in a 13-bit rank mask, or -1. Handles the A-5 wheel."""
    m = (mask << 1) | ((mask >> 12) & 1)  # bit 0 = ace-low
    for high in range(13, 3, -1):
        if (m >> (high - 4)) & 0x1F == 0x1F:
            return high - 1
    return -1


def _val(cat, ranks):
    v = cat
    for i in range(5):
        v = v * 16 + (ranks[i] + 1 if i < len(ranks) else 0)
    return v


def category(value):
    return value >> 20


def evaluate(cards):
    """Score the best 5-card hand from 1..7 cards. Higher is better."""
    counts = [0] * 13
    suit_cnt = [0] * 4
    suit_mask = [0] * 4
    mask = 0
    for c in cards:
        r = c >> 2
        s = c & 3
        counts[r] += 1
        mask |= 1 << r
        suit_cnt[s] += 1
        suit_mask[s] |= 1 << r

    # With at most 7 cards a flush can't coexist with quads or a full house.
    for s in range(4):
        if suit_cnt[s] >= 5:
            sm = suit_mask[s]
            sh = straight_high(sm)
            if sh >= 0:
                return _val(STRAIGHT_FLUSH, [sh])
            return _val(FLUSH, [r for r in range(12, -1, -1) if sm >> r & 1][:5])

    quads, trips, pairs, singles = [], [], [], []
    for r in range(12, -1, -1):
        n = counts[r]
        if n == 4:
            quads.append(r)
        elif n == 3:
            trips.append(r)
        elif n == 2:
            pairs.append(r)
        elif n == 1:
            singles.append(r)

    if quads:
        q = quads[0]
        rest = [r for r in range(12, -1, -1) if counts[r] and r != q]
        return _val(QUADS, [q] + rest[:1])
    if trips and (len(trips) > 1 or pairs):
        return _val(FULL_HOUSE, [trips[0], max(trips[1:] + pairs)])
    sh = straight_high(mask)
    if sh >= 0:
        return _val(STRAIGHT, [sh])
    if trips:
        return _val(TRIPS, [trips[0]] + singles[:2])
    if len(pairs) >= 2:
        kick = sorted(pairs[2:] + singles, reverse=True)[:1]
        return _val(TWO_PAIR, pairs[:2] + kick)
    if pairs:
        return _val(PAIR, [pairs[0]] + singles[:3])
    return _val(HIGH, singles[:5])
