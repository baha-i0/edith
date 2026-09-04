"""Saf Python teknik gostergeler.

Neden numpy yok: bot birkac bin mumla calisiyor, bagimlilik yuzeyini kucuk
tutmak canli para tasiyan bir surecte kurulum riskini azaltiyor.

Sozlesme: her fonksiyon girdiyle ayni uzunlukta liste doner, hesaplanamayan
bas kisim None ile doldurulur. Boylece index hizalamasi hic bozulmaz.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

Num = Optional[float]


def sma(values: Sequence[float], period: int) -> List[Num]:
    if period <= 0:
        raise ValueError("period > 0 olmali")
    out: List[Num] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> List[Num]:
    """SMA ile tohumlanmis EMA (TradingView/Binance davranisi)."""
    if period <= 0:
        raise ValueError("period > 0 olmali")
    out: List[Num] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def rma(values: Sequence[float], period: int) -> List[Num]:
    """Wilder'in yumusatmasi (RSI/ATR/ADX bunu kullanir)."""
    if period <= 0:
        raise ValueError("period > 0 olmali")
    out: List[Num] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> List[float]:
    tr = [highs[0] - lows[0]] if highs else []
    for i in range(1, len(highs)):
        pc = closes[i - 1]
        tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return tr


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> List[Num]:
    if not highs:
        return []
    tr = true_range(highs, lows, closes)
    # ilk TR gercek bir TR degil (onceki kapanis yok), Wilder gibi dahil ediyoruz
    return rma(tr, period)


def rsi(closes: Sequence[float], period: int = 14) -> List[Num]:
    n = len(closes)
    out: List[Num] = [None] * n
    if n < period + 1:
        return out
    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # ilk eleman sahte (fark yok), Wilder ortalamasini 1..period uzerinden baslat
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> tuple[List[Num], List[Num], List[Num]]:
    """(adx, +DI, -DI) doner. Trend gucu filtresi icin."""
    n = len(highs)
    empty: List[Num] = [None] * n
    if n < period * 2:
        return empty, list(empty), list(empty)

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    tr = true_range(highs, lows, closes)
    atr_s = rma(tr, period)
    plus_s = rma(plus_dm, period)
    minus_s = rma(minus_dm, period)

    plus_di: List[Num] = [None] * n
    minus_di: List[Num] = [None] * n
    dx: List[float] = []
    dx_index: List[int] = []
    for i in range(n):
        a, p, m = atr_s[i], plus_s[i], minus_s[i]
        if a is None or p is None or m is None or a == 0:
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)
        dx_index.append(i)

    adx_out: List[Num] = [None] * n
    smoothed = rma(dx, period)
    for j, i in enumerate(dx_index):
        adx_out[i] = smoothed[j]
    return adx_out, plus_di, minus_di


def rolling_max(values: Sequence[float], period: int) -> List[Num]:
    out: List[Num] = [None] * len(values)
    for i in range(len(values)):
        if i >= period - 1:
            out[i] = max(values[i - period + 1 : i + 1])
    return out


def rolling_min(values: Sequence[float], period: int) -> List[Num]:
    out: List[Num] = [None] * len(values)
    for i in range(len(values)):
        if i >= period - 1:
            out[i] = min(values[i - period + 1 : i + 1])
    return out


def percentile(values: Sequence[float], q: float) -> float:
    """Lineer interpolasyonlu yuzdelik (q: 0..1)."""
    if not values:
        raise ValueError("bos dizi")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac
