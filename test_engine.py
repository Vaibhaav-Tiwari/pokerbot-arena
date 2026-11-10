"""Sanity checks: python3 test_engine.py"""
from cards import parse, evaluate, category, PAIR, TWO_PAIR, STRAIGHT, FLUSH, FULL_HOUSE, STRAIGHT_FLUSH, HIGH
from engine import icm


def ev(s):
    return evaluate([parse(x) for x in s.split()])


def test_evaluator():
    assert category(ev("Ah 2c 3d 4s 5h Kd Qc")) == STRAIGHT  # wheel
    assert category(ev("Ah Kh Qh Jh Th 2c 3d")) == STRAIGHT_FLUSH
    assert category(ev("Ah Kh 9h 4h 2h As Ad")) == FLUSH
    assert category(ev("Ah As Ad Kc Kd 2s 3c")) == FULL_HOUSE
    assert category(ev("Ah As Kd Kc Qd Qs 3c")) == TWO_PAIR
    assert ev("Ah As Kd Kc Qd Qs 3c") > ev("Ah As Kd Kc Jd Js 3c")  # kicker Q > J
    assert category(ev("Ah 9s 7d 5c 3d")) == HIGH
    assert ev("6h 2c 3d 4s 5h") > ev("Ah 2c 3d 4s 5h")  # 6-high straight beats wheel
    assert ev("Ah Ad 9c") > ev("Kh Kd Qc") and category(ev("Ah Ad 9c")) == PAIR


def test_icm():
    eq = icm([1000, 1000, 1000], [10, 5, 2])
    assert all(abs(x - 17 / 3) < 1e-9 for x in eq)
    eq = icm([9000, 500, 500], [10, 5, 2])
    assert eq[0] > eq[1] and abs(sum(eq) - 17) < 1e-9


if __name__ == "__main__":
    test_evaluator()
    test_icm()
    print("ok")
