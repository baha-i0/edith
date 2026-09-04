"""Canli broker mutabakati ve gercek PnL hesabi.

Bu kod yolu gercek parayla calisir ve borsayla konusur; testler sahte bir
Binance istemcisiyle senaryolari suruyor.
"""
import time

import pytest

from bot.config import Config
from bot.exchange.binance import BinanceError
from bot.exchange.live import LiveBroker
from bot.models import LONG, Position, SymbolFilters
from bot.state import Store

# Gercekci zaman: pozisyonlar gercek zamanda kapanir. Sabit bir gecmis
# damga kullanmak, userTrades'in 6 gunluk penceresiyle catisirdi.
T0 = int(time.time() * 1000) - 3_600_000


class SahteBinance:
    def __init__(self):
        self.pozisyonlar = []
        self.fills = []
        self.patlat = False
        self.emirler = []
        # varsayilan: koruma emri borsada duruyor
        self.acik_emirler = [{"type": "STOP_MARKET"}]

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

    def open_orders(self, sym):
        return self.acik_emirler

    def stop_market(self, sym, side, stop_price, client_id=""):
        self.emirler.append(("stop", sym, side, stop_price, client_id))
        return {}

    def take_profit_market(self, sym, side, stop_price, qty=None, client_id=""):
        self.emirler.append(("tp", sym, side, stop_price, qty, client_id))
        return {}

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


# ============================================ ikinci denetim: korektlik bulgulari
def test_TP1_dolduysa_yeniden_konmaz(tmp_path):
    """Fiyat TP1'in otesindeyken emri yeniden koymak ya borsa reddiyle
    pozisyonu piyasadan kapattirir ya da kalan kosucuyu de satar. Iki
    halde de 4R hedefine giden kisim olur."""
    cfg, c, b, store = _kur(tmp_path)
    p = _poz(qty=1.0)
    p.tp1_filled = True                       # TP1 zaten dolmus
    b._positions["BNBUSDT"] = p
    c.emirler.clear()
    b._place_protection(p, "SELL", c.filters("BNBUSDT"))
    tipler = [e for e in c.emirler]
    # sadece stop + tp2 konmali, tp1 KONMAMALI
    assert not any("t1" in str(e) for e in tipler)


def test_update_stop_kapanan_islemi_GERI_DONER(tmp_path):
    """Koruma kurulamayip pozisyon kapatilirsa o islem deftere gecmeli;
    yoksa gunluk zarar limiti ve ust uste zarar sayaci kaybi hic gormez."""
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz(qty=1.0)
    c.fills = [{"symbol": "BNBUSDT", "realizedPnl": "-30.0", "commission": "0.3",
                "price": "580", "time": T0 + 10_000}]

    def patla(*a, **k):
        raise BinanceError(-2021, "would immediately trigger")

    c.stop_market = patla
    trade = b.update_stop("BNBUSDT", 600.0)
    assert trade is not None, "kapanan islem yutuldu"
    assert trade.pnl == pytest.approx(-30.3)


def test_trade_context_canlida_TASINIR(tmp_path):
    """Ogrenme katmani rejim kovasini trade.context'ten okur. Bos kalirsa
    her islem adx=0/atr=0 kabul edilip tek kovaya dolar."""
    cfg, c, b, store = _kur(tmp_path)
    p = _poz(qty=1.0)
    p.context = {"adx": 31.2, "atr_pct": 1.4}
    b._positions["BNBUSDT"] = p
    c.fills = [{"symbol": "BNBUSDT", "realizedPnl": "20.0", "commission": "0.2",
                "price": "620", "time": T0 + 10_000}]
    t = b._finalize(p, "tp2")
    assert t.context.get("adx") == pytest.approx(31.2)
    assert t.context.get("atr_pct") == pytest.approx(1.4)


def test_borsada_stop_YOKSA_yeniden_kurulur(tmp_path):
    """Tum guvenlik tasarimi 'koruma emri borsada durur' varsayimina
    dayaniyordu ama hicbir yerde dogrulanmiyordu. Kullanici Binance
    uygulamasindan stop'u iptal ederse reconcile bunu fark etmiyordu --
    positionAmt degismedigi icin. Kaldiracli pozisyon 33 gune kadar
    korumasiz tasinabiliyordu."""
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz(qty=2.0)
    c.pozisyonlar = [{"symbol": "BNBUSDT", "positionAmt": "2.0"}]
    c.acik_emirler = []                    # kullanici stop'u iptal etti
    c.emirler.clear()
    b.reconcile()
    assert any(e[0] == "stop" for e in c.emirler), "koruma yeniden kurulmadi"
    assert "BNBUSDT" in b.positions(), "pozisyon gereksiz yere kapatildi"


def test_koruma_duruyorsa_gereksiz_emir_gonderilmez(tmp_path):
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz(qty=2.0)
    c.pozisyonlar = [{"symbol": "BNBUSDT", "positionAmt": "2.0"}]
    c.emirler.clear()
    b.reconcile()
    assert not c.emirler, f"koruma dururken emir gonderildi: {c.emirler}"


def test_koruma_kurulamazsa_pozisyon_kapanir_ve_islem_DONER(tmp_path):
    cfg, c, b, store = _kur(tmp_path)
    b._positions["BNBUSDT"] = _poz(qty=1.0)
    c.pozisyonlar = [{"symbol": "BNBUSDT", "positionAmt": "1.0"}]
    c.acik_emirler = []
    c.fills = [{"symbol": "BNBUSDT", "realizedPnl": "-15.0", "commission": "0.2",
                "price": "585", "time": T0 + 10_000}]

    def patla(*a, **k):
        raise BinanceError(-2021, "reddedildi")

    c.stop_market = patla
    trades = b.reconcile()
    assert len(trades) == 1, "korumasiz kapanan islem deftere gecmedi"
    assert trades[0].pnl == pytest.approx(-15.2)


def test_PnL_okunamazsa_ZARAR_sifir_yazilmaz(tmp_path):
    """Eski davranis: userTrades patlayinca realized = pos.realized_pnl,
    ki canlida hep 0'di. Gercek bir stop zarari pnl=0 kaydediliyordu ->
    gunluk zarar limiti kaybi gormuyor, 'pnl < 0' yanlis oldugu icin ust
    uste zarar sayaci SIFIRLANIYOR ve soguma hic tetiklenmiyordu."""
    cfg, c, b, store = _kur(tmp_path)
    p = _poz(qty=1.0)                     # giris 600, stop 580
    b._positions["BNBUSDT"] = p

    def patla(*a, **k):
        raise BinanceError(-1003, "rate limit")

    c.user_trades = patla
    t = b._finalize(p, "borsada-kapandi", fallback_exit=p.stop)
    assert t.pnl < 0, f"gercek zarar {t.pnl} olarak kaydedildi"
    assert t.pnl == pytest.approx(-20.0)
    assert "tahmini" in t.exit_reason
