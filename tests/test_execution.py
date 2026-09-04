"""Giris emri tipi (market vs post_only) ve Telegram komut katmani testleri.

post_only'nin BEDELI vardir: emir dolmayabilir. Bu testlerin isi, tasarrufun
degil, dolmama halinin dogru ele alindigini kanitlamak -- cunku para orada
kaybedilir.
"""
import time

import pytest

from bot.config import Config, ConfigError
from bot.exchange.paper import PaperBroker
from bot.models import LONG, SHORT, Signal, SymbolFilters
from bot.notify import CommandRouter, Notifier
from bot.risk import RiskGuard, RiskState
from bot.state import Store

from test_engine import FakeMarket
from conftest import make_candles


# --------------------------------------------------------------------- config
def test_gecersiz_emir_tipi_reddedilir():
    c = Config()
    c.execution.entry_order_type = "iceberg"
    with pytest.raises(ConfigError):
        c.validate()


def test_negatif_bekleme_reddedilir():
    c = Config()
    c.execution.post_only_wait_bars = 0
    with pytest.raises(ConfigError):
        c.validate()


# ------------------------------------------------------------- paper post_only
def _broker(tmp_path, prices, **exec_kw):
    cfg = Config()
    cfg.timeframe = "4h"
    cfg.execution.entry_order_type = "post_only"
    for k, v in exec_kw.items():
        setattr(cfg.execution, k, v)
    candles = make_candles(prices, step_ms=14_400_000)
    market = FakeMarket(candles, spread_bps=0.0)
    store = Store(str(tmp_path / "t.db"))
    return cfg, market, PaperBroker(cfg, market, store), store


def _sig(entry, side=LONG):
    if side == SHORT:
        return Signal(symbol="BNBUSDT", side=side, entry=entry,
                      stop=entry * 1.03, tp1=entry * 0.97, tp2=entry * 0.88,
                      atr=entry * 0.01, reason="test", meta={"adx": 30.0})
    return Signal(symbol="BNBUSDT", side=side, entry=entry,
                  stop=entry * 0.97, tp1=entry * 1.03, tp2=entry * 1.12,
                  atr=entry * 0.01, reason="test", meta={"adx": 30.0})


def test_post_only_hemen_pozisyon_acmaz(tmp_path):
    """Limit emri tahtaya yazilir; pozisyon HENUZ yoktur."""
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10)
    assert broker.open_position(_sig(100.0), qty=1.0, leverage=3) is None
    assert broker.positions() == {}
    assert broker.pending_entries() == {"BNBUSDT": LONG}


def test_post_only_fiyat_gelince_maker_komisyonuyla_dolar(tmp_path):
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10, slippage_bps=20.0)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    limit = broker._pending["BNBUSDT"]["limit"]
    assert limit < 100.0, "long limiti fiyatin ALTINA konmali"

    # fiyat limite inmedi -> dolmaz
    assert broker.poll_pending() == []
    assert broker.positions() == {}

    # fiyat limite iniyor -> dolar
    market.all = make_candles([100.0] * 9 + [limit * 0.999], step_ms=14_400_000)
    market.cursor = len(market.all)
    opened = broker.poll_pending()
    assert len(opened) == 1
    pos = opened[0]
    assert pos.entry_price == pytest.approx(limit)
    # maker komisyonu odenmis olmali, taker degil
    assert pos.fees_paid == pytest.approx(limit * 1.0 * cfg.execution.maker_fee)
    assert broker.pending_entries() == {}


def test_sure_dolunca_markete_dusulur(tmp_path):
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10,
                                     post_only_fallback_market=True)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    broker._pending["BNBUSDT"]["deadline_ms"] = 0   # suresi gecmis say
    opened = broker.poll_pending()
    assert len(opened) == 1
    # market girisi -> taker komisyonu
    assert opened[0].fees_paid > 1.0 * 100.0 * cfg.execution.maker_fee


def test_sure_dolunca_vazgecilebilir(tmp_path):
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10,
                                     post_only_fallback_market=False)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    broker._pending["BNBUSDT"]["deadline_ms"] = 0
    assert broker.poll_pending() == []
    assert broker.positions() == {}
    assert broker.pending_entries() == {}


def test_short_limiti_fiyatin_USTUNE_konur(tmp_path):
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10, slippage_bps=20.0)
    broker.open_position(_sig(100.0, side=SHORT), qty=1.0, leverage=3)
    assert broker._pending["BNBUSDT"]["limit"] > 100.0


def test_bekleyen_emir_yeniden_baslatmada_kaybolmaz(tmp_path):
    """Restart'ta bekleyen emir unutulursa borsada sahipsiz limit kalir."""
    cfg, market, broker, store = _broker(tmp_path, [100.0] * 10)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    yeni = PaperBroker(cfg, market, store)
    assert yeni.pending_entries() == {"BNBUSDT": LONG}


def test_ayni_sembole_ikinci_emir_gitmez(tmp_path):
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    assert len(broker._pending) == 1


# ------------------------------------------------------------------ /dur kapisi
def test_paused_girisi_engeller():
    cfg = Config()
    st = RiskState(day_start_equity=1000.0, paused=True)
    guard = RiskGuard(cfg, st)
    ok, why = guard.can_open(int(time.time() * 1000), 0, 1000.0)
    assert not ok and "elle durduruldu" in why


def test_paused_gun_degisiminde_SIFIRLANMAZ():
    """Patron durdurduysa gun donunce kendiliginden acilmaz."""
    cfg = Config()
    st = RiskState(day="2020-01-01", paused=True, halted=True,
                   halt_reason="gunluk limit", day_start_equity=1000.0)
    guard = RiskGuard(cfg, st)
    guard.roll_day(int(time.time() * 1000), 1000.0)
    assert st.halted is False, "gunluk limit gun donunce kalkmali"
    assert st.paused is True, "elle durdurma gun donunce KALKMAMALI"


