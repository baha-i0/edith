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
        # Kagit modda da maker girisi taklit edilir. Aksi halde prova canliyla
        # ayni olmaz: kagitta market, canlida limit -> farkli fiyat, farkli
        # komisyon, farkli sonuc. Parite sart.
        self._pending: Dict[str, dict] = store.get_kv("paper_pending") or {}

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

    def realized_equity(self) -> float:
        return self.balance

    def free_margin(self) -> float:
        used = sum(p.notional(p.entry_price) / p.leverage for p in self._positions.values())
        return max(0.0, self.balance - used)

    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    def pending_entries(self) -> Dict[str, str]:
        return {sym: rec["side"] for sym, rec in self._pending.items()}

    def pending_risk(self) -> float:
        return sum(abs(r["entry"] - r["stop"]) * r["qty"]
                   for r in self._pending.values())

    def cancel_pending(self) -> int:
        n = len(self._pending)
        self._pending.clear()
        self.store.set_kv("paper_pending", self._pending)
        if n:
            log.info("[PAPER] %d bekleyen giris emri iptal edildi", n)
        return n

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
        e = self.cfg.execution
        if e.entry_order_type == "post_only" and signal.symbol not in self._pending:
            off = e.slippage_bps / 10_000.0
            limit = (signal.entry * (1 - off) if signal.side == LONG
                     else signal.entry * (1 + off))
            now = int(time.time() * 1000)
            self._pending[signal.symbol] = {
                "side": signal.side, "qty": qty, "leverage": leverage, "limit": limit,
                "entry": signal.entry, "stop": signal.stop, "tp1": signal.tp1,
                "tp2": signal.tp2, "reason": signal.reason,
                "meta": dict(getattr(signal, "meta", {}) or {}), "placed_ms": now,
                "deadline_ms": now + _timeframe_ms(self.cfg.timeframe) * e.post_only_wait_bars,
            }
            self.store.set_kv("paper_pending", self._pending)
            log.info("[PAPER] POST_ONLY %s %s qty=%s @ %.4f (limit bekliyor)",
                     signal.side, signal.symbol, qty, limit)
            return None
        return self._fill_entry(signal.symbol, signal.side, qty, leverage,
                                signal.entry, signal.stop, signal.tp1, signal.tp2,
                                signal.reason, dict(getattr(signal, "meta", {}) or {}),
                                maker=False)

    def poll_pending(self) -> list:
        """Bekleyen kagit limit emirlerini kontrol eder.

        Canlidaki mantigin aynisi: fiyat limite geldiyse maker olarak dolar,
        sure dolduysa ayara gore market'e dusulur ya da vazgecilir.
        """
        e = self.cfg.execution
        now = int(time.time() * 1000)
        opened: list = []
        for symbol in list(self._pending):
            rec = self._pending[symbol]
            try:
                book = self.market.book_ticker(symbol)
            except Exception:
                continue
            px = book["ask"] if rec["side"] == LONG else book["bid"]
            px = px or book["mid"]
            hit = px <= rec["limit"] if rec["side"] == LONG else px >= rec["limit"]
            if hit:
                self._pending.pop(symbol)
                self.store.set_kv("paper_pending", self._pending)
                pos = self._fill_entry(symbol, rec["side"], rec["qty"], rec["leverage"],
                                       rec["entry"], rec["stop"], rec["tp1"], rec["tp2"],
                                       rec["reason"], rec.get("meta") or {}, maker=True,
                                       price=rec["limit"])
                if pos:
                    opened.append(pos)
                continue
            if now < rec["deadline_ms"]:
                continue
            self._pending.pop(symbol)
            self.store.set_kv("paper_pending", self._pending)
            if e.post_only_fallback_market:
                pos = self._fill_entry(symbol, rec["side"], rec["qty"], rec["leverage"],
                                       rec["entry"], rec["stop"], rec["tp1"], rec["tp2"],
                                       rec["reason"], rec.get("meta") or {}, maker=False)
                if pos:
                    opened.append(pos)
            else:
                log.info("[PAPER] %s limit dolmadi - islem iptal", symbol)
        return opened

    def _fill_entry(self, symbol: str, side: str, qty: float, leverage: int,
                    sig_entry: float, sig_stop: float, sig_tp1: float, sig_tp2: float,
                    reason: str, meta: dict, *, maker: bool,
                    price: Optional[float] = None) -> Optional[Position]:
        if price is None:
            price = self._fill_price(symbol, side, is_entry=True)
        fee = price * qty * (self.cfg.execution.maker_fee if maker
                             else self.cfg.execution.taker_fee)
        self.balance -= fee
        now = int(time.time() * 1000)

        # Giris fiyati kaydigi icin stop/hedefler ayni R mesafesiyle kaydirilir
        drift = price - sig_entry
        pos = Position(
            symbol=symbol, side=side, qty=qty, entry_price=price,
            stop=sig_stop + drift, tp1=sig_tp1 + drift, tp2=sig_tp2 + drift,
            initial_risk_per_unit=abs(price - (sig_stop + drift)),
            opened_at=now, leverage=leverage, initial_qty=qty, fees_paid=fee,
            entry_reason=reason, client_id=f"paper-{now}", context=dict(meta),
        )
        self._positions[pos.symbol] = pos
        self.store.save_position(pos)
        self._persist_balance()
        log.info("[PAPER] GIRIS%s %s %s qty=%s @ %.4f stop=%.4f tp2=%.4f",
                 "(maker)" if maker else "", pos.side, pos.symbol, qty, price,
                 pos.stop, pos.tp2)
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
                context=dict(pos.context),
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


_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _timeframe_ms(tf: str) -> int:
    return _TF_MS.get(tf, 14_400_000)
