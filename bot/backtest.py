"""Backtest motoru.

Kurallar (hepsi *sana karsi* calisir, kasten):
  1. Sinyal mumun KAPANISINDA uretilir, giris BIR SONRAKI mumun ACILISINDA
     olur. Gordugun fiyattan giremezsin.
  2. Ayni mumda hem stop hem hedef gorunuyorsa STOP kabul edilir.
  3. Her giris/cikista taker komisyonu + slipaj uygulanir.
  4. Funding 8 saatte bir kesilir.

Bu varsayimlar sonucu kotulestirir. Amac guzel egri degil, canlida
surpriz yasamamak. Iyimser backtest, gerceklikte odenen faturadir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence

from .config import Config
from .models import LONG, SHORT, Candle, Position, SymbolFilters, Trade
from .learning import Learner
from .risk import (RiskGuard, RiskState, open_risk_total, size_position,
                   update_floor, validate_signal_quality)
from .strategy import Features, build_strategy

FUNDING_INTERVAL_MS = 8 * 3600 * 1000


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start_equity: float
    end_equity: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)
    fees: float = 0.0
    funding_paid: float = 0.0
    buy_hold_return_pct: float = 0.0
    bars: int = 0
    skipped_signals: int = 0

    # ------------------------------------------------------------- metrikler
    @property
    def net_pnl(self) -> float:
        return self.end_equity - self.start_equity

    @property
    def return_pct(self) -> float:
        return 100.0 * self.net_pnl / self.start_equity if self.start_equity else 0.0

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return 100.0 * sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl <= 0))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    @property
    def expectancy_r(self) -> float:
        return sum(t.r_multiple for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = -float("inf")
        mdd = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                mdd = max(mdd, 100.0 * (peak - eq) / peak)
        return mdd

    @property
    def max_losing_streak(self) -> int:
        streak = worst = 0
        for t in self.trades:
            streak = streak + 1 if t.pnl <= 0 else 0
            worst = max(worst, streak)
        return worst

    def sharpe(self) -> float:
        """Islem bazli kaba Sharpe. Az islemde guvenilmez, oyle de raporlanir."""
        rs = [t.r_multiple for t in self.trades]
        if len(rs) < 5:
            return 0.0
        mean = sum(rs) / len(rs)
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
        sd = math.sqrt(var)
        return (mean / sd) * math.sqrt(len(rs)) if sd else 0.0

    def summary(self) -> str:
        pf = self.profit_factor
        lines = [
            f"Sembol/TF        : {self.symbol} {self.timeframe}  ({self.bars} mum)",
            f"Baslangic equity : {self.start_equity:.2f}",
            f"Bitis equity     : {self.end_equity:.2f}  ({self.return_pct:+.2f}%)",
            f"Al-tut getirisi  : {self.buy_hold_return_pct:+.2f}%",
            f"Islem sayisi     : {len(self.trades)}",
            f"Isabet orani     : {self.win_rate:.1f}%",
            f"Profit factor    : {'sonsuz' if pf == float('inf') else f'{pf:.2f}'}",
            f"Beklenti (R)     : {self.expectancy_r:+.3f} R / islem",
            f"Maks. dusus      : {self.max_drawdown_pct:.2f}%",
            f"En uzun kayip s. : {self.max_losing_streak}",
            f"Odenen komisyon  : {self.fees:.2f}  | funding: {self.funding_paid:.2f}",
            f"Sharpe (kaba)    : {self.sharpe():.2f}",
            f"Elenen sinyal    : {self.skipped_signals}",
        ]
        return "\n".join(lines)

    def verdict(self) -> str:
        """Duz konusan degerlendirme. Kendini kandirmayi zorlastirir."""
        if len(self.trades) < 30:
            return ("YETERSIZ VERI: 30'dan az islem. Bu sonuc istatistiksel olarak "
                    "gurultu; canliya gecme gerekcesi olamaz.")
        if self.expectancy_r <= 0:
            return "NEGATIF BEKLENTI: Bu parametrelerle uzun vadede kaybedersin. Canliya gecme."
        if self.profit_factor < 1.2:
            return ("ZAYIF: Profit factor 1.2'nin altinda. Komisyon/slipaj biraz kotulesince "
                    "artiya gecen taraf borsa olur.")
        if self.max_drawdown_pct > 25:
            return ("RISKLI: %25 uzeri dusus. Matematiksel olarak hayatta kalsan bile "
                    "psikolojik olarak bota sadik kalman zor.")
        return ("KABUL EDILEBILIR: Pozitif beklenti ve makul dusus. Yine de once "
                "testnet/paper'da en az 2 hafta calistir.")


def run_backtest(
    cfg: Config,
    candles: Sequence[Candle],
    symbol: str,
    filters: Optional[SymbolFilters] = None,
    start_equity: Optional[float] = None,
) -> BacktestResult:
    strat = build_strategy(cfg.strategy)
    f = filters or SymbolFilters(symbol, 0.01, 0.001, 0.001, 5.0)
    equity = start_equity or cfg.account.paper_start_balance
    res = BacktestResult(symbol=symbol, timeframe=cfg.timeframe, start_equity=equity,
                         end_equity=equity, bars=len(candles))
    if len(candles) < cfg.strategy.warmup_bars:
        return res

    feats = Features(candles, cfg.strategy)
    guard = RiskGuard(cfg, RiskState())
    e = cfg.execution
    slip = e.slippage_bps / 10_000.0
    pos: Optional[Position] = None
    entry_index = 0
    pending: Optional[object] = None  # bir sonraki acilista girilecek sinyal
    last_funding = candles[0].open_time

    res.buy_hold_return_pct = 100.0 * (candles[-1].close - candles[cfg.strategy.warmup_bars].close) \
        / candles[cfg.strategy.warmup_bars].close

    for i in range(cfg.strategy.warmup_bars, len(candles)):
        bar = candles[i]
        now = bar.open_time
        guard.roll_day(now, equity)

        # --- 1) Bekleyen giris bu mumun acilisinda dolar
        if pending is not None and pos is None:
            sig = pending
            pending = None
            entry = bar.open * (1 + slip) if sig.side == LONG else bar.open * (1 - slip)
            drift = entry - sig.entry
            sizing = size_position(equity, equity, entry, sig.stop + drift, f,
                                   cfg.risk, cfg.account.leverage)
            if sizing.ok:
                fee = entry * sizing.qty * e.taker_fee
                equity -= fee
                res.fees += fee
                pos = Position(
                    symbol=symbol, side=sig.side, qty=sizing.qty, entry_price=entry,
                    stop=sig.stop + drift, tp1=sig.tp1 + drift, tp2=sig.tp2 + drift,
                    initial_risk_per_unit=abs(entry - (sig.stop + drift)), opened_at=now,
                    leverage=sizing.leverage, initial_qty=sizing.qty, fees_paid=fee,
                    entry_reason=sig.reason,
                )
                entry_index = i
                guard.record_open(now)
            else:
                res.skipped_signals += 1

        # --- 2) Funding (8 saatte bir)
        if pos and now - last_funding >= FUNDING_INTERVAL_MS:
            cost = pos.notional(bar.open) * 0.0001 * pos.direction  # tipik +%0.01
            equity -= cost
            res.funding_paid += cost
            last_funding = now
        elif not pos and now - last_funding >= FUNDING_INTERVAL_MS:
            last_funding = now

        # --- 3) Acik pozisyonu yonet (kotumser sirayla)
        if pos:
            pos.bars_held = i - entry_index
            cur_atr = feats.atr[i] or 0.0
            for act in strat.manage(pos, bar, cur_atr):
                if act["type"] == "move_stop":
                    pos.stop = act["price"]
                    pos.breakeven_moved = True
                elif act["type"] == "partial":
                    qty = pos.qty * act["portion"]
                    px = act["price"] * (1 - slip) if pos.side == LONG else act["price"] * (1 + slip)
                    pnl = (px - pos.entry_price) * qty * pos.direction
                    fee = px * qty * e.taker_fee
                    equity += pnl - fee
                    res.fees += fee
                    pos.qty -= qty
                    pos.realized_pnl += pnl
                    pos.fees_paid += fee
                    pos.tp1_filled = True
                elif act["type"] == "exit":
                    px = act["price"] * (1 - slip) if pos.side == LONG else act["price"] * (1 + slip)
                    pnl = (px - pos.entry_price) * pos.qty * pos.direction
                    fee = px * pos.qty * e.taker_fee
                    equity += pnl - fee
                    res.fees += fee
                    pos.realized_pnl += pnl
                    pos.fees_paid += fee
                    risk_total = pos.initial_risk_per_unit * pos.initial_qty
                    net = pos.realized_pnl - pos.fees_paid
                    trade = Trade(
                        symbol=symbol, side=pos.side, qty=pos.initial_qty,
                        entry_price=pos.entry_price, exit_price=px,
                        opened_at=pos.opened_at, closed_at=bar.close_time,
                        pnl=net, fees=pos.fees_paid,
                        r_multiple=(net / risk_total) if risk_total > 0 else 0.0,
                        exit_reason=act["reason"], entry_reason=pos.entry_reason,
                    )
                    res.trades.append(trade)
                    guard.record_close(now, net)
                    pos = None
                    break

        # --- 4) Yeni sinyal (sadece pozisyon yokken ve risk kapisi acikken)
        if pos is None and pending is None and i < len(candles) - 1:
            allowed, _ = guard.can_open(now, 0, equity)
            if allowed:
                sig = strat.evaluate(symbol, candles, feats, index=i)
                if sig:
                    ok, _reason = validate_signal_quality(sig, cfg)
                    if ok:
                        pending = sig
                    else:
                        res.skipped_signals += 1

        mtm = equity + (pos.unrealized(bar.close) if pos else 0.0)
        res.equity_curve.append((bar.close_time, mtm))

    if pos:  # acik pozisyonu son fiyattan kapat (rapor tutarliligi icin)
        px = candles[-1].close
        pnl = (px - pos.entry_price) * pos.qty * pos.direction
        equity += pnl - px * pos.qty * e.taker_fee
    res.end_equity = equity
    return res



# ===========================================================================
# Portfoy backtesti
# ===========================================================================
"""Tek sembol backtesti yaniltir: ortak equity, es zamanli pozisyon limiti ve
gunluk zarar limiti yokmus gibi davranir. 20 sembolde "yillik %22" hesabi,
ayni anda 20 pozisyon acabildigini varsayar -- ki acamazsin.

