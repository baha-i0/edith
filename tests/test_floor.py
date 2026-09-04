"""Sermaye tabani (CPPI) testleri.

Tabanin tek isi var: strateji bozuldugunda hesabin tamamini yemesini
engellemek. Testler o isi yaptigini kanitliyor -- ve tabanin ASLA
dusmedigini, cunku dusen bir taban taban degildir.
"""
import pytest

from bot.config import Config, ConfigError
from bot.models import LONG, Position, SymbolFilters
from bot.risk import (RiskState, effective_floor, open_risk_total, risk_base,
                      size_position, update_floor)


@pytest.fixture
def f():
    return SymbolFilters("BNBUSDT", 0.01, 0.001, 0.001, 5.0)


def _cfg(**kw):
    c = Config()
    for k, v in kw.items():
        setattr(c.risk, k, v)
    return c


# ------------------------------------------------------------ risk tabani
def test_taban_yoksa_risk_tum_bakiye_uzerinden():
    r = _cfg().risk
    assert risk_base(300.0, r) == 300.0


def test_taban_varsa_risk_SADECE_yastik_uzerinden():
    r = _cfg(capital_floor_usdt=170.0).risk
    assert risk_base(300.0, r) == 130.0


def test_bakiye_dustukce_yastik_kucululur():
    """Tabanin butun fikri bu: dususte pozisyonlar kendiliginden kucululur."""
    r = _cfg(capital_floor_usdt=170.0).risk
    assert risk_base(300.0, r) == 130.0
    assert risk_base(250.0, r) == 80.0
    assert risk_base(200.0, r) == 30.0
    assert risk_base(170.0, r) == 0.0
    assert risk_base(150.0, r) == 0.0   # negatif olmaz


# -------------------------------------------------------- cirpinan taban
def test_taban_zirveyle_yukselir():
    c = _cfg(capital_floor_usdt=170.0, capital_floor_ratchet_pct=70.0)
    st = RiskState()
    update_floor(c.risk, st, 300.0)
    assert st.floor_usdt == pytest.approx(210.0)   # 300 * %70
    update_floor(c.risk, st, 5000.0)
    assert st.floor_usdt == pytest.approx(3500.0)


def test_taban_ASLA_dusmez():
    """Kilitlenen kar kilitli kalir. Dusen taban taban degildir."""
    c = _cfg(capital_floor_usdt=170.0, capital_floor_ratchet_pct=70.0)
    st = RiskState()
    update_floor(c.risk, st, 1000.0)
    assert st.floor_usdt == pytest.approx(700.0)
    update_floor(c.risk, st, 400.0)          # bakiye cakildi
    assert st.floor_usdt == pytest.approx(700.0), "taban dusmemeli"
    assert st.peak_equity == pytest.approx(1000.0)


def test_sabit_taban_ile_cirpinanin_BUYUGU_gecerli():
    c = _cfg(capital_floor_usdt=500.0, capital_floor_ratchet_pct=70.0)
    st = RiskState()
    update_floor(c.risk, st, 600.0)          # cirpinan 420 < sabit 500
    assert effective_floor(c.risk, st) == pytest.approx(500.0)
    update_floor(c.risk, st, 1000.0)         # cirpinan 700 > sabit 500
    assert effective_floor(c.risk, st) == pytest.approx(700.0)


# ------------------------------------------------------------- sizing
def test_taban_yaklasinca_pozisyon_kuculur(f):
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=4.0)
    boyutlar = []
    for eq in (300.0, 250.0, 220.0):
        s = size_position(eq, eq, 100.0, 97.0, f, c.risk, 5)
        assert s.ok, s.reason
        boyutlar.append(s.risk_amount)
    assert boyutlar[0] > boyutlar[1] > boyutlar[2]


def test_yastik_bitince_islem_ACILMAZ(f):
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=4.0,
             min_cushion_usdt=25.0)
    s = size_position(190.0, 190.0, 100.0, 97.0, f, c.risk, 5)
    assert not s.ok
    assert "yastik tukendi" in s.reason


def test_taban_altinda_islem_ACILMAZ(f):
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=4.0)
    s = size_position(150.0, 150.0, 100.0, 97.0, f, c.risk, 5)
    assert not s.ok


