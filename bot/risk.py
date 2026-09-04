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


def effective_floor(risk_cfg: RiskConfig, state: Optional["RiskState"] = None) -> float:
    """Su anda gecerli taban.

    Iki kaynagin BUYUGU: sabit taban ve zirveden cirpinan taban.
    Cirpinan kisim state'te saklanir cunku ASLA dusmemeli -- bakiye
    zirveden geri gelse bile kilitlenen kar kilitli kalir.
    """
    floor = risk_cfg.capital_floor_usdt
    if state is not None:
        floor = max(floor, state.floor_usdt)
    return floor


def update_floor(risk_cfg: RiskConfig, state: "RiskState", equity: float) -> float:
    """Zirveyi ve cirpinan tabani gunceller. Yeni tabani doner."""
    if equity > state.peak_equity:
        state.peak_equity = equity
    if risk_cfg.capital_floor_ratchet_pct > 0:
        hedef = state.peak_equity * risk_cfg.capital_floor_ratchet_pct / 100.0
        if hedef > state.floor_usdt:
            state.floor_usdt = hedef
    return effective_floor(risk_cfg, state)


def risk_base(equity: float, risk_cfg: RiskConfig,
              state: Optional["RiskState"] = None) -> float:
    """Risk hangi para uzerinden olculur: tum bakiye mi, yastik mi?

    Taban tanimliysa bot sadece YASTIGI (bakiye - taban) riske atar.
    Bakiye dustukce yastik kucululur ve pozisyonlar kendiliginden
    kucululur -- tabana varmadan bot durur.
    """
    floor = effective_floor(risk_cfg, state)
    if floor <= 0:
        return equity
    return max(0.0, equity - floor)


def open_risk_total(positions) -> float:
    """Acik pozisyonlarin toplam kalan riski (USDT).

    Stop breakeven'a cekilmisse o pozisyonun riski artik sifirdir --
    en kotu ihtimalde girisinden cikar. Bunu saymamak yeni islem
    acilmasini gereksiz yere engellerdi.
    """
    total = 0.0
    for p in positions:
        if getattr(p, "breakeven_moved", False):
            continue
        total += abs(p.entry_price - p.stop) * p.qty
    return total


def size_position(
    equity: float,
    free_margin: float,
    entry: float,
    stop: float,
    filters: SymbolFilters,
    risk_cfg: RiskConfig,
    desired_leverage: int,
    open_risk: float = 0.0,
    state: Optional["RiskState"] = None,
) -> SizingResult:
    if equity <= 0 or entry <= 0:
        return SizingResult(False, reason="equity/fiyat gecersiz")

    floor = effective_floor(risk_cfg, state)
    base = risk_base(equity, risk_cfg, state)
    if floor > 0 and base < risk_cfg.min_cushion_usdt:
        return SizingResult(
            False,
            reason=(f"yastik tukendi: bakiye {equity:.2f}, taban {floor:.2f} "
                    f"-> riske atilabilir {base:.2f} < "
                    f"{risk_cfg.min_cushion_usdt:.2f}"),
        )

    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return SizingResult(False, reason="stop mesafesi sifir")
    stop_pct = stop_dist / entry

    lev_cap = max_safe_leverage(stop_pct, risk_cfg.max_stop_vs_liquidation)
    leverage = max(1, min(desired_leverage, risk_cfg.max_leverage, lev_cap))

    # Ogrenme katmani risk_per_trade_pct'i carpabiliyor (varsayilan tavan
    # 1.0, yani artiramaz -- ama config 1.5'e izin veriyor). Config
    # dogrulamasi "taban varken en fazla %6" diye soz veriyor; runtime'in
    # o sozu bozmamasi icin burada da kirpiliyor.
    yuzde = min(risk_cfg.risk_per_trade_pct, 6.0 if floor > 0 else 2.0)
    risk_amount = base * yuzde / 100.0

    # Tavan 0: ayni anda acik TUM pozisyonlarin toplam riski. Tek islem
    # tabani delemez ama es zamanli 4 islem birden ters giderse delebilir.
    # Kripto pozisyonlari yuksek korelasyonlu -- hepsi ayni anda ters
    # gitmesi uzak bir ihtimal degil, tipik bir cokus gunu.
    if floor > 0:
        toplam_tavan = base * risk_cfg.max_total_risk_pct_of_cushion / 100.0
        kalan = toplam_tavan - open_risk
        if kalan <= 0:
            return SizingResult(
                False,
                reason=(f"acik risk tavani dolu: {open_risk:.2f} / "
                        f"{toplam_tavan:.2f} USDT (yastigin %"
                        f"{risk_cfg.max_total_risk_pct_of_cushion:.0f}'i)"),
            )
        risk_amount = min(risk_amount, kalan)

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
    if actual_risk > base * (yuzde * 1.05) / 100.0:
        return SizingResult(False, reason="hesaplanan risk butceyi asiyor")

    return SizingResult(
        ok=True,
        qty=qty,
        notional=notional,
        margin=margin,
        leverage=leverage,
        risk_amount=actual_risk,
        stop_pct=stop_pct * 100.0,
        reason=(f"risk {actual_risk:.2f} USDT / stop %{stop_pct*100:.2f} / {leverage}x"
                + (f" / yastik {base:.2f} (taban {floor:.0f})" if floor > 0 else "")),
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
    # Cirpinan taban icin: gorulen en yuksek bakiye ve ondan turetilen
    # gecerli taban. Taban ASLA dusmez -- kalici olarak saklanir, cunku
    # yeniden baslatmada sifirlanirsa "kilitlenen kar" kilidi acilirdi.
    peak_equity: float = 0.0
    floor_usdt: float = 0.0
    # Sahibin Telegram'dan /dur demesiyle set edilir. Gun degisiminde
    # SIFIRLANMAZ: patron durdurduysa, durur. Sadece /devam kaldirir.
    paused: bool = False


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

        # Sahibin elle durdurmasi her seyin onunde: hicbir hesaplama bunu
        # gecersiz kilamaz.
        if s.paused:
            return False, "elle durduruldu (/devam ile ac)"

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
