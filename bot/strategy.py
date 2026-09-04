"""Trend-pullback stratejisi.

Fikir: rastgele yon tahmini yerine *kosullu* islem. Uc filtre ayni anda
uymadan pozisyon acilmaz:

  1) Yapisal trend  : EMA50 > EMA200 ve fiyat EMA200'un ustunde (long icin)
  2) Trend gucu     : ADX >= esik ve +DI > -DI  (yatay piyasada islem yok)
  3) Volatilite bandi: ATR/price belirli aralikta
     - cok dusukse hareket komisyonu bile karsilamaz
     - cok yuksekse stop mesafesi ve slipaj kontrolden cikar

Giris tetigi trendin *kendisi* degil, trend icindeki geri cekilme sonrasi
devam sinyali. Boylece ortalama giris fiyati iyilesir ve stop mesafesi
kisalir; kucuk stop = ayni risk butcesiyle daha buyuk pozisyon degil,
daha yuksek R:R demek.

Onemli: sinyal SADECE kapanmis mumda uretilir. Kapanmamis mumla calismak
backtest ile canliyi ayirir ve "lookahead" yanilgisi yaratir.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .config import StrategyConfig
from .indicators import adx, atr, ema, rsi
from .models import LONG, SHORT, Candle, Position, Signal


class Features:
    """Bir mum serisinden turetilen hizalanmis gosterge dizileri."""

    __slots__ = ("closes", "highs", "lows", "ema_fast", "ema_mid", "ema_slow",
                 "rsi", "atr", "adx", "plus_di", "minus_di", "n")

    def __init__(self, candles: Sequence[Candle], cfg: StrategyConfig):
        self.closes = [c.close for c in candles]
        self.highs = [c.high for c in candles]
        self.lows = [c.low for c in candles]
        self.n = len(candles)
        self.ema_fast = ema(self.closes, cfg.ema_fast)
        self.ema_mid = ema(self.closes, cfg.ema_mid)
        self.ema_slow = ema(self.closes, cfg.ema_slow)
        self.rsi = rsi(self.closes, cfg.rsi_period)
        self.atr = atr(self.highs, self.lows, self.closes, cfg.atr_period)
        self.adx, self.plus_di, self.minus_di = adx(
            self.highs, self.lows, self.closes, cfg.adx_period
        )

    def ready(self, i: int) -> bool:
        return all(
            x is not None
            for x in (self.ema_fast[i], self.ema_mid[i], self.ema_slow[i],
                      self.rsi[i], self.atr[i], self.adx[i])
        )


class TrendPullbackStrategy:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    # ------------------------------------------------------------------ giris
    def evaluate(
        self,
        symbol: str,
        candles: Sequence[Candle],
        features: Optional[Features] = None,
        index: Optional[int] = None,
    ) -> Optional[Signal]:
        """Verilen (kapanmis) mum indeksinde sinyal uretir, yoksa None."""
        c = self.cfg
        f = features or Features(candles, c)
        i = f.n - 1 if index is None else index
        if i < c.ema_slow + c.pullback_lookback or not f.ready(i):
            return None

        price = f.closes[i]
        a = f.atr[i]
        if a is None or a <= 0:
            return None

        atr_pct = 100.0 * a / price
        if not (c.atr_pct_min <= atr_pct <= c.atr_pct_max):
            return None
        if f.adx[i] < c.min_adx:
            return None

        long_ok = self._long_setup(f, i)
        short_ok = self._short_setup(f, i)
        if long_ok == short_ok:  # ikisi de yok ya da celiskili
            return None

        side = LONG if long_ok else SHORT
        entry = price
        stop = self._stop_for(f, i, side, entry, a)
        dist = abs(entry - stop)
        if dist <= 0:
            return None

        if side == LONG:
            tp1, tp2 = entry + c.tp1_r * dist, entry + c.tp2_r * dist
        else:
            tp1, tp2 = entry - c.tp1_r * dist, entry - c.tp2_r * dist

        return Signal(
            symbol=symbol,
            side=side,
            entry=entry,
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            atr=a,
            reason=f"{'trend-up' if side == LONG else 'trend-down'} pullback devam",
            meta={
                "adx": round(f.adx[i], 2),
                "rsi": round(f.rsi[i], 2),
                "atr_pct": round(atr_pct, 3),
                "stop_atr": round(dist / a, 2),
                "ema_fast": round(f.ema_fast[i], 6),
                "ema_slow": round(f.ema_slow[i], 6),
            },
        )

    def _long_setup(self, f: Features, i: int) -> bool:
        c = self.cfg
        if not (f.ema_mid[i] > f.ema_slow[i] and f.closes[i] > f.ema_slow[i]):
            return False
        if f.plus_di[i] is None or f.minus_di[i] is None or f.plus_di[i] <= f.minus_di[i]:
            return False
        if not (c.rsi_long_min <= f.rsi[i] <= c.rsi_long_max):
            return False
        # geri cekilme: son N mumda EMA_fast'a temas / alti
        window = range(max(0, i - c.pullback_lookback), i)
        touched = any(
            f.ema_fast[j] is not None and f.lows[j] <= f.ema_fast[j] for j in window
        )
        if not touched:
            return False
        # devam tetigi: EMA_fast uzerinde kapanis + onceki tepeyi asma
        return f.closes[i] > f.ema_fast[i] and f.closes[i] > f.highs[i - 1]

    def _short_setup(self, f: Features, i: int) -> bool:
        c = self.cfg
        if not (f.ema_mid[i] < f.ema_slow[i] and f.closes[i] < f.ema_slow[i]):
            return False
        if f.plus_di[i] is None or f.minus_di[i] is None or f.minus_di[i] <= f.plus_di[i]:
            return False
        if not (c.rsi_short_min <= f.rsi[i] <= c.rsi_short_max):
            return False
        window = range(max(0, i - c.pullback_lookback), i)
        touched = any(
            f.ema_fast[j] is not None and f.highs[j] >= f.ema_fast[j] for j in window
        )
        if not touched:
            return False
        return f.closes[i] < f.ema_fast[i] and f.closes[i] < f.lows[i - 1]

    def _stop_for(self, f: Features, i: int, side: str, entry: float, a: float) -> float:
        """Stop = yapisal seviye (swing) + ATR tamponu, ATR ile sinirlandirilmis.

        Alt sinir gurultuye takilmayi, ust sinir tek islemde asiri genis
        stop yuzunden mikroskobik pozisyon acmayi engeller.
        """
        c = self.cfg
        lo = max(0, i - c.swing_lookback + 1)
        min_dist = 0.8 * a
        max_dist = c.stop_atr_mult * a
        if side == LONG:
            swing = min(f.lows[lo : i + 1])
            dist = entry - swing + c.stop_buffer_atr * a
        else:
            swing = max(f.highs[lo : i + 1])
            dist = swing - entry + c.stop_buffer_atr * a
        dist = max(min_dist, min(dist, max_dist))
        return entry - dist if side == LONG else entry + dist

    # ------------------------------------------------------------- yonetim
    def manage(self, pos: Position, candle: Candle, current_atr: float) -> List[dict]:
        """Acik pozisyon icin yapilacak islemler listesi.

        Sira onemli: once stop (kotumser varsayim), sonra hedefler.
        Ayni mumda hem stop hem hedef gorunuyorsa stop varsayilir -- bu
        backtest'i gercekcilestiren en kritik kural.
        """
        c = self.cfg
        actions: List[dict] = []
        d = pos.direction

        if pos.stop_hit(candle.low, candle.high):
            actions.append({"type": "exit", "price": pos.stop, "reason": "stop", "portion": 1.0})
            return actions

        hit_tp1 = (candle.high >= pos.tp1) if d > 0 else (candle.low <= pos.tp1)
        if not pos.tp1_filled and hit_tp1 and c.tp1_size_pct < 100:
            actions.append(
                {"type": "partial", "price": pos.tp1, "reason": "tp1",
                 "portion": c.tp1_size_pct / 100.0}
            )
        elif not pos.tp1_filled and hit_tp1 and c.tp1_size_pct >= 100:
            actions.append({"type": "exit", "price": pos.tp1, "reason": "tp1", "portion": 1.0})
            return actions

        hit_tp2 = (candle.high >= pos.tp2) if d > 0 else (candle.low <= pos.tp2)
        if hit_tp2:
            actions.append({"type": "exit", "price": pos.tp2, "reason": "tp2", "portion": 1.0})
            return actions

        # Basabas ve iz suren stop -- sadece stop'u LEHE tasir, asla geriye almaz.
        r_now = pos.r_multiple(candle.close)
        if not pos.breakeven_moved and r_now >= c.breakeven_at_r:
            be = pos.entry_price + d * (0.05 * pos.initial_risk_per_unit)  # komisyon payi
            if (d > 0 and be > pos.stop) or (d < 0 and be < pos.stop):
                actions.append({"type": "move_stop", "price": be, "reason": "breakeven"})

        if r_now >= c.breakeven_at_r and current_atr > 0:
            trail = candle.close - d * c.trail_atr_mult * current_atr
            if (d > 0 and trail > pos.stop) or (d < 0 and trail < pos.stop):
                actions.append({"type": "move_stop", "price": trail, "reason": "trail"})

        if pos.bars_held >= c.max_bars_in_trade:
            actions.append({"type": "exit", "price": candle.close, "reason": "zaman-asimi", "portion": 1.0})

        return actions


def build_strategy(cfg: StrategyConfig) -> TrendPullbackStrategy:
    if cfg.name != "trend_pullback":
        raise ValueError(f"bilinmeyen strateji: {cfg.name}")
    return TrendPullbackStrategy(cfg)
