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
from bot.models import LONG, SHORT, Candle, Signal, SymbolFilters
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


# ------------------------------------------------------- ogrenme entegrasyonu
def test_engine_blocks_entry_for_benched_symbol(tmp_path, monkeypatch, trending_up):
    """Kanitlanmis negatif kova motor seviyesinde de girisi engellemeli."""
    from bot.models import Trade
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    now = int(time.time() * 1000)
    for _ in range(35):
        engine.learner.record_trade(
            Trade("TESTUSDT", LONG, 1, 100, 99, now, now, -1.0, 0.1, -1.0, "stop",
                  context={"adx": 30.0, "atr_pct": 1.0}), now)
    engine.tick()
    assert broker.positions() == {}, "banklanmis sembolde pozisyon acildi"


def test_engine_records_min_notional_mistake(tmp_path, monkeypatch, trending_up):
    """Bakiye yetmiyorsa bot ayni hatayi sonsuza kadar tekrarlamamali."""
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    engine.cfg.account.paper_start_balance = 5.0
    engine.broker.balance = 5.0
    engine.cfg.learning.mistake_repeat_threshold = 2

    def big_min_notional(symbol):
        return SymbolFilters(symbol, 0.01, 0.01, 0.01, 100_000.0)

    monkeypatch.setattr(engine.market, "filters", big_min_notional)
    _forced_signal(monkeypatch)

    for _ in range(3):
        engine._last_bar.clear()
        engine.tick()

    assert broker.positions() == {}
    assert engine.learner.mistakes.counts, "hata defterine hicbir sey yazilmadi"
    ok, why = engine.learner.allow_entry("TESTUSDT", {"adx": 30, "atr_pct": 1.0},
                                         int(time.time() * 1000))
    assert not ok and "operasyonel" in why


def test_engine_applies_learned_stop_width(tmp_path, monkeypatch, trending_up):
    """Ogrenilmis stop carpani uygulanmali ve R katlari korunmali."""
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch, stop_pct=0.02)
    engine.learner.buckets["sym:TESTUSDT"] = __import__(
        "bot.learning", fromlist=["BucketStats"]).BucketStats(
        key="sym:TESTUSDT", stop_widen_mult=1.5)

    engine.tick()
    pos = broker.positions()["TESTUSDT"]
    stop_dist = abs(pos.entry_price - pos.stop)
    # %2 stop x 1.5 = %3
    assert stop_dist / pos.entry_price == pytest.approx(0.03, rel=0.02)
    # R katlari korunmali
    c = engine.cfg.strategy
    assert (pos.tp2 - pos.entry_price) / stop_dist == pytest.approx(c.tp2_r, rel=0.02)


def test_learning_survives_restart(tmp_path, monkeypatch, trending_up):
    from bot.learning import Learner
    from bot.models import Trade
    engine, _m, _b, store = _engine(tmp_path, trending_up)
    now = int(time.time() * 1000)
    for _ in range(35):
        engine.learner.record_trade(
            Trade("TESTUSDT", LONG, 1, 100, 99, now, now, -1.0, 0.1, -1.0, "stop",
                  context={"adx": 30.0, "atr_pct": 1.0}), now)
    engine.learner.save()

    fresh = Learner(engine.cfg, store)
    assert not fresh.allow_entry("TESTUSDT", {"adx": 30, "atr_pct": 1.0}, now)[0]


# ------------------------------------------------------- golge modu (otonomi)
def test_engine_enters_shadow_instead_of_stopping(tmp_path, monkeypatch, trending_up):
    """Edge oldugunde bot DURMAZ, golgeye gecer. Otonomi budur."""
    from bot.models import Trade
    engine, _m, broker, store = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    now = int(time.time() * 1000)

    # kanitlanmis negatif performans yaz
    for i in range(60):
        store.record_trade(Trade("TESTUSDT", LONG, 1, 100, 99, now, now,
                                 -1.5, 0.05, -1.0, "stop"))
    store.record_equity(900.0, now)

    engine._last_health_ms = 0
    engine._maybe_health_check(now)

    assert engine.guard.state.shadow_mode, "golge moduna gecilmedi"
    assert not engine.guard.state.halted, "bot durmamali, golgeye gecmeli"


def test_shadow_mode_opens_virtual_positions_not_real(tmp_path, monkeypatch,
                                                      trending_up):
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    engine.guard.state.shadow_mode = True

    engine.tick()

    assert broker.positions() == {}, "golge modunda GERCEK pozisyon acilmis"
    assert engine.shadow.has_position("TESTUSDT"), "sanal pozisyon acilmadi"
    assert broker.balance == pytest.approx(1000.0), "golge modunda para harcanmis"


