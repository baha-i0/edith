"""Gecmis mum verisi indirme + yerel onbellek.

Binance tek istekte 1500 mum verir; daha uzun gecmis icin sayfalama sart.
Onbellek, ayni veriyi tekrar tekrar cekip rate limite takilmayi onler.
"""

from __future__ import annotations

import csv
import logging
import time
from pathlib import Path
from typing import List, Optional

from .models import Candle

log = logging.getLogger(__name__)

TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
         "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}


def fetch_history(client, symbol: str, interval: str, days: int,
                  cache_dir: str = "data/klines") -> List[Candle]:
    step = TF_MS[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    cache = Path(cache_dir) / f"{symbol}-{interval}-{days}d.csv"

    cached = _load_cache(cache)
    if cached and cached[-1].close_time >= end - 2 * step and cached[0].open_time <= start + step:
        log.info("Onbellekten %d mum okundu (%s)", len(cached), cache)
        return cached

    out: List[Candle] = []
    cursor = start
    while cursor < end:
        batch = client.klines(symbol, interval, limit=1500, start_ms=cursor, end_ms=end)
        if not batch:
            break
        out.extend(batch)
        new_cursor = batch[-1].open_time + step
        if new_cursor <= cursor:
            break
        cursor = new_cursor
        if len(batch) < 1500:
            break
        time.sleep(0.25)  # rate limite saygi

    # tekilleştir + sirala, kapanmamis son mumu at
    seen = {}
    for c in out:
        seen[c.open_time] = c
    candles = [c for _, c in sorted(seen.items()) if c.closed]
    _save_cache(cache, candles)
    log.info("%s icin %d mum indirildi (%d gun)", symbol, len(candles), days)
    return candles


def _load_cache(path: Path) -> Optional[List[Candle]]:
    if not path.exists():
        return None
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        return [
            Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                   float(r[5]), int(r[6]), True)
            for r in rows[1:]
        ]
    except (ValueError, IndexError, OSError):
        log.warning("Onbellek bozuk, yeniden indirilecek: %s", path)
        return None


def _save_cache(path: Path, candles: List[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["open_time", "open", "high", "low", "close", "volume", "close_time"])
        for c in candles:
            w.writerow([c.open_time, c.open, c.high, c.low, c.close, c.volume, c.close_time])
