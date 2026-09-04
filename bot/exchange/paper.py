"""Kagit (paper) broker: gercek fiyat, sahte para.

Amaci "guzel gorunen sonuc" uretmek degil, gercege yakin olmak. Bu yuzden:
  - girisler her zaman spread'in kotu tarafindan + slipaj ile dolar
  - her giris ve cikista taker komisyonu kesilir
  - funding maliyeti 8 saatlik periyotlarda pozisyondan dusulur
Kagitta kar etmeyen bir kurulum canlida asla kar etmez.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

from ..config import Config
from ..models import LONG, SHORT, Position, Trade
from ..state import Store
from .base import Broker, MarketData

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    def __init__(self, cfg: Config, market: MarketData, store: Store):
        self.cfg = cfg
        self.market = market
        self.store = store
        saved = store.get_kv("paper_balance")
        self.balance = float(saved["balance"]) if saved else cfg.account.paper_start_balance
        self._positions: Dict[str, Position] = store.load_positions()

    # ------------------------------------------------------------- durum
    def _persist_balance(self) -> None:
        self.store.set_kv("paper_balance", {"balance": self.balance})

    def equity(self) -> float:
        total = self.balance
        for sym, pos in self._positions.items():
            try:
                total += pos.unrealized(self.market.book_ticker(sym)["mid"])
            except Exception:  # fiyat alinamazsa gerceklesmemis kari sayma
                pass
        return total

    def free_margin(self) -> float:
        used = sum(p.notional(p.entry_price) / p.leverage for p in self._positions.values())
        return max(0.0, self.balance - used)

    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    # ------------------------------------------------------------- islemler
    def _fill_price(self, symbol: str, side: str, is_entry: bool) -> float:
        book = self.market.book_ticker(symbol)
        slip = self.cfg.execution.slippage_bps / 10_000.0
        buying = (side == LONG) == is_entry  # long girisi ve short cikisi = alis
        price = book["ask"] if buying else book["bid"]
        if not price:
            price = book["mid"]
        return price * (1 + slip) if buying else price * (1 - slip)

    def open_position(self, signal, qty: float, leverage: int) -> Optional[Position]:
        if signal.symbol in self._positions:
            return None
        price = self._fill_price(signal.symbol, signal.side, is_entry=True)
        fee = price * qty * self.cfg.execution.taker_fee
        self.balance -= fee
        now = int(time.time() * 1000)

        # Giris fiyati kaydigi icin stop/hedefler ayni R mesafesiyle kaydirilir
        drift = price - signal.entry
        pos = Position(
            symbol=signal.symbol, side=signal.side, qty=qty, entry_price=price,
            stop=signal.stop + drift, tp1=signal.tp1 + drift, tp2=signal.tp2 + drift,
            initial_risk_per_unit=abs(price - (signal.stop + drift)),
            opened_at=now, leverage=leverage, initial_qty=qty, fees_paid=fee,
            entry_reason=signal.reason, client_id=f"paper-{now}",
        )
        self._positions[pos.symbol] = pos
        self.store.save_position(pos)
        self._persist_balance()
        log.info("[PAPER] GIRIS %s %s qty=%s @ %.4f stop=%.4f tp2=%.4f",
                 pos.side, pos.symbol, qty, price, pos.stop, pos.tp2)
        return pos

    def close_position(self, symbol: str, portion: float, price_hint: float,
                       reason: str) -> Optional[Trade]:
        pos = self._positions.get(symbol)
        if not pos:
            return None
        portion = max(0.0, min(1.0, portion))
        qty = pos.qty if portion >= 1.0 else pos.qty * portion

        # Stop/hedef emirleri tetiklenmis fiyattan dolar; piyasa cikislari canli fiyattan
        if reason in ("stop", "tp1", "tp2"):
            slip = self.cfg.execution.slippage_bps / 10_000.0
            price = price_hint * (1 - slip) if pos.side == LONG else price_hint * (1 + slip)
        else:
            price = self._fill_price(symbol, pos.side, is_entry=False)

        pnl = (price - pos.entry_price) * qty * pos.direction
        fee = price * qty * self.cfg.execution.taker_fee
        self.balance += pnl - fee
        pos.fees_paid += fee
        pos.realized_pnl += pnl
        now = int(time.time() * 1000)

        if portion >= 1.0 or pos.qty - qty <= 0:
            trade = Trade(
                symbol=symbol, side=pos.side, qty=pos.initial_qty,
                entry_price=pos.entry_price, exit_price=price, opened_at=pos.opened_at,
                closed_at=now, pnl=pos.realized_pnl - pos.fees_paid, fees=pos.fees_paid,
                r_multiple=((pos.realized_pnl - pos.fees_paid) /
                            (pos.initial_risk_per_unit * pos.initial_qty)
                            if pos.initial_risk_per_unit > 0 else 0.0),
                exit_reason=reason, entry_reason=pos.entry_reason,
            )
            del self._positions[symbol]
            self.store.clear_position(symbol)
            self.store.record_trade(trade)
            self._persist_balance()
            log.info("[PAPER] CIKIS %s %s @ %.4f pnl=%.2f (%s)",
                     pos.side, symbol, price, trade.pnl, reason)
            return trade

        pos.qty -= qty
        if reason == "tp1":
            pos.tp1_filled = True
        self.store.save_position(pos)
        self._persist_balance()
        log.info("[PAPER] KISMI CIKIS %s %s qty=%s @ %.4f pnl=%.2f (%s)",
                 pos.side, symbol, qty, price, pnl - fee, reason)
        return None

    def update_stop(self, symbol: str, new_stop: float) -> None:
        pos = self._positions.get(symbol)
        if not pos:
            return
        pos.stop = new_stop
        pos.breakeven_moved = True
        self.store.save_position(pos)

    def apply_funding(self, symbol: str, rate: float) -> None:
        pos = self._positions.get(symbol)
        if not pos:
            return
        # long funding pozitifse oder, negatifse alir; short tam tersi
        cost = pos.notional(pos.entry_price) * rate * pos.direction
        self.balance -= cost
        pos.fees_paid += cost
        self._persist_balance()