def test_shadow_mode_ignores_daily_limits(tmp_path, monkeypatch, trending_up):
    """Golgede para riski yok, o yuzden gunluk zarar limiti olcumu durdurmamali."""
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    engine.guard.state.shadow_mode = True
    now = int(time.time() * 1000)
    engine.guard.roll_day(now, 1000)
    engine.guard.record_close(now, -500)      # gunluk limiti fena halde as

    engine.tick()
    assert engine.shadow.has_position("TESTUSDT"), \
        "golgede olcum gunluk limit yuzunden durmus"


def test_engine_resumes_live_when_edge_returns(tmp_path, monkeypatch, trending_up):
    """Insan mudahalesi olmadan canliya donus."""
    engine, _m, broker, store = _engine(tmp_path, trending_up)
    now = int(time.time() * 1000)
    engine.guard.state.shadow_mode = True
    engine.guard.state.shadow_reason = "test"
    engine.shadow.state.r_values = [0.9] * 59      # bir eksik

    # son sanal islem kapanip kaniti tamamlasin
    _forced_signal(monkeypatch, stop_pct=0.02)
    engine.tick()                                   # sanal pozisyon acilir
    assert engine.shadow.has_position("TESTUSDT")

    pos = engine.shadow.state.positions["TESTUSDT"]
    rally = trending_up + make_candles([pos["tp2"] * 1.05] * 2,
                                       start_ms=trending_up[-1].close_time + 1)
    _m.all = rally
    _m.cursor = len(rally)
    engine.tick()                                   # hedefe ulasir, kapanir

    assert not engine.guard.state.shadow_mode, "kanit geldi ama canliya donulmedi"
    assert engine.shadow.state.resumed_count == 1


def test_engine_stays_in_shadow_without_proof(tmp_path, monkeypatch, trending_up):
    engine, _m, broker, _s = _engine(tmp_path, trending_up)
    engine.guard.state.shadow_mode = True
    engine.shadow.state.r_values = [-1.0] * 59
    _forced_signal(monkeypatch)

    engine.tick()
    pos = engine.shadow.state.positions["TESTUSDT"]
    crash = trending_up + make_candles([pos["stop"] * 0.95] * 2,
                                       start_ms=trending_up[-1].close_time + 1)
    _m.all = crash
    _m.cursor = len(crash)
    engine.tick()

    assert engine.guard.state.shadow_mode, "kanit yokken canliya donulmus"
    assert broker.positions() == {}


def test_shadow_state_survives_restart(tmp_path, monkeypatch, trending_up):
    from bot.shadow import ShadowTracker
    engine, _m, _b, store = _engine(tmp_path, trending_up)
    _forced_signal(monkeypatch)
    engine.guard.state.shadow_mode = True
    engine.tick()
    engine.shadow.save()

    fresh = ShadowTracker(engine.cfg, engine.strategy, store)
    assert fresh.has_position("TESTUSDT")


def test_daily_report_is_plain_language(tmp_path, monkeypatch, trending_up):
    engine, _m, _b, store = _engine(tmp_path, trending_up)
    now = int(time.time() * 1000)
    store.record_equity(1000.0, now)
    text = engine.daily_report(now)
    assert "GUNLUK OZET" in text and "Bakiye" in text and "DURUM" in text


def test_daily_report_explains_shadow_mode(tmp_path, monkeypatch, trending_up):
    engine, _m, _b, store = _engine(tmp_path, trending_up)
    now = int(time.time() * 1000)
    store.record_equity(1000.0, now)
    engine.guard.state.shadow_mode = True
    engine.guard.state.shadow_reason = "beklenti negatif"
    text = engine.daily_report(now)
    assert "GOLGE MODU" in text
    assert "kendiliginden canliya doner" in text


# ------------------------------------------------ genislik filtresi (parite)
def _multi_engine(tmp_path, candles, symbols, **risk_over):
    """Cok sembollu motor: genislik filtresi ancak boyle test edilebilir."""
    from bot.config import Config
    from bot.state import Store
    from bot.exchange.paper import PaperBroker
    from bot.engine import TradingEngine
    cfg = Config()
    cfg.symbols = list(symbols)
    cfg.state_path = str(tmp_path / "b.db")
    cfg.account.paper_start_balance = 5000.0
    for k, v in risk_over.items():
        setattr(cfg.risk, k, v)
    cfg.validate()
    market = FakeMarket(candles)
    store = Store(cfg.state_path, mode="paper")
    broker = PaperBroker(cfg, market, store)
    return TradingEngine(cfg, market, broker, store), broker


