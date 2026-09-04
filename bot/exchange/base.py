"""Broker arayuzu.

Backtest, paper ve live ayni arayuzu konusur. Boylece motor kodu hangi
ortamda oldugunu bilmez -- test edilebilirligin tamami buradan geliyor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ..models import Candle, Position, SymbolFilters


class MarketData(ABC):
    @abstractmethod
    def klines(self, symbol: str, interval: str, limit: int = 500,
               start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[Candle]:
        ...

    @abstractmethod
    def book_ticker(self, symbol: str) -> Dict[str, float]:
        """{'bid': .., 'ask': .., 'spread_bps': ..}"""

    @abstractmethod
    def funding(self, symbol: str) -> Dict[str, float]:
        """{'rate': .., 'next_funding_ms': ..}"""

    @abstractmethod
    def filters(self, symbol: str) -> SymbolFilters:
        ...


class Broker(ABC):
    @abstractmethod
    def equity(self) -> float:
        ...

    @abstractmethod
    def free_margin(self) -> float:
        ...

    @abstractmethod
    def positions(self) -> Dict[str, Position]:
        ...

    @abstractmethod
    def open_position(self, signal, qty: float, leverage: int) -> Optional[Position]:
        ...

    @abstractmethod
    def close_position(self, symbol: str, portion: float, price_hint: float, reason: str):
        ...

    def poll_pending(self) -> list:
        """Tahtada bekleyen giris emirlerini kontrol eder; dolanlari doner.

        Market emirli modda hep bostur. Motor her turda cagirir.
        """
        return []

    def pending_entries(self) -> Dict[str, str]:
        """Tahtada bekleyen giris emirleri: sembol -> yon.

        Sadece post_only modunda dolu olur. Slot ve genislik sayimlari bunu
        hesaba katmak zorunda: emir tahtada beklerken slot MESGULDUR, yoksa
        ayni sembole ikinci emir gider ya da limit asilir.
        """
        return {}

    @abstractmethod
    def update_stop(self, symbol: str, new_stop: float) -> None:
        ...
