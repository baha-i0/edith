"""Uctan uca motor testi: sahte piyasa + paper broker.

Amac, borsaya baglanmadan "sinyal -> boyutlandirma -> emir -> yonetim ->
kapanis -> kayit" zincirinin tamaminin calistigini kanitlamak.
"""
import time

import pytest

from bot.config import Config
from bot.engine import TradingEngine
from bot.exchange.base import MarketData
from bot.exchange.paper import PaperBroker
from bot.models import LONG, Candle, Signal, SymbolFilters
from bot.state import Store
from bot.strategy import TrendPullbackStrategy
from conftest import make_candles


class FakeMarket(MarketData):
    """Deterministik piyasa. Fiyat, verilen mum serisinden ilerler."""

    def __init__(self, candles, spread_bps=2.0, funding_rate=0.0001):
        self.all = candles
        self.cursor = len(candles)
        self.spread_bps = spread_bps
        self.funding_rate = funding_rate
        self.calls = 0

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        self.calls += 1
        return self.all[max(0, self.cursor - limit):self.cursor]

    def book_ticker(self, symbol):
        mid = self.all[self.cursor - 1].close
        half = mid * self.spread_bps / 20_000
        return {"bid": mid - half, "ask": mid + half, "mid": mid,
                "spread_bps": self.spread_bps}

    def funding(self, symbol):
        return {"rate": self.funding_rate,
                "next_funding_ms": int(time.time() * 1000) + 4 * 3600_000, "mark": 0}

    def filters(self, symbol):
        return SymbolFilters(symbol, 0.0001, 0.0001, 0.0001, 1.0)


def _engine(tmp_path, candles, cfg=None, **market_kw):
    cfg = cfg or Config()
    cfg.symbols = ["TESTUSDT"]
    cfg.state_path = str(tmp_path / "s.db")
    cfg.account.paper_start_balance = 1000.0
    cfg.validate()
    market = FakeMarket(candles, **market_kw)
    store = Store(cfg.state_path, mode="paper")
    broker = PaperBroker(cfg, market, store)
    return TradingEngine(cfg, market, broker, store), market, broker, store


def _forced_signal(monkeypatch, side=LONG, stop_pct=0.02, tp2_r=2.2):
    def fake(self, symbol, candles, features=None, index=None):
        px = candles[-1].close
        d = px * stop_pct
        if side == LONG:
            return Signal(symbol, LONG, px, px - d, px + d, px + tp2_r * d,
                          atr=d, reason="test", meta={"adx": 30})
        return Signal(symbol, side, px, px + d, px - d, px - tp2_r * d,
                      atr=d, reason="test", meta={"adx": 30})
    monkeypatch.setattr(TrendPullbackStrategy, "evaluate", fake)


def test_full_cycle_open_then_stop(tmp_path, monkeypatch, trending_up):
    engine, market, broker, store = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)

    engine.tick()
    pos = broker.positions().get("TESTUSDT")
    assert pos is not None, "sinyal geldi ama pozisyon acilmadi"
    assert pos.side == LONG
    assert pos.stop < pos.entry_price < pos.tp2
    assert store.load_positions()  # kalici hale getirildi

    # Fiyati stop'un altina dusur -> intrabar kontrolu pozisyonu kapatmali
    crash = trending_up + make_candles([pos.stop * 0.9] * 3,
                                       start_ms=trending_up[-1].close_time + 1)
    market.all = crash
    market.cursor = len(crash)
    engine.tick()

    assert broker.positions() == {}, "stop tetiklendi ama pozisyon kapanmadi"
    assert store.stats()["trades"] == 1
    assert store.stats()["net_pnl"] < 0
    assert not store.load_positions()


def test_full_cycle_open_then_target(tmp_path, monkeypatch, trending_up):
    engine, market, broker, store = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    engine.tick()
    pos = broker.positions()["TESTUSDT"]

    rally = trending_up + make_candles([pos.tp2 * 1.02] * 3,
                                       start_ms=trending_up[-1].close_time + 1)
    market.all = rally
    market.cursor = len(rally)
    engine.tick()

    assert broker.positions() == {}
    st = store.stats()
    assert st["trades"] == 1 and st["net_pnl"] > 0


def test_wide_spread_blocks_entry(tmp_path, monkeypatch, trending_up):
    engine, _m, broker, _s = _engine(tmp_path, trending_up, spread_bps=50.0)
    _forced_signal(monkeypatch)
    engine.tick()
    assert broker.positions() == {}, "genis spread'e ragmen pozisyon acildi"


def test_poor_reward_risk_signal_rejected(tmp_path, monkeypatch, trending_up):
    """Kullanicinin ilk fikri: -40 zarar hedefi, +20 kar hedefi (0.5 R:R)."""
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch, tp2_r=0.5)
    engine.tick()
    assert broker.positions() == {}


def test_daily_loss_limit_stops_new_entries(tmp_path, monkeypatch, trending_up):
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    now = int(time.time() * 1000)
    engine.guard.roll_day(now, 1000)
    engine.guard.record_close(now, -100)   # %10 zarar, limit %4
    engine.tick()
    assert broker.positions() == {}
    assert engine.guard.state.halted


def test_no_duplicate_position_same_symbol(tmp_path, monkeypatch, trending_up):
    engine, market, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    engine.tick()
    engine.tick()
    engine.tick()
    assert len(broker.positions()) == 1


def test_state_survives_restart(tmp_path, monkeypatch, trending_up):
    cfg = Config()
    cfg.symbols = ["TESTUSDT"]
    cfg.state_path = str(tmp_path / "s.db")
    cfg.account.paper_start_balance = 1000.0
    cfg.validate()
    engine, market, broker, store = _engine(tmp_path, trending_up, cfg=cfg)
    _forced_signal(monkeypatch)
    engine.tick()
    assert broker.positions()

    # yeni surec gibi davran: ayni DB'den yeniden yukle
    store2 = Store(cfg.state_path, mode="paper")
    broker2 = PaperBroker(cfg, market, store2)
    assert "TESTUSDT" in broker2.positions()
    assert broker2.balance == pytest.approx(broker.balance, rel=1e-9)


def test_engine_survives_market_data_error(tmp_path, monkeypatch, trending_up):
    """Tek bir sembolun hatasi tum dongoyu dusurmemeli."""
    engine, market, broker, _s = _engine(tmp_path, trending_up)

    def boom(*a, **k):
        raise RuntimeError("borsa coktu")

    monkeypatch.setattr(market, "klines", boom)
    engine.tick()   # exception disari sizmamali