Portfoy backtesti tum sembolleri TEK bir zaman cizgisinde, TEK bir kasa ve
gercek limitlerle yurutur. Raporlanmasi gereken sayi budur.
"""


@dataclass
class PortfolioResult:
    start_equity: float
    end_equity: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)
    fees: float = 0.0
    symbols: List[str] = field(default_factory=list)
    days: float = 0.0
    blocked_by_slots: int = 0
    blocked_by_guard: int = 0
    unfilled_entries: int = 0
    fallback_entries: int = 0

    @property
    def return_pct(self) -> float:
        return 100.0 * (self.end_equity - self.start_equity) / self.start_equity

    @property
    def cagr_pct(self) -> float:
        yrs = self.days / 365.25
        if yrs <= 0 or self.start_equity <= 0 or self.end_equity <= 0:
            return 0.0
        return 100.0 * ((self.end_equity / self.start_equity) ** (1 / yrs) - 1)

    @property
    def win_rate(self) -> float:
        return 100.0 * sum(1 for t in self.trades if t.pnl > 0) / len(self.trades) \
            if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        gw = sum(t.pnl for t in self.trades if t.pnl > 0)
        gl = abs(sum(t.pnl for t in self.trades if t.pnl <= 0))
        return (gw / gl) if gl else (float("inf") if gw else 0.0)

    @property
    def expectancy_r(self) -> float:
        return sum(t.r_multiple for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        peak, mdd = -float("inf"), 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                mdd = max(mdd, 100.0 * (peak - eq) / peak)
        return mdd

    def t_stat(self) -> float:
        """Beklentinin sifirdan farkli olup olmadigina dair kaba t degeri.

        Uyari: islemler bagimsiz degil (kripto piyasasi korelasyonlu), bu
        yuzden gercek t bundan belirgin sekilde dusuk. t < 2 ise "sans"
        aciklamasi elenemez.
        """
        rs = [t.r_multiple for t in self.trades]
        if len(rs) < 10:
            return 0.0
        mean = sum(rs) / len(rs)
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
        sd = math.sqrt(var)
        return (mean / sd) * math.sqrt(len(rs)) if sd else 0.0

    def summary(self) -> str:
        pf = self.profit_factor
        return "\n".join([
            f"Semboller        : {len(self.symbols)} ({', '.join(self.symbols[:8])}"
            + (" ..." if len(self.symbols) > 8 else "") + ")",
            f"Sure             : {self.days/365.25:.1f} yil",
            f"Equity           : {self.start_equity:.2f} -> {self.end_equity:.2f} "
            f"({self.return_pct:+.1f}%)",
            f"Yillik bilesik   : {self.cagr_pct:+.1f}%",
            f"Islem            : {len(self.trades)}  ({len(self.trades)/max(self.days/365.25,0.01):.0f}/yil)",
            f"Isabet / PF      : %{self.win_rate:.1f} / {'sonsuz' if pf==float('inf') else f'{pf:.2f}'}",
            f"Beklenti         : {self.expectancy_r:+.3f} R/islem",
            f"Maks. dusus      : {self.max_drawdown_pct:.1f}%",
            f"Komisyon         : {self.fees:.2f}",
            f"t-degeri (kaba)  : {self.t_stat():.2f}  "
            f"({'zayif kanit' if self.t_stat() < 2 else 'anlamli ama korelasyon icin duzeltilmemis'})",
            f"Slot doluydu     : {self.blocked_by_slots} sinyal kacti",
            f"Risk kapisi      : {self.blocked_by_guard} sinyal engellendi",
            f"Dolmayan emir    : {self.unfilled_entries} post_only girisi kacti",
            f"Market'e dusen   : {self.fallback_entries} giris taker olarak yapildi",
        ])


def run_portfolio_backtest(cfg: Config, data: Dict[str, Sequence[Candle]],
                           filters: Optional[Dict[str, SymbolFilters]] = None,
                           start_equity: Optional[float] = None,
                           learner: Optional[Learner] = None) -> PortfolioResult:
    strat = build_strategy(cfg.strategy)
    e = cfg.execution
    slip = e.slippage_bps / 10_000.0
    equity = start_equity or cfg.account.paper_start_balance
    filters = filters or {}
    warm = cfg.strategy.warmup_bars

    symbols = [s for s, c in data.items() if len(c) > warm + 50]
    res = PortfolioResult(start_equity=equity, end_equity=equity, symbols=sorted(symbols))
    if not symbols:
        return res

    feats = {s: Features(data[s], cfg.strategy) for s in symbols}
    idx: Dict[str, Dict[int, int]] = {
        s: {c.close_time: i for i, c in enumerate(data[s])} for s in symbols
    }
    timeline = sorted({c.close_time for s in symbols for c in data[s][warm:]})
    if not timeline:
        return res
    res.days = (timeline[-1] - timeline[0]) / 86_400_000

    guard = RiskGuard(cfg, RiskState())
    # Ogrenme katmani backtest'te de calisir. Amac: "ogrenen bot" fikrinin
    # gercekten faydali olup olmadigini olcmek. Olculemeyen ozellik, ozellik degil.
    if learner is None and cfg.learning.enabled:
        learner = Learner(cfg, store=None)
    positions: Dict[str, Position] = {}
    # sym -> [sinyal, kalan_bekleme_bari]. Market emirde 1 bar, post_only'de
    # emrin tahtada bekleyecegi bar sayisi.
    pending: Dict[str, list] = {}
    post_only = e.entry_order_type == "post_only"

    for ts in timeline:
        # Taban her zaman adiminda guncellenir -- canli motor da her
        # donguda yapiyor. Sadece islem acilirken guncellemek zirveleri
        # kacirir ve cirpinan tabani oldugundan az kisitlayici olcerdi.
        # backtest'te equity zaten gerceklesmis bakiye (acik pozisyonun
        # kagit kari sayilmiyor), yani canlidaki realized_equity ile ayni.
        update_floor(cfg.risk, guard.state, equity)

        # Gun durdurulduysa bekleyen emirler de iptal edilir -- canli motor
        # tam olarak bunu yapiyor (engine.tick). Burada yapmazsak backtest
        # gunluk limitten SONRA da pozisyon acar ve canliyla ayrisir.
        if guard.state.halted and pending:
            pending.clear()

        # ---- 1) Bekleyen girisleri bu barin acilisinda doldur
        for sym in list(pending):
            i = idx[sym].get(ts)
            if i is None:
                continue
            sig, waited = pending[sym]
            bar = data[sym][i]

            if post_only:
                # Maker limit emri fiyatin GERISINE konur (long icin altina).
                # Taker'da slipaji ODERIZ; maker'da ayni kadar iyilesme ISTERIZ.
                # Emir ancak fiyat gelip degerse dolar; degmezse islem kacar.
                limit = sig.entry * (1 - slip) if sig.side == LONG else sig.entry * (1 + slip)
                # Limite DEGMEK dolmak degildir: tahtada onunde emirler var.
                # Doldu saymak icin fiyatin limiti bir miktar ASMASINI sart kos.
                need = limit * (1 - e.post_only_fill_margin_bps / 10_000.0) \
                    if sig.side == LONG else limit * (1 + e.post_only_fill_margin_bps / 10_000.0)
                touched = bar.low <= need if sig.side == LONG else bar.high >= need
                if not touched:
                    waited += 1
                    if waited < e.post_only_wait_bars:
                        pending[sym] = [sig, waited]
                        continue
                    if not e.post_only_fallback_market:
                        pending.pop(sym)
                        res.unfilled_entries += 1
                        continue
                    # Vazgecmek yerine market ile gir: komisyon tasarrufu
                    # kacar ama islem kacmaz.
                    res.fallback_entries += 1
                    entry = (bar.close * (1 + slip) if sig.side == LONG
                             else bar.close * (1 - slip))
                    entry_fee_rate = e.taker_fee
                else:
                    entry = limit
                    entry_fee_rate = e.maker_fee
            else:
                entry = bar.open * (1 + slip) if sig.side == LONG else bar.open * (1 - slip)
                entry_fee_rate = e.taker_fee

            pending.pop(sym)
            if sym in positions or len(positions) >= cfg.risk.max_concurrent_positions:
                continue
            drift = entry - sig.entry
            f = filters.get(sym) or SymbolFilters(sym, 0.0001, 0.001, 0.001, 5.0)
            risk_cfg = cfg.risk
            if learner:
                mult, _why = learner.risk_multiplier(sym, sig.meta, equity)
                if mult != 1.0:
                    risk_cfg = replace(risk_cfg,
                                       risk_per_trade_pct=risk_cfg.risk_per_trade_pct * mult)
            sizing = size_position(equity, equity, entry, sig.stop + drift, f,
                                   risk_cfg, cfg.account.leverage,
                                   open_risk=open_risk_total(positions.values()),
                                   state=guard.state)
            if not sizing.ok:
                continue
            fee = entry * sizing.qty * entry_fee_rate
            equity -= fee
            res.fees += fee
            positions[sym] = Position(
                symbol=sym, side=sig.side, qty=sizing.qty, entry_price=entry,
                stop=sig.stop + drift, tp1=sig.tp1 + drift, tp2=sig.tp2 + drift,
                initial_risk_per_unit=abs(entry - (sig.stop + drift)), opened_at=ts,
                leverage=sizing.leverage, initial_qty=sizing.qty, fees_paid=fee,
                entry_reason=sig.reason, context=dict(sig.meta),
            )
            guard.record_open(ts)

        # ---- 2) Acik pozisyonlari yonet
        for sym in list(positions):
            i = idx[sym].get(ts)
            if i is None:
                continue
            pos = positions[sym]
            bar = data[sym][i]
            pos.bars_held += 1
            for act in strat.manage(pos, bar, feats[sym].atr[i] or 0.0):
                if act["type"] == "move_stop":
                    pos.stop = act["price"]
                    pos.breakeven_moved = True
                elif act["type"] == "partial":
                    qty = pos.qty * act["portion"]
                    px = act["price"] * (1 - slip) if pos.side == LONG else act["price"] * (1 + slip)
                    pnl = (px - pos.entry_price) * qty * pos.direction
                    fee = px * qty * e.taker_fee
                    equity += pnl - fee
                    res.fees += fee
                    pos.qty -= qty
                    pos.realized_pnl += pnl
                    pos.fees_paid += fee
                    pos.tp1_filled = True
                elif act["type"] == "exit":
                    px = act["price"] * (1 - slip) if pos.side == LONG else act["price"] * (1 + slip)
                    pnl = (px - pos.entry_price) * pos.qty * pos.direction
                    fee = px * pos.qty * e.taker_fee
                    equity += pnl - fee
                    res.fees += fee
                    pos.realized_pnl += pnl
                    pos.fees_paid += fee
                    risk_total = pos.initial_risk_per_unit * pos.initial_qty
                    net = pos.realized_pnl - pos.fees_paid
                    trade = Trade(
                        symbol=sym, side=pos.side, qty=pos.initial_qty,
                        entry_price=pos.entry_price, exit_price=px, opened_at=pos.opened_at,
                        closed_at=ts, pnl=net, fees=pos.fees_paid,
                        r_multiple=(net / risk_total) if risk_total > 0 else 0.0,
                        exit_reason=act["reason"], entry_reason=pos.entry_reason,
                        context=dict(pos.context),
                    )
                    res.trades.append(trade)
                    guard.record_close(ts, net)
                    if learner:
                        learner.record_trade(trade, ts)
                        if act["reason"] == "stop":
                            _measure_stop_hunt(learner, cfg, data[sym], i, pos, ts)
                    del positions[sym]
                    break

        # ---- 3) Yeni sinyaller
        #
        # Ayni anda birden fazla sembol sinyal verebilir ama slot sinirli.
        # Sembolleri liste sirasina gore secmek gizli bir yanlilik yaratir
        # (listenin basindaki coin her zaman oncelik alir ve sonuc o coinin
        # kaderine baglanir). Bunun yerine sinyaller KALITEYE gore siralanir:
        # once trend gucu (ADX), esitlikte daha genis R:R.
        if len(positions) + len(pending) < cfg.risk.max_concurrent_positions:
            allowed, _why = guard.can_open(ts, len(positions), equity)
            if allowed:
                candidates = []
                for sym in symbols:
                    if sym in positions or sym in pending:
                        continue
                    i = idx[sym].get(ts)
                    if i is None or i < warm or i >= len(data[sym]) - 1:
                        continue
                    sig = strat.evaluate(sym, data[sym], feats[sym], index=i)
                    if not sig:
                        continue
                    if learner:
                        if not learner.allow_entry(sym, sig.meta, ts)[0]:
                            continue
                        _apply_learned_stop(learner, cfg, sig, sym)
                    if validate_signal_quality(sig, cfg)[0]:
                        candidates.append((sig.meta.get("adx", 0.0), sig.reward_risk, sym, sig))
                candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))

                # Genislik filtresi: ayni yonde kac sembol es zamanli sinyal
                # veriyor? Piyasa geneli tutarliligin olcusu.
                if cfg.risk.min_breadth > 1:
                    side_counts: Dict[str, int] = {}
                    for _a, _r, _s, sg in candidates:
                        side_counts[sg.side] = side_counts.get(sg.side, 0) + 1
                    candidates = [c for c in candidates
                                  if side_counts.get(c[3].side, 0) >= cfg.risk.min_breadth]

                free = cfg.risk.max_concurrent_positions - len(positions) - len(pending)
                cap = cfg.risk.max_same_direction
                taken = 0
                for _adx, _rr, sym, sig in candidates:
                    if taken >= max(0, free):
                        break
                    if cap > 0:
                        same = sum(1 for p in positions.values() if p.side == sig.side)
                        same += sum(1 for g, _w in pending.values() if g.side == sig.side)
                        if same >= cap:
                            continue
                    pending[sym] = [sig, 0]
                    taken += 1
                res.blocked_by_slots += max(0, len(candidates) - taken)
            else:
                res.blocked_by_guard += 1

        mtm = equity
        for sym, pos in positions.items():
            i = idx[sym].get(ts)
            if i is not None:
                mtm += pos.unrealized(data[sym][i].close)
        res.equity_curve.append((ts, mtm))
        if learner:
            learner.record_equity(mtm)

    res.end_equity = equity
    return res


def _apply_learned_stop(learner: Learner, cfg: Config, sig, symbol: str) -> None:
    """Ogrenilmis stop carpani, R katlari korunarak uygulanir."""
    mult = learner.stop_multiplier(symbol)
    if mult == 1.0:
        return
    d = abs(sig.entry - sig.stop) * mult
    c = cfg.strategy
    if sig.side == LONG:
        sig.stop, sig.tp1, sig.tp2 = (sig.entry - d, sig.entry + c.tp1_r * d,
                                      sig.entry + c.tp2_r * d)
    else:
        sig.stop, sig.tp1, sig.tp2 = (sig.entry + d, sig.entry - c.tp1_r * d,
                                      sig.entry - c.tp2_r * d)


def _measure_stop_hunt(learner: Learner, cfg: Config, candles: Sequence[Candle],
                       i: int, pos: Position, ts: int) -> None:
    """Stop sonrasi N mumda fiyat orijinal hedefe ulasti mi?

    Bu bir KARAR degil, olcumdur: sonuc yalnizca gelecekteki stop
    genisligini kalibre eder, o anki islemi etkilemez. Bu yuzden ileriye
    bakmak burada mesru (backtest'te de canlida da ayni sey olculur).
    """
    look = cfg.learning.stop_hunt_lookback_bars
    d = abs(pos.entry_price - pos.stop)
    target = (pos.entry_price + cfg.strategy.tp1_r * d if pos.side == LONG
              else pos.entry_price - cfg.strategy.tp1_r * d)
    window = candles[i + 1: i + 1 + look]
    hit = (any(c.high >= target for c in window) if pos.side == LONG
           else any(c.low <= target for c in window))
    if hit:
        learner.note_stop_hunt(pos.symbol, ts)
