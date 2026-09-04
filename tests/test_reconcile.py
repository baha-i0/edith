"""Canli broker mutabakati ve gercek PnL hesabi.

Bu kod yolu gercek parayla calisir ve borsayla konusur; testler sahte bir
Binance istemcisiyle senaryolari suruyor.
"""
import pytest

from bot.config import Config
from bot.exchange.binance import BinanceError
from bot.exchange.live import LiveBroker
from bot.models import LONG, Position, SymbolFilters
from bot.state import Store

T0 = 1_700_000_000_000


class SahteBinance:
    def __init__(self):
        self.pozisyonlar = []
        self.fills = []
        self.patlat = False
        self.emirler = []

    def sync_time(self):
        pass

    def filters(self, sym):
        return SymbolFilters(sym, 0.01, 0.001, 0.001, 5.0)

    def position_risk(self, symbol=None):
        if self.patlat:
            raise BinanceError(-1, "ag hatasi")
        return self.pozisyonlar

    def user_trades(self, symbol, start_ms, limit=500):
        return [f for f in self.fills if f["time"] >= start_ms]

    def cancel_all(self, sym):
        self.emirler.append(("cancel_all", sym))

    def cancel_order(self, sym, cid):
        self.emirler.append(("cancel", sym, cid))

    def market_order(self, sym, side, qty, reduce_only=False, client_id=""):
        self.emirler.append(("market", sym, side, qty))
        return {"avgPrice": "610", "executedQty": str(qty)}

    def balances(self):
        return {"equity": 300.0, "available": 300.0, "wallet": 300.0}


def _kur(tmp_path):
    cfg = Config()
    cfg.symbols = ["BNBUSDT"]
    cfg.validate()
    store = Store(str(tmp_path / "r.db"), mode="live")
    c = SahteBinance()
    return cfg, c, LiveBroker(cfg, c, store), store


def _poz(qty=2.0, opened_at=T0):
    return Position(symbol="BNBUSDT", side=LONG, qty=qty, entry_price=600.0,
                    stop=580.0, tp1=620.0, tp2=680.0, initial_risk_per_unit=20.0,
                    opened_at=opened_at, leverage=5, initial_qty=qty,
                    client_id="edith1")


# ------------------------------------------------------------- mutabakat
def test_borsada_kapanan_pozisyon_yakalanir(tmp_path):
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz()
    c.fills = [{"symbol": "BNBUSDT", "realizedPnl": "-40.0", "commission": "0.5",
                "price": "580", "time": T0 + 30_000}]
    trades = b.reconcile()
    assert len(trades) == 1
    assert trades[0].pnl == pytest.approx(-40.5)
    assert "BNBUSDT" not in b.positions()


def test_kismi_dolum_pozisyonu_KAPATMAZ(tmp_path):
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz(qty=2.0)
    c.pozisyonlar = [{"symbol": "BNBUSDT", "positionAmt": "1.0"}]
    assert b.reconcile() == []
    p = b.positions()["BNBUSDT"]
    assert p.qty == pytest.approx(1.0)
    assert p.tp1_filled
    assert store.load_positions()["BNBUSDT"].qty == pytest.approx(1.0)


def test_bota_ait_olmayan_pozisyona_DOKUNULMAZ(tmp_path):
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz()
    c.pozisyonlar = [{"symbol": "BNBUSDT", "positionAmt": "2.0"},
                     {"symbol": "ETHUSDT", "positionAmt": "5.0"}]
    once = len(c.emirler)
    b.reconcile()
    assert len(c.emirler) == once, "yabanci pozisyona emir gonderildi"
    assert "ETHUSDT" not in b.positions()


def test_api_patlarsa_pozisyon_silinmez(tmp_path):
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz()
    c.patlat = True
    assert b.reconcile() == []
    assert "BNBUSDT" in b.positions(), "ag hatasinda pozisyon kaybedildi"


# ------------------------------------------------------------ gercek PnL
def test_ardisik_iki_islem_PnL_CIFT_SAYILMAZ(tmp_path):
    """userTrades penceresi acilistan 60 sn geriye bakiyor (saat kaymasi
    icin). O pencere ayni sembolde onceki islemin fill'lerine uzanirsa
    onun PnL'i ikinci isleme de yazilir. Gercek para, yanlis istatistik."""
    cfg, c, b, store = _kur(tmp_path)

    b._positions["BNBUSDT"] = _poz(qty=1.0, opened_at=T0)
    c.fills = [{"symbol": "BNBUSDT", "realizedPnl": "50.0", "commission": "0.5",
                "price": "610", "time": T0 + 30_000}]
    t1 = b._finalize(b._positions["BNBUSDT"], "tp2")
    assert t1.pnl == pytest.approx(49.5)

    # ikinci islem 70 sn sonra -> 60 sn'lik pencere birinciyi kapsar
    T1 = T0 + 70_000
    c.fills.append({"symbol": "BNBUSDT", "realizedPnl": "-20.0",
                    "commission": "0.5", "price": "580", "time": T1 + 30_000})
    b._positions["BNBUSDT"] = _poz(qty=1.0, opened_at=T1)
    t2 = b._finalize(b._positions["BNBUSDT"], "stop")
    assert t2.pnl == pytest.approx(-20.5), \
        f"onceki islemin kari yutuldu: {t2.pnl:+.2f}"


def test_kismi_cikis_realized_pnl_biriktirir(tmp_path):
    """Kismi TP1 islem kaydi olusturmaz ama gerceklesen kar cuzdana girer.
    Pozisyonda takip edilmezse nakit akisi tespiti bunu para yatirma sanir."""
    cfg, c, b, store = _kur(tmp_path)
    p = _poz(qty=2.0)
    b._positions["BNBUSDT"] = p
    store.save_position(p)

    trade = b.close_position("BNBUSDT", 0.5, 610.0, "tp1")
    assert trade is None, "kismi cikis islem kaydi olusturmamali"
    assert p.realized_pnl == pytest.approx((610.0 - 600.0) * 1.0)
    assert p.qty == pytest.approx(1.0)
    assert p.tp1_filled
    assert store.load_positions()["BNBUSDT"].realized_pnl == pytest.approx(10.0)
