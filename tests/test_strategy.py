"""Strateji testleri: sinyalin ne zaman URETILMEDIGI en az uretildigi kadar onemli."""
import pytest

from bot.config import Config
from bot.models import LONG, SHORT, Candle, Position
from bot.strategy import Features, build_strategy
from conftest import make_candles


def test_no_signal_in_chop(cfg, choppy):
    """Yatay piyasada islem acmamak stratejinin en degerli ozelligi."""
    strat = build_strategy(cfg.strategy)
    signals = []
    feats = Features(choppy, cfg.strategy)
    for i in range(cfg.strategy.warmup_bars, len(choppy)):
        s = strat.evaluate("TEST", choppy, feats, index=i)
        if s:
            signals.append(s)
    assert len(signals) == 0, f"yatay piyasada {len(signals)} sinyal uretildi"


def test_uptrend_produces_only_long_signals(cfg, trending_up):
    strat = build_strategy(cfg.strategy)
    feats = Features(trending_up, cfg.strategy)
    sides = set()
    for i in range(cfg.strategy.warmup_bars, len(trending_up)):
        s = strat.evaluate("TEST", trending_up, feats, index=i)
        if s:
            sides.add(s.side)
    assert SHORT not in sides, "yukselen trendte short sinyali uretilmemeli"


def test_signal_geometry_is_consistent(cfg, trending_up):
    strat = build_strategy(cfg.strategy)
    feats = Features(trending_up, cfg.strategy)
    found = 0
    for i in range(cfg.strategy.warmup_bars, len(trending_up)):
        s = strat.evaluate("TEST", trending_up, feats, index=i)
        if not s:
            continue
        found += 1
        if s.side == LONG:
            assert s.stop < s.entry < s.tp1 < s.tp2
        else:
            assert s.stop > s.entry > s.tp1 > s.tp2
        # R katlari config ile uyumlu olmali
        d = abs(s.entry - s.stop)
        assert abs(s.tp1 - s.entry) == pytest.approx(cfg.strategy.tp1_r * d, rel=1e-6)
        assert abs(s.tp2 - s.entry) == pytest.approx(cfg.strategy.tp2_r * d, rel=1e-6)
        assert s.reward_risk == pytest.approx(cfg.strategy.tp2_r, rel=1e-6)
    assert found > 0, "trend verisinde hic sinyal cikmadi - filtreler cok sıkı"


def test_stop_distance_bounded_by_atr(cfg, trending_up):
    strat = build_strategy(cfg.strategy)
    feats = Features(trending_up, cfg.strategy)
    for i in range(cfg.strategy.warmup_bars, len(trending_up)):
        s = strat.evaluate("TEST", trending_up, feats, index=i)
        if s:
            d = abs(s.entry - s.stop)
            assert 0.79 * s.atr <= d <= cfg.strategy.stop_atr_mult * s.atr * 1.01


def test_no_signal_without_warmup(cfg, trending_up):
    strat = build_strategy(cfg.strategy)
    short_series = trending_up[:50]
    assert strat.evaluate("TEST", short_series) is None


# ------------------------------------------------------------------ yonetim
def _pos(side=LONG, entry=100.0, stop=98.0):
    return Position(symbol="T", side=side, qty=1.0, entry_price=entry, stop=stop,
                    tp1=entry + 2 if side == LONG else entry - 2,
                    tp2=entry + 4.4 if side == LONG else entry - 4.4,
                    initial_risk_per_unit=abs(entry - stop), opened_at=0,
                    leverage=5, initial_qty=1.0)


def _candle(o, h, l, c):
    return Candle(0, o, h, l, c, 1.0, 1)


def test_stop_wins_when_both_stop_and_target_touched(cfg):
    """Ayni mumda stop ve hedef gorunuyorsa STOP varsayilir - kotumser ve dogru."""
    strat = build_strategy(cfg.strategy)
    pos = _pos()
    actions = strat.manage(pos, _candle(100, 105, 97, 104), current_atr=1.0)
    assert actions[0]["type"] == "exit"
    assert actions[0]["reason"] == "stop"
    assert len(actions) == 1


def test_partial_take_profit_at_tp1(cfg):
    strat = build_strategy(cfg.strategy)
    pos = _pos()
    actions = strat.manage(pos, _candle(100, 102.5, 99.5, 102.2), current_atr=1.0)
    kinds = [a["reason"] for a in actions]
    assert "tp1" in kinds
    partial = next(a for a in actions if a["reason"] == "tp1")
    assert partial["portion"] == pytest.approx(cfg.strategy.tp1_size_pct / 100)


def test_stop_only_moves_in_favour(cfg):
    strat = build_strategy(cfg.strategy)
    pos = _pos()
    pos.stop = 100.5  # zaten basabasin ustunde
    actions = strat.manage(pos, _candle(101, 102.4, 100.8, 102.2), current_atr=1.0)
    for a in actions:
        if a["type"] == "move_stop":
            assert a["price"] > pos.stop, "stop geriye alinamaz"


def test_short_position_mirror_logic(cfg):
    strat = build_strategy(cfg.strategy)
    pos = _pos(side=SHORT, entry=100.0, stop=102.0)
    actions = strat.manage(pos, _candle(100, 103, 99, 99.5), current_atr=1.0)
    assert actions[0]["reason"] == "stop"

    pos2 = _pos(side=SHORT, entry=100.0, stop=102.0)
    actions2 = strat.manage(pos2, _candle(100, 100.5, 95.5, 95.6), current_atr=1.0)
    assert any(a["reason"] == "tp2" for a in actions2)


def test_time_stop_closes_stale_position(cfg):
    strat = build_strategy(cfg.strategy)
    pos = _pos()
    pos.bars_held = cfg.strategy.max_bars_in_trade + 1
    actions = strat.manage(pos, _candle(100, 100.4, 99.6, 100.1), current_atr=1.0)
    assert any(a["reason"] == "zaman-asimi" for a in actions)


def test_r_multiple_calculation():
    pos = _pos()          # giris 100, stop 98 -> 1R = 2
    assert pos.r_multiple(102) == pytest.approx(1.0)
    assert pos.r_multiple(98) == pytest.approx(-1.0)
    assert pos.r_multiple(104.4) == pytest.approx(2.2)
