"""Binance resmi public arsivinden gecmis mum verisi.

Neden gerekli:
  - fapi.binance.com bazi lokasyonlardan 451 (kisitli bolge) donuyor;
    arsiv (data.binance.vision) acik.
  - Arsiv 2020'ye kadar gider; REST API tek istekte 1500 mumla sinirli.

Kaynak: https://github.com/binance/binance-public-data
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, List, Optional

import requests

from .models import Candle

log = logging.getLogger(__name__)

BASE = "https://data.binance.vision/data/futures/um"


def _month_iter(months: int, end: Optional[date] = None) -> Iterator[str]:
    d = end or date.today()
    # ic icinde bulunulan ay genelde henuz yayinlanmamis olur
    y, m = d.year, d.month
    for _ in range(months + 1):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        yield f"{y:04d}-{m:02d}"


def fetch_archive(symbol: str, interval: str, months: int = 12,
                  cache_dir: str = "data/archive",
                  session: Optional[requests.Session] = None) -> List[Candle]:
    """Son `months` ayin aylik zip dosyalarini indirir, mum listesi doner."""
    sess = session or requests.Session()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    out: List[Candle] = []

    for ym in sorted(_month_iter(months, None)):
        name = f"{symbol}-{interval}-{ym}.zip"
        local = cache / name
        if not local.exists():
            url = f"{BASE}/monthly/klines/{symbol}/{interval}/{name}"
            try:
                resp = sess.get(url, timeout=60)
            except requests.RequestException as exc:
                log.warning("%s indirilemedi: %s", name, exc)
                continue
            if resp.status_code != 200:
                log.info("%s arsivde yok (HTTP %s)", name, resp.status_code)
                continue
            local.write_bytes(resp.content)
            log.info("%s indirildi (%.1f KB)", name, len(resp.content) / 1024)
        try:
            out.extend(_parse_zip(local))
        except (zipfile.BadZipFile, ValueError):
            log.warning("%s bozuk, siliniyor", local)
            local.unlink(missing_ok=True)

    dedup = {c.open_time: c for c in out}
    candles = [c for _, c in sorted(dedup.items())]
    log.info("%s %s: %d mum (%d ay)", symbol, interval, len(candles), months)
    return candles


def _parse_zip(path: Path) -> List[Candle]:
    candles: List[Candle] = []
    with zipfile.ZipFile(path) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as fh:
            reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
            for row in reader:
                if not row or not row[0].replace(".", "").isdigit():
                    continue  # baslik satiri
                ot = _to_ms(float(row[0]))
                ct = _to_ms(float(row[6]))
                candles.append(Candle(
                    open_time=ot, open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]), volume=float(row[5]),
                    close_time=ct, closed=True,
                ))
    return candles


def _to_ms(ts: float) -> int:
    """Binance 2025 sonrasi bazi dosyalarda mikrosaniye kullaniyor."""
    return int(ts / 1000) if ts > 1e14 else int(ts)
