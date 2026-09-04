"""Saglik kontrolu testleri.

Kritik davranis: bot bozuldugunda DURMALI ve haber vermeli; kendini
yeniden ayarlamamali. Ve kotu bir seriyi bozulma sanmamali.
"""
import time

import pytest

from bot.config import Config, ConfigError
from bot.health import CRITICAL, INFO, WARN, run_health_checks
from bot.learning import Learner
from bot.models import LONG, Trade
from bot.state import Store

NOW = 1_700_000_000_000


def _setup(tmp_path, mode="paper"):
    cfg = Config()
    cfg.state_path = str(tmp_path / "h.db")
    cfg.validate()
    store = Store(cfg.state_path, mode=mode)
    return cfg, store


def _add_trades(store, rs, pnl_scale=1.0, fees=0.05):
    for i, r in enumerate(rs):
        store.record_trade(Trade("BTCUSDT", LONG, 1.0, 100.0, 100 + r,
                                 NOW + i, NOW + i + 1, r * pnl_scale, fees, r,
                                 "tp2" if r > 0 else "stop"))


def _find(rep, name):
    return next(c for c in rep.checks if c.name == name)


def test_fresh_install_is_healthy(tmp_path):
    cfg, store = _setup(tmp_path)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    assert rep.worst == INFO
    assert not rep.halt_required


def test_stale_bot_is_flagged_critical(tmp_path):
    cfg, store = _setup(tmp_path)
    store.record_equity(200.0, NOW)
    rep = run_health_checks(cfg, store, Learner(cfg, store),
                            now_ms=NOW + 12 * 3600_000)
    c = _find(rep, "Bot calisiyor mu")
    assert c.severity == CRITICAL
    assert "yeniden baslat" in c.action


def test_running_bot_is_ok(tmp_path):
    cfg, store = _setup(tmp_path)
    store.record_equity(200.0, NOW)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW + 60_000)
    assert _find(rep, "Bot calisiyor mu").severity == INFO


def test_bad_streak_is_not_treated_as_broken_strategy(tmp_path):
    """En onemli test: kotu bir seri 'sistem bozuldu' demek DEGIL."""
    cfg, store = _setup(tmp_path)
    _add_trades(store, [-1.0] * 12)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    c = _find(rep, "Strateji hala calisiyor mu")
    assert c.severity == INFO, "12 zarar sistemi bozuk ilan etmemeli"
    assert not rep.halt_required


def test_proven_dead_edge_halts_the_bot(tmp_path):
    """Kanit varsa DUR. Yeniden optimize etme."""
    cfg, store = _setup(tmp_path)
    _add_trades(store, [-1.0] * 60)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    c = _find(rep, "Strateji hala calisiyor mu")
    assert c.severity == CRITICAL
    assert rep.halt_required
    assert "backtest" in c.action, "kullaniciya somut adim verilmeli"


def test_negative_but_inconclusive_only_warns(tmp_path):
    """Ortalama negatif ama kanit yok -> uyar, durdurma."""
    cfg, store = _setup(tmp_path)
    rs = ([-1.0] * 40) + ([2.2] * 25)     # ortalama hafif negatif, varyans yuksek
    _add_trades(store, rs)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    c = _find(rep, "Strateji hala calisiyor mu")
    assert c.severity in (INFO, WARN)
    assert not rep.halt_required


def test_edge_check_inactive_below_min_sample(tmp_path):
    cfg, store = _setup(tmp_path)
    _add_trades(store, [-1.0] * 20)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    c = _find(rep, "Strateji hala calisiyor mu")
    assert c.severity == INFO
    assert "henuz aktif degil" in c.message


def test_drawdown_thresholds(tmp_path):
    cfg, store = _setup(tmp_path)
    store.record_equity(200.0, NOW)
    store.record_equity(170.0, NOW + 1)        # %15 dusus
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW + 2)
    assert _find(rep, "Dusus").severity == WARN

    store.record_equity(150.0, NOW + 2)        # %25 dusus
    rep2 = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW + 3)
    assert _find(rep2, "Dusus").severity == CRITICAL


def test_drawdown_warning_tells_user_not_to_panic(tmp_path):
    cfg, store = _setup(tmp_path)
    store.record_equity(200.0, NOW)
    store.record_equity(168.0, NOW + 1)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW + 2)
    c = _find(rep, "Dusus")
    assert "NORMAL" in c.message
    assert "kapatmak" in c.action


def test_fee_drag_flagged(tmp_path):
    cfg, store = _setup(tmp_path)
    # brut kar kucuk, komisyon buyuk
    _add_trades(store, [0.2] * 20, pnl_scale=1.0, fees=0.5)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    assert _find(rep, "Komisyon yuku").severity == WARN


def test_operational_blocks_surface(tmp_path):
    cfg, store = _setup(tmp_path)
    lrn = Learner(cfg, store)
    for _ in range(cfg.learning.mistake_repeat_threshold):
        lrn.record_mistake("FILUSDT", "min_notional", "kucuk", NOW)
    rep = run_health_checks(cfg, store, lrn, now_ms=NOW)
    c = _find(rep, "Operasyonel hatalar")
    assert c.severity == WARN and "FILUSDT" in c.message


def test_verdict_is_plain_language(tmp_path):
    cfg, store = _setup(tmp_path)
    _add_trades(store, [-1.0] * 60)
    rep = run_health_checks(cfg, store, Learner(cfg, store), now_ms=NOW)
    text = rep.render()
    assert "MUDAHALE GEREKIYOR" in text
    assert "YAP:" in text, "her kritik bulguda somut adim olmali"


def test_config_rejects_trigger_happy_health_settings():
    c = Config()
    c.health.min_trades_for_edge_check = 10
    with pytest.raises(ConfigError, match="min_trades_for_edge_check"):
        c.validate()


# ============================================================ denetim bulgusu
def test_yastik_tukenince_saglik_kontrolu_SESSIZ_KALMAZ(tmp_path):
    """Taban aktifken bot yastik bitince sessizce durur: hata yok, uyari yok,
    sadece hicbir islem acilmaz. Sessiz durma gorunur olmali."""
    from bot.config import Config
    from bot.health import CRITICAL, run_health_checks
    from bot.state import Store

    cfg = Config()
    cfg.risk.capital_floor_usdt = 170.0
    cfg.risk.min_cushion_usdt = 25.0
    store = Store(str(tmp_path / "c.db"), mode="paper")

    class B:
        def __init__(self, eq):
            self._eq = eq

        def equity(self):
            return self._eq

        def positions(self):
            return {}

    rep = run_health_checks(cfg, store, None, B(180.0))
    cek = [c for c in rep.checks if c.name == "Sermaye tabani"]
    assert cek, "taban kontrolu hic calismadi"
    assert cek[0].severity == CRITICAL
    assert "yeni islem ACMIYOR" in cek[0].message
    assert cek[0].action


def test_taban_kapaliyken_kontrol_gurultu_YAPMAZ(tmp_path):
    from bot.config import Config
    from bot.health import run_health_checks
    from bot.state import Store

    cfg = Config()
    store = Store(str(tmp_path / "d.db"), mode="paper")

    class B:
        def equity(self):
            return 300.0

        def positions(self):
            return {}

    rep = run_health_checks(cfg, store, None, B())
    assert not [c for c in rep.checks if c.name == "Sermaye tabani"]
