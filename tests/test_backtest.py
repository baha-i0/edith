"""Backtest motorunun kotumser varsayimlarini dogrular.

Iyimser backtest, gelecekte odenen faturadir. Bu testler o faturayi
simdiden kesmeye calisir.
"""
import pytest

from bot.backtest import run_backtest
from bot.config import Config
from bot.models import LONG, SHORT, Candle, SymbolFilters, Signal
from bot.strategy import TrendPullbackStrategy
from conftest import make_candles


F = SymbolFilters("T", tick_size=0.01, step_size=0.001, min_qty=0.001, min_notional=5)


def test_flat_market_produces_no_trades(cfg, choppy):
    res = run_backtest(cfg, choppy, "T", F)
    assert res.trades == []
    assert res.end_equity == pytest.approx(res.start_equity)


def test_insufficient_data_returns_empty(cfg):
    res = run_backtest(cfg, make_candles([100.0] * 50), "T", F)
    assert res.trades == []
    assert res.bars == 50


def test_no_lookahead_entry_uses_next_open(cfg, monkeypatch):
    """Sinyal mumun kapanisinda uretilir, giris SONRAKI mumun acilisinda olur."""
    prices = [100.0] * 320 + [101.0, 130.0]     # ani sicrama
    candles = make_candles(prices)
    fired = {}

    real = TrendPullbackStrategy.evaluate

    def fake(self, symbol, cs, features=None, index=None):
        i = len(cs) - 1 if index is None else index
        if i != 320:
            return None
        entry = cs[i].close
        fired["signal_bar_close"] = entry
        fired["next_bar_open"] = cs[i + 1].open
        return Signal(symbol, LONG, entry, entry * 0.98, entry * 1.02,
                      entry * 1.06, atr=entry * 0.01, reason="test")

    monkeypatch.setattr(TrendPullbackStrategy, "evaluate", fake)
    res = run_backtest(cfg, candles, "T", F)
    monkeypatch.setattr(TrendPullbackStrategy, "evaluate", real)

    assert res.trades, "islem acilmaliydi"
    t = res.trades[0]
    slip = cfg.execution.slippage_bps / 10_000
    # Giris SONRAKI mumun acilisindan olmali...
    assert t.entry_price == pytest.approx(fired["next_bar_open"] * (1 + slip), rel=1e-6)
    # ...ve o mumun 130'a firlayan kapanisindan HABERSIZ olmali (lookahead yok)
    assert t.entry_price < 105, "backtest gelecegi goruyor"


def test_fees_are_charged(cfg):
    """Komisyon sifir degilse net PnL, brut PnL'den kucuk olmali."""
    prices = [100.0] * 320 + [101.0] + [100 + i for i in range(1, 40)]
    candles = make_candles(prices)

    real = TrendPullbackStrategy.evaluate

    def fake(self, symbol, cs, features=None, index=None):
        i = len(cs) - 1 if index is None else index
        if i != 320:
            return None
        e = cs[i].close
        return Signal(symbol, LONG, e, e * 0.98, e * 1.02, e * 1.04,
                      atr=e * 0.01, reason="test")

    cfg.execution.taker_fee = 0.001
    TrendPullbackStrategy.evaluate = fake
    try:
        res = run_backtest(cfg, candles, "T", F)
    finally:
        TrendPullbackStrategy.evaluate = real
    assert res.fees > 0


def test_stop_beats_target_within_same_bar(cfg):
    """Hem stop hem hedefin gorundugu mumda backtest STOP saymali."""
    strat = TrendPullbackStrategy(cfg.strategy)
    from bot.models import Position
    pos = Position("T", LONG, 1.0, 100.0, 98.0, 102.0, 104.4, 2.0, 0, 5, 1.0)
    acts = strat.manage(pos, Candle(0, 100, 110, 97, 109, 1.0, 1), 1.0)
    assert acts == [{"type": "exit", "price": 98.0, "reason": "stop", "portion": 1.0}]


