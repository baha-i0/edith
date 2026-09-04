"""Ortak veri tipleri. Backtest, paper ve live ayni tipleri kullanir;
boylece backtest'te dogrulanan mantik canlida farkli davranmaz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Dict, List, Optional

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class Candle:
    open_time: int  # ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    closed: bool = True


@dataclass(frozen=True)
class SymbolFilters:
    """Binance exchangeInfo suzgecleri. Yanlis yuvarlama = reddedilen emir."""

    symbol: str
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float
    price_precision: int = 8
    qty_precision: int = 8
    max_qty: float = float("inf")

    def round_price(self, price: float, side_up: bool = False) -> float:
        return _quantize(price, self.tick_size, up=side_up)

    def round_qty(self, qty: float) -> float:
        q = _quantize(qty, self.step_size, up=False)
        return min(q, self.max_qty)

    def qty_ok(self, qty: float, price: float) -> bool:
        return qty >= self.min_qty and qty * price >= self.min_notional


def _quantize(value: float, step: float, up: bool = False) -> float:
    if step <= 0:
        return value
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    mode = ROUND_UP if up else ROUND_DOWN
    n = (d_val / d_step).to_integral_value(rounding=mode)
    return float(n * d_step)


@dataclass
class Signal:
    symbol: str
    side: str  # LONG | SHORT
    entry: float
    stop: float
    tp1: float
    tp2: float
    atr: float
    reason: str
    meta: Dict[str, float] = field(default_factory=dict)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        r = self.risk_per_unit
        return abs(self.tp2 - self.entry) / r if r > 0 else 0.0


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    initial_risk_per_unit: float
    opened_at: int  # ms
    leverage: int
    initial_qty: float
    tp1_filled: bool = False
    bars_held: int = 0
    fees_paid: float = 0.0
    realized_pnl: float = 0.0
    breakeven_moved: bool = False
    entry_reason: str = ""
    client_id: str = ""

    @property
    def direction(self) -> int:
        return 1 if self.side == LONG else -1

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.qty * self.direction

    def r_multiple(self, price: float) -> float:
        if self.initial_risk_per_unit <= 0:
            return 0.0
        return (price - self.entry_price) * self.direction / self.initial_risk_per_unit

    def stop_hit(self, low: float, high: float) -> bool:
        return low <= self.stop if self.side == LONG else high >= self.stop

    def notional(self, price: float) -> float:
        return abs(self.qty) * price


@dataclass
class Trade:
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float
    opened_at: int
    closed_at: int
    pnl: float
    fees: float
    r_multiple: float
    exit_reason: str
    entry_reason: str = ""

    @property
    def net_pnl(self) -> float:
        return self.pnl


def liquidation_distance_pct(leverage: int, maintenance_margin_rate: float = 0.005) -> float:
    """Izole marjda yaklasik likidasyon mesafesi (giris fiyatina oran).

    Yaklasik: 1/L - mmr. 10x'te ~%9.5. Bu yuzden stop'un likidasyondan
    belirgin sekilde once olmasi zorunlu; aksi halde borsa senin yerine
    'stop' calistirir ve tum marji alir.
    """
    if leverage <= 0:
        return 1.0
    return max(1.0 / leverage - maintenance_margin_rate, 1e-6)


def pct(a: float, b: float) -> float:
    return 0.0 if b == 0 else (a / b) * 100.0


def safe_float(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default
