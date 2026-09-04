"""Config dogrulamasi: kotu ayarla calismaya baslamak, hatanin en pahali seklidir."""
import textwrap

import pytest

from bot.config import Config, ConfigError, load_config


def _write(tmp_path, body: str):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_defaults_are_valid():
    Config().validate()


def test_rejects_excessive_risk_per_trade():
    c = Config()
    c.risk.risk_per_trade_pct = 5.0
    with pytest.raises(ConfigError, match="risk_per_trade_pct"):
        c.validate()


def test_rejects_leverage_above_cap():
    c = Config()
    c.risk.max_leverage = 10
    c.account.leverage = 20
    with pytest.raises(ConfigError, match="leverage"):
        c.validate()


def test_rejects_max_leverage_above_20():
    c = Config()
    c.risk.max_leverage = 50
    c.account.leverage = 50
    with pytest.raises(ConfigError):
        c.validate()


def test_rejects_low_reward_risk():
    """Kullanicinin ilk fikri: -40 zarar / +20 kar. Bot bunu kabul etmemeli."""
    c = Config()
    c.risk.min_reward_risk = 0.5
    with pytest.raises(ConfigError, match="min_reward_risk"):
        c.validate()


def test_rejects_targets_below_min_reward_risk():
    c = Config()
    c.strategy.tp1_r = 0.4
    c.strategy.tp2_r = 0.5      # agirlikli hedef 0.45R -> reddedilmeli
    with pytest.raises(ConfigError, match="agirlikli hedef"):
        c.validate()


def test_rejects_inverted_targets():
    c = Config()
    c.strategy.tp2_r = 0.5
    c.strategy.tp1_r = 1.0
    with pytest.raises(ConfigError, match="tp2_r"):
        c.validate()


def test_rejects_bad_ema_order():
    c = Config()
    c.strategy.ema_fast = 100
    with pytest.raises(ConfigError, match="ema_fast"):
        c.validate()


def test_rejects_unknown_timeframe():
    c = Config()
    c.timeframe = "7m"
    with pytest.raises(ConfigError, match="timeframe"):
        c.validate()


def test_rejects_daily_loss_limit_too_high():
    c = Config()
    c.risk.daily_loss_limit_pct = 50
    with pytest.raises(ConfigError, match="daily_loss_limit_pct"):
        c.validate()


def test_live_mode_requires_keys(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    c = Config()
    c.mode = "live"
    with pytest.raises(ConfigError, match="API anahtarlari"):
        c.validate()


def test_unknown_key_is_rejected(tmp_path):
    p = _write(tmp_path, """
        mode: paper
        symbols: [BNBUSDT]
        risk:
          risk_per_trade_pct: 1.0
          typo_key: 5
    """)
    with pytest.raises(ConfigError, match="bilinmeyen anahtar"):
        load_config(p)


def test_loads_valid_file(tmp_path):
    p = _write(tmp_path, """
        mode: paper
        symbols: [bnbusdt]
        timeframe: 4h
        risk:
          risk_per_trade_pct: 1.0
        strategy:
          tp1_r: 1.0
          tp2_r: 3.0
    """)
    cfg = load_config(p)
    assert cfg.symbols == ["BNBUSDT"]
    assert cfg.timeframe == "4h"
    assert cfg.risk.risk_per_trade_pct == 1.0


def test_breakeven_win_rate_math():
    c = Config()
    c.strategy.tp1_r = 1.0
    c.strategy.tp2_r = 3.0
    c.strategy.tp1_size_pct = 50
    assert c.blended_target_r() == pytest.approx(2.0)
    assert c.breakeven_win_rate() == pytest.approx(1 / 3)


def test_shipped_example_config_is_valid():
    """Depoda duran ornek config her zaman gecerli olmali."""
    cfg = load_config("config.example.yaml")
    assert cfg.mode in ("paper", "testnet", "live")
    assert cfg.timeframe == "4h", "backtest kanitina gore 4h sevk ediliyor"
