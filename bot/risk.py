"""Risk yonetimi: pozisyon boyutu ve islem izinleri.

Merkezi fikir: **kaldirac risk degildir, stop mesafesi risktir.**

Yaygin hata: "10x ile girerim, 40 dolar zarara stop". Bu, pozisyon boyutunu
kaldiraca gore secmektir ve kaybi hesaplanmis degil tesadufi kilar. Dogru
siralama tam tersi:

    1. Bu islemde kaybetmeye razi oldugum para  = equity * risk%
    2. Stop mesafesi (ATR/yapi belirler)        = |giris - stop|
    3. Miktar                                   = (1) / (2)
    4. Kaldirac                                 = sadece bu miktarin marjini
                                                  karsilamaya yetecek kadar

Boylece kaldiraci artirmak riski artirmaz; sadece daha az marj bloke eder.
Kaldiracin gercek tehlikesi likidasyon mesafesidir, o da ayrica sinirlanir.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .config import Config, RiskConfig
from .models import SymbolFilters, liquidation_distance_pct

MAINTENANCE_MARGIN_RATE = 0.005  # BNBUSDT gibi ana pariteler icin tipik alt kademe


@dataclass
class SizingResult:
    ok: bool
    qty: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    leverage: int = 1
    risk_amount: float = 0.0
    stop_pct: float = 0.0
    reason: str = ""


def max_safe_leverage(stop_pct: float, max_stop_vs_liq: float,
                      mmr: float = MAINTENANCE_MARGIN_RATE) -> int:
    """Stop'un likidasyondan once tetiklenmesini garantileyen en yuksek kaldirac.

    Kosul:  stop_pct <= max_stop_vs_liq * (1/L - mmr)
    Cozum:  L <= 1 / (stop_pct / max_stop_vs_liq + mmr)
    """
    if stop_pct <= 0:
        return 1
    raw = 1.0 / (stop_pct / max_stop_vs_liq + mmr)
    return max(1, int(raw))


def size_position(
    equity: float,
    free_margin: float,
    entry: float,
    stop: float,
    filters: SymbolFilters,
    risk_cfg: RiskConfig,
    desired_leverage: int,
) -> SizingResult:
    if equity <= 0 or entry <= 0:
        return SizingResult(False, reason="equity/fiyat gecersiz")

    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return SizingResult(False, reason="stop mesafesi sifir")
    stop_pct = stop_dist / entry

    lev_cap = max_safe_leverage(stop_pct, risk_cfg.max_stop_vs_liquidation)
    leverage = max(1, min(desired_leverage, risk_cfg.max_leverage, lev_cap))

    risk_amount = equity * risk_cfg.risk_per_trade_pct / 100.0
    qty = risk_amount / stop_dist

    # Tavan 1: toplam notional (equity yuzdesi)
    max_notional = equity * risk_cfg.max_position_notional_pct / 100.0
    if qty * entry > max_notional:
        qty = max_notional / entry

    # Tavan 2: kullanilabilir marj (tampon birakarak)
    usable = free_margin * (1.0 - risk_cfg.min_free_margin_pct / 100.0)
    if usable <= 0:
        return SizingResult(False, reason="serbest marj tamponun altinda")
    max_qty_by_margin = (usable * leverage) / entry
    if qty > max_qty_by_margin:
        qty = max_qty_by_margin

    qty = filters.round_qty(qty)
    if qty <= 0:
        return SizingResult(False, reason="yuvarlama sonrasi miktar sifir (bakiye cok kucuk)")
    if not filters.qty_ok(qty, entry):
        return SizingResult(
            False,
            reason=(f"minimum emir sarti saglanmadi (qty={qty}, notional={qty*entry:.2f}, "
                    f"min_notional={filters.min_notional})"),
        )

    notional = qty * entry
    margin = notional / leverage
    actual_risk = qty * stop_dist
    if actual_risk > equity * (risk_cfg.risk_per_trade_pct * 1.05) / 100.0:
        return SizingResult(False, reason="hesaplanan risk butceyi asiyor")

    return SizingResult(
        ok=True,
        qty=qty,
        notional=notional,
        margin=margin,
        leverage=leverage,
        risk_amount=actual_risk,
        stop_pct=stop_pct * 100.0,
        reason=f"risk {actual_risk:.2f} {'USDT'} / stop %{stop_pct*100:.2f} / {leverage}x",
    )


@dataclass
class RiskState:
    day: str = ""
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    cooldown_until_ms: int = 0
    day_start_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    # Golge modu: bot islem yapmaya DEVAM eder ama para riske atmaz.
    # Edge oldugunda "durup insani bekle" yerine kullanilir -- boylece
    # sistem otonom kalir, sermaye korunur ve kanit geri gelirse
    # kendiliginden canliya doner.
    shadow_mode: bool = False
    shadow_since_ms: int = 0
    shadow_reason: str = ""


class RiskGuard:
    """Islem oncesi kapi. Hepsi 'hayir' diyebilir; hicbiri 'evet' demeye zorlamaz."""

    def __init__(self, cfg: Config, state: RiskState):
        self.cfg = cfg
        self.state = state

    def roll_day(self, now_ms: int, equity: float) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
        if self.state.day != day:
            self.state.day = day
            self.state.realized_pnl_today = 0.0
            self.state.trades_today = 0
            self.state.day_start_equity = equity
            self.state.halted = False
            self.state.halt_reason = ""
        if self.state.day_start_equity <= 0:
            self.state.day_start_equity = equity

    def can_open(self, now_ms: int, open_positions: int, equity: float) -> Tuple[bool, str]:
        r = self.cfg.risk
        s = self.state
        self.roll_day(now_ms, equity)

        # Sira onemli: once GUN BOYU gecerli durdurmalar (bayrak set edilmeli),
        # sonra gecici engeller. Ters sirada 'halted' bayragi soguma bitene
        # kadar yazilmaz ve limit restart'ta kaybolur.
        if s.halted:
            return False, f"gunluk durdurma aktif: {s.halt_reason}"

        base = s.day_start_equity or equity
        loss_limit = -base * r.daily_loss_limit_pct / 100.0
        if s.realized_pnl_today <= loss_limit:
            s.halted = True
            s.halt_reason = f"gunluk zarar limiti ({r.daily_loss_limit_pct}%)"
            return False, s.halt_reason
        profit_target = base * r.daily_profit_target_pct / 100.0
        if r.daily_profit_target_pct > 0 and s.realized_pnl_today >= profit_target:
            s.halted = True
            s.halt_reason = f"gunluk kar hedefi ({r.daily_profit_target_pct}%) - masayi birak"
            return False, s.halt_reason

        if now_ms < s.cooldown_until_ms:
            kalan = int((s.cooldown_until_ms - now_ms) / 60000) + 1
            return False, f"soguma suresi: {kalan} dk"
        if open_positions >= r.max_concurrent_positions:
            return False, f"acik pozisyon limiti ({r.max_concurrent_positions})"
        if s.trades_today >= r.max_trades_per_day:
            return False, f"gunluk islem limiti ({r.max_trades_per_day})"
        if s.consecutive_losses >= r.max_consecutive_losses:
            return False, f"ust uste {s.consecutive_losses} zarar - soguma"
        return True, "ok"

    def record_open(self, now_ms: int) -> None:
        self.state.trades_today += 1

    def record_close(self, now_ms: int, pnl: float) -> None:
        r = self.cfg.risk
        s = self.state
        s.realized_pnl_today += pnl
        if pnl < 0:
            s.consecutive_losses += 1
            cd = r.cooldown_minutes_after_loss
            if s.consecutive_losses >= r.max_consecutive_losses:
                cd = r.cooldown_minutes_after_streak
                s.consecutive_losses = 0  # sogumadan sonra sayac sifirlanir
            s.cooldown_until_ms = max(s.cooldown_until_ms, now_ms + cd * 60_000)
        else:
            s.consecutive_losses = 0


def validate_signal_quality(signal, cfg: Config) -> Tuple[bool, str]:
    """Sinyalin komisyon sonrasi anlamli olup olmadigini kontrol eder."""
    e = cfg.execution
    rr = signal.reward_risk
    if rr < cfg.risk.min_reward_risk:
        return False, f"R:R {rr:.2f} < {cfg.risk.min_reward_risk}"

    # Gidis-donus maliyet (fiyat yuzdesi olarak): 2 x taker + slipaj
    round_trip = 2 * e.taker_fee + 2 * (e.slippage_bps / 10_000.0)
    cost_in_price = round_trip * signal.entry
    if signal.risk_per_unit <= 3 * cost_in_price:
        return False, "stop mesafesi islem maliyetine gore cok dar (edge komisyona gider)"
    return True, "ok"
