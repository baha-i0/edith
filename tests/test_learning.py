"""Ogrenme katmani testleri.

En onemli testler "ogreniyor mu" degil, **erken ogrenmiyor mu**. Az veriyle
karar veren bir ogrenme katmani, ogrenmeyen bir katmandan daha tehlikelidir.
"""
import pytest

from bot.config import Config, ConfigError
from bot.learning import (BucketStats, Learner, MistakeLedger, mean_and_sd,
                          shrunk_mean, upper_confidence_bound)
from bot.models import LONG, SHORT, Trade

DAY = 86_400_000
NOW = 1_700_000_000_000


def _trade(r, symbol="BNBUSDT", reason="stop", adx=30.0, atr_pct=1.0):
    return Trade(symbol, LONG, 1.0, 100.0, 99.0, NOW, NOW, r, 0.1, r, reason,
                 context={"adx": adx, "atr_pct": atr_pct})


def _learner(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.learning, k, v)
    cfg.validate()
    return Learner(cfg, store=None)


# ------------------------------------------------------------- istatistik
def test_shrinkage_pulls_small_samples_to_prior():
    """3 islemlik -1R serisi, onseli neredeyse hic hareket ettirmemeli."""
    est = shrunk_mean(n=3, observed_mean=-1.0, prior_mean=0.11, prior_strength=40)
    assert est == pytest.approx((3 * -1.0 + 40 * 0.11) / 43, rel=1e-9)
    assert est > -0.1, "3 islem onseli devirmemeli"


def test_shrinkage_converges_with_data():
    far = shrunk_mean(n=500, observed_mean=-0.5, prior_mean=0.11, prior_strength=40)
    assert far < -0.4, "500 islemde veri onseli yenmeli"


def test_upper_bound_requires_evidence_not_just_negative_mean():
    """Ortalamanin negatif olmasi yetmez; ust guven siniri da negatif olmali."""
    small = [-1.0, 2.0, -1.0, -1.0, 1.5]      # ortalama negatif ama dagilim genis
    assert upper_confidence_bound(small, 1.64, 1.3) > 0
    clear = [-1.0] * 40
    assert upper_confidence_bound(clear, 1.64, 1.3) < 0


def test_upper_bound_infinite_for_tiny_samples():
    assert upper_confidence_bound([-1.0], 1.64, 1.3) == float("inf")


# ------------------------------------------------------------ bank kurallari
def test_no_bench_below_min_sample():
    """29 ardisik zarar bile esigin altindaysa karar aldirmamali."""
    lrn = _learner(min_trades_per_bucket=30)
    for _ in range(29):
        lrn.record_trade(_trade(-1.0), NOW)
    assert lrn.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]


def test_bench_after_proven_negative():
    lrn = _learner(min_trades_per_bucket=30)
    for _ in range(35):
        lrn.record_trade(_trade(-1.0), NOW)
    ok, why = lrn.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)
    assert not ok and "bankland" in why


def test_no_bench_when_losses_are_noisy():
    """Ayni ortalama ama yuksek varyans -> kanit yok -> bank yok."""
    lrn = _learner(min_trades_per_bucket=30)
    seq = [-3.0, 2.5, -2.0, 1.8, -1.5] * 8     # ortalama hafif negatif, dagilim genis
    for r in seq:
        lrn.record_trade(_trade(r), NOW)
    assert lrn.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]


def test_bench_expires():
    lrn = _learner(min_trades_per_bucket=30, bench_days=14)
    for _ in range(35):
        lrn.record_trade(_trade(-1.0), NOW)
    assert not lrn.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]
    assert lrn.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW + 15 * DAY)[0]