# ------------------------------------------------------------------- komutlar
def test_yikici_komut_tek_mesajla_calismaz():
    r = CommandRouter()
    calls = []
    r.register("kapat", lambda: calls.append(1) or "kapatildi", confirm=True)
    out = r.dispatch("/kapat", 1000)
    assert calls == [], "onaysiz calismamali"
    assert "onayla" in out.lower()
    assert r.dispatch("/onayla", 2000) == "kapatildi"
    assert calls == [1]


def test_onay_suresi_dolarsa_calismaz():
    r = CommandRouter()
    calls = []
    r.register("kapat", lambda: calls.append(1) or "ok", confirm=True)
    r.dispatch("/kapat", 0)
    out = r.dispatch("/onayla", CommandRouter.CONFIRM_WINDOW_MS + 1)
    assert calls == []
    assert "sure" in out.lower()


def test_iptal_bekleyen_onayi_siler():
    r = CommandRouter()
    calls = []
    r.register("kapat", lambda: calls.append(1) or "ok", confirm=True)
    r.dispatch("/kapat", 0)
    r.dispatch("/iptal", 10)
    assert "yok" in r.dispatch("/onayla", 20).lower()
    assert calls == []


def test_bilinmeyen_komut_None_doner():
    r = CommandRouter()
    r.register("durum", lambda: "ok")
    assert r.dispatch("/hicbirsey", 0) is None


def test_takma_adlar_ve_grup_soneki_calisir():
    r = CommandRouter()
    r.register("dur", lambda: "durdu", aliases=("durdur", "stop"))
    assert r.dispatch("/durdur", 0) == "durdu"
    assert r.dispatch("/stop", 0) == "durdu"
    assert r.dispatch("/dur@edithbot", 0) == "durdu"
    assert r.dispatch("DUR", 0) == "durdu"


# ------------------------------------------------------- Telegram guvenligi
class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _updates(*msgs):
    return {"ok": True, "result": [
        {"update_id": i + 1, "message": m} for i, m in enumerate(msgs)]}


def test_yabanci_sohbetten_gelen_komut_YOK_SAYILIR(monkeypatch):
    """Token sizarsa tek savunma bu. Kirilirsa biri hesabi bosaltabilir."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    n = Notifier()
    payload = _updates(
        {"chat": {"id": 999}, "text": "/kapat"},     # saldirgan
        {"chat": {"id": 111}, "text": "/durum"},     # sahibi
    )
    monkeypatch.setattr("bot.notify.requests.get",
                        lambda *a, **k: _FakeResp(payload))
    assert n.poll_commands() == ["/durum"]


def test_ayni_komut_iki_kez_islenmez(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    store = Store(str(tmp_path / "o.db"))
    n = Notifier(store)
    payload = _updates({"chat": {"id": 111}, "text": "/kapat"})
    monkeypatch.setattr("bot.notify.requests.get",
                        lambda *a, **k: _FakeResp(payload))
    assert n.poll_commands() == ["/kapat"]
    # offset kalici: yeni ornek ayni guncellemeyi tekrar istemez
    n2 = Notifier(store)
    assert n2._offset == 2


def test_token_yoksa_sessizce_devre_disi(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    n = Notifier()
    assert n.enabled is False
    assert n.poll_commands() == []
    n.send("bir sey")  # patlamamali


def test_ag_hatasi_botu_durdurmaz(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    n = Notifier()

    def patla(*a, **k):
        raise OSError("ag yok")

    monkeypatch.setattr("bot.notify.requests.get", patla)
    assert n.poll_commands() == []


# ============================================================ denetim bulgulari
def test_acil_kapatma_bekleyen_emirleri_de_IPTAL_eder(tmp_path):
    """Pozisyonlari kapatip tahtadaki limiti birakmak, kullanici 'her sey
    kapandi' sanirken dakikalar sonra yeni pozisyon acilmasi demektir."""
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10)
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    assert broker.pending_entries() != {}
    assert broker.cancel_pending() == 1
    assert broker.pending_entries() == {}
    # iptal kalici olmali: yeniden baslatmada geri gelmemeli
    yeni = PaperBroker(cfg, market, broker.store)
    assert yeni.pending_entries() == {}


def test_cancel_pending_bos_durumda_sifir_doner(tmp_path):
    _cfg, _m, broker, _ = _broker(tmp_path, [100.0] * 10)
    assert broker.cancel_pending() == 0


def test_realized_equity_kagit_kari_SAYMAZ(tmp_path):
    """Cirpinan taban bunun uzerinden hesaplanir: acik pozisyonun anlik
    kari zirve sayilirsa, hic bankaya girmemis paraya gore taban kilitlenir."""
    cfg, market, broker, _ = _broker(tmp_path, [100.0] * 10,
                                     entry_order_type="market")
    baslangic = broker.realized_equity()
    broker.open_position(_sig(100.0), qty=1.0, leverage=3)
    # fiyat lehte hareket etti -> equity artar, realized_equity artmaz
    market.all = make_candles([100.0] * 9 + [130.0], step_ms=14_400_000)
    market.cursor = len(market.all)
    assert broker.equity() > broker.realized_equity()
    # komisyon disinda gerceklesmis bakiye degismemis olmali
    assert broker.realized_equity() < baslangic          # sadece komisyon dustu
    assert baslangic - broker.realized_equity() < 1.0
