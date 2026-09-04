"""Gostergeler icin referans testleri.

Gosterge hatasi sessizce yanlis sinyal uretir; bu yuzden bilinen degerlerle
ve matematiksel ozelliklerle dogruluyoruz.
"""
import math

import pytest

from bot.indicators import adx, atr, ema, percentile, rma, rolling_max, rsi, sma, true_range


def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_seeded_with_sma():
    vals = [1, 2, 3, 4, 5, 6]
    out = ema(vals, 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)          # ilk deger SMA
    assert out[3] == pytest.approx(3.0)          # (4-2)*0.5+2
    assert out[4] == pytest.approx(4.0)


def test_ema_shorter_than_period_returns_all_none():
    assert ema([1, 2], 5) == [None, None]


def test_ema_converges_to_constant():
    out = ema([10.0] * 50, 10)
    assert out[-1] == pytest.approx(10.0)


def test_rsi_monotonic_up_is_100():
    assert rsi([float(i) for i in range(1, 40)], 14)[-1] == pytest.approx(100.0)


def test_rsi_monotonic_down_is_zero():
    assert rsi([float(i) for i in range(40, 1, -1)], 14)[-1] == pytest.approx(0.0)


def test_rsi_bounds():
    import random
    random.seed(7)
    closes = [100.0]
    for _ in range(300):
        closes.append(max(1.0, closes[-1] * (1 + random.uniform(-0.02, 0.02))))
    for v in rsi(closes, 14):
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_true_range_uses_previous_close():
    h, l, c = [10, 12], [9, 11], [9.5, 11.5]
    tr = true_range(h, l, c)
    assert tr[1] == pytest.approx(max(12 - 11, abs(12 - 9.5), abs(11 - 9.5)))


def test_atr_constant_range():
    n = 40
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    assert atr(highs, lows, closes, 14)[-1] == pytest.approx(2.0)


def test_adx_strong_trend_is_high():
    closes = [float(i) for i in range(1, 80)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    a, pdi, mdi = adx(highs, lows, closes, 14)
    assert a[-1] > 50
    assert pdi[-1] > mdi[-1]


def test_adx_returns_none_when_insufficient_data():
    a, p, m = adx([1.0] * 10, [0.5] * 10, [0.8] * 10, 14)
    assert all(x is None for x in a)


def test_all_indicators_preserve_length():
    n = 120
    closes = [100 + math.sin(i / 5) for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    assert len(ema(closes, 20)) == n
    assert len(rsi(closes, 14)) == n
    assert len(atr(highs, lows, closes, 14)) == n
    assert len(adx(highs, lows, closes, 14)[0]) == n
    assert len(rolling_max(closes, 10)) == n


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert percentile([5], 0.9) == 5


def test_rma_matches_manual():
    out = rma([1, 2, 3, 4, 5, 6], 3)
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx((2.0 * 2 + 4) / 3)


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        ema([1, 2, 3], 0)
    with pytest.raises(ValueError):
        sma([1, 2, 3], -1)


def test_insufficient_data_returns_none_not_error():
    """Sozlesme: veri yetmiyorsa None doner, patlamaz."""
    assert rsi([1.0, 2.0, 3.0], 14) == [None, None, None]
    assert atr([2.0], [1.0], [1.5], 14) == [None]
