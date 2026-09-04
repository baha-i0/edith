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
        # Tahtada bekleyen post_only giris emirleri. Yeniden baslatmada
        # kaybolmamali: aksi halde borsada sahipsiz bir limit emir kalir.
        self._pending: Dict[str, dict] = (store.get_kv(f"pending_entries:{cfg.mode}") or {})
        self._orphan_trades: list = []
        self._bal_cache: Optional[Dict[str, float]] = None
        self._bal_ts = 0.0
        self.client.sync_time()

    # --------------------------------------------------------------- hesap
    def _balances(self) -> Dict[str, float]:
        """Bakiye sorgusunu tur icinde bir kez yapar.

        equity(), free_margin() ve realized_equity() ayni turda arka arkaya
        cagriliyor; her biri icin ayri istek atmak gereksiz.
        """
        now = time.time()
        if self._bal_cache is None or now - self._bal_ts > 2.0:
            self._bal_cache = self.client.balances()
            self._bal_ts = now
        return self._bal_cache

    def equity(self) -> float:
        return self._balances()["equity"]

    def realized_equity(self) -> float:
        # totalWalletBalance: gerceklesmemis PnL DAHIL DEGIL.
        b = self._balances()
        return b.get("wallet") or b["equity"]

    def free_margin(self) -> float:
        return self._balances()["available"]

    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    def pending_entries(self) -> Dict[str, str]:
        return {sym: rec["side"] for sym, rec in self._pending.items()}

    def pending_risk(self) -> float:
        """Tahtada bekleyen giris emirlerinin toplam riski (USDT).

        Bunlar henuz Position degil, ama para TAAHHUT EDILMIS durumda.
        Sayilmazsa ayni turda secilen N aday da ayni 'acik risk' degerini
        gorur ve yastik tavani N kez bagimsiz uygulanir -- yani tavan
        fiilen N katina cikar.
        """
        return sum(abs(r["entry"] - r["stop"]) * r["qty"]
                   for r in self._pending.values())

    def cancel_pending(self) -> int:
        """Tahtadaki bekleyen giris emirlerini borsadan iptal eder."""
        n = 0
        for symbol in list(self._pending):
            rec = self._pending[symbol]
            try:
                self.client.cancel_order(symbol, rec["cid"])
                n += 1
            except BinanceError:
                # Emir bu arada dolmus ya da zaten iptal olmus olabilir.
                # Dolduysa reconcile() bir sonraki turda pozisyonu yakalar.
                log.warning("%s bekleyen emir iptal edilemedi", symbol, exc_info=True)
            self._pending.pop(symbol, None)
        self._save_pending()
        if n:
            log.warning("[LIVE] %d bekleyen giris emri iptal edildi", n)
        return n

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
        now = int(time.time() * 1000)
        cid = f"edith{now}"

        if self.cfg.execution.entry_order_type == "post_only" and symbol not in self._pending:
            return self._place_post_only(signal, qty, leverage, f, side, cid, now)
        return self._market_entry(signal, qty, leverage, f, side, cid, now)

    def _market_entry(self, signal, qty: float, leverage: int, f: SymbolFilters,
                      side: str, cid: str, now: int) -> Optional[Position]:
        symbol = signal.symbol
        exit_side = "SELL" if side == "BUY" else "BUY"
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
            context=dict(getattr(signal, "meta", {}) or {}),
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

    # ------------------------------------------------- post_only giris akisi
    def _save_pending(self) -> None:
        self.store.set_kv(f"pending_entries:{self.cfg.mode}", self._pending)

    def _place_post_only(self, signal, qty: float, leverage: int, f: SymbolFilters,
                         side: str, cid: str, now: int) -> None:
        """Maker limit emri tahtaya yazar. Pozisyon HENUZ acilmadi.

        Emir fiyatin gerisine konur: long icin altina, short icin ustune.
        Market emirde slipaji odersin; burada ayni kadar iyilesme istersin.
        Dolup dolmadigi her dongude poll_pending() ile kontrol edilir.
        """
        e = self.cfg.execution
        off = e.slippage_bps / 10_000.0
        limit = f.round_price(signal.entry * (1 - off) if signal.side == LONG
                              else signal.entry * (1 + off))
        try:
            self.client.post_only_order(signal.symbol, side, qty, limit, client_id=cid)
        except BinanceError as exc:
            # GTX reddi = emir aninda dolacakti (taker olurdu). Hata degil.
            log.info("%s post_only reddedildi (%s) - market'e dusuluyor",
                     signal.symbol, exc)
            return self._market_entry(signal, qty, leverage, f, side, cid, now)
        # Bekleme suresi bar cinsinden verilir; saniyeye cevir.
        bar_ms = _timeframe_ms(self.cfg.timeframe)
        self._pending[signal.symbol] = {
            "cid": cid, "side": signal.side, "qty": qty, "leverage": leverage,
            "limit": limit, "stop": signal.stop, "tp1": signal.tp1, "tp2": signal.tp2,
            "entry": signal.entry, "reason": signal.reason,
            "meta": dict(getattr(signal, "meta", {}) or {}),
            "placed_ms": now, "deadline_ms": now + bar_ms * e.post_only_wait_bars,
        }
        self._save_pending()
        log.info("[LIVE] POST_ONLY %s %s qty=%s @ %.4f (limit tahtada)",
                 signal.side, signal.symbol, qty, limit)
        return None

    def poll_pending(self) -> list:
        """Bekleyen limit emirlerini kontrol eder. Dolanlari pozisyona cevirir.

        Her dongude cagrilir. Uc sonuc mumkun:
          FILLED               -> pozisyon ac, korumayi hemen kur
          suresi doldu, bos    -> iptal; ayara gore market ile gir ya da vazgec
          kismi dolum          -> iptal; dolan miktar yeterliyse onunla devam
        """
        opened: list = []
        for symbol in list(self._pending):
            rec = self._pending[symbol]
            try:
                o = self.client.query_order(symbol, rec["cid"])
            except BinanceError:
                # Sonsuza kadar denemek sembolu KALICI olarak bloke eder:
                # kayit _pending'de kalir, pending_entries onu raporlar,
                # _allocate o sembolu atlar ve open_position yeni emir
                # koymaz. Borsa iptal edilmis bir emri bir sure sonra
                # unutur (-2013), yani bu hata kalici olabilir.
                rec["hata"] = int(rec.get("hata", 0)) + 1
                if rec["hata"] < 5:
                    log.warning("%s bekleyen emir sorgulanamadi (%d/5)",
                                symbol, rec["hata"], exc_info=True)
                    self._pending[symbol] = rec
                    self._save_pending()
                    continue
                log.error("%s bekleyen emir 5 kez sorgulanamadi - kayit "
                          "dusuruluyor, emir iptal deneniyor", symbol)
                try:
                    self.client.cancel_order(symbol, rec["cid"])
                except BinanceError:
                    pass
                self._pending.pop(symbol, None)
                self._save_pending()
                continue
            status = str(o.get("status", ""))
            done = float(o.get("executedQty") or 0)
            now = int(time.time() * 1000)

            if status == "FILLED":
                self._pending.pop(symbol); self._save_pending()
                pos = self._adopt_fill(rec, symbol, done or rec["qty"],
                                       float(o.get("avgPrice") or rec["limit"]), now)
                if pos:
                    opened.append(pos)
                continue

            if status in ("CANCELED", "EXPIRED", "REJECTED"):
                self._pending.pop(symbol); self._save_pending()
                if done > 0:
                    pos = self._adopt_fill(rec, symbol, done,
                                           float(o.get("avgPrice") or rec["limit"]), now)
                    if pos:
                        opened.append(pos)
                elif self.cfg.execution.post_only_fallback_market:
                    pos = self._fallback(rec, symbol, now)
                    if pos:
                        opened.append(pos)
                continue

            if now < rec["deadline_ms"]:
                continue

            # Sure doldu: iptal et, sonra karar ver.
            try:
                self.client.cancel_order(symbol, rec["cid"])
            except BinanceError:
                log.warning("%s bekleyen emir iptal edilemedi", symbol, exc_info=True)
                continue
            self._pending.pop(symbol); self._save_pending()
            if done > 0:
                pos = self._adopt_fill(rec, symbol, done,
                                       float(o.get("avgPrice") or rec["limit"]), now)
                if pos:
                    opened.append(pos)
            elif self.cfg.execution.post_only_fallback_market:
                log.info("%s limit dolmadi - market ile giriliyor", symbol)
                pos = self._fallback(rec, symbol, now)
                if pos:
                    opened.append(pos)
            else:
                log.info("%s limit dolmadi - islem iptal", symbol)
        return opened

    def _adopt_fill(self, rec: dict, symbol: str, filled: float, avg: float,
                    now: int) -> Optional[Position]:
        """Dolan limit emrini pozisyona cevirir ve korumayi kurar."""
        f = self.client.filters(symbol)
        filled = f.round_qty(filled)
        if filled <= 0 or not f.qty_ok(filled, avg):
            log.warning("%s dolan miktar cok kucuk (%s) - kapatiliyor", symbol, filled)
            if filled > 0:
                self.client.market_order(
                    symbol, "SELL" if rec["side"] == LONG else "BUY",
                    filled, reduce_only=True)
            return None
        drift = avg - rec["entry"]
        pos = Position(
            symbol=symbol, side=rec["side"], qty=filled, entry_price=avg,
            stop=f.round_price(rec["stop"] + drift),
            tp1=f.round_price(rec["tp1"] + drift),
            tp2=f.round_price(rec["tp2"] + drift),
            initial_risk_per_unit=abs(avg - (rec["stop"] + drift)), opened_at=now,
            leverage=rec["leverage"], initial_qty=filled,
            entry_reason=rec["reason"], client_id=rec["cid"],
            context=dict(rec.get("meta") or {}),
        )
        exit_side = "SELL" if pos.side == LONG else "BUY"
        try:
            self._place_protection(pos, exit_side, f)
        except Exception:
            log.exception("Koruma emri basarisiz - pozisyon aninda kapatiliyor")
            self.client.market_order(symbol, exit_side, filled, reduce_only=True)
            return None
        self._positions[symbol] = pos
        self.store.save_position(pos)
        log.info("[LIVE] GIRIS(maker) %s %s qty=%s @ %.4f stop=%.4f",
                 pos.side, symbol, filled, avg, pos.stop)
        return pos

    def _fallback(self, rec: dict, symbol: str, now: int) -> Optional[Position]:
        sig = _SigView(rec, symbol)
        f = self.client.filters(symbol)
        side = "BUY" if rec["side"] == LONG else "SELL"
        return self._market_entry(sig, rec["qty"], rec["leverage"], f, side,
                                  f"{rec['cid']}m", now)

    def _ensure_protected(self, pos: Position) -> None:
        """Stop emri BORSADA hala duruyor mu? Durmuyorsa yeniden kurar.

        Bu modulun tum guvenlik tasarimi "koruma emri borsada durur"
        varsayimina dayaniyordu ama hicbir yerde DOGRULANMIYORDU.
        Korumasiz kalmanin uc yolu var ve hicbiri crash gerektirmiyor:
          - kullanici Binance uygulamasindan stop'u elle iptal eder
          - close_position once cancel_all yapar, sonraki market emri
            reddedilirse pozisyon sifir emirle kalir
          - giris ile save_position arasinda surec olur

        reconcile sadece positionAmt'a bakiyordu; miktar degismedigi icin
        hicbiri fark edilmiyordu. Kaldiracli bir pozisyon 33 gune kadar
        korumasiz tasinabiliyordu.
        """
        try:
            acik = self.client.open_orders(pos.symbol)
        except BinanceError:
            log.warning("%s acik emirler okunamadi, koruma dogrulanamadi",
                        pos.symbol, exc_info=True)
            return
        if any(str(o.get("type", "")).startswith("STOP") for o in acik):
            return
        log.error("%s KORUMASIZ: borsada stop emri yok. Yeniden kuruluyor.",
                  pos.symbol)
        exit_side = "SELL" if pos.side == LONG else "BUY"
        try:
            self._place_protection(pos, exit_side, self.client.filters(pos.symbol))
            log.warning("%s koruma yeniden kuruldu (stop %.4f)", pos.symbol, pos.stop)
        except Exception:
            log.exception("%s koruma kurulamadi - pozisyon kapatiliyor", pos.symbol)
            trade = self.close_position(pos.symbol, 1.0, pos.stop, "koruma-kurulamadi")
            if trade is not None:
                self._orphan_trades.append(trade)

    def _place_protection(self, pos: Position, exit_side: str, f: SymbolFilters) -> None:
        self.client.stop_market(pos.symbol, exit_side, pos.stop, client_id=f"{pos.client_id}sl")
        # TP1 zaten dolduysa YENIDEN KOYMA. Koyarsak fiyat hedefin otesinde
        # oldugu icin borsa ya emri reddeder (-2021 "would immediately
        # trigger") -- ki bu koruma-hatasi yoluna dusup pozisyonu piyasadan
        # kapatir -- ya da kabul edip aninda tetikler ve kalan %50'lik
        # kosucuyu da satar. Iki halde de 4R hedefine giden kisim olur.
        tp1_qty = f.round_qty(pos.qty * self.cfg.strategy.tp1_size_pct / 100.0)
        if not pos.tp1_filled and 0 < tp1_qty < pos.qty and f.qty_ok(tp1_qty, pos.tp1):
            self.client.take_profit_market(pos.symbol, exit_side, pos.tp1, qty=tp1_qty,
                                           client_id=f"{pos.client_id}t1")
        self.client.take_profit_market(pos.symbol, exit_side, pos.tp2,
                                       client_id=f"{pos.client_id}t2")

    def update_stop(self, symbol: str, new_stop: float) -> Optional[Trade]:
        """Stop'u tasir. Koruma kurulamazsa pozisyon kapanir ve o islem DONER.

        Eskiden None donuyordu ve kapanan islem sessizce yutuluyordu:
        _on_trade_closed calismadigi icin gunluk zarar sayaci artmiyor,
        ust uste zarar sayaci sifirlanmiyor, ogrenme kaydi olusmuyordu --
        yani gunluk limit o islemi hic gormuyordu.
        """
        pos = self._positions.get(symbol)
        if not pos:
            return None
        f = self.client.filters(symbol)
        new_stop = f.round_price(new_stop)
        if new_stop == pos.stop:
            return None
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
            return self.close_position(symbol, 1.0, pos.stop, "koruma-hatasi")
        pos.stop = new_stop
        pos.breakeven_moved = True
        self.store.save_position(pos)
        log.info("[LIVE] STOP guncellendi %s -> %.4f", symbol, new_stop)
        return None

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
        order = self.client.market_order(symbol, exit_side, qty, reduce_only=True)
        if portion >= 1.0:
            px = float(order.get("avgPrice") or 0) or price_hint
            return self._finalize(pos, reason, fallback_exit=px)

        # KISMI cikis: islem kaydi olusmaz (istatistikleri bozardi) ama
        # gerceklesen kar cuzdana girer. Bunu pozisyonda takip etmezsek
        # nakit akisi tespiti bunu "para yatirma" saniyor -- ve pozisyon
        # tamamen kapandiginda ayni tutari "para cekme" saniyor. Ikisi de
        # tabani yanlis oynatir.
        px = float(order.get("avgPrice") or 0) or price_hint
        pos.realized_pnl += (px - pos.entry_price) * qty * pos.direction
        pos.qty = max(0.0, pos.qty - qty)
        pos.tp1_filled = True
        self.store.save_position(pos)
        return None

    # ------------------------------------------------------------ mutabakat
    def reconcile(self) -> list[Trade]:
        """Borsadaki gercek pozisyonlarla yerel kaydi esitler.

        Stop veya hedef emirleri bot uyurken tetiklenmis olabilir; kapanan
        islemi burada yakalayip gercek PnL ile kaydediyoruz.
        """
        closed: list[Trade] = []
        self._orphan_trades = []
        try:
            live = {p["symbol"]: p for p in self.client.position_risk()}
        except BinanceError:
            log.exception("positionRisk okunamadi, mutabakat atlandi")
            return closed

        for symbol, pos in list(self._positions.items()):
            exch = live.get(symbol)
            if exch is None:
                # Borsada kapanmis; fiyat bilinmiyorsa stop varsayilir
                # (kotumser: kazanci degil kaybi varsay).
                trade = self._finalize(pos, "borsada-kapandi",
                                       fallback_exit=pos.stop)
                if trade:
                    closed.append(trade)
                continue
            amt = abs(float(exch["positionAmt"]))
            if amt < pos.qty * 0.98:  # kismi hedef dolmus
                pos.qty = amt
                pos.tp1_filled = True
                self.store.save_position(pos)
            self._ensure_protected(pos)

        closed.extend(self._orphan_trades)
        self._orphan_trades = []

        for symbol, exch in live.items():
            if symbol not in self._positions and symbol in self.cfg.symbols:
                log.warning("Bota ait olmayan acik pozisyon: %s (%s). Dokunulmuyor.",
                            symbol, exch["positionAmt"])
        return closed

    def _finalize(self, pos: Position, reason: str,
                  fallback_exit: Optional[float] = None) -> Optional[Trade]:
        """Gerceklesmis PnL'i borsanin kendi kayitlarindan toplar."""
        realized = 0.0
        commission = 0.0
        exit_price = pos.entry_price
        okundu = False
        # 60 sn geriye bakmanin sebebi saat kaymasi: opened_at yerel saatten,
        # fill zamani borsadan gelir. AMA bu pencere ayni sembolde bir ONCEKI
        # islemin fill'lerine uzanirsa o islemin PnL'i buraya da yazilir ve
        # cift sayilir. Onceki kapanis zamaniyla sinirlandiriyoruz.
        # Sinir BORSANIN fill zamanidir, yerel kapanis saati degil: iki saat
        # birbirinden kayabilir ve zaten 60 sn'lik pencerenin sebebi bu kayma.
        son_fill = int((self.store.get_kv("last_fill_ms") or {}).get(pos.symbol, 0))
        baslangic = max(pos.opened_at - 60_000, son_fill + 1)
        # Binance, startTime-only userTrades sorgusunu 7 gunle sinirlar.
        # max_bars_in_trade 4h'te 33 gune kadar cikabiliyor; eski bir
        # baslangicla sorgu bos donerdi. Cikis fill'leri her zaman SON'da
        # oldugu icin pencereyi sona yaslamak PnL'i kaybettirmez (giris
        # komisyonu disinda -- o da PnL'e degil fees'e yazilir).
        simdi = int(time.time() * 1000)
        en_erken = simdi - 6 * 86_400_000
        if baslangic < en_erken:
            log.info("%s 6 gunden uzun tutuldu; userTrades penceresi sona "
                     "yaslandi (giris komisyonu eksik olabilir)", pos.symbol)
            baslangic = en_erken
        yeni_son_fill = son_fill
        try:
            fills = self.client.user_trades(pos.symbol, baslangic)
            for t in fills:
                ts = int(t.get("time", 0))
                if ts and ts < baslangic:     # sunucu filtreyi uygulamadiysa
                    continue
                yeni_son_fill = max(yeni_son_fill, ts)
                realized += float(t.get("realizedPnl", 0))
                commission += float(t.get("commission", 0))
                if float(t.get("realizedPnl", 0)) != 0:
                    exit_price = float(t.get("price", exit_price))
                    okundu = True
        except BinanceError:
            # ESKIDEN: realized = pos.realized_pnl -> canlida bu HEP 0'di.
            # Yani ag hatasi sirasinda kapanan bir stop, pnl=0 olarak
            # kaydediliyordu: gunluk zarar limiti kaybi hic gormuyor,
            # 'pnl < 0' yanlis oldugu icin ust uste zarar sayaci SIFIRLANIYOR
            # ve 4 saatlik soguma hic tetiklenmiyordu. Gercek zarari sifir
            # yazmak, bir riski gizlemenin en sessiz yolu.
            log.exception("userTrades okunamadi, PnL fiyattan TAHMIN edilecek")
            tahmin_px = fallback_exit if fallback_exit else pos.stop
            realized = (pos.realized_pnl
                        + (tahmin_px - pos.entry_price) * pos.qty * pos.direction)
            exit_price = tahmin_px
            reason = f"{reason}(tahmini)"
        if not okundu and exit_price == pos.entry_price and fallback_exit:
            exit_price = fallback_exit

        net = realized - commission
        risk_total = pos.initial_risk_per_unit * pos.initial_qty
        trade = Trade(
            symbol=pos.symbol, side=pos.side, qty=pos.initial_qty,
            entry_price=pos.entry_price, exit_price=exit_price,
            opened_at=pos.opened_at, closed_at=int(time.time() * 1000),
            pnl=net, fees=commission,
            r_multiple=(net / risk_total) if risk_total > 0 else 0.0,
            exit_reason=reason, entry_reason=pos.entry_reason,
            # Giris kosullari ogrenme katmanina TASINMAK ZORUNDA. Bos
            # birakilirsa regime_bucket her islemi adx=0/atr=0 kabul edip
            # hepsini tek kovaya atar; kova gecmisi entegre calismaz ve
            # backtest ile canli ayrisir.
            context=dict(pos.context),
        )
        self._positions.pop(pos.symbol, None)
        self.store.clear_position(pos.symbol)
        self.store.record_trade(trade)
        if yeni_son_fill > son_fill:
            kayit = self.store.get_kv("last_fill_ms") or {}
            kayit[pos.symbol] = yeni_son_fill
            self.store.set_kv("last_fill_ms", kayit)
        try:
            self.client.cancel_all(pos.symbol)  # artik pozisyon yok, artik emir kalmasin
        except BinanceError:
            pass
        log.info("[LIVE] KAPANDI %s %s pnl=%.2f (%s)", pos.side, pos.symbol, net, reason)
        return trade


class _SigView:
    """Bekleyen kayittan sinyal benzeri hafif nesne (market'e dusus icin)."""

    def __init__(self, rec: dict, symbol: str):
        self.symbol = symbol
        self.side = rec["side"]
        self.entry = rec["entry"]
        self.stop = rec["stop"]
        self.tp1 = rec["tp1"]
        self.tp2 = rec["tp2"]
        self.reason = rec["reason"]
        self.meta = dict(rec.get("meta") or {})


_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _timeframe_ms(tf: str) -> int:
    return _TF_MS.get(tf, 14_400_000)