def test_toplam_acik_risk_tavani(f):
    """Tek islem tabani delemez ama 4 islem birden delebilir."""
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=6.0,
             max_total_risk_pct_of_cushion=30.0)
    # yastik 130 -> toplam tavan 39 USDT
    s = size_position(300.0, 300.0, 100.0, 97.0, f, c.risk, 5, open_risk=0.0)
    assert s.ok and s.risk_amount == pytest.approx(7.8, rel=0.02)   # 130 * %6

    # 35 USDT zaten riskteyse yeni islem 4 USDT'ye kirpilir
    s2 = size_position(300.0, 300.0, 100.0, 97.0, f, c.risk, 5, open_risk=35.0)
    assert s2.ok and s2.risk_amount <= 4.05

    # tavan doluysa hic acilmaz
    s3 = size_position(300.0, 300.0, 100.0, 97.0, f, c.risk, 5, open_risk=39.0)
    assert not s3.ok and "risk tavani dolu" in s3.reason


def test_state_ile_cirpinan_taban_sizing_e_yansir(f):
    c = _cfg(capital_floor_usdt=170.0, capital_floor_ratchet_pct=70.0,
             risk_per_trade_pct=4.0)
    st = RiskState()
    update_floor(c.risk, st, 1000.0)     # taban 700'e cikti
    # bakiye 800'e dustu -> yastik 100, taban 170 degil 700 uzerinden
    s = size_position(800.0, 800.0, 100.0, 97.0, f, c.risk, 5, state=st)
    assert s.ok
    assert s.risk_amount == pytest.approx(4.0, rel=0.02)   # 100 * %4


# ---------------------------------------------------------- acik risk
def _pos(entry, stop, qty, be=False):
    p = Position(symbol="X", side=LONG, qty=qty, entry_price=entry, stop=stop,
                 tp1=entry * 1.02, tp2=entry * 1.05,
                 initial_risk_per_unit=abs(entry - stop), opened_at=0,
                 leverage=5, initial_qty=qty)
    p.breakeven_moved = be
    return p


def test_acik_risk_toplami():
    assert open_risk_total([_pos(100, 97, 2), _pos(50, 48, 5)]) == pytest.approx(16.0)


def test_breakeven_e_cekilen_pozisyon_risk_SAYILMAZ():
    """Stop girise cekildiyse o pozisyon artik para riske atmiyor."""
    assert open_risk_total([_pos(100, 100, 2, be=True)]) == 0.0
    assert open_risk_total([_pos(100, 97, 2, be=True), _pos(50, 48, 5)]) \
        == pytest.approx(10.0)


# ------------------------------------------------------------ dogrulama
def test_tabansiz_yuksek_risk_reddedilir():
    c = _cfg(risk_per_trade_pct=6.0)
    with pytest.raises(ConfigError):
        c.validate()


def test_tabanla_yuksek_risk_kabul_edilir():
    _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=6.0).validate()
    _cfg(capital_floor_ratchet_pct=70.0, risk_per_trade_pct=6.0).validate()


def test_taban_varken_bile_sinirsiz_risk_yok():
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=25.0)
    with pytest.raises(ConfigError):
        c.validate()


def test_tek_islem_toplam_tavani_asamaz():
    """Asarsa hicbir islem acilmaz -- bot sessizce hicbir sey yapmaz."""
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=5.0,
             max_total_risk_pct_of_cushion=3.0)
    with pytest.raises(ConfigError) as e:
        c.validate()
    assert "hicbir islem acilmaz" in str(e.value)


def test_gecersiz_ratchet_reddedilir():
    with pytest.raises(ConfigError):
        _cfg(capital_floor_ratchet_pct=100.0).validate()
    with pytest.raises(ConfigError):
        _cfg(capital_floor_ratchet_pct=-5.0).validate()


# ============================================================ denetim bulgulari
# Asagidaki testler bagimsiz denetimde bulunan gercek hatalari sabitliyor.

def test_ogrenme_carpani_dogrulanan_tavani_ASAMAZ(f):
    """Config 'taban varken en fazla %6' diye soz veriyor. Ogrenme katmani
    risk_per_trade_pct'i carpabildigi icin runtime o sozu bozabiliyordu."""
    c = _cfg(capital_floor_usdt=170.0, risk_per_trade_pct=9.0)   # 6 x 1.5
    s = size_position(300.0, 300.0, 100.0, 97.0, f, c.risk, 5)
    assert s.ok
    assert s.risk_amount <= 130.0 * 0.06 * 1.05, "yastigin %6 tavani asildi"


def test_tabansiz_carpan_da_kirpilir(f):
    c = _cfg(risk_per_trade_pct=3.0)   # dogrulamadan gecmez ama runtime gorebilir
    s = size_position(1000.0, 1000.0, 100.0, 97.0, f, c.risk, 5)
    assert s.ok
    assert s.risk_amount <= 1000.0 * 0.02 * 1.05, "equity'nin %2 tavani asildi"


# ==================================================== ikinci denetim: nakit akisi
from bot.risk import apply_cash_flow


