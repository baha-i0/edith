"""Ticaret motoru: dongu, filtreler, emir kararlari.

Motor hangi ortamda oldugunu bilmez -- Broker arayuzu uzerinden calisir.
Ayni kod paper'da, testnet'te ve canlida ayni kararlari verir.
"""

from __future__ import annotations

import logging
import signal as os_signal
import time
from dataclasses import replace
from typing import Dict, List, Optional

from .config import Config
from .exchange.base import Broker, MarketData
from .exchange.live import LiveBroker
from .exchange.paper import PaperBroker
from .health import CRITICAL, WARN, run_health_checks
from .learning import Learner
from .shadow import ShadowTracker
from .models import LONG, Candle, Position, Trade
from .dashboard import DashboardServer, build_state
from .notify import CommandRouter, Notifier
from .risk import (RiskGuard, apply_cash_flow, effective_floor,
                   open_risk_total, size_position, update_floor,
                   validate_signal_quality)
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
        self.learner = Learner(cfg, store)
        self.shadow = ShadowTracker(cfg, self.strategy, store)
        self.notifier = Notifier(store)
        self.router = CommandRouter()
        self._register_commands()
        self._last_bar: Dict[str, int] = {}
        # Stop yiyen islemler: fiyat sonradan hedefe giderse "stop avlanmasi"
        self._hunt_watch: Dict[str, dict] = {}
        self._last_health_ms = 0
        self._last_report_day = ""
        self._last_alert: Dict[str, int] = {}
        self._running = True
        # Panel motorun icinde yasar: acik pozisyonun ANLIK durumu
        # veritabaninda degil, burada ve borsada.
        self.dashboard = DashboardServer(cfg, lambda: build_state(cfg, store, self))

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
        if self.dashboard.start():
            print(f"Panel: {self.dashboard.url}")
        log.info("Basabas isabet orani (komisyon haric): %%%.1f | agirlikli hedef R=%.2f",
                 self.cfg.breakeven_win_rate() * 100, self.cfg.blended_target_r())
        if self.guard.state.shadow_mode:
            log.warning("GOLGE MODUNDA baslatildi: %s | %s",
                        self.guard.state.shadow_reason, self.shadow.report())
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
        self.dashboard.stop()
        log.info("Bot durdu. Acik pozisyonlarin koruma emirleri borsada duruyor.")

    # -------------------------------------------------------------------- tick
    def tick(self) -> None:
        now = int(time.time() * 1000)
        # Sahibin komutlari her seyden once islenir: "/kapat" yazdiysan,
        # bot once onu yapar, sonra piyasaya bakar.
        self._handle_commands(now)
        equity = self.broker.equity()
        self.store.record_equity(equity, now)
        # Cirpinan taban: zirve yukseldiyse taban da yukselir ve bir daha
        # dusmez. Once bunu yap -- boyutlandirma gecerli tabani gormeli.
        onceki = self.guard.state.floor_usdt
        # Taban GERCEKLESMIS bakiye uzerinden cirpinir. Acik pozisyonun
        # kagit uzerindeki kari zirve saydirilirsa, hic bankaya girmemis
        # bir paraya gore taban kilitlenir ve bot felc olur.
        self.guard.roll_day(now, equity)
        self.learner.record_equity(equity)

        if isinstance(self.broker, LiveBroker):
            for trade in self.broker.reconcile():
                self._on_trade_closed(trade, now)

        # SIRA ONEMLI: nakit akisi ancak reconcile islemleri deftere
        # yazdiktan SONRA olculebilir. Once olcersek, borsada kapanan bir
        # islem "bakiye dustu ama pnl degismedi" gorunur ve PARA CEKME
        # sanilir; bir sonraki turda ayni tutar PARA YATIRMA sanilir.
        # Iki yanlis bildirim, ve bir tur boyunca yanlis taban.
        self._update_floor_and_flows(now)

        # Gun durdurulduysa (zarar limiti / kar hedefi) ya da sahibi
        # durdurduysa, tahtadaki emirler de iptal edilir. Aksi halde
        # "bot bugun durdu" derken bir limit dolup YENI pozisyon acar.
        st = self.guard.state
        if (st.halted or st.paused) and self.broker.pending_entries():
            try:
                n = self.broker.cancel_pending()
                if n:
                    log.info("Gun durduruldu -> %d bekleyen giris emri iptal", n)
            except Exception:
                log.exception("Durdurma sirasinda bekleyen emirler iptal edilemedi")

        # Tahtada bekleyen maker giris emirleri: dolan var mi?
        # Slot sayaci ancak emir DOLDUGUNDA artar; bekleyen emirler
        # _allocate icinde ayrica hesaba katilir.
        if self.cfg.execution.entry_order_type == "post_only":
            try:
                for pos in self.broker.poll_pending():
                    self.store.save_position(pos)
                    self.guard.record_open(now)
                    self.store.save_risk_state(self.guard.state)
                    self.notifier.send(
                        f"GIRIS {pos.side} {pos.symbol} @ {pos.entry_price:.4f}\n"
                        f"stop {pos.stop:.4f} | tp2 {pos.tp2:.4f} | {pos.leverage}x"
                    )
            except Exception:
                log.exception("Bekleyen giris emirleri kontrol edilemedi")

        # Faz 1: acik pozisyonlari yonet ve aday sinyalleri TOPLA.
        # Karar bu fazda verilmez -- genislik filtresi portfoy seviyesinde
        # calisir, tek sembole bakarak hesaplanamaz. Backtest de tam olarak
        # boyle calisiyor; parite sart, yoksa backtest yalan soyler.
        candidates: List[tuple] = []
        for symbol in self.cfg.symbols:
            try:
                sig = self.process_symbol(symbol, now, equity)
                if sig is not None:
                    candidates.append(sig)
            except Exception:
                log.exception("%s islenirken hata", symbol)

        # Faz 2: portfoy seviyesinde secim
        try:
            self._allocate(candidates, now, equity)
        except Exception:
            log.exception("Sinyal dagitimi basarisiz")

        self._maybe_health_check(now)
        # Gunluk rapor tick'in SONUNDA, kosulsuz. Onceden _allocate'in son
        # satirindaydi ve _allocate sinyal yoksa / genislik filtresi bosaltinca
        # / risk kapisi kapaliyken ERKEN DONUYORDU -- yani rapor ancak ayni
        # anda 4 sembol sinyal verdiginde gonderilebiliyordu. "Sen bota gitme,
        # o sana gelsin" tasariminin tamami bu yuzden calismiyordu.
        self._maybe_daily_report(now)

    def _update_floor_and_flows(self, now: int) -> None:
        """Nakit akisini olcup tabani gunceller. reconcile'dan SONRA cagrilir."""
        st = self.guard.state
        onceki = st.floor_usdt
        gerceklesmis = self.broker.realized_equity()
        # Toplam gerceklesen kar = kapanmis islemler + ACIK pozisyonlarin
        # kismi cikislarindan gelen kar. Ikincisi olmadan kismi TP1 dolumu
        # "para yatirma", tam kapanis da "para cekme" sanilirdi.
        toplam_pnl = self.store.stats().get("net_pnl", 0.0) + sum(
            p.realized_pnl for p in self.broker.positions().values())
        akis = apply_cash_flow(self.cfg.risk, st, gerceklesmis, toplam_pnl)
        if akis:
            log.warning("Nakit akisi: %+.2f USDT -> zirve %.2f, taban %.2f",
                        akis, st.peak_equity, st.floor_usdt)
            if effective_floor(self.cfg.risk, st) > 0:
                self.notifier.send(
                    f"{'PARA YATIRMA' if akis > 0 else 'PARA CEKME'} algilandi: "
                    f"{akis:+.2f} USDT\n"
                    f"Sermaye tabani guncellendi: {st.floor_usdt:.2f} USDT")
        yeni = update_floor(self.cfg.risk, st, gerceklesmis)
        if yeni > onceki > 0:
            log.info("Taban yukseldi: %.2f -> %.2f USDT (zirve %.2f)",
                     onceki, yeni, st.peak_equity)

    def _allocate(self, candidates: List[tuple], now: int, equity: float) -> None:
        """Aday sinyaller arasindan slot dagitimi.

        Iki kural:
          1. GENISLIK -- ayni yonde en az N sembol es zamanli sinyal vermeli.
             Olculdu: piyasa geneli tutarli oldugunda trend takibi calisiyor,
             tek basina gelen sinyal calismiyor (+0.277R vs +0.091R).
          2. KALITE SIRASI -- slot yetmiyorsa once yuksek ADX, sonra genis R:R.
             Liste sirasina gore secmek gizli yanlilik yaratir.
        """
        if not candidates:
            return
        r = self.cfg.risk

        if r.min_breadth > 1:
            side_counts: Dict[str, int] = {}
            for _adx, _rr, _sym, sg in candidates:
                side_counts[sg.side] = side_counts.get(sg.side, 0) + 1
            before = len(candidates)
            candidates = [c for c in candidates
                          if side_counts.get(c[3].side, 0) >= r.min_breadth]
            if before != len(candidates):
                log.info("Genislik filtresi: %d adaydan %d kaldi (esik %d, dagilim %s)",
                         before, len(candidates), r.min_breadth, side_counts)
        if not candidates:
            return

        candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
        shadow_on = self.guard.state.shadow_mode and self.cfg.shadow.enabled

        for _adx, _rr, symbol, sig in candidates:
            positions = self.broker.positions()
            # Tahtada bekleyen maker emri de slot tutar: henuz pozisyon degil
            # ama para taahhut edilmis durumda.
            pending = self.broker.pending_entries()
            if symbol in positions or symbol in pending:
                continue
            if r.max_same_direction > 0:
                same = sum(1 for p in positions.values() if p.side == sig.side)
                same += sum(1 for side in pending.values() if side == sig.side)
                if same >= r.max_same_direction:
                    continue
            # Golgede para riski yok; gunluk limitler olcumu durdurmamali,
            # yoksa "canliya donus" karari eksik veriye dayanir.
            if not shadow_on:
                allowed, why = self.guard.can_open(now, len(positions) + len(pending),
                                                   equity)
                if not allowed:
                    log.debug("giris kapali (%s)", why)
                    return
            self._enter(symbol, sig, now, equity)

    def _maybe_daily_report(self, now: int) -> None:
        """Gunde bir kez telefona ozet gonderir.

        Patron rolu pasif olmali: sen bota gitme, o sana gelsin. Gunde
        BIR mesaj -- surekli kar/zarar bildirimi kotu kararlarin kaynagidir.
        """
        day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        if self._last_report_day == day:
            return
        if not self._last_report_day:      # ilk dongu, gecmis gun yok
            self._last_report_day = day
            return
        self._last_report_day = day
        try:
            self.notifier.send(self.daily_report(now))
        except Exception:
            log.warning("Gunluk rapor gonderilemedi", exc_info=True)

    def daily_report(self, now: int) -> str:
        """Duz Turkce gunluk ozet. Teknik terim yok, aksiyon varsa yazili."""
        from .health import CRITICAL, WARN, run_health_checks

        st = self.store.stats()
        equity = self.broker.equity()
        rs = self.guard.state
        lines = [f"GUNLUK OZET - {time.strftime('%d.%m.%Y', time.gmtime(now/1000))}",
                 "", f"Bakiye: {equity:.2f} USDT"]

        if st.get("trades", 0):
            lines.append(f"Toplam {st['trades']} islem | isabet %{st['win_rate']:.0f} "
                         f"| net {st['net_pnl']:+.2f}")
        else:
            lines.append("Henuz kapanmis islem yok.")

        pos = self.broker.positions()
        lines.append(f"Acik pozisyon: {len(pos)}" +
                     (f" ({', '.join(pos)})" if pos else ""))

        if rs.shadow_mode:
            lines += ["", "DURUM: GOLGE MODU (para riske atilmiyor)",
                      rs.shadow_reason, self.shadow.report(),
                      "Kanit geri gelirse bot kendiliginden canliya doner."]
        elif rs.halted:
            lines += ["", f"DURUM: bugun duruldu ({rs.halt_reason})",
                      "Yarin kendiliginden acilir."]
        else:
            lines += ["", "DURUM: normal calisiyor"]

        try:
            rep = run_health_checks(self.cfg, self.store, self.learner,
                                    self.broker, now)
            issues = [c for c in rep.checks if c.severity in (CRITICAL, WARN)]
            if issues:
                lines.append("")
                for c in issues:
                    lines.append(f"[{c.severity.upper()}] {c.message}")
                    if c.action:
                        lines.append(f"  YAP: {c.action}")
            else:
                lines.append("Kontroller: her sey yolunda, yapman gereken bir sey yok.")
        except Exception:
            log.warning("Rapor icin saglik kontrolu basarisiz", exc_info=True)
        return "\n".join(lines)

    def _maybe_health_check(self, now: int) -> None:
        """Periyodik kendini denetleme.

        Kanit stratejinin bozuldugunu gosterirse bot YENI POZISYON ACMAYI
        durdurur ve haber verir. Parametreleri kendiliginden degistirmez --
        bu, bozulmayi gizlemenin en kolay yolu olurdu.
        """
        hc = self.cfg.health
        if not hc.enabled:
            return
        if now - self._last_health_ms < hc.check_every_minutes * 60_000:
            return
        self._last_health_ms = now
        try:
            rep = run_health_checks(self.cfg, self.store, self.learner,
                                    self.broker, now)
        except Exception:
            log.exception("Saglik kontrolu basarisiz")
            return

        for c in rep.checks:
            if c.severity == CRITICAL:
                log.error("SAGLIK [%s] %s | YAP: %s", c.name, c.message, c.action)
            elif c.severity == WARN:
                log.warning("SAGLIK [%s] %s", c.name, c.message)

        if rep.halt_required and not self.guard.state.shadow_mode:
            if self.cfg.shadow.enabled:
                # Durup insani beklemek otonom degil. Golge modunda bot
                # calismaya devam eder, para riske atmaz ve kanit geri
                # gelirse kendiliginden canliya doner.
                self._enter_shadow(rep.halt_reason, now)
            elif hc.halt_on_dead_edge and not self.guard.state.halted:
                self.guard.state.halted = True
                self.guard.state.halt_reason = "saglik kontrolu: " + rep.halt_reason
                self.store.save_risk_state(self.guard.state)
                log.error("BOT DURDURULDU: %s", rep.halt_reason)

        self._alert(rep, now)

    def _enter_shadow(self, reason: str, now: int) -> None:
        """Canli islemi durdur, kagit uzerinde devam et."""
        st = self.guard.state
        st.shadow_mode = True
        st.shadow_since_ms = now
        st.shadow_reason = reason
        self.store.save_risk_state(st)
        self.shadow.enter(reason, now)
        # Golge modu "gercek para riske atma" demek. Tahtada duran bir
        # limit emri dolarsa GERCEK pozisyon acilir ve golge modunun tum
        # anlami kaybolur.
        try:
            self.broker.cancel_pending()
        except Exception:
            log.exception("Golgeye gecerken bekleyen emirler iptal edilemedi")
        for symbol, pos in list(self.broker.positions().items()):
            trade = self.broker.close_position(symbol, 1.0, pos.tp2, "golge-moduna-gecis")
            if trade:
                self._on_trade_closed(trade, now)
        if self.cfg.shadow.notify_on_transition:
            self.notifier.send(
                "GOLGE MODUNA GECILDI\n\n"
                f"{reason}\n\n"
                "Bot durmadi. Sinyal uretmeye ve islem yapmaya devam ediyor "
                "ama PARA RISKE ATILMIYOR. Acik pozisyonlar kapatildi.\n\n"
                f"Golgede {self.cfg.shadow.min_trades_to_resume} sanal islemde "
                "beklentinin pozitif oldugu kanitlanirsa kendiliginden canliya "
                "doner. Senin bir sey yapmana gerek yok."
            )

    def _maybe_resume_live(self, now: int) -> None:
        """Golge performansi kanit uretti mi? Uretti ise canliya don."""
        ok, why = self.shadow.should_resume()
        log.info("[GOLGE] donus kontrolu: %s", why)
        if not ok:
            return
        st = self.guard.state
        st.shadow_mode = False
        st.shadow_reason = ""
        st.halted = False
        st.halt_reason = ""
        self.store.save_risk_state(st)
        self.shadow.state.resumed_count += 1
        self.shadow.save()
        log.warning("CANLIYA DONULDU: %s", why)
        if self.cfg.shadow.notify_on_transition:
            self.notifier.send(f"CANLIYA DONULDU\n\n{why}\n\n"
                               "Bot gercek islem yapmaya devam ediyor.")

    # --------------------------------------------------------------- komutlar
    def _register_commands(self) -> None:
        """Telefondan verilebilecek komutlar.

        Tasarim: OKUMA komutlari serbest, YAZMA komutlari sinirli, YIKICI
        komut onay ister. Bot otonom calisir; bu komutlar botu yonetmek
        icin degil, sahibin acil durumda mudahale edebilmesi icindir.
        """
        r = self.router
        r.register("durum", self._cmd_durum, aliases=("status", "d"))
        r.register("bakiye", self._cmd_bakiye, aliases=("balance",))
        r.register("rapor", lambda: self.daily_report(int(time.time() * 1000)))
        r.register("dur", self._cmd_dur, aliases=("durdur", "stop", "pause"))
        r.register("devam", self._cmd_devam, aliases=("resume", "basla"))
        r.register("ogrenme", self._cmd_ogrenme, aliases=("learn",))
        r.register("kapat", self._cmd_kapat, confirm=True,
                   aliases=("acil_kapat", "acilkapat", "panic"))
        r.register("yardim", self._cmd_yardim, aliases=("help", "start", "komutlar"))

    def _handle_commands(self, now: int) -> None:
        try:
            texts = self.notifier.poll_commands()
        except Exception:
            log.exception("Komutlar okunamadi")
            return
        for text in texts:
            try:
                reply = self.router.dispatch(text, now)
            except Exception as exc:
                log.exception("Komut basarisiz: %s", text)
                reply = f"Komut calistirilamadi: {exc}"
            if reply is None:
                reply = ("Anlamadim. /yardim yaz.")
            log.info("[KOMUT] %s -> %s", text.split()[0], reply.splitlines()[0][:60])
            self.notifier.send(reply)

    def _cmd_yardim(self) -> str:
        return ("EDITH komutlari\n\n"
                "/durum   - acik pozisyonlar, gunun ozeti\n"
                "/bakiye  - hesap bakiyesi ve serbest marj\n"
                "/rapor   - gunluk tam ozet\n"
                "/ogrenme - bot neyi ogrendi\n"
                "/dur     - yeni islem acmayi durdur (acik pozisyonlar korunur)\n"
                "/devam   - tekrar islem acmaya basla\n"
                "/kapat   - TUM pozisyonlari hemen kapat ve dur (onay ister)\n\n"
                "Not: /dur ve /kapat acik pozisyonlarin borsadaki stop "
                "emirlerini KALDIRMAZ. Koruma her zaman yerinde.")

    def _cmd_bakiye(self) -> str:
        eq = self.broker.equity()
        free = self.broker.free_margin()
        rs = self.guard.state
        base = rs.day_start_equity or eq
        return (f"Bakiye     : {eq:.2f} USDT\n"
                f"Serbest marj: {free:.2f} USDT\n"
                f"Bugun      : {rs.realized_pnl_today:+.2f} USDT "
                f"({rs.realized_pnl_today / base * 100:+.2f}%)\n"
                f"Mod        : {self.cfg.mode}")

    def _cmd_durum(self) -> str:
        rs = self.guard.state
        eq = self.broker.equity()
        lines = []
        if rs.paused:
            lines.append("DURUM: ELLE DURDURULDU (/devam ile ac)")
        elif rs.shadow_mode:
            lines.append("DURUM: GOLGE MODU - para riske atilmiyor")
        elif rs.halted:
            lines.append(f"DURUM: bugun durdu ({rs.halt_reason})")
        else:
            lines.append("DURUM: normal calisiyor")
        lines.append(f"Bakiye: {eq:.2f} USDT | bugun {rs.realized_pnl_today:+.2f}")

        pos = self.broker.positions()
        if not pos:
            lines.append("\nAcik pozisyon yok.")
        else:
            lines.append(f"\nAcik pozisyon ({len(pos)}):")
            for sym, p in pos.items():
                try:
                    px = self.market.book_ticker(sym)["mid"]
                except Exception:
                    px = p.entry_price
                pnl = (px - p.entry_price) * p.qty * (1 if p.side == LONG else -1)
                rmult = pnl / (p.initial_risk_per_unit * p.initial_qty) \
                    if p.initial_risk_per_unit and p.initial_qty else 0.0
                lines.append(f"  {sym} {p.side} @ {p.entry_price:.4f} -> {px:.4f}  "
                             f"{pnl:+.2f} USDT ({rmult:+.2f}R)  stop {p.stop:.4f}")
        floor = effective_floor(self.cfg.risk, rs)
        if floor > 0:
            yastik = max(0.0, eq - floor)
            lines.append(f"\nTaban: {floor:.2f} korunuyor | yastik: {yastik:.2f}")
            if yastik < self.cfg.risk.min_cushion_usdt:
                lines.append("YASTIK TUKENDI - bot yeni islem ACMIYOR. "
                             "Para eklemeden islem baslamaz.")

        pend = self.broker.pending_entries()
        if pend:
            lines.append(f"\nTahtada bekleyen giris: {', '.join(pend)}")
        lines.append(f"\nBugun {rs.trades_today}/{self.cfg.risk.max_trades_per_day} islem"
                     f" | ust uste zarar {rs.consecutive_losses}")
        return "\n".join(lines)

    def _cmd_ogrenme(self) -> str:
        return self.learner.report()

    def _cmd_dur(self) -> str:
        st = self.guard.state
        if st.paused:
            return "Zaten durdurulmus durumda. /devam ile acabilirsin."
        st.paused = True
        self.store.save_risk_state(st)
        log.warning("[KOMUT] Sahibin talebiyle yeni islem acma DURDURULDU")
        # Tahtada bekleyen limit emri de bir GIRIStir: birakilirsa "yeni
        # islem acma" talimatina ragmen pozisyon acar.
        try:
            iptal = self.broker.cancel_pending()
        except Exception:
            log.exception("Bekleyen emirler iptal edilemedi")
            iptal = 0
        n = len(self.broker.positions())
        return ("Yeni islem acma DURDURULDU.\n\n"
                f"Acik {n} pozisyon oldugu gibi devam ediyor; borsadaki stop ve "
                "hedef emirleri yerinde. Hepsini simdi kapatmak istersen /kapat.\n"
                + (f"Tahtada bekleyen {iptal} giris emri iptal edildi.\n" if iptal else "")
                + "Tekrar acmak icin /devam.")

    def _cmd_devam(self) -> str:
        st = self.guard.state
        if not st.paused:
            return "Zaten calisiyor."
        st.paused = False
        self.store.save_risk_state(st)
        log.warning("[KOMUT] Sahibin talebiyle islem acma tekrar ACILDI")
        extra = ""
        if st.halted:
            extra = f"\nNot: gunluk limit hala aktif ({st.halt_reason}). Yarin acilir."
        if st.shadow_mode:
            extra += "\nNot: bot golge modunda; kanit gelene kadar para riske atmaz."
        return "Islem acma tekrar ACIK." + extra

    def _cmd_kapat(self) -> str:
        now = int(time.time() * 1000)
        st = self.guard.state
        st.paused = True
        self.store.save_risk_state(st)
        # SIRA ONEMLI: once tahtadaki bekleyen emirleri iptal et, sonra
        # pozisyonlari kapat. Ters sirada, kapatma ile iptal arasindaki
        # saniyelerde bir limit dolup YENI pozisyon acabilir -- kullanici
        # her sey kapandi sanirken.
        iptal = 0
        try:
            iptal = self.broker.cancel_pending()
        except Exception:
            log.exception("Bekleyen emirler iptal edilemedi")
        closed, failed = [], []
        for symbol, pos in list(self.broker.positions().items()):
            try:
                trade = self.broker.close_position(symbol, 1.0, pos.entry_price,
                                                   "acil-kapatma")
                if trade:
                    self._on_trade_closed(trade, now)
                    closed.append(f"{symbol} {trade.pnl:+.2f}")
                else:
                    failed.append(symbol)
            except Exception as exc:
                log.exception("%s acil kapatilamadi", symbol)
                failed.append(f"{symbol} ({exc})")
        log.warning("[KOMUT] ACIL KAPATMA: %d kapandi, %d basarisiz",
                    len(closed), len(failed))
        out = ["ACIL KAPATMA yapildi. Bot ayrica DURDURULDU."]
        out.append(f"Kapanan: {', '.join(closed) if closed else 'yok'}")
        if iptal:
            out.append(f"Iptal edilen bekleyen emir: {iptal}")
        if failed:
            out.append(f"KAPATILAMAYAN: {', '.join(failed)}\n"
                       "Bunlari Binance uygulamasindan elle kontrol et.")
        out.append(f"Bakiye: {self.broker.equity():.2f} USDT")
        out.append("Tekrar baslatmak icin /devam.")
        return "\n".join(out)

    def _alert(self, rep, now: int) -> None:
        """Bildirim gonderir ama spam yapmaz: ayni uyari gunde bir kez."""
        levels = [CRITICAL] + ([WARN] if self.cfg.health.notify_on_warn else [])
        for c in rep.checks:
            if c.severity not in levels:
                continue
            if now - self._last_alert.get(c.name, 0) < 86_400_000:
                continue
            self._last_alert[c.name] = now
            self.notifier.send(f"[{c.severity.upper()}] {c.name}\n{c.message}"
                               + (f"\n\nYAP: {c.action}" if c.action else ""))

    def process_symbol(self, symbol: str, now: int, equity: float):
        """Bir sembolu isler. Acik pozisyonu yonetir; yeni sinyal varsa
        ADAY olarak doner (girisi burada YAPMAZ).

        Girisin burada yapilmamasi kasitli: genislik filtresi tum sembollerin
        sinyallerini ayni anda gormeyi gerektiriyor.
        """
        candles = self.market.klines(symbol, self.cfg.timeframe,
                                     limit=self.cfg.strategy.warmup_bars)
        closed = [c for c in candles if c.closed]
        if len(closed) < self.cfg.strategy.ema_slow + 20:
            log.warning("%s: yeterli mum yok (%d)", symbol, len(closed))
            return None

        feats = Features(closed, self.cfg.strategy)
        last = closed[-1]

        self._check_stop_hunt(symbol, closed, now)

        pos = self.broker.positions().get(symbol)
        if pos:
            self._manage(pos, last, feats, now)
            return None

        shadow_on = self.guard.state.shadow_mode and self.cfg.shadow.enabled

        # Golge modunda sanal pozisyonlar da yonetilmeli -- yoksa olcum durur
        if shadow_on and self.shadow.has_position(symbol):
            r = self.shadow.update(symbol, last, feats.atr[-1] or 0.0, now)
            if r is not None:
                self._maybe_resume_live(now)
            return None

        # Ayni mumda birden fazla giris denemesi yok
        if self._last_bar.get(symbol) == last.open_time:
            return None

        sig = self.strategy.evaluate(symbol, closed, feats)
        if not sig:
            return None
        self._last_bar[symbol] = last.open_time

        # --- Ogrenme kapisi: kanitlanmis negatif kova / tekrarlayan hata ---
        learn_ok, learn_why = self.learner.allow_entry(symbol, sig.meta, now)
        if not learn_ok:
            log.info("%s ogrenme kapisi: %s", symbol, learn_why)
            return None

        # --- Ogrenilmis stop genisletmesi (R katlari korunur) ---
        self._apply_learned_stop(sig, symbol)

        ok, reason = validate_signal_quality(sig, self.cfg)
        if not ok:
            log.info("%s sinyal elendi: %s", symbol, reason)
            return None
        if not self._microstructure_ok(symbol, now):
            return None

        return (sig.meta.get("adx", 0.0), sig.reward_risk, symbol, sig)

    def _enter(self, symbol: str, sig, now: int, equity: float) -> None:
        """Secilmis bir sinyali gercek (ya da golge modunda sanal) pozisyona cevirir."""
        # Golge modunda sinyal gercek, emir sanal. Ayni genislik filtresinden
        # gectigi icin golge olcumu canli davranisla ayni kalir -- yoksa
        # "canliya donus" karari yanlis bir sistemin sinavina dayanirdi.
        if self.guard.state.shadow_mode and self.cfg.shadow.enabled:
            self.shadow.open(sig, now)
            return

        filters = self.market.filters(symbol)
        risk_cfg = self.cfg.risk
        mult, mult_why = self.learner.risk_multiplier(symbol, sig.meta, equity)
        if mult != 1.0:
            risk_cfg = replace(risk_cfg,
                               risk_per_trade_pct=risk_cfg.risk_per_trade_pct * mult)
            log.info("%s risk carpani %.2f (%s)", symbol, mult, mult_why)

        sizing = size_position(
            equity=equity, free_margin=self.broker.free_margin(),
            entry=sig.entry, stop=sig.stop, filters=filters,
            risk_cfg=risk_cfg, desired_leverage=self.cfg.account.leverage,
            open_risk=(open_risk_total(self.broker.positions().values())
                       + self.broker.pending_risk()),
            state=self.guard.state,
        )
        if not sizing.ok:
            log.info("%s pozisyon acilmadi: %s", symbol, sizing.reason)
            if "minimum emir" in sizing.reason:
                lesson = self.learner.record_mistake(
                    symbol, "min_notional", sizing.reason, now)
                if lesson:
                    log.warning(lesson)
                    self.notifier.send("DERS: " + lesson)
                self.learner.save()
            return

        log.info("%s SINYAL %s | giris=%.4f stop=%.4f tp1=%.4f tp2=%.4f | R:R=%.2f | %s | %s",
                 symbol, sig.side, sig.entry, sig.stop, sig.tp1, sig.tp2,
                 sig.reward_risk, sizing.reason, sig.meta)

        try:
            opened = self.broker.open_position(sig, sizing.qty, sizing.leverage)
        except Exception as exc:
            lesson = self.learner.record_mistake(symbol, "emir_reddi", str(exc)[:120], now)
            self.learner.save()
            if lesson:
                log.warning(lesson)
                self.notifier.send("DERS: " + lesson)
            raise
        if opened:
            opened.context = dict(sig.meta)
            self.store.save_position(opened)
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
                kapanan = self.broker.update_stop(pos.symbol, act["price"])
                if kapanan is not None:
                    # Koruma kurulamadi ve pozisyon kapatildi. Bu islem
                    # deftere GECMELI: yoksa gunluk zarar limiti ve ust uste
                    # zarar sayaci o kaybi hic gormez.
                    self._on_trade_closed(kapanan, now)
                    continue
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

    def _apply_learned_stop(self, sig, symbol: str) -> None:
        """Ogrenilmis stop carpanini uygular, R katlarini korur.

        Stop genisledikce hedefler de ayni R oraninda uzar. Aksi halde
        stop'u genisletmek R:R'yi sessizce bozar -- yani bir sorunu
        cozerken digerini yaratir.
        """
        mult = self.learner.stop_multiplier(symbol)
        if mult == 1.0:
            return
        d = abs(sig.entry - sig.stop) * mult
        c = self.cfg.strategy
        if sig.side == LONG:
            sig.stop, sig.tp1, sig.tp2 = (sig.entry - d, sig.entry + c.tp1_r * d,
                                          sig.entry + c.tp2_r * d)
        else:
            sig.stop, sig.tp1, sig.tp2 = (sig.entry + d, sig.entry - c.tp1_r * d,
                                          sig.entry - c.tp2_r * d)
        sig.meta["stop_widen"] = mult
        log.info("%s ogrenilmis stop carpani %.2fx uygulandi", symbol, mult)

    def _check_stop_hunt(self, symbol: str, candles, now: int) -> None:
        """Stop yedikten sonra fiyat hedefe gitti mi?

        Karar aninda degil, olaydan SONRA olculur -- lookahead degil,
        geriye donuk hata analizi. Yon dogru + stop cok dar kombinasyonu
        tekrar ediyorsa stop mesafesi ogrenilerek genisletilir.
        """
        watch = self._hunt_watch.get(symbol)
        if not watch:
            return
        bar_ms = TF_MS[self.cfg.timeframe]
        bars_passed = (now - watch["since"]) / bar_ms
        recent = [c for c in candles if c.open_time > watch["since"]]
        hit = any(c.high >= watch["target"] for c in recent) if watch["side"] == LONG \
            else any(c.low <= watch["target"] for c in recent)
        if hit:
            for lesson in self.learner.note_stop_hunt(symbol, now):
                log.warning(lesson)
                self.notifier.send("DERS: " + lesson)
            self.learner.save()
            del self._hunt_watch[symbol]
        elif bars_passed > self.cfg.learning.stop_hunt_lookback_bars:
            del self._hunt_watch[symbol]   # sure doldu, stop hakliydi

    def _on_trade_closed(self, trade: Trade, now: int) -> None:
        self.guard.record_close(now, trade.pnl)
        self.store.save_risk_state(self.guard.state)
        for lesson in self.learner.record_trade(trade, now):
            self.notifier.send("DERS: " + lesson)
        if trade.exit_reason == "stop" and self.cfg.learning.stop_calibration:
            # orijinal hedef: giris + tp1_r * ilk stop mesafesi
            d = abs(trade.entry_price - trade.exit_price)
            target = (trade.entry_price + self.cfg.strategy.tp1_r * d
                      if trade.side == LONG
                      else trade.entry_price - self.cfg.strategy.tp1_r * d)
            self._hunt_watch[trade.symbol] = {
                "since": now, "target": target, "side": trade.side}
        self.learner.save()
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