def test_metrics_are_consistent(cfg, trending_up):
    res = run_backtest(cfg, trending_up, "T", F)
    if not res.trades:
        pytest.skip("bu veride islem yok")
    wins = [t for t in res.trades if t.pnl > 0]
    assert res.win_rate == pytest.approx(100 * len(wins) / len(res.trades))
    assert res.max_drawdown_pct >= 0
    assert len(res.equity_curve) > 0


def test_verdict_rejects_small_sample(cfg, trending_up):
    res = run_backtest(cfg, trending_up, "T", F)
    if len(res.trades) < 30:
        assert "YETERSIZ VERI" in res.verdict()


def test_verdict_rejects_negative_expectancy(cfg):
    from bot.backtest import BacktestResult
    from bot.models import Trade
    res = BacktestResult("T", "4h", 100, 80)
    res.trades = [Trade("T", LONG, 1, 100, 99, 0, 1, -1.0, 0.1, -1.0, "stop")] * 40
    assert "NEGATIF BEKLENTI" in res.verdict()


def test_risk_guard_limits_apply_in_backtest(cfg):
    """Backtest ve canli ayni risk kodunu kullanmali - yoksa backtest yalan soyler."""
    cfg.risk.max_trades_per_day = 1
    prices = [100.0] * 320 + [100 + (i % 7) for i in range(200)]
    candles = make_candles(prices, step_ms=3_600_000)
    res = run_backtest(cfg, candles, "T", F)
    from collections import Counter
    import time
    per_day = Counter(time.strftime("%Y-%m-%d", time.gmtime(t.opened_at / 1000))
                      for t in res.trades)
    assert all(v <= 1 for v in per_day.values()), per_day


# --------------------------------------------------------------- portfoy
def test_portfolio_respects_concurrency_limit(cfg, trending_up, choppy):
    """Es zamanli pozisyon limiti asilamaz - portfoy backtestinin varlik sebebi."""
    from bot.backtest import run_portfolio_backtest
    from bot.models import Signal
    from bot.strategy import TrendPullbackStrategy

    cfg.risk.max_concurrent_positions = 1
    data = {"A": trending_up, "B": list(trending_up), "C": list(trending_up)}

    real = TrendPullbackStrategy.evaluate

    def always(self, symbol, cs, features=None, index=None):
        i = len(cs) - 1 if index is None else index
        if i < cfg.strategy.warmup_bars or i >= len(cs) - 1:
            return None
        px = cs[i].close
        d = px * 0.02
        return Signal(symbol, LONG, px, px - d, px + d, px + 2.5 * d,
                      atr=d, reason="t", meta={"adx": 30})

    TrendPullbackStrategy.evaluate = always
    try:
        res = run_portfolio_backtest(cfg, data, start_equity=1000)
    finally:
        TrendPullbackStrategy.evaluate = real

    # Ayni anda birden fazla pozisyon acilmis olamaz: aciliş/kapanış araliklari
    # kesismemeli
    spans = sorted((t.opened_at, t.closed_at) for t in res.trades)
    for (o1, c1), (o2, _c2) in zip(spans, spans[1:]):
        assert o2 >= c1, "es zamanli pozisyon limiti asildi"
    assert res.blocked_by_slots > 0, "slot dolulugu hic kaydedilmedi"


def test_portfolio_shares_one_equity_pool(cfg, trending_up):
    from bot.backtest import run_portfolio_backtest
    res = run_portfolio_backtest(cfg, {"A": trending_up}, start_equity=500)
    assert res.start_equity == 500
    assert res.equity_curve
    # equity egrisi mark-to-market, bitis equity gerceklesmis
    assert res.end_equity > 0


def test_portfolio_empty_data_is_safe(cfg):
    from bot.backtest import run_portfolio_backtest
    res = run_portfolio_backtest(cfg, {}, start_equity=100)
    assert res.trades == [] and res.end_equity == 100
