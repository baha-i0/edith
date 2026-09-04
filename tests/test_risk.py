"""Risk katmani testleri - botun para kaybetmeyi reddettigi yer."""
import pytest

from bot.config import Config, RiskConfig
from bot.models import SymbolFilters
from bot.risk import (RiskGuard, RiskState, max_safe_leverage, size_position,
                      validate_signal_quality)
from bot.models import Signal


def test_size_scales_with_risk_budget(filters):
    rc = RiskConfig(risk_per_trade_pct=1.0)
    r = size_position(1000, 1000, 100.0, 99.0, filters, rc, 5)
    assert r.ok
    # 1000'in %1'i = 10 USDT risk, 1 USDT stop mesafesi -> 10 birim
    assert r.qty == pytest.approx(10.0, abs=0.01)
    assert r.risk_amount == pytest.approx(10.0, abs=0.1)


def test_wider_stop_gives_smaller_position(filters):
    rc = RiskConfig()
    tight = size_position(1000, 1000, 100.0, 99.0, filters, rc, 5)
    wide = size_position(1000, 1000, 100.0, 95.0, filters, rc, 5)
    assert tight.qty > wide.qty
    # ama risk MIKTARI ayni kalir - isin puf noktasi bu
    assert tight.risk_amount == pytest.approx(wide.risk_amount, rel=0.05)


def test_leverage_does_not_change_risk(filters):
    """Kaldiraci artirmak riski artirmamali, sadece marji dusurmeli."""
    rc = RiskConfig()
    low = size_position(1000, 1000, 100.0, 98.0, filters, rc, 2)
    high = size_position(1000, 1000, 100.0, 98.0, filters, rc, 10)
    assert low.qty == pytest.approx(high.qty)
    assert low.risk_amount == pytest.approx(high.risk_amount)
    assert high.margin < low.margin


def test_leverage_capped_by_liquidation_distance(filters):
    """Stop, likidasyondan once tetiklenmek zorunda."""
    rc = RiskConfig(max_leverage=20, max_stop_vs_liquidation=0.6)
    r = size_position(1000, 1000, 100.0, 92.0, filters, rc, 20)  # %8 stop
    assert r.ok
    assert r.leverage < 20
    # kontrol: stop mesafesi likidasyon mesafesinin %60'ini asmamali
    liq = 1 / r.leverage - 0.005
    assert 0.08 <= liq * 0.6 + 1e-9


def test_max_safe_leverage_monotonic():
    assert max_safe_leverage(0.005, 0.6) > max_safe_leverage(0.02, 0.6)
    assert max_safe_leverage(0.5, 0.6) >= 1


def test_rejects_when_below_min_notional(filters):
    rc = RiskConfig(risk_per_trade_pct=0.1)
    r = size_position(20, 20, 600.0, 594.0, filters, rc, 5)
    assert not r.ok
    assert "minimum" in r.reason or "sifir" in r.reason


def test_rejects_zero_stop_distance(filters):
    r = size_position(1000, 1000, 100.0, 100.0, filters, RiskConfig(), 5)
    assert not r.ok


def test_notional_cap_respected(filters):
    rc = RiskConfig(risk_per_trade_pct=2.0, max_position_notional_pct=100)
    r = size_position(1000, 1000, 100.0, 99.9, filters, rc, 10)
    assert r.ok
    assert r.notional <= 1000 * 1.001


def test_free_margin_buffer(filters):
    rc = RiskConfig(min_free_margin_pct=100)
    r = size_position(1000, 1000, 100.0, 99.0, filters, rc, 5)
    assert not r.ok


# ------------------------------------------------------------------ guard
def _guard(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.risk, k, v)
    cfg.validate()
    return RiskGuard(cfg, RiskState())


def test_daily_loss_limit_halts_trading():
    g = _guard(daily_loss_limit_pct=4)
    now = 1_700_000_000_000
    g.roll_day(now, 1000)
    g.record_close(now, -45)          # %4.5 zarar
    ok, why = g.can_open(now, 0, 955)
    assert not ok and "zarar limiti" in why
    # yeni bir dongude hala kapali
    assert not g.can_open(now + 60_000, 0, 955)[0]


def test_daily_profit_target_halts_trading():
    g = _guard(daily_profit_target_pct=6)
    now = 1_700_000_000_000
    g.roll_day(now, 1000)
    g.record_close(now, 70)
    ok, why = g.can_open(now, 0, 1070)
    assert not ok and "kar hedefi" in why


def test_cooldown_after_loss():
    g = _guard(cooldown_minutes_after_loss=30)
    now = 1_700_000_000_000
    g.roll_day(now, 1000)
    g.record_close(now, -5)
    assert not g.can_open(now + 60_000, 0, 995)[0]
    assert g.can_open(now + 31 * 60_000, 0, 995)[0]


def test_consecutive_losses_trigger_long_cooldown():
    g = _guard(max_consecutive_losses=3, cooldown_minutes_after_streak=240,
               cooldown_minutes_after_loss=1)
    now = 1_700_000_000_000
    g.roll_day(now, 1000)
    for _ in range(3):
        g.record_close(now, -5)
    assert not g.can_open(now + 60 * 60_000, 0, 985)[0]   # 1 saat sonra hala kapali
    assert g.can_open(now + 241 * 60_000, 0, 985)[0]


def test_max_concurrent_positions():
    g = _guard(max_concurrent_positions=2)
    now = 1_700_000_000_000
    assert g.can_open(now, 1, 1000)[0]
    assert not g.can_open(now, 2, 1000)[0]


def test_max_trades_per_day():
    g = _guard(max_trades_per_day=2)
    now = 1_700_000_000_000
    g.roll_day(now, 1000)
    g.record_open(now); g.record_open(now)
    assert not g.can_open(now, 0, 1000)[0]


def test_new_day_resets_limits():
    g = _guard(daily_loss_limit_pct=4)
    day1 = 1_700_000_000_000
    g.roll_day(day1, 1000)
    g.record_close(day1, -50)
    assert not g.can_open(day1, 0, 950)[0]
    day2 = day1 + 86_400_000
    ok, _ = g.can_open(day2, 0, 950)
    assert ok
    assert g.state.realized_pnl_today == 0


def test_signal_quality_rejects_poor_rr():
    cfg = Config()
    sig = Signal("BNBUSDT", "LONG", entry=600, stop=594, tp1=603, tp2=606,
                 atr=3, reason="test")
    ok, why = validate_signal_quality(sig, cfg)
    assert not ok and "R:R" in why


def test_signal_quality_rejects_stop_too_tight_vs_fees():
    cfg = Config()
    sig = Signal("BNBUSDT", "LONG", entry=600, stop=599.8, tp1=600.3, tp2=600.6,
                 atr=0.2, reason="test")
    ok, why = validate_signal_quality(sig, cfg)
    assert not ok


def test_signal_quality_accepts_good_setup():
    cfg = Config()
    sig = Signal("BNBUSDT", "LONG", entry=600, stop=594, tp1=606, tp2=613.2,
                 atr=4, reason="test")
    ok, why = validate_signal_quality(sig, cfg)
    assert ok, why
