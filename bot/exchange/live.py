"""Canli/testnet broker.

Tasarim ilkesi: **koruma emri borsada durur.** Bot cokerse, internet giderse,
container yeniden baslarsa stop hala aktiftir. Botun hafizasinda tutulan
"ben zararda cikarim" sozu koruma degildir.

Akis:
  1. marj tipi + kaldirac ayarlanir
  2. MARKET giris emri
  3. STOP_MARKET (closePosition=true)  -> felaket korumasi
  4. TAKE_PROFIT_MARKET kismi (reduceOnly) + TAKE_PROFIT_MARKET closePosition
  5. her dongude borsadaki pozisyon ile yerel kayit karsilastirilir (reconcile)
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from ..config import Config
from ..models import LONG, SHORT, Position, SymbolFilters, Trade
from ..state import Store
from .base import Broker
from .binance import BinanceError, BinanceFutures

log = logging.getLogger(__name__)


class LiveBroker(Broker):
    def __init__(self, cfg: Config, client: BinanceFutures, store: Store):
        self.cfg = cfg
        self.client = client
        self.store = store
        self._positions: Dict[str, Position] = store.load_positions()
        self._prepared: set[str] = set()
        self.client.sync_time()

    # --------------------------------------------------------------- hesap
    def equity(self) -> float:
        return self.client.balances()["equity"]

    def free_margin(self) -> float:
        return self.client.balances()["available"]

    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    def prepare_symbol(self, symbol: str, leverage: int) -> None:
        key = f"{symbol}:{leverage}"
        if key in self._prepared:
            return
        self.client.set_margin_type(symbol, self.cfg.account.margin_type)
        self.client.set_leverage(symbol, leverage)
        self._prepared = {k for k in self._prepared if not k.startswith(f"{symbol}:")}
        self._prepared.add(key)

    # -------------------------------------------------------------- islemler
    def open_position(self, signal, qty: float, leverage: int) -> Optional[Position]:
        symbol = signal.symbol
        if symbol in self._positions:
            return None
        f: SymbolFilters = self.client.filters(symbol)
        self.prepare_symbol(symbol, leverage)

        side = "BUY" if signal.side == LONG else "SELL"
        exit_side = "SELL" if signal.side == LONG else "BUY"
        now = int(time.time() * 1000)
        cid = f"edith{now}"

        order = self.client.market_order(symbol, side, qty, client_id=cid)
        avg = float(order.get("avgPrice") or 0) or signal.entry
        filled = float(order.get("executedQty") or qty)
        if filled <= 0:
            raise BinanceError(-1, "giris emri dolmadi")

        drift = avg - signal.entry
        stop = f.round_price(signal.stop + drift)
        tp1 = f.round_price(signal.tp1 + drift)
        tp2 = f.round_price(signal.tp2 + drift)

        pos = Position(
            symbol=symbol, side=signal.side, qty=filled, entry_price=avg,
            stop=stop, tp1=tp1, tp2=tp2,
            initial_risk_per_unit=abs(avg - stop), opened_at=now, leverage=leverage,
            initial_qty=filled, entry_reason=signal.reason, client_id=cid,
        )
        try:
            self._place_protection(pos, exit_side, f)
        except Exception:
            # Koruma emri konulamadiysa ciplak pozisyon tasimak yasak.
            log.exception("Koruma emri basarisiz - pozisyon aninda kapatiliyor")
            self.client.market_order(symbol, exit_side, filled, reduce_only=True)
            raise

        self._positions[symbol] = pos
        self.store.save_position(pos)
        log.info("[LIVE] GIRIS %s %s qty=%s @ %.4f stop=%.4f tp1=%.4f tp2=%.4f",
                 pos.side, symbol, filled, avg, stop, tp1, tp2)
        return pos

    def _place_protection(self, pos: Position, exit_side: str, f: SymbolFilters) -> None:
        self.client.stop_market(pos.symbol, exit_side, pos.stop, client_id=f"{pos.client_id}sl")
        tp1_qty = f.round_qty(pos.qty * self.cfg.strategy.tp1_size_pct / 100.0)
        if 0 < tp1_qty < pos.qty and f.qty_ok(tp1_qty, pos.tp1):
            self.client.take_profit_market(pos.symbol, exit_side, pos.tp1, qty=tp1_qty,
                                           client_id=f"{pos.client_id}t1")
        self.client.take_profit_market(pos.symbol, exit_side, pos.tp2,
                                       client_id=f"{pos.client_id}t2")

    def update_stop(self, symbol: str, new_stop: float) -> None:
        pos = self._positions.get(symbol)
        if not pos:
            return
        f = self.client.filters(symbol)
        new_stop = f.round_price(new_stop)
        if new_stop == pos.stop:
            return
        exit_side = "SELL" if pos.side == LONG else "BUY"
        # Once yeni koruma, sonra eskilerin iptali mumkun degil (closePosition tekil):
        # bu yuzden iptal + yeniden kurma sirasinda hata olursa pozisyon kapatilir.
        try:
            self.client.cancel_all(symbol)
            self._place_protection(
                Position(**{**pos.__dict__, "stop": new_stop}), exit_side, f
            )
        except Exception:
            log.exception("Stop guncellenemedi - pozisyon kapatiliyor (korumasiz kalmaktansa)")
            self.close_position(symbol, 1.0, pos.stop, "koruma-hatasi")
            return
        pos.stop = new_stop
        pos.breakeven_moved = True
        self.store.save_position(pos)
        log.info("[LIVE] STOP guncellendi %s -> %.4f", symbol, new_stop)

    def close_position(self, symbol: str, portion: float, price_hint: float,
                       reason: str) -> Optional[Trade]:
        pos = self._positions.get(symbol)
        if not pos:
            return None
        f = self.client.filters(symbol)
        qty = f.round_qty(pos.qty if portion >= 1.0 else pos.qty * portion)
        if qty <= 0:
            return None
        exit_side = "SELL" if pos.side == LONG else "BUY"
        if portion >= 1.0:
            self.client.cancel_all(symbol)
        self.client.market_order(symbol, exit_side, qty, reduce_only=True)
        return self._finalize(pos, reason) if portion >= 1.0 else None

    # ------------------------------------------------------------ mutabakat
    def reconcile(self) -> list[Trade]:
        """Borsadaki gercek pozisyonlarla yerel kaydi esitler.

        Stop veya hedef emirleri bot uyurken tetiklenmis olabilir; kapanan
        islemi burada yakalayip gercek PnL ile kaydediyoruz.
        """
        closed: list[Trade] = []
        try:
            live = {p["symbol"]: p for p in self.client.position_risk()}
        except BinanceError:
            log.exception("positionRisk okunamadi, mutabakat atlandi")
            return closed

        for symbol, pos in list(self._positions.items()):
            exch = live.get(symbol)
            if exch is None:
                trade = self._finalize(pos, "borsada-kapandi")
                if trade:
                    closed.append(trade)
                continue
            amt = abs(float(exch["positionAmt"]))
            if amt < pos.qty * 0.98:  # kismi hedef dolmus
                pos.qty = amt
                pos.tp1_filled = True
                self.store.save_position(pos)

        for symbol, exch in live.items():
            if symbol not in self._positions and symbol in self.cfg.symbols:
                log.warning("Bota ait olmayan acik pozisyon: %s (%s). Dokunulmuyor.",
                            symbol, exch["positionAmt"])
        return closed

    def _finalize(self, pos: Position, reason: str) -> Optional[Trade]:
        """Gerceklesmis PnL'i borsanin kendi kayitlarindan toplar."""
        realized = 0.0
        commission = 0.0
        exit_price = pos.entry_price
        try:
            fills = self.client.user_trades(pos.symbol, pos.opened_at - 60_000)
            for t in fills:
                realized += float(t.get("realizedPnl", 0))
                commission += float(t.get("commission", 0))
                if float(t.get("realizedPnl", 0)) != 0:
                    exit_price = float(t.get("price", exit_price))
        except BinanceError:
            log.exception("userTrades okunamadi, PnL tahmini kullanilacak")
            realized = pos.realized_pnl

        net = realized - commission
        risk_total = pos.initial_risk_per_unit * pos.initial_qty
        trade = Trade(
            symbol=pos.symbol, side=pos.side, qty=pos.initial_qty,
            entry_price=pos.entry_price, exit_price=exit_price,
            opened_at=pos.opened_at, closed_at=int(time.time() * 1000),
            pnl=net, fees=commission,
            r_multiple=(net / risk_total) if risk_total > 0 else 0.0,
            exit_reason=reason, entry_reason=pos.entry_reason,
        )
        self._positions.pop(pos.symbol, None)
        self.store.clear_position(pos.symbol)
        self.store.record_trade(trade)
        try:
            self.client.cancel_all(pos.symbol)  # artik pozisyon yok, artik emir kalmasin
        except BinanceError:
            pass
        log.info("[LIVE] KAPANDI %s %s pnl=%.2f (%s)", pos.side, pos.symbol, net, reason)
        return trade
