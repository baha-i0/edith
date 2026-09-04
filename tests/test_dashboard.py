"""Panel testleri.

Panel bir raporlama ozelligidir; botun para kazanmasina katkisi yok ama
BOZULMASI zarar verebilir: (a) panel cokerse bot da cokmemeli, (b) panel
yanlis sayi gosterirse yanlis karar verirsin, (c) panel yanlis adrese
acilirsa bakiyeni agdaki herkes gorur. Testler bu ucunu koruyor.
"""
import json
import os
import socket
import threading
import urllib.error
import urllib.request

import pytest

from bot.config import Config, ConfigError
from bot.dashboard import (DashboardServer, _downsample, _histogram, _streak,
                           build_state)
from bot.models import Trade
from bot.state import Store


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _store(tmp_path, trades=()):
    st = Store(str(tmp_path / "d.db"), mode="paper")
    for i, r in enumerate(trades):
        st.record_trade(Trade(
            symbol="BNBUSDT", side="LONG", qty=1.0, entry_price=100.0,
            exit_price=100.0 + r, opened_at=1000 + i, closed_at=2000 + i,
            pnl=r * 10.0, fees=0.1, r_multiple=r, exit_reason="tp2",
            entry_reason="test"))
        st.record_equity(200.0 + r * 10.0, 2000 + i)
    return st


# ------------------------------------------------------------- yardimcilar
def test_downsample_uc_ve_dip_noktalari_korur():
    """Seyreltme zirveyi silmemeli: dususu oldugundan kucuk gosterirdi."""
    pts = [(i, 100.0) for i in range(3000)]
    pts[1500] = (1500, 500.0)   # zirve
    pts[2500] = (2500, 10.0)    # dip
    out = _downsample(pts, target=300)
    vals = [v for _t, v in out]
    assert 500.0 in vals, "zirve kayboldu"
    assert 10.0 in vals, "dip kayboldu"
    assert len(out) <= 300 * 1.1


def test_downsample_kucuk_seriye_dokunmaz():
    pts = [(i, float(i)) for i in range(50)]
    assert _downsample(pts, target=400) == pts


def test_histogram_toplam_islem_sayisini_korur():
    vals = [-1.0, -1.0, 2.5, 4.0, 0.3, -0.7]
    bins = _histogram(vals)
    assert sum(b["n"] for b in bins) == len(vals)


def test_histogram_sinir_disi_degerleri_kenara_koyar():
    bins = _histogram([-99.0, 99.0])
    assert sum(b["n"] for b in bins) == 2
    assert bins[0]["n"] == 1 and bins[-1]["n"] == 1


def test_seri_hesabi():
    assert _streak([1.0, -1.0, 2.0, 3.0]) == 2      # 2 kazanc
    assert _streak([1.0, -1.0, -1.0]) == -2         # 2 kayip
    assert _streak([]) == 0


# ----------------------------------------------------------------- durum
def test_bos_veritabani_cokmez(tmp_path):
    cfg = Config()
    d = build_state(cfg, _store(tmp_path))
    assert d["stats"]["trades"] == 0
    assert d["equity"] == []
    json.dumps(d)  # JSON'a cevrilebilmeli


def test_istatistikler_dogru_hesaplanir(tmp_path):
    # 3 kazanc (+2R), 2 kayip (-1R) -> isabet %60, beklenti +0.8R
    store = _store(tmp_path, [2.0, 2.0, 2.0, -1.0, -1.0])
    d = build_state(Config(), store)
    s = d["stats"]
    assert s["trades"] == 5
    assert s["win_rate"] == pytest.approx(60.0)
    assert s["expectancy_r"] == pytest.approx(0.8)
    assert s["payoff"] == pytest.approx(2.0)
    # basabas isabet = 1/(1+R) -> 1/3
    assert s["breakeven_wr"] == pytest.approx(100 / 3, rel=1e-3)
    assert s["streak"] == -2


def test_dusus_hesabi_zirveden_olculur(tmp_path):
    store = Store(str(tmp_path / "e.db"), mode="paper")
    for ts, eq in [(1, 100.0), (2, 200.0), (3, 150.0), (4, 180.0)]:
        store.record_equity(eq, ts)
    d = build_state(Config(), store)
    # zirve 200 -> dip 150 = %25
    assert d["account"]["peak_drawdown_pct"] == pytest.approx(25.0)
    # su an 180 -> zirveden %10 asagida
    assert d["account"]["current_drawdown_pct"] == pytest.approx(10.0)


def test_sembol_kirilimi(tmp_path):
    store = Store(str(tmp_path / "f.db"), mode="paper")
    for sym, r in [("BTCUSDT", 3.0), ("BTCUSDT", -1.0), ("ETHUSDT", -1.0)]:
        store.record_trade(Trade(symbol=sym, side="LONG", qty=1.0, entry_price=1,
                                 exit_price=1, opened_at=1, closed_at=2, pnl=r,
                                 fees=0.0, r_multiple=r, exit_reason="tp2",
                                 entry_reason="t"))
    d = build_state(Config(), store)
    by = {x["symbol"]: x for x in d["symbols"]}
    assert by["BTCUSDT"]["n"] == 2
    assert by["BTCUSDT"]["avg_r"] == pytest.approx(1.0)
    assert by["ETHUSDT"]["win_rate"] == pytest.approx(0.0)
    # en iyi sembol basta olmali
    assert d["symbols"][0]["symbol"] == "BTCUSDT"


