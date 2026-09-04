"""Konfigurasyon: YAML + env, ve *sert* dogrulama.

Buradaki dogrulamalar dekoratif degil. Canli para tasiyan bir sistemde
"yanlis config ile calismaya devam etmek" en pahali hata modu; bu yuzden
sinir disi degerler uyari degil exception uretir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml

VALID_MODES = ("paper", "testnet", "live")
VALID_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h")


class ConfigError(ValueError):
    pass


@dataclass
class AccountConfig:
    quote_asset: str = "USDT"
    leverage: int = 5
    margin_type: str = "ISOLATED"
    paper_start_balance: float = 200.0


@dataclass
class RiskConfig:
    # Pozisyon buyuklugunu kaldirac degil, stop mesafesi belirler.
    risk_per_trade_pct: float = 0.75
    max_leverage: int = 10
    max_position_notional_pct: float = 300.0  # equity yuzdesi cinsinden tavan
    daily_loss_limit_pct: float = 4.0
    daily_profit_target_pct: float = 6.0
    max_trades_per_day: int = 12
    max_concurrent_positions: int = 2
    max_consecutive_losses: int = 3
    cooldown_minutes_after_loss: int = 20
    cooldown_minutes_after_streak: int = 240
    min_reward_risk: float = 1.5
    # Stop, likidasyon mesafesinin bu oranindan uzak olamaz (0.6 = %60).
    max_stop_vs_liquidation: float = 0.6
    min_free_margin_pct: float = 25.0


@dataclass
class StrategyConfig:
    name: str = "trend_pullback"
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    min_adx: float = 20.0
    rsi_long_min: float = 45.0
    rsi_long_max: float = 72.0
    rsi_short_min: float = 28.0
    rsi_short_max: float = 55.0
    atr_pct_min: float = 0.15
    atr_pct_max: float = 2.5
    pullback_lookback: int = 6
    swing_lookback: int = 10
    stop_atr_mult: float = 1.6
    stop_buffer_atr: float = 0.25
    tp1_r: float = 1.0
    tp1_size_pct: float = 50.0
    tp2_r: float = 2.2
    breakeven_at_r: float = 1.0
    trail_atr_mult: float = 2.2
    max_bars_in_trade: int = 96
    warmup_bars: int = 250


@dataclass
class ExecutionConfig:
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    slippage_bps: float = 2.0
    max_spread_bps: float = 6.0
    avoid_funding_minutes: int = 10
    funding_rate_abort: float = 0.0006
    recv_window: int = 5000
    request_timeout: float = 10.0
    max_retries: int = 4


@dataclass
class LearningConfig:
    """Ogrenme katmani ayarlari.

    Varsayilanlar KASTEN muhafazakar: bir kovayi devre disi birakmak icin
    en az 30 islem ve tek yonlu %95 anlamlilik gerekiyor. Bu esikleri
    dusurmek "daha hizli ogrenen" bir bot yapmaz, gurultuye uyum saglayan
    bir bot yapar.
    """
    enabled: bool = True
    # --- yavas ogrenme (istatistiksel) ---
    min_trades_per_bucket: int = 30      # bu sayinin altinda hicbir karar alinmaz
    min_trades_for_sizing: int = 40      # risk carpani icin daha da fazlasi gerekir
    significance_z: float = 2.33         # tek yonlu %99 -- 1.64 DEGIL, cunku
                                         # her islemde test etmek nominal hatayi
                                         # asar (bkz. learning._maybe_bench)
    bench_eval_every: int = 10           # bank testi her N islemde bir yapilir
    prior_expectancy_r: float = 0.11     # backtest beklentisi (onsel inanc)
    prior_strength: float = 40.0         # onsele kac islemlik guven veriliyor
    trade_r_sd: float = 1.3              # islem basi R standart sapmasi
    bench_days: int = 14
    probation_multiplier: float = 0.6
    # --- risk olcegi ---
    max_risk_multiplier: float = 1.0     # 1.0 = ogrenme riski ARTIRAMAZ
    min_risk_multiplier: float = 0.6
    # VARSAYILAN KAPALI. Olculdu: ~3 puan CAGR maliyetine ~4 puan dusus
    # azaltiyor -- yani risk-getiri oranini iyilestirmiyor, sadece olcegi
    # kuculutuyor. Sermaye korumasi psikolojik olarak degerliyse ac.
    drawdown_scaling: bool = False
    drawdown_full_cut_pct: float = 25.0
    # --- stop kalibrasyonu ---
    stop_calibration: bool = True
    stop_hunt_lookback_bars: int = 12
    stop_hunt_rate_threshold: float = 0.45
    stop_widen_step: float = 0.15
    stop_widen_max: float = 1.5
    # --- hizli ogrenme (operasyonel) ---
    mistake_repeat_threshold: int = 3
    mistake_block_days: int = 7


@dataclass
class HealthConfig:
    """Kendini denetleme esikleri.

    Buradaki tek yonlu kural onemli: kanit kotuyse bot DURUR, iyi diye
    kendini buyutmez. Kendini yeniden optimize eden bir sistem bozuldugunu
    asla soylemez, cunku her seferinde gecmise uyan yeni bir parametre bulur.
    """
    enabled: bool = True
    check_every_minutes: int = 60
    # Edge oldu mu kontrolu -- esik kasten yuksek, kotu bir aya tepki
    # vermek icin degil, edge'in gercekten bittigini yakalamak icin.
    min_trades_for_edge_check: int = 50
    edge_z: float = 2.33
    halt_on_dead_edge: bool = True
    drawdown_warn_pct: float = 15.0
    drawdown_critical_pct: float = 22.0
    fee_drag_warn_pct: float = 40.0
    notify_on_warn: bool = False   # sadece 'mudahale' seviyesi bildirim gonderir


@dataclass
class ShadowConfig:
    """Golge modu: edge oldugunde durmak yerine kagit uzerinde devam etmek.

    Asimetrik esikler kasten:
      - golgeye GECMEK icin: beklentinin negatif oldugu kanitlanmali
      - canliya DONMEK icin: beklentinin pozitif oldugu kanitlanmali
    Yani supheli durumda sermaye risk almaz. Yanlis yonde hata yapmanin
    maliyeti simetrik degil.
    """
    enabled: bool = True
    min_trades_to_resume: int = 40   # golgede en az bu kadar sanal islem
    resume_z: float = 1.64           # alt guven siniri > 0 (tek yonlu %95)
    notify_on_transition: bool = True


@dataclass
class Config:
    mode: str = "paper"
    symbols: List[str] = field(default_factory=lambda: ["BNBUSDT"])
    timeframe: str = "5m"
    loop_seconds: int = 20
    state_path: str = "data/bot.db"
    log_path: str = "logs/bot.log"
    account: AccountConfig = field(default_factory=AccountConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    shadow: ShadowConfig = field(default_factory=ShadowConfig)

    # ---- API kimlik bilgileri sadece ortam degiskeninden okunur ----
    @property
    def api_key(self) -> str:
        if self.mode == "testnet":
            return os.getenv("BINANCE_TESTNET_API_KEY", "")
        return os.getenv("BINANCE_API_KEY", "")

    @property
    def api_secret(self) -> str:
        if self.mode == "testnet":
            return os.getenv("BINANCE_TESTNET_API_SECRET", "")
        return os.getenv("BINANCE_API_SECRET", "")

    def validate(self) -> None:
        r, s, a, e, lrn = self.risk, self.strategy, self.account, self.execution, self.learning
        errs: List[str] = []

        if self.mode not in VALID_MODES:
            errs.append(f"mode '{self.mode}' gecersiz, {VALID_MODES} olmali")
        if self.timeframe not in VALID_TIMEFRAMES:
            errs.append(f"timeframe '{self.timeframe}' desteklenmiyor: {VALID_TIMEFRAMES}")
        if not self.symbols:
            errs.append("en az bir sembol gerekli")
        if self.loop_seconds < 5:
            errs.append("loop_seconds >= 5 olmali (rate limit)")

        if not 0 < r.risk_per_trade_pct <= 2.0:
            errs.append("risk_per_trade_pct 0 ile 2.0 arasinda olmali (tek islemde equity'nin %2'sinden fazlasi kumar)")
        if not 1 <= a.leverage <= r.max_leverage:
            errs.append(f"leverage 1..{r.max_leverage} araliginda olmali")
        if r.max_leverage > 20:
            errs.append("max_leverage 20'yi asamaz")
        if r.min_reward_risk < 1.2:
            errs.append("min_reward_risk >= 1.2 olmali (dusuk R:R matematiksel olarak kaybettirir)")
        if not 0 < r.daily_loss_limit_pct <= 10:
            errs.append("daily_loss_limit_pct 0..10 araliginda olmali")
        if r.max_concurrent_positions < 1:
            errs.append("max_concurrent_positions >= 1 olmali")
        if not 0 < r.max_stop_vs_liquidation <= 0.9:
            errs.append("max_stop_vs_liquidation 0..0.9 araliginda olmali")
        if a.margin_type not in ("ISOLATED", "CROSSED"):
            errs.append("margin_type ISOLATED veya CROSSED olmali")

        if s.ema_fast >= s.ema_mid or s.ema_mid >= s.ema_slow:
            errs.append("ema_fast < ema_mid < ema_slow olmali")
        if s.tp2_r <= s.tp1_r:
            errs.append("tp2_r > tp1_r olmali")
        if s.tp1_r < r.min_reward_risk and s.tp1_size_pct >= 100:
            errs.append("tek hedefli cikista tp1_r >= min_reward_risk olmali")
        if not 0 < s.tp1_size_pct <= 100:
            errs.append("tp1_size_pct 0..100 araliginda olmali")
        if s.stop_atr_mult <= 0:
            errs.append("stop_atr_mult > 0 olmali")
        if s.atr_pct_min >= s.atr_pct_max:
            errs.append("atr_pct_min < atr_pct_max olmali")
        if s.warmup_bars < s.ema_slow + 50:
            errs.append("warmup_bars >= ema_slow + 50 olmali")

        # Beklenen deger kontrolu: karma hedefin agirlikli R'si komisyonu asmali
        blended_r = (s.tp1_size_pct / 100.0) * s.tp1_r + (1 - s.tp1_size_pct / 100.0) * s.tp2_r
        if blended_r < r.min_reward_risk:
            errs.append(
                f"agirlikli hedef R = {blended_r:.2f} < min_reward_risk {r.min_reward_risk}. "
                "Hedefleri buyut ya da min_reward_risk'i dusur (dusurme onerilmez)."
            )

        if e.taker_fee < 0 or e.maker_fee < 0:
            errs.append("komisyonlar negatif olamaz")
        if e.max_spread_bps <= 0:
            errs.append("max_spread_bps > 0 olmali")

        sh = self.shadow
        if sh.enabled:
            if sh.min_trades_to_resume < 20:
                errs.append("shadow.min_trades_to_resume >= 20 olmali")
            if sh.resume_z < 1.28:
                errs.append("shadow.resume_z >= 1.28 olmali")

        h = self.health
        if h.enabled:
            if h.min_trades_for_edge_check < 30:
                errs.append("health.min_trades_for_edge_check >= 30 olmali "
                            "(daha azi kotu bir seriye tepki vermektir)")
            if h.edge_z < 1.64:
                errs.append("health.edge_z >= 1.64 olmali")
            if h.check_every_minutes < 5:
                errs.append("health.check_every_minutes >= 5 olmali")
            if not 0 < h.drawdown_warn_pct < h.drawdown_critical_pct:
                errs.append("0 < drawdown_warn_pct < drawdown_critical_pct olmali")

        if lrn.enabled:
            if lrn.min_trades_per_bucket < 20:
                errs.append("learning.min_trades_per_bucket >= 20 olmali "
                            "(daha azi gurultuden ders cikarmaktir)")
            if lrn.significance_z < 1.64:
                errs.append("learning.significance_z >= 1.64 olmali; olculen yanlis "
                            "bank orani bu esigin altinda hizla buyuyor")
            if lrn.bench_eval_every < 1:
                errs.append("learning.bench_eval_every >= 1 olmali")
            if lrn.max_risk_multiplier > 1.5:
                errs.append("learning.max_risk_multiplier 1.5'i asamaz")
            if not 0 < lrn.min_risk_multiplier <= lrn.max_risk_multiplier:
                errs.append("0 < min_risk_multiplier <= max_risk_multiplier olmali")
            if lrn.prior_strength < 0:
                errs.append("learning.prior_strength negatif olamaz")
            if lrn.stop_widen_max < 1.0:
                errs.append("learning.stop_widen_max >= 1.0 olmali")
            if lrn.bench_days < 1:
                errs.append("learning.bench_days >= 1 olmali")

        if self.mode in ("testnet", "live") and not (self.api_key and self.api_secret):
            errs.append(f"{self.mode} modu icin API anahtarlari ortam degiskenlerinde tanimli degil")

        if errs:
            raise ConfigError("Config hatalari:\n  - " + "\n  - ".join(errs))

    def blended_target_r(self) -> float:
        s = self.strategy
        w = s.tp1_size_pct / 100.0
        return w * s.tp1_r + (1 - w) * s.tp2_r

    def breakeven_win_rate(self) -> float:
        """Komisyon oncesi basabas isabet orani. Gercekci beklenti icin."""
        r = self.blended_target_r()
        return 1.0 / (1.0 + r)


def _build(cls, data: Dict[str, Any]):
    if not is_dataclass(cls):
        return data
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"{cls.__name__} icinde bilinmeyen anahtar(lar): {sorted(unknown)}")
    kwargs = {}
    for name, val in data.items():
        ftype = known[name].type
        if is_dataclass(ftype) and isinstance(val, dict):
            kwargs[name] = _build(ftype, val)
        else:
            kwargs[name] = val
    return cls(**kwargs)


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config bulunamadi: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config kok seviyesi bir sozluk olmali")

    nested = {}
    for key, cls in (
        ("account", AccountConfig),
        ("risk", RiskConfig),
        ("strategy", StrategyConfig),
        ("execution", ExecutionConfig),
        ("learning", LearningConfig),
        ("health", HealthConfig),
        ("shadow", ShadowConfig),
    ):
        nested[key] = _build(cls, raw.pop(key, {}) or {})

    known_top = {f.name for f in fields(Config)}
    unknown = set(raw) - known_top
    if unknown:
        raise ConfigError(f"config icinde bilinmeyen anahtar(lar): {sorted(unknown)}")

    cfg = Config(**raw, **nested)
    cfg.symbols = [s.upper() for s in cfg.symbols]
    cfg.validate()
    return cfg


def load_dotenv(path: str | Path = ".env") -> None:
    """Bagimlilik eklememek icin minik .env okuyucu. Var olan env'i ezmez."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v
