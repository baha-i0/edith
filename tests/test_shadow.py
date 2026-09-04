"""Golge modu testleri.

Kritik davranis: edge oldugunde bot DURMAZ, golgeye gecer; kanit geri
gelirse insan mudahalesi olmadan canliya doner. Ve supheli durumda
donmez -- asimetri kasten.
"""
import time

import pytest

from bot.config import Config, ConfigError
from bot.models import LONG, SHORT, Candle, Signal
from bot.shadow import ShadowTracker
from bot.strategy import build_strategy

NOW = 1_700_000_000_000


def _tracker(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.shadow, k, v)
    cfg.validate()
    return ShadowTracker(cfg, build_strategy(cfg.strategy), store=None)


def _sig(symbol="BTCUSDT", side=LONG, entry=100.0, stop_pct=0.02):
    d = entry * stop_pct
    c = Config().strategy
    if side == LONG:
        return Signal(symbol, side, entry, entry - d, entry + c.tp1_r * d,
                      entry + c.tp2_r * d, atr=d, reason="t", meta={"adx": 30})
    return Signal(symbol, side, entry, entry + d, entry - c.tp1_r * d,
                  entry - c.tp2_r * d, atr=d, reason="t", meta={"adx": 30})


def _candle(o, h, l, c, t=0):
    return Candle(t, o, h, l, c, 1.0, t + 1)


def test_shadow_opens_without_touching_money():
    t = _tracker()
    t.open(_sig(), NOW)
    assert t.has_position("BTCUSDT")
    assert t.state.r_values == []       # henuz kapanmadi


def test_shadow_respects_concurrency_limit():
    t = _tracker()
    limit = t.cfg.risk.max_concurrent_positions
    for i in range(limit + 3):
        t.open(_sig(symbol=f"S{i}USDT"), NOW)
    assert len(t.state.positions) == limit


def test_shadow_records_loss_at_stop():
    t = _tracker()
    t.open(_sig(entry=100.0, stop_pct=0.02), NOW)      # stop 98
    r = t.update("BTCUSDT", _candle(100, 100.5, 97.0, 97.5), 1.0, NOW)
    assert r is not None and r < -0.9
    assert not t.has_position("BTCUSDT")


def test_shadow_records_win_at_target():
    t = _tracker()
    t.open(_sig(entry=100.0, stop_pct=0.02), NOW)      # tp2 = 100 + 4*2 = 108
    r = t.update("BTCUSDT", _candle(100, 109.0, 99.9, 108.5), 1.0, NOW)
    assert r is not None and r > 1.0


def test_shadow_applies_trading_costs():
    """Golge sonuclari komisyonsuz olsaydi iyimser olurdu ve yanlis karar verdirirdi."""
    t = _tracker()
    t.open(_sig(entry=100.0, stop_pct=0.02), NOW)
    r = t.update("BTCUSDT", _candle(100, 109.0, 99.9, 108.5), 1.0, NOW)
    c = t.cfg.strategy
    gross = c.tp2_r                       # komisyonsuz beklenen R
    assert r < gross, "golge islemine maliyet uygulanmamis"


def test_shadow_mirrors_short_side():
    t = _tracker()
    t.open(_sig(side=SHORT, entry=100.0, stop_pct=0.02), NOW)   # stop 102
    r = t.update("BTCUSDT", _candle(100, 103.0, 99.0, 99.5), 1.0, NOW)
    assert r is not None and r < -0.9


# ------------------------------------------------------------ donus kurallari
def _fill(t, values):
    t.state.r_values.extend(values)


def test_no_resume_below_min_sample():
    t = _tracker(min_trades_to_resume=40)
    _fill(t, [2.0] * 30)                  # harika ama az
    ok, why = t.should_resume()
    assert not ok and "40" in why


def test_no_resume_when_positive_but_unproven():
    """Ortalama pozitif ama dagilim genis -> kanit yok -> donme."""
    t = _tracker(min_trades_to_resume=40)
    # 11 buyuk kazanc, 39 kucuk zarar -> ortalama +0.10R ama dagilim cok genis
    _fill(t, ([4.0] * 11) + ([-1.0] * 39))
    mean = sum(t.state.r_values) / len(t.state.r_values)
    assert mean > 0
    ok, why = t.should_resume()
    assert not ok and "guven siniri" in why


def test_resume_when_edge_is_proven_again():
    t = _tracker(min_trades_to_resume=40)
    _fill(t, [0.9] * 60)                  # tutarli pozitif
    ok, why = t.should_resume()
    assert ok and "geri geldi" in why


def test_no_resume_while_still_losing():
    t = _tracker(min_trades_to_resume=40)
    _fill(t, [-1.0] * 60)
    assert not t.should_resume()[0]


def test_resume_is_asymmetric_with_entry():
    """Golgeye girmek icin negatif KANIT, cikmak icin pozitif KANIT gerekir.

    Supheli durumda sermaye risk almaz -- yanlis yonde hata yapmanin
    maliyeti simetrik degil.
    """
    t = _tracker(min_trades_to_resume=40)
    _fill(t, [0.0] * 60)                  # tam notr
    assert not t.should_resume()[0], "notr performansla canliya donulmemeli"


def test_state_survives_restart(tmp_path):
    from bot.state import Store
    cfg = Config()
    cfg.state_path = str(tmp_path / "s.db")
    cfg.validate()
    store = Store(cfg.state_path, mode="paper")
    t = ShadowTracker(cfg, build_strategy(cfg.strategy), store)
    t.open(_sig(), NOW)
    t.state.r_values.extend([0.5, -1.0])
    t.save()

    t2 = ShadowTracker(cfg, build_strategy(cfg.strategy), store)
    assert t2.has_position("BTCUSDT")
    assert t2.state.r_values == [0.5, -1.0]


def test_config_rejects_loose_resume_settings():
    c = Config()
    c.shadow.min_trades_to_resume = 5
    with pytest.raises(ConfigError, match="min_trades_to_resume"):
        c.validate()
