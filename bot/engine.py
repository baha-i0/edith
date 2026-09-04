"""Ticaret motoru: dongu, filtreler, emir kararlari.

Motor hangi ortamda oldugunu bilmez -- Broker arayuzu uzerinden calisir.
Ayni kod paper'da, testnet'te ve canlida ayni kararlari verir.
"""

from __future__ import annotations

import logging
import signal as os_signal
import time
from typing import Dict, List, Optional

from .config import Config
from .exchange.base import Broker, MarketData
from .exchange.live import LiveBroker
from .exchange.paper import PaperBroker
from .models import LONG, Candle, Position, Trade
from .notify import Notifier
from .risk import RiskGuard, size_position, validate_signal_quality
from .state import Store
from .strategy import Features, TrendPullbackStrategy, build_strategy

log = logging.getLogger(__name__)

TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
         "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}


class TradingEngine:
    def __init__(self, cfg: Config, market: MarketData, broker: Broker, store: Store):
        self.cfg = cfg
        self.market = market
        self.broker = broker
        self.store = store
        self.strategy: TrendPullbackStrategy = build_strategy(cfg.strategy)
        self.guard = RiskGuard(cfg, store.load_risk_state())
        self.notifier = Notifier()
        self._last_bar: Dict[str, int] = {}
        self._running = True

    # ------------------------------------------------------------ yasam dongusu
    def install_signal_handlers(self) -> None:
        for sig in (os_signal.SIGINT, os_signal.SIGTERM):
            os_signal.signal(sig, self._stop)

    def _stop(self, *_args) -> None:
        log.warning("Kapatma sinyali alindi, dongu bitirilecek...")
        self._running = False

    def run_forever(self) -> None:
        self.install_signal_handlers()
        log.info("Bot basladi | mod=%s | semboller=%s | tf=%s | equity=%.2f",
                 self.cfg.mode, ",".join(self.cfg.symbols), self.cfg.timeframe,
                 self.broker.equity())
        log.info("Basabas isabet orani (komisyon haric): %%%.1f | agirlikli hedef R=%.2f",
                 self.cfg.breakeven_win_rate() * 100, self.cfg.blended_target_r())
        while self._running:
            started = time.time()
            try:
                self.tick()
            except Exception:
                log.exception("Dongu hatasi - bot devam ediyor")
            self.store.save_risk_state(self.guard.state)
            elapsed = time.time() - started
            time.sleep(max(1.0, self.cfg.loop_seconds - elapsed))
        self.store.save_risk_state(self.guard.state)
        log.info("Bot durdu. Acik pozisyonlarin koruma emirleri borsada duruyor.")

    # -------------------------------------------------------------------- tick
    def tick(self) -> None:
        now = int(time.time() * 1000)
        equity = self.broker.equity()
        self.store.record_equity(equity, now)
        self.guard.roll_day(now, equity)

        if isinstance(self.broker, LiveBroker):
            for trade in self.broker.reconcile():
                self._on_trade_closed(trade, now)

        for symbol in self.cfg.symbols:
            try:
                self.process_symbol(symbol, now, equity)
            except Exception:
                log.exception("%s islenirken hata", symbol)

    def process_symbol(self, symbol: str, now: int, equity: float) -> None:
        candles = self.market.klines(symbol, self.cfg.timeframe,
                                     limit=self.cfg.strategy.warmup_bars)
        closed = [c for c in candles if c.closed]
        if len(closed) < self.cfg.strategy.ema_slow + 20:
            log.warning("%s: yeterli mum yok (%d)", symbol, len(closed))
            return

        feats = Features(closed, self.cfg.strategy)
        last = closed[-1]

        pos = self.broker.positions().get(symbol)
        if pos:
            self._manage(pos, last, feats, now)
            return

        # Ayni mumda birden fazla giris denemesi yok
        if self._last_bar.get(symbol) == last.open_time:
            return

        allowed, why = self.guard.can_open(now, len(self.broker.positions()), equity)
        if not allowed:
            log.debug("%s: giris kapali (%s)", symbol, why)
            return

        sig = self.strategy.evaluate(symbol, closed, feats)
        if not sig:
            return
        self._last_bar[symbol] = last.open_time

        ok, reason = validate_signal_quality(sig, self.cfg)
        if not ok:
            log.info("%s sinyal elendi: %s", symbol, reason)
            return
        if not self._microstructure_ok(symbol, now):
            return

        filters = self.market.filters(symbol)
        sizing = size_position(
            equity=equity, free_margin=self.broker.free_margin(),
            entry=sig.entry, stop=sig.stop, filters=filters,
            risk_cfg=self.cfg.risk, desired_leverage=self.cfg.account.leverage,
        )
        if not sizing.ok:
            log.info("%s pozisyon acilmadi: %s", symbol, sizing.reason)
            return

        log.info("%s SINYAL %s | giris=%.4f stop=%.4f tp1=%.4f tp2=%.4f | R:R=%.2f | %s | %s",
                 symbol, sig.side, sig.entry, sig.stop, sig.tp1, sig.tp2,
                 sig.reward_risk, sizing.reason, sig.meta)

        opened = self.broker.open_position(sig, sizing.qty, sizing.leverage)
        if opened:
            self.guard.record_open(now)
            self.store.save_risk_state(self.guard.state)
            self.notifier.send(
                f"GIRIS {sig.side} {symbol} @ {opened.entry_price:.4f}\n"
                f"stop {opened.stop:.4f} | tp2 {opened.tp2:.4f}\n"
                f"risk {sizing.risk_amount:.2f} USDT ({self.cfg.risk.risk_per_trade_pct}%) "
                f"| {sizing.leverage}x"
            )

    # ---------------------------------------------------------------- yonetim
    def _manage(self, pos: Position, last: Candle, feats: Features, now: int) -> None:
        bar_ms = TF_MS[self.cfg.timeframe]
        pos.bars_held = max(0, int((now - pos.opened_at) / bar_ms))
        cur_atr = feats.atr[-1] or 0.0

        # Canlida stop/hedef borsada duruyor; motor sadece stop'u lehe tasir
        live_mode = isinstance(self.broker, LiveBroker)
        actions = self.strategy.manage(pos, last, cur_atr)

        for act in actions:
            if act["type"] == "move_stop":
                self.broker.update_stop(pos.symbol, act["price"])
                log.info("%s stop %s -> %.4f", pos.symbol, act["reason"], act["price"])
            elif live_mode and act["reason"] in ("stop", "tp1", "tp2"):
                continue  # borsadaki emirler halleder, mutabakat yakalar
            elif act["type"] == "partial":
                self.broker.close_position(pos.symbol, act["portion"], act["price"], act["reason"])
            elif act["type"] == "exit":
                trade = self.broker.close_position(pos.symbol, 1.0, act["price"], act["reason"])
                if trade:
                    self._on_trade_closed(trade, now)
                return

        # Kagit modda mum arasi hareket icin canli fiyat kontrolu
        if not live_mode:
            self._intrabar_check(pos, now)

    def _intrabar_check(self, pos: Position, now: int) -> None:
        try:
            mid = self.market.book_ticker(pos.symbol)["mid"]
        except Exception:
            return
        if not mid:
            return
        d = pos.direction
        if (d > 0 and mid <= pos.stop) or (d < 0 and mid >= pos.stop):
            trade = self.broker.close_position(pos.symbol, 1.0, pos.stop, "stop")
            if trade:
                self._on_trade_closed(trade, now)
        elif (d > 0 and mid >= pos.tp2) or (d < 0 and mid <= pos.tp2):
            trade = self.broker.close_position(pos.symbol, 1.0, pos.tp2, "tp2")
            if trade:
                self._on_trade_closed(trade, now)

    def _on_trade_closed(self, trade: Trade, now: int) -> None:
        self.guard.record_close(now, trade.pnl)
        self.store.save_risk_state(self.guard.state)
        s = self.guard.state
        log.info("KAPANDI %s %s pnl=%.2f (%.2fR) | gun: %.2f USDT / %d islem",
                 trade.side, trade.symbol, trade.pnl, trade.r_multiple,
                 s.realized_pnl_today, s.trades_today)
        self.notifier.send(
            f"KAPANDI {trade.side} {trade.symbol} @ {trade.exit_price:.4f}\n"
            f"PnL {trade.pnl:+.2f} USDT ({trade.r_multiple:+.2f}R) | {trade.exit_reason}\n"
            f"Gunluk: {s.realized_pnl_today:+.2f} USDT"
        )

    # ---------------------------------------------------------- mikroyapi filtresi
    def _microstructure_ok(self, symbol: str, now: int) -> bool:
        """Spread ve funding kontrolu.

        Genis spread + yaklasan funding, kucuk hedefli islemlerde beklenen
        degeri dogrudan negatife cevirir. Bu kontrol kar getirmez, zarar keser.
        """
        e = self.cfg.execution
        try:
            book = self.market.book_ticker(symbol)
        except Exception:
            log.warning("%s: order book okunamadi, islem atlandi", symbol)
            return False
        if book["spread_bps"] > e.max_spread_bps:
            log.info("%s: spread cok genis (%.1f bps > %.1f)", symbol,
                     book["spread_bps"], e.max_spread_bps)
            return False
        try:
            fund = self.market.funding(symbol)
        except Exception:
            return True  # funding bilgisi yoksa engelleme
        mins_to_funding = (fund["next_funding_ms"] - now) / 60_000 if fund["next_funding_ms"] else 999
        if 0 <= mins_to_funding <= e.avoid_funding_minutes and abs(fund["rate"]) >= e.funding_rate_abort:
            log.info("%s: funding'e %.0f dk kaldi ve oran %.4f%% - islem yok",
                     symbol, mins_to_funding, fund["rate"] * 100)
            return False
        return True


def build_engine(cfg: Config) -> TradingEngine:
    from .exchange.binance import BinanceFutures

    store = Store(cfg.state_path, mode=cfg.mode)
    if cfg.mode == "paper":
        market = BinanceFutures(cfg)  # anahtarsiz public veri
        broker: Broker = PaperBroker(cfg, market, store)
    else:
        client = BinanceFutures(cfg, cfg.api_key, cfg.api_secret,
                                testnet=(cfg.mode == "testnet"))
        market = client
        broker = LiveBroker(cfg, client, store)
    return TradingEngine(cfg, market, broker, store)