def test_kar_cekince_bot_KILITLENMEZ():
    """En olasi gercek senaryo: bot kar etti, kullanici karini cekti.

    Cirpinan taban 'asla dusmez' kuralindan dolayi taban eski zirvede
    kalirsa yastik sifirlanir ve bot KALICI olarak islem acmaz -- hesapta
    para dururken. Nakit akisi tespiti bunu onlemeli.
    """
    c = _cfg(capital_floor_ratchet_pct=70.0, risk_per_trade_pct=6.0)
    st = RiskState()
    apply_cash_flow(c.risk, st, 1000.0, 0.0)
    update_floor(c.risk, st, 1000.0)
    assert effective_floor(c.risk, st) == pytest.approx(700.0)

    # 500 cekildi: bakiye dustu ama islem kar/zarari DEGISMEDI
    akis = apply_cash_flow(c.risk, st, 500.0, 0.0)
    assert akis == pytest.approx(-500.0)
    assert effective_floor(c.risk, st) == pytest.approx(350.0)
    assert risk_base(500.0, c.risk, st) == pytest.approx(150.0)


def test_para_yatirinca_taban_yukselir():
    c = _cfg(capital_floor_ratchet_pct=70.0)
    st = RiskState()
    apply_cash_flow(c.risk, st, 300.0, 0.0)
    update_floor(c.risk, st, 300.0)
    akis = apply_cash_flow(c.risk, st, 800.0, 0.0)      # +500 yatirildi
    assert akis == pytest.approx(500.0)
    assert effective_floor(c.risk, st) == pytest.approx(560.0)   # 800 * %70


def test_GERCEK_zararda_taban_DUSMEZ():
    """Nakit akisi duzeltmesi 'asla dusmez' kuralini delmemeli."""
    c = _cfg(capital_floor_ratchet_pct=70.0)
    st = RiskState()
    apply_cash_flow(c.risk, st, 1000.0, 0.0)
    update_floor(c.risk, st, 1000.0)
    # 300 zarar: bakiye 700, kumulatif pnl -300 -> akis SIFIR olmali
    akis = apply_cash_flow(c.risk, st, 700.0, -300.0)
    assert akis == 0.0
    assert effective_floor(c.risk, st) == pytest.approx(700.0), "taban dusmemeli"


def test_kismi_cikis_para_yatirma_SANILMAZ():
    """Kismi TP1 cuzdani buyutur ama islem kaydi olusturmaz. Acik
    pozisyonun realized_pnl'i hesaba katilmazsa 'para yatirma' sanilir,
    tam kapanista da ayni tutar 'para cekme' sanilirdi."""
    c = _cfg(capital_floor_ratchet_pct=70.0)
    st = RiskState()
    apply_cash_flow(c.risk, st, 1000.0, 0.0)
    update_floor(c.risk, st, 1000.0)
    # kismi TP1: +80 cuzdana girdi, toplam_pnl de +80 (pos.realized_pnl dahil)
    assert apply_cash_flow(c.risk, st, 1080.0, 80.0) == 0.0
    # tam kapanis: +60 daha, trade kaydi toplam 140
    assert apply_cash_flow(c.risk, st, 1140.0, 140.0) == 0.0


def test_kucuk_farklar_nakit_akisi_SAYILMAZ():
    """Funding odemeleri ve yuvarlama farklari tabani oynatmamali."""
    c = _cfg(capital_floor_ratchet_pct=70.0)
    st = RiskState()
    apply_cash_flow(c.risk, st, 1000.0, 0.0)
    assert apply_cash_flow(c.risk, st, 1000.30, 0.0) == 0.0   # funding
    assert apply_cash_flow(c.risk, st, 999.50, 0.0) == 0.0


def test_ilk_calismada_akis_uretilmez():
    c = _cfg(capital_floor_ratchet_pct=70.0)
    st = RiskState()
    assert apply_cash_flow(c.risk, st, 300.0, 0.0) == 0.0
    assert st.last_equity_seen == pytest.approx(300.0)


def test_akis_referansi_yeniden_baslatmaya_dayanir(tmp_path):
    from bot.state import Store
    c = _cfg(capital_floor_ratchet_pct=70.0)
    store = Store(str(tmp_path / "f.db"), mode="paper")
    st = store.load_risk_state()
    apply_cash_flow(c.risk, st, 1000.0, 0.0)
    update_floor(c.risk, st, 1000.0)
    store.save_risk_state(st)

    st2 = store.load_risk_state()        # yeniden baslatma
    assert st2.last_equity_seen == pytest.approx(1000.0)
    assert st2.floor_usdt == pytest.approx(700.0)
    # restart sonrasi cekme hala dogru algilanmali
    assert apply_cash_flow(c.risk, st2, 500.0, 0.0) == pytest.approx(-500.0)
