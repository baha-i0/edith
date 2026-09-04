import math
import random
from typing import List

import pytest

from bot.config import Config
from bot.models import Candle, SymbolFilters


@pytest.fixture
def cfg() -> Config:
    c = Config()
    c.validate()
    return c


@pytest.fixture
def filters() -> SymbolFilters:
    return SymbolFilters("BNBUSDT", tick_size=0.01, step_size=0.01,
                         min_qty=0.01, min_notional=5.0)


def make_candles(prices, start_ms: int = 1_700_000_000_000, step_ms: int = 300_000,
                 wick: float = 0.002) -> List[Candle]:
    out = []
    for i, p in enumerate(prices):
        prev = prices[i - 1] if i else p
        out.append(Candle(
            open_time=start_ms + i * step_ms,
            open=prev, high=max(prev, p) * (1 + wick), low=min(prev, p) * (1 - wick),
            close=p, volume=100.0, close_time=start_ms + (i + 1) * step_ms - 1, closed=True,
        ))
    return out


@pytest.fixture
def trending_up():
    """Gurultulu ama net yukselen trend - long setup uretmeli.

    Duz bir sinus egrisi gercekci degil: RSI surekli 70+ takilir ve
    strateji hicbir zaman geri cekilme goremez. Rastgele gurultu +
    periyodik duzeltme gercek piyasaya cok daha yakin.
    """
    random.seed(11)
    prices = []
    p = 100.0
    for i in range(500):
        drift = 0.0015
        shock = random.gauss(0, 0.006)
        correction = -0.012 if i % 37 in (0, 1, 2) else 0.0   # periyodik duzeltme
        p *= (1 + drift + shock + correction)
        prices.append(p)
    return make_candles(prices)


@pytest.fixture
def choppy():
    """Yonu olmayan yatay piyasa - islem URETMEMELI."""
    random.seed(42)
    prices = []
    p = 100.0
    for _ in range(400):
        p *= (1 + random.uniform(-0.001, 0.001))
        prices.append(p)
    return make_candles(prices)