def test_durum_etiketleri(tmp_path):
    cfg = Config()
    store = _store(tmp_path)
    rs = store.load_risk_state()
    rs.paused = True
    store.save_risk_state(rs)
    assert build_state(cfg, store)["status"]["kind"] == "paused"

    rs.paused = False
    rs.shadow_mode = True
    store.save_risk_state(rs)
    assert build_state(cfg, store)["status"]["kind"] == "shadow"


# ---------------------------------------------------------------- sunucu
@pytest.fixture
def server(tmp_path):
    cfg = Config()
    cfg.dashboard.port = _free_port()
    store = _store(tmp_path, [2.0, -1.0, 1.0])
    srv = DashboardServer(cfg, lambda: build_state(cfg, store))
    assert srv.start()
    yield srv, f"http://127.0.0.1:{cfg.dashboard.port}"
    srv.stop()


def test_ana_sayfa_html_doner(server):
    _srv, url = server
    body = urllib.request.urlopen(url + "/").read().decode()
    assert "<title>EDITH</title>" in body


def test_sayfa_disaridan_kaynak_YUKLEMEZ(server):
    """Panel internetsiz calismali ve ucuncu taraf script yuklememeli."""
    _srv, url = server
    body = urllib.request.urlopen(url + "/").read().decode()
    for bad in ("cdn", "googleapis", "unpkg", "jsdelivr", "<script src", "<link "):
        assert bad not in body.lower(), f"panelde dis kaynak: {bad}"


def test_api_json_doner(server):
    _srv, url = server
    d = json.loads(urllib.request.urlopen(url + "/api/state").read())
    assert d["stats"]["trades"] == 3
    assert "equity" in d and "health" in d


def test_yazma_istekleri_REDDEDILIR(server):
    """Panelin kontrol ucu yok. Olsaydi rastgele bir web sitesi CSRF ile
    pozisyon kapatabilirdi."""
    _srv, url = server
    for method in ("POST", "PUT", "DELETE"):
        req = urllib.request.Request(url + "/api/state", data=b"{}", method=method)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 405


def test_bilinmeyen_yol_404(server):
    _srv, url = server
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/gizli")
    assert e.value.code == 404


def test_port_doluysa_bot_COKMEZ(tmp_path):
    """Panel acilamamasi islem yapmayi engellememeli."""
    cfg = Config()
    cfg.dashboard.port = _free_port()
    store = _store(tmp_path)
    a = DashboardServer(cfg, lambda: build_state(cfg, store))
    assert a.start()
    b = DashboardServer(cfg, lambda: build_state(cfg, store))
    assert b.start() is False   # patlamamali, sadece False donmeli
    a.stop()


def test_durum_uretimi_patlarsa_500_doner(tmp_path):
    cfg = Config()
    cfg.dashboard.port = _free_port()

    def bozuk():
        raise RuntimeError("bilerek")

    srv = DashboardServer(cfg, bozuk)
    assert srv.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{cfg.dashboard.port}/api/state")
        assert e.value.code == 500
    finally:
        srv.stop()


def test_kapali_panel_baslamaz(tmp_path):
    cfg = Config()
    cfg.dashboard.enabled = False
    store = _store(tmp_path)
    assert DashboardServer(cfg, lambda: build_state(cfg, store)).start() is False


# -------------------------------------------------------------- guvenlik
def test_localhost_disi_adres_token_ISTER(monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    cfg = Config()
    cfg.dashboard.host = "0.0.0.0"
    with pytest.raises(ConfigError) as e:
        cfg.validate()
    assert "DASHBOARD_TOKEN" in str(e.value)


def test_token_varsa_localhost_disi_adrese_izin_verilir(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "gizli")
    cfg = Config()
    cfg.dashboard.host = "0.0.0.0"
    cfg.validate()   # patlamamali


def test_token_varken_yanlis_token_reddedilir(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "dogru-token")
    cfg = Config()
    cfg.dashboard.port = _free_port()
    store = _store(tmp_path)
    srv = DashboardServer(cfg, lambda: build_state(cfg, store))
    assert srv.start()
    url = f"http://127.0.0.1:{cfg.dashboard.port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(url + "/")
        assert e.value.code == 401

        req = urllib.request.Request(url + "/",
                                     headers={"Authorization": "Bearer dogru-token"})
        assert b"EDITH" in urllib.request.urlopen(req).read()
    finally:
        srv.stop()


def test_gecersiz_port_reddedilir():
    cfg = Config()
    cfg.dashboard.port = 80
    with pytest.raises(ConfigError):
        cfg.validate()
