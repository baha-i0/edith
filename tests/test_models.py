"""Yuvarlama ve pozisyon matematigi. Yanlis yuvarlama = borsa emri reddeder."""
import pytest

from bot.models import (LONG, SHORT, Position, SymbolFilters, liquidation_distance_pct)


def test_price_rounds_down_to_tick():
    f = SymbolFilters("X", tick_size=0.01, step_size=0.001, min_qty=0.001, min_notional=5)
    assert f.round_price(612.3456) == pytest.approx(612.34)
    assert f.round_price(612.3456, side_up=True) == pytest.approx(612.35)


def test_qty_rounds_down_to_step():
    f = SymbolFilters("X", tick_size=0.01, step_size=0.01, min_qty=0.01, min_notional=5)
    assert f.round_qty(0.12789) == pytest.approx(0.12)
    assert f.round_qty(0.009) == pytest.approx(0.0)


def test_no_float_drift_on_exotic_steps():
    """0.1 + 0.2 problemi: Decimal kullanmazsak borsa emri reddeder."""
    f = SymbolFilters("X", tick_size=0.001, step_size=0.1, min_qty=0.1, min_notional=5)
    assert f.round_qty(0.3) == pytest.approx(0.3)
    assert f.round_qty(2.9999999) == pytest.approx(2.9)


def test_min_notional_enforced():
    f = SymbolFilters("X", tick_size=0.01, step_size=0.01, min_qty=0.01, min_notional=5)
    assert f.qty_ok(0.01, 600)      # 6 USDT
    assert not f.qty_ok(0.005, 600)  # miktar minQty altinda
    assert not f.qty_ok(0.01, 100)   # 1 USDT, minNotional altinda


def test_max_qty_cap():
    f = SymbolFilters("X", 0.01, 0.01, 0.01, 5, max_qty=1.0)
    assert f.round_qty(5.0) == pytest.approx(1.0)


def test_liquidation_distance_shrinks_with_leverage():
    assert liquidation_distance_pct(2) > liquidation_distance_pct(10)
    assert liquidation_distance_pct(10) == pytest.approx(0.095, abs=1e-3)
    assert liquidation_distance_pct(20) == pytest.approx(0.045, abs=1e-3)


def _p(side, entry, stop, qty=1.0):
    return Position("X", side, qty, entry, stop, 0, 0, abs(entry - stop), 0, 5, qty)


def test_unrealized_pnl_direction():
    long = _p(LONG, 100, 98)
    short = _p(SHORT, 100, 102)
    assert long.unrealized(105) == pytest.approx(5.0)
    assert long.unrealized(95) == pytest.approx(-5.0)
    assert short.unrealized(95) == pytest.approx(5.0)
    assert short.unrealized(105) == pytest.approx(-5.0)


def test_stop_hit_detection():
    long = _p(LONG, 100, 98)
    assert long.stop_hit(low=97.5, high=101)
    assert not long.stop_hit(low=98.5, high=101)
    short = _p(SHORT, 100, 102)
    assert short.stop_hit(low=99, high=102.5)
    assert not short.stop_hit(low=99, high=101.5)