def _side_signal(monkeypatch, side_for):
    """Sembole gore yon ureten sahte strateji."""
    def fake(self, symbol, candles, features=None, index=None):
        side = side_for(symbol)
        if side is None:
            return None
        px = candles[-1].close
        d = px * 0.02
        if side == LONG:
            return Signal(symbol, LONG, px, px - d, px + d, px + 2.5 * d,
                          atr=d, reason="t", meta={"adx": 30})
        return Signal(symbol, SHORT, px, px + d, px - d, px - 2.5 * d,
                      atr=d, reason="t", meta={"adx": 30})
    monkeypatch.setattr(TrendPullbackStrategy, "evaluate", fake)


def test_breadth_filter_blocks_lonely_signal(tmp_path, monkeypatch, trending_up):
    """Tek basina gelen sinyal alinmamali -- olculen en buyuk iyilestirme."""
    syms = ["AUSDT", "BUSDT", "CUSDT", "DUSDT"]
    engine, broker = _multi_engine(tmp_path, trending_up, syms, min_breadth=4)
    _side_signal(monkeypatch, lambda s: LONG if s == "AUSDT" else None)
    engine.tick()
    assert broker.positions() == {}, "yalniz sinyal genislik filtresini gecti"


def test_breadth_filter_allows_coherent_market(tmp_path, monkeypatch, trending_up):
    syms = ["AUSDT", "BUSDT", "CUSDT", "DUSDT"]
    engine, broker = _multi_engine(tmp_path, trending_up, syms, min_breadth=4)
    _side_signal(monkeypatch, lambda s: LONG)
    engine.tick()
    assert len(broker.positions()) > 0, "tutarli piyasada hic pozisyon acilmadi"


def test_breadth_counts_per_direction(tmp_path, monkeypatch, trending_up):
    """3 long + 1 short, esik 3 -> sadece longlar gecmeli."""
    syms = ["AUSDT", "BUSDT", "CUSDT", "DUSDT"]
    engine, broker = _multi_engine(tmp_path, trending_up, syms,
                                   min_breadth=3, max_concurrent_positions=4)
    _side_signal(monkeypatch, lambda s: SHORT if s == "DUSDT" else LONG)
    engine.tick()
    sides = {s: p.side for s, p in broker.positions().items()}
    assert sides, "hic pozisyon acilmadi"
    assert SHORT not in sides.values(), "yalniz kalan short genislik filtresini gecti"


def test_breadth_disabled_by_default_value_one(tmp_path, monkeypatch, trending_up):
    syms = ["AUSDT", "BUSDT"]
    engine, broker = _multi_engine(tmp_path, trending_up, syms, min_breadth=1)
    _side_signal(monkeypatch, lambda s: LONG if s == "AUSDT" else None)
    engine.tick()
    assert "AUSDT" in broker.positions()


def test_same_direction_cap_when_enabled(tmp_path, monkeypatch, trending_up):
    syms = ["AUSDT", "BUSDT", "CUSDT", "DUSDT"]
    engine, broker = _multi_engine(tmp_path, trending_up, syms,
                                   min_breadth=1, max_same_direction=2,
                                   max_concurrent_positions=4)
    _side_signal(monkeypatch, lambda s: LONG)
    engine.tick()
    assert len(broker.positions()) <= 2


def test_allocation_prefers_stronger_signal(tmp_path, monkeypatch, trending_up):
    """Slot yetmiyorsa liste sirasina gore degil, ADX'e gore secilmeli."""
    syms = ["AUSDT", "BUSDT", "CUSDT"]
    engine, broker = _multi_engine(tmp_path, trending_up, syms,
                                   min_breadth=1, max_concurrent_positions=1)

    def fake(self, symbol, candles, features=None, index=None):
        px = candles[-1].close
        d = px * 0.02
        adx = {"AUSDT": 22.0, "BUSDT": 45.0, "CUSDT": 30.0}[symbol]
        return Signal(symbol, LONG, px, px - d, px + d, px + 2.5 * d,
                      atr=d, reason="t", meta={"adx": adx})

    monkeypatch.setattr(TrendPullbackStrategy, "evaluate", fake)
    engine.tick()
    assert list(broker.positions()) == ["BUSDT"], "en guclu sinyal secilmedi"
