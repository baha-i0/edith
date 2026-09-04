"""Komut satiri arayuzu.

    python -m bot check                 -> config + baglanti + risk ozeti
    python -m bot backtest --days 120   -> gecmis veriyle test
    python -m bot paper                 -> gercek fiyat, sahte para
    python -m bot testnet               -> Binance testnet (gercek emir akisi)
    python -m bot live                  -> GERCEK PARA (onay ister)
    python -m bot status                -> kayitli islem istatistikleri
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config, ConfigError, load_config, load_dotenv
from .logging_setup import setup_logging
from .models import liquidation_distance_pct
from .risk import max_safe_leverage
from .state import Store

log = logging.getLogger(__name__)


def _load(args) -> Config:
    load_dotenv(args.env)
    cfg = load_config(args.config)
    if getattr(args, "symbol", None):
        cfg.symbols = [args.symbol.upper()]
    if getattr(args, "timeframe", None):
        cfg.timeframe = args.timeframe
    return cfg


# --------------------------------------------------------------------- check
def cmd_check(args) -> int:
    cfg = _load(args)
    from .exchange.binance import BinanceFutures

    print("=== CONFIG ===")
    print(f"mod={cfg.mode} semboller={cfg.symbols} tf={cfg.timeframe}")
    print(f"risk/islem=%{cfg.risk.risk_per_trade_pct} | gunluk zarar limiti=%{cfg.risk.daily_loss_limit_pct}")
    print(f"istenen kaldirac={cfg.account.leverage}x (tavan {cfg.risk.max_leverage}x)")
    print()
    print("=== MATEMATIK ===")
    r = cfg.blended_target_r()
    be = cfg.breakeven_win_rate() * 100
    rt_cost = 2 * cfg.execution.taker_fee + 2 * cfg.execution.slippage_bps / 10_000
    print(f"Agirlikli hedef R      : {r:.2f}")
    print(f"Basabas isabet orani   : %{be:.1f}  (komisyon haric)")
    print(f"Gidis-donus maliyet    : %{rt_cost*100:.3f} (fiyat uzerinden)")
    print(f"{cfg.account.leverage}x'te likidasyon mesafesi : %{liquidation_distance_pct(cfg.account.leverage)*100:.2f}")
    for sp in (0.5, 1.0, 2.0):
        print(f"  stop %{sp:.1f} icin guvenli maks kaldirac: "
              f"{max_safe_leverage(sp/100, cfg.risk.max_stop_vs_liquidation)}x")
    print()

    print("=== BAGLANTI ===")
    client = BinanceFutures(cfg, cfg.api_key, cfg.api_secret, testnet=(cfg.mode == "testnet"))
    try:
        offset = client.sync_time()
        print(f"Sunucu saat farki: {offset} ms")
        for sym in cfg.symbols:
            f = client.filters(sym)
            book = client.book_ticker(sym)
            fund = client.funding(sym)
            print(f"{sym}: fiyat={book['mid']:.4f} spread={book['spread_bps']:.2f}bps "
                  f"tick={f.tick_size} step={f.step_size} minNotional={f.min_notional} "
                  f"funding={fund['rate']*100:.4f}%")
    except Exception as exc:
        print(f"HATA: {exc}")
        return 1

    if cfg.mode in ("testnet", "live"):
        try:
            b = client.balances()
            print(f"Hesap: equity={b['equity']:.2f} kullanilabilir={b['available']:.2f}")
            eq = b["equity"]
        except Exception as exc:
            print(f"Hesap okunamadi: {exc}")
            return 1
    else:
        eq = cfg.account.paper_start_balance
        print(f"Paper baslangic bakiye: {eq:.2f}")

    print()
    print("=== ORNEK POZISYON (stop %1 varsayimi) ===")
    from .risk import size_position
    f = client.filters(cfg.symbols[0])
    px = client.book_ticker(cfg.symbols[0])["mid"]
    s = size_position(eq, eq, px, px * 0.99, f, cfg.risk, cfg.account.leverage)
    print(s.reason if s.ok else f"acilamaz: {s.reason}")
    if s.ok:
        print(f"miktar={s.qty} notional={s.notional:.2f} marj={s.margin:.2f} "
              f"kaldirac={s.leverage}x risk={s.risk_amount:.2f}")
    return 0


# ------------------------------------------------------------------ backtest
def _load_candles(cfg, client, symbol: str, args):
    """Veri kaynagi secimi: arsiv (genis gecmis, bolge kisiti yok) veya REST API."""
    from .archive import fetch_archive
    from .data import fetch_history

    if args.source == "archive":
        return fetch_archive(symbol, cfg.timeframe, months=args.months)
    try:
        return fetch_history(client, symbol, cfg.timeframe, args.days)
    except Exception as exc:
        log.warning("REST API veri cekilemedi (%s), arsive dusuluyor", exc)
        return fetch_archive(symbol, cfg.timeframe, months=args.months)


def _load_filters(cfg, client, symbol: str):
    from .models import SymbolFilters
    try:
        return client.filters(symbol)
    except Exception:
        log.warning("exchangeInfo alinamadi, varsayilan suzgecler kullaniliyor")
        return SymbolFilters(symbol, tick_size=0.01, step_size=0.01,
                             min_qty=0.01, min_notional=5.0)


def cmd_backtest(args) -> int:
    cfg = _load(args)
    from .backtest import run_backtest, run_portfolio_backtest
    from .exchange.binance import BinanceFutures

    client = BinanceFutures(cfg)

    if args.portfolio:
        # Gercekci olan bu: ortak kasa, es zamanli pozisyon limiti, gunluk limitler.
        data, filters = {}, {}
        for sym in cfg.symbols:
            candles = _load_candles(cfg, client, sym, args)
            if len(candles) > cfg.strategy.warmup_bars + 50:
                data[sym] = candles
                filters[sym] = _load_filters(cfg, client, sym)
        if not data:
            print("Yeterli veri yok.")
            return 1
        res = run_portfolio_backtest(cfg, data, filters)
        print("\n" + "=" * 62)
        print("PORTFOY BACKTESTI (ortak kasa + gercek limitler)")
        print("=" * 62)
        print(res.summary())
        return 0

    total_start = total_end = 0.0
    for sym in cfg.symbols:
        candles = _load_candles(cfg, client, sym, args)
        if len(candles) < cfg.strategy.warmup_bars + 50:
            print(f"{sym}: yeterli veri yok ({len(candles)} mum)")
            continue
        filters = _load_filters(cfg, client, sym)
        res = run_backtest(cfg, candles, sym, filters)
        print("\n" + "=" * 62)
        print(res.summary())
        print("-" * 62)
        print(res.verdict())
        total_start += res.start_equity
        total_end += res.end_equity
        if args.trades:
            print("\nSon islemler:")
            for t in res.trades[-args.trades:]:
                print(f"  {t.side:5} giris={t.entry_price:.4f} cikis={t.exit_price:.4f} "
                      f"pnl={t.pnl:+7.2f} R={t.r_multiple:+5.2f} {t.exit_reason}")
    if total_start:
        print("\n" + "=" * 62)
        print(f"TOPLAM: {total_start:.2f} -> {total_end:.2f} "
              f"({100*(total_end-total_start)/total_start:+.2f}%)")
    return 0


# --------------------------------------------------------------------- calis
def cmd_run(args, mode: str) -> int:
    cfg = _load(args)
    cfg.mode = mode
    cfg.validate()
    setup_logging(cfg.log_path, args.log_level)

    if mode == "live":
        print("\n" + "!" * 62)
        print("GERCEK PARA MODU. Bu bot gercek emir gonderecek.")
        print(f"  semboller : {cfg.symbols}")
        print(f"  risk/islem: %{cfg.risk.risk_per_trade_pct} | kaldirac tavani: {cfg.risk.max_leverage}x")
        print(f"  gunluk zarar limiti: %{cfg.risk.daily_loss_limit_pct}")
        print("!" * 62)
        if input("Devam etmek icin 'ANLADIM' yaz: ").strip() != "ANLADIM":
            print("Iptal edildi.")
            return 1

    from .engine import build_engine
    engine = build_engine(cfg)
    engine.run_forever()
    return 0


# -------------------------------------------------------------------- status
def cmd_status(args) -> int:
    cfg = _load(args)
    store = Store(cfg.state_path, mode=args.mode or cfg.mode)
    stats = store.stats()
    if stats.get("trades", 0) == 0:
        print("Kayitli islem yok.")
        return 0
    print(f"Islem      : {stats['trades']}")
    print(f"Isabet     : %{stats['win_rate']:.1f}")
    print(f"Net PnL    : {stats['net_pnl']:+.2f}")
    print(f"Komisyon   : {stats['fees']:.2f}")
    pf = stats["profit_factor"]
    print(f"Profit fac.: {'sonsuz' if pf == float('inf') else f'{pf:.2f}'}")
    print(f"Ort. R     : {stats['avg_r']:+.3f}")
    print(f"En iyi/kotu: {stats['best']:+.2f} / {stats['worst']:+.2f}")
    print("\nSon islemler:")
    for r in store.recent_trades(10):
        print(f"  {r['symbol']} {r['side']:5} pnl={r['pnl']:+7.2f} R={r['r_multiple']:+5.2f} "
              f"{r['exit_reason']}")
    risk = store.load_risk_state()
    print(f"\nGun: {risk.day} | bugun PnL={risk.realized_pnl_today:+.2f} | "
          f"islem={risk.trades_today} | durduruldu={risk.halted} {risk.halt_reason}")
    return 0


# -------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    """'Her sey yolunda mi?' -- duz Turkce cevap, istatistik bilmek gerekmez."""
    cfg = _load(args)
    from .health import CRITICAL, WARN, run_health_checks
    from .learning import Learner

    store = Store(cfg.state_path, mode=args.mode or cfg.mode)
    learner = Learner(cfg, store)
    rep = run_health_checks(cfg, store, learner)
    print(rep.render())

    if rep.halt_required:
        print("!" * 64)
        print("BOT YENI POZISYON ACMAYI DURDURDU.")
        print("Sebep:", rep.halt_reason)
        print()
        print("Bu bir hata degil, tasarim. Kanit stratejinin bozuldugunu")
        print("gosterdiginde dogru davranis kendini yeniden ayarlamak degil,")
        print("durup sana haber vermektir. Kendini optimize eden bir sistem")
        print("bozuldugunu asla soylemez.")
        print("!" * 64)

    if args.acknowledge:
        rs = store.load_risk_state()
        rs.halted = False
        rs.halt_reason = ""
        store.save_risk_state(rs)
        print("\nDurdurma kaldirildi. Bot bir sonraki dongude tekrar islem acabilir.")
    return {"bilgi": 0, "dikkat": 0, "mudahale": 1}[rep.worst]


# --------------------------------------------------------------------- learn
def cmd_learn(args) -> int:
    """Botun neyi ogrendigini gosterir. Kara kutu birakmanin anlami yok."""
    cfg = _load(args)
    from .learning import Learner

    store = Store(cfg.state_path, mode=args.mode or cfg.mode)
    learner = Learner(cfg, store)
    print(learner.report())
    print()
    lc = cfg.learning
    conf = {1.64: 95, 2.33: 99, 2.58: 99.5, 3.09: 99.9}.get(round(lc.significance_z, 2))
    conf_txt = f"%{conf}" if conf else f"z={lc.significance_z}"
    print("Nasil okunur:")
    print(f"  - 'ust sinir' ortalamanin tek yonlu {conf_txt} ust guven siniri.")
    print("    Sifirin ALTINDA ise beklentinin negatif oldugu kanitlanmis demektir.")
    print("    Ortalamanin negatif olmasi TEK BASINA yeterli degil -- kucuk")
    print("    orneklerde ortalama surekli isaret degistirir.")
    print(f"  - {lc.min_trades_per_bucket} islemin altindaki kovalardan hicbir sonuc")
    print(f"    cikarilmaz, bilerek. Test her {lc.bench_eval_every} islemde bir yapilir")
    print("    (her islemde test etmek yanlis alarm oranini sisirir).")
    print(f"  - Ogrenme riski artiramaz: risk carpani tavani "
          f"{lc.max_risk_multiplier:.2f}x.")
    if args.reset:
        confirm = input("\nTum ogrenilenleri silmek icin 'SIFIRLA' yaz: ").strip()
        if confirm == "SIFIRLA":
            store.delete_kv(f"learning:{store.mode}")
            print("Ogrenme durumu sifirlandi.")
        else:
            print("Iptal edildi.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bot", description="Binance Futures trading bot")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--env", default=".env")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="config + baglanti dogrulamasi")
    c.add_argument("--symbol")
    c.add_argument("--timeframe")
    c.set_defaults(func=cmd_check)

    b = sub.add_parser("backtest", help="gecmis veriyle test")
    b.add_argument("--days", type=int, default=120, help="REST API kaynagi icin gun sayisi")
    b.add_argument("--months", type=int, default=12, help="arsiv kaynagi icin ay sayisi")
    b.add_argument("--source", choices=("api", "archive"), default="archive",
                   help="archive = data.binance.vision (genis gecmis, bolge kisiti yok)")
    b.add_argument("--symbol")
    b.add_argument("--timeframe")
    b.add_argument("--trades", type=int, default=0, help="son N islemi listele")
    b.add_argument("--portfolio", action="store_true",
                   help="tum sembolleri tek kasa + es zamanli pozisyon limiti ile test et")
    b.set_defaults(func=cmd_backtest)

    for name, mode in (("paper", "paper"), ("testnet", "testnet"), ("live", "live")):
        r = sub.add_parser(name, help=f"{mode} modunda calistir")
        r.add_argument("--symbol")
        r.add_argument("--timeframe")
        r.set_defaults(func=lambda a, m=mode: cmd_run(a, m))

    d = sub.add_parser("doctor", help="her sey yolunda mi? (duz Turkce rapor)")
    d.add_argument("--mode", help="paper/testnet/live")
    d.add_argument("--symbol")
    d.add_argument("--acknowledge", action="store_true",
                   help="durdurmayi kaldir (sorunu gorduğunu onayla)")
    d.set_defaults(func=cmd_doctor)

    l = sub.add_parser("learn", help="botun ogrendiklerini goster")
    l.add_argument("--mode", help="paper/testnet/live")
    l.add_argument("--symbol")
    l.add_argument("--reset", action="store_true", help="ogrenme durumunu sifirla")
    l.set_defaults(func=cmd_learn)

    s = sub.add_parser("status", help="kayitli istatistikler")
    s.add_argument("--mode", help="paper/testnet/live")
    s.add_argument("--symbol")
    s.set_defaults(func=cmd_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s | %(levelname)-7s | %(message)s")
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nDurduruldu.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