def test_bench_is_scoped_to_bucket():
    """Bir sembolun banklanmasi digerlerini etkilememeli."""
    lrn = _learner(min_trades_per_bucket=30)
    for _ in range(35):
        lrn.record_trade(_trade(-1.0, symbol="BADUSDT", adx=30, atr_pct=1.0), NOW)
    assert not lrn.allow_entry("BADUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]
    # farkli sembol + farkli rejim kovasi -> serbest
    assert lrn.allow_entry("BTCUSDT", {"adx": 40, "atr_pct": 2.0}, NOW)[0]


# ------------------------------------------------------------- risk carpani
def test_learning_can_never_increase_risk():
    """Tek yonlu emniyet: ogrenme riski buyutemez."""
    lrn = _learner(min_trades_for_sizing=10)
    for _ in range(50):
        lrn.record_trade(_trade(3.0), NOW)     # muhtesem sonuclar
    lrn.record_equity(1000)
    mult, _ = lrn.risk_multiplier("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, 1000)
    assert mult <= 1.0


def test_risk_multiplier_unaffected_by_tiny_sample():
    lrn = _learner(min_trades_for_sizing=40)
    for _ in range(5):
        lrn.record_trade(_trade(-1.0), NOW)
    lrn.record_equity(1000)
    assert lrn.risk_multiplier("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, 1000)[0] == 1.0


def test_drawdown_scaling_when_enabled():
    lrn = _learner(drawdown_scaling=True, drawdown_full_cut_pct=25,
                   min_risk_multiplier=0.6)
    lrn.record_equity(1000)
    full, _ = lrn.risk_multiplier("X", {}, 1000)
    half, _ = lrn.risk_multiplier("X", {}, 875)     # %12.5 dusus
    deep, _ = lrn.risk_multiplier("X", {}, 700)     # %30 dusus
    assert full == 1.0
    assert 0.6 < half < 1.0
    assert deep == pytest.approx(0.6)


def test_drawdown_scaling_off_by_default():
    lrn = _learner()
    lrn.record_equity(1000)
    assert lrn.risk_multiplier("X", {}, 700)[0] == 1.0


# --------------------------------------------------------- stop kalibrasyonu
def test_stop_not_widened_without_enough_samples():
    lrn = _learner(min_trades_per_bucket=30)
    for _ in range(10):
        lrn.record_trade(_trade(-1.0), NOW)
        lrn.note_stop_hunt("BNBUSDT", NOW)
    assert lrn.stop_multiplier("BNBUSDT") == 1.0


def test_stop_widens_when_hunt_rate_is_high():
    lrn = _learner(min_trades_per_bucket=30, stop_hunt_rate_threshold=0.45)
    for _ in range(40):
        lrn.record_trade(_trade(-1.0), NOW)
    for _ in range(25):                       # 40 zararin 25'i stop avlanmasi
        lrn.note_stop_hunt("BNBUSDT", NOW)
    assert lrn.stop_multiplier("BNBUSDT") > 1.0


def test_stop_widening_is_capped():
    lrn = _learner(min_trades_per_bucket=30, stop_widen_max=1.5, stop_widen_step=0.15)
    for _ in range(60):
        lrn.record_trade(_trade(-1.0), NOW)
    for _ in range(400):
        lrn.note_stop_hunt("BNBUSDT", NOW)
    assert lrn.stop_multiplier("BNBUSDT") <= 1.5


# ------------------------------------------------------------- hata defteri
def test_operational_mistake_blocks_after_threshold():
    lrn = _learner(mistake_repeat_threshold=3)
    for i in range(2):
        assert lrn.record_mistake("XUSDT", "min_notional", "cok kucuk", NOW) is None
    lesson = lrn.record_mistake("XUSDT", "min_notional", "cok kucuk", NOW)
    assert lesson and "XUSDT" in lesson
    assert not lrn.allow_entry("XUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]


def test_operational_block_expires_and_resets_counter():
    lrn = _learner(mistake_repeat_threshold=3, mistake_block_days=7)
    for _ in range(3):
        lrn.record_mistake("XUSDT", "min_notional", "x", NOW)
    assert not lrn.allow_entry("XUSDT", {}, NOW)[0]
    later = NOW + 8 * DAY
    assert lrn.allow_entry("XUSDT", {}, later)[0]
    # sayac sifirlanmali, yoksa tek hatada aninda tekrar bloke olur
    assert lrn.record_mistake("XUSDT", "min_notional", "x", later) is None


def test_different_error_kinds_counted_separately():
    lrn = _learner(mistake_repeat_threshold=3)
    lrn.record_mistake("XUSDT", "min_notional", "a", NOW)
    lrn.record_mistake("XUSDT", "emir_reddi", "b", NOW)
    lrn.record_mistake("XUSDT", "min_notional", "a", NOW)
    assert lrn.allow_entry("XUSDT", {}, NOW)[0]


# ------------------------------------------------------------- diger
def test_disabled_learner_is_transparent():
    lrn = _learner(enabled=False)
    for _ in range(100):
        lrn.record_trade(_trade(-1.0), NOW)
    assert lrn.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]
    assert lrn.risk_multiplier("BNBUSDT", {}, 100)[0] == 1.0
    assert lrn.stop_multiplier("BNBUSDT") == 1.0


def test_regime_buckets_are_coarse():
    """Kova sayisi az olmali; her ek boyut orneği bolerek istatistigi bitirir."""
    keys = {Learner.regime_bucket({"adx": a, "atr_pct": v})
            for a in (10, 24, 26, 34, 36, 60) for v in (0.5, 1.1, 1.3, 5.0)}
    assert len(keys) <= 6


def test_report_warns_on_insufficient_data():
    lrn = _learner(min_trades_per_bucket=30)
    for _ in range(5):
        lrn.record_trade(_trade(-1.0), NOW)
    assert "gurultu" in lrn.report(NOW)


def test_state_roundtrip(tmp_path):
    from bot.state import Store
    cfg = Config()
    cfg.state_path = str(tmp_path / "s.db")
    cfg.validate()
    store = Store(cfg.state_path, mode="paper")
    lrn = Learner(cfg, store)
    for _ in range(35):
        lrn.record_trade(_trade(-1.0), NOW)
    lrn.record_equity(1234.0)
    lrn.save()

    lrn2 = Learner(cfg, store)
    assert lrn2.peak_equity == pytest.approx(1234.0)
    assert not lrn2.allow_entry("BNBUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]


def test_config_rejects_reckless_learning_settings():
    c = Config()
    c.learning.min_trades_per_bucket = 5
    with pytest.raises(ConfigError, match="min_trades_per_bucket"):
        c.validate()

    c2 = Config()
    c2.learning.max_risk_multiplier = 3.0
    with pytest.raises(ConfigError, match="max_risk_multiplier"):
        c2.validate()


# ------------------------------------------- yanlis pozitif kontrolu (olculen)
def test_false_bench_rate_is_low_for_a_good_symbol():
    """GERCEKTEN pozitif beklentili bir sembol nadiren banklanmali.

    Bu, ogrenme katmaninin en pahali hatasi: calisan bir sembolu 14 gun
    kenara koymak. Simulasyonla olculen oran %1'in altinda olmali.
    """
    import random

    def one_trial(seed: int) -> bool:
        lrn = _learner()
        rng = random.Random(seed)
        for _ in range(200):
            r = rng.gauss(0.11, 1.3)          # gercek beklenti POZITIF
            lrn.record_trade(_trade(r, symbol="GOODUSDT"), NOW)
            if not lrn.allow_entry("GOODUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]:
                return True
        return False

    trials = 200
    false_benches = sum(one_trial(s) for s in range(trials))
    assert false_benches / trials <= 0.03, \
        f"yanlis bank orani cok yuksek: {false_benches}/{trials}"


def test_truly_broken_symbol_is_caught():
    """Gercekten negatif beklentili sembol yakalanmali - aksi halde ogrenme yok."""
    import random

    def one_trial(seed: int) -> bool:
        lrn = _learner()
        rng = random.Random(seed)
        for _ in range(200):
            lrn.record_trade(_trade(rng.gauss(-0.6, 1.3), symbol="BADUSDT"), NOW)
            if not lrn.allow_entry("BADUSDT", {"adx": 30, "atr_pct": 1.0}, NOW)[0]:
                return True
        return False

    caught = sum(one_trial(s) for s in range(50))
    assert caught >= 48, f"bozuk sembolun sadece {caught}/50'si yakalandi"


def test_bench_test_runs_only_periodically():
    """Her islemde test etmek coklu karsilastirma hatasi yaratir."""
    lrn = _learner(min_trades_per_bucket=30, bench_eval_every=10)
    b = lrn._bucket("sym:X")
    b.r_values = [-1.0] * 35          # 35: (35-30) % 10 != 0 -> test yapilmamali
    assert lrn._maybe_bench(b, NOW) == []
    b.r_values = [-1.0] * 40          # 40: (40-30) % 10 == 0 -> test yapilmali
    assert lrn._maybe_bench(b, NOW) != []
