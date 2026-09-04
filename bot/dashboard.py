"""Yerel izleme paneli.

Neden ayri bir surec degil de botun icinde bir thread:
  Panelin gostermesi gereken en degerli sey ACIK POZISYONUN ANLIK DURUMU.
  Bu bilgi veritabaninda degil, botun hafizasinda ve borsada. Ayri bir
  surec sadece SQLite'i okuyabilirdi ve "su an ne kadar kardayim" sorusuna
  cevap veremezdi. Bu yuzden panel motorun icinde yasar.

Neden Flask yok:
  Projenin tum calisma zamani bagimliligi `requests` + `PyYAML`. Bir izleme
  paneli icin web framework eklemek, botun guvenlik yuzeyini ve kurulum
  karmasikligini bir raporlama ozelligi ugruna buyutmek olurdu. stdlib'in
  http.server'i bu is icin fazlasiyla yeterli.

Neden salt okunur:
  bkz. DashboardConfig dokumantasyonu -- ozeti: localhost'a baglanan bir
  sunucuya kullanicinin actigi her web sitesi istek gonderebilir.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from .config import Config
from .models import LONG
from .risk import effective_floor, open_risk_total

log = logging.getLogger(__name__)


# ============================================================== veri toplama
def _drawdown_series(points: List[tuple]) -> tuple:
    """Her noktada zirveden dusus yuzdesi + gorulen en kotu dusus."""
    peak = -math.inf
    dd: List[float] = []
    worst = 0.0
    for _ts, eq in points:
        peak = max(peak, eq)
        d = (peak - eq) / peak * 100 if peak > 0 else 0.0
        dd.append(d)
        worst = max(worst, d)
    return dd, worst


def _downsample(points: List[tuple], target: int = 400) -> List[tuple]:
    """Grafik icin nokta seyreltme. Zirve ve dipleri kaybetmemek icin
    her dilimden ilk, en yuksek ve en dusuk deger korunur."""
    if len(points) <= target:
        return points
    # Her dilimden 3 nokta cikacagi icin dilim sayisi hedefin ucte biri.
    slices = max(1, target // 3)
    step = len(points) / slices
    out: List[tuple] = []
    for i in range(slices):
        chunk = points[int(i * step):int((i + 1) * step)] or points[int(i * step):]
        if not chunk:
            continue
        out.append(chunk[0])
        hi = max(chunk, key=lambda p: p[1])
        lo = min(chunk, key=lambda p: p[1])
        for p in (lo, hi):
            if p is not chunk[0]:
                out.append(p)
    out.sort(key=lambda p: p[0])
    return out


def _histogram(values: List[float], lo: float = -3.0, hi: float = 6.0,
               bins: int = 18) -> List[Dict[str, float]]:
    if not values:
        return []
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = int((min(max(v, lo), hi - 1e-9) - lo) / width)
        counts[min(max(idx, 0), bins - 1)] += 1
    return [{"from": lo + i * width, "to": lo + (i + 1) * width, "n": c}
            for i, c in enumerate(counts)]


def _streak(rs: List[float]) -> int:
    """Suregelen kazanc (+) ya da zarar (-) serisi."""
    if not rs:
        return 0
    sign = 1 if rs[-1] > 0 else -1
    n = 0
    for v in reversed(rs):
        if (1 if v > 0 else -1) != sign:
            break
        n += 1
    return n * sign


def _floor_state(cfg: Config, risk, equity: float, engine) -> Optional[Dict[str, Any]]:
    """Sermaye tabani paneli. Taban kapaliysa None.

    Panelin bunu gostermesi sart: taban aktifken bot yastik tukendiginde
    SESSIZCE durur. Gorunmezse kullanici "neden islem yapmiyor" diye
    saatlerce bakar.
    """
    floor = effective_floor(cfg.risk, risk)
    if floor <= 0:
        return None
    cushion = max(0.0, equity - floor)
    acik = 0.0
    if engine is not None:
        try:
            acik = open_risk_total(engine.broker.positions().values())
        except Exception:
            acik = 0.0
    tavan = cushion * cfg.risk.max_total_risk_pct_of_cushion / 100.0
    return {
        "floor": floor,
        "fixed_floor": cfg.risk.capital_floor_usdt,
        "ratchet_pct": cfg.risk.capital_floor_ratchet_pct,
        "peak_equity": risk.peak_equity,
        "cushion": cushion,
        "min_cushion": cfg.risk.min_cushion_usdt,
        "cushion_pct": (cushion / equity * 100) if equity > 0 else 0.0,
        "open_risk": acik,
        "risk_cap": tavan,
        "exhausted": cushion < cfg.risk.min_cushion_usdt,
    }


def build_state(cfg: Config, store, engine=None) -> Dict[str, Any]:
    """Panelin gosterdigi her sey tek bir JSON'da. Motor verilirse acik
    pozisyonlar ANLIK fiyatla degerlenir; verilmezse sadece veritabani."""
    now = int(time.time() * 1000)
    rows = store.all_trades()
    rs = [r["r_multiple"] for r in rows]
    pnls = [r["pnl"] for r in rows]

    eq_points = store.equity_series(limit=20_000)
    dd, worst_dd = _drawdown_series(eq_points)
    shown = _downsample(list(zip([p[0] for p in eq_points],
                                 [p[1] for p in eq_points])))
    shown_idx = {p[0]: i for i, p in enumerate(eq_points)}
    equity_chart = [{"ts": ts, "eq": eq, "dd": dd[shown_idx.get(ts, 0)]}
                    for ts, eq in shown]

    risk = store.load_risk_state()
    equity = eq_points[-1][1] if eq_points else 0.0
    positions: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []

    if engine is not None:
        try:
            equity = engine.broker.equity()
        except Exception:
            log.debug("panel: bakiye okunamadi", exc_info=True)
        for sym, p in engine.broker.positions().items():
            try:
                px = engine.market.book_ticker(sym)["mid"]
            except Exception:
                px = p.entry_price
            pnl = (px - p.entry_price) * p.qty * (1 if p.side == LONG else -1)
            risk_amt = p.initial_risk_per_unit * p.initial_qty
            positions.append({
                "symbol": sym, "side": p.side, "qty": p.qty,
                "entry": p.entry_price, "price": px, "stop": p.stop,
                "tp1": p.tp1, "tp2": p.tp2, "pnl": pnl,
                "r": pnl / risk_amt if risk_amt else 0.0,
                "bars": p.bars_held, "opened_at": p.opened_at,
                "breakeven": p.breakeven_moved,
                "reason": p.entry_reason,
            })
        try:
            pending = [{"symbol": s, "side": side}
                       for s, side in engine.broker.pending_entries().items()]
        except Exception:
            pending = []

    base = risk.day_start_equity or equity or 1.0
    if risk.paused:
        status, kind = "ELLE DURDURULDU", "paused"
    elif risk.shadow_mode:
        status, kind = "GOLGE MODU - para riske atilmiyor", "shadow"
    elif risk.halted:
        status, kind = f"BUGUN DURDU: {risk.halt_reason}", "halted"
    else:
        status, kind = "CALISIYOR", "ok"

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    payoff = avg_win / avg_loss if avg_loss else 0.0
    mean_r = sum(rs) / len(rs) if rs else 0.0
    sd = (sum((x - mean_r) ** 2 for x in rs) / len(rs)) ** 0.5 if len(rs) > 1 else 0.0
    tstat = mean_r / (sd / len(rs) ** 0.5) if sd > 0 and rs else 0.0

    per_symbol: Dict[str, Dict[str, float]] = {}
    for r in rows:
        d = per_symbol.setdefault(r["symbol"], {"n": 0, "r": 0.0, "pnl": 0.0, "w": 0})
        d["n"] += 1
        d["r"] += r["r_multiple"]
        d["pnl"] += r["pnl"]
        d["w"] += 1 if r["pnl"] > 0 else 0

    health: List[Dict[str, str]] = []
    try:
        from .health import run_health_checks
        rep = run_health_checks(cfg, store,
                                getattr(engine, "learner", None),
                                getattr(engine, "broker", None), now)
        health = [{"name": c.name, "severity": c.severity,
                   "message": c.message, "action": c.action} for c in rep.checks]
    except Exception:
        log.debug("panel: saglik kontrolu calismadi", exc_info=True)

    learning = ""
    if engine is not None:
        try:
            learning = engine.learner.report()
        except Exception:
            learning = ""

    return {
        "meta": {
            "mode": cfg.mode, "timeframe": cfg.timeframe,
            "symbols": cfg.symbols, "generated_at": now,
            "refresh": cfg.dashboard.refresh_seconds,
            "entry_order_type": cfg.execution.entry_order_type,
        },
        "status": {"text": status, "kind": kind, "reason": risk.shadow_reason or risk.halt_reason},
        "account": {
            "equity": equity,
            "day_pnl": risk.realized_pnl_today,
            "day_pnl_pct": risk.realized_pnl_today / base * 100,
            "peak_drawdown_pct": worst_dd,
            "current_drawdown_pct": dd[-1] if dd else 0.0,
        },
        "floor": _floor_state(cfg, risk, equity, engine),
        "risk": {
            "trades_today": risk.trades_today,
            "max_trades": cfg.risk.max_trades_per_day,
            "consecutive_losses": risk.consecutive_losses,
            "max_consecutive": cfg.risk.max_consecutive_losses,
            "cooldown_min": max(0, int((risk.cooldown_until_ms - now) / 60000)),
            "day_loss_limit_pct": cfg.risk.daily_loss_limit_pct,
            "day_profit_target_pct": cfg.risk.daily_profit_target_pct,
            "open_slots": cfg.risk.max_concurrent_positions,
            "risk_per_trade_pct": cfg.risk.risk_per_trade_pct,
        },
        "stats": {
            "trades": len(rows),
            "win_rate": 100.0 * len(wins) / len(rs) if rs else 0.0,
            "expectancy_r": mean_r,
            "t_stat": tstat,
            "payoff": payoff,
            "avg_win_r": avg_win, "avg_loss_r": -avg_loss,
            "net_pnl": sum(pnls), "fees": sum(r["fees"] for r in rows),
            "profit_factor": (sum(p for p in pnls if p > 0) /
                              abs(sum(p for p in pnls if p <= 0)))
            if any(p <= 0 for p in pnls) and sum(abs(p) for p in pnls if p <= 0) else 0.0,
            "streak": _streak(rs),
            # 1/(1+R) -- bu odul/risk oraninda basabas kalmak icin gereken isabet
            "breakeven_wr": 100.0 / (1.0 + payoff) if payoff else 0.0,
        },
        "equity": equity_chart,
        "histogram": _histogram(rs),
        "positions": positions,
        "pending": pending,
        "symbols": [
            {"symbol": k, "n": v["n"], "avg_r": v["r"] / v["n"],
             "pnl": v["pnl"], "win_rate": 100.0 * v["w"] / v["n"]}
            for k, v in sorted(per_symbol.items(), key=lambda kv: -kv[1]["r"])
        ],
        "trades": [
            {"symbol": r["symbol"], "side": r["side"], "pnl": r["pnl"],
             "r": r["r_multiple"], "exit": r["exit_reason"],
             "entry_price": r["entry_price"], "exit_price": r["exit_price"],
             "closed_at": r["closed_at"], "fees": r["fees"]}
            for r in store.recent_trades(cfg.dashboard.max_trades_shown)
        ],
        "health": health,
        "learning": learning,
    }


# =================================================================== sunucu
class _Handler(BaseHTTPRequestHandler):
    server_version = "edith"
    sys_version = ""

    # ---- yardimcilar
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Panel disaridan hicbir kaynak yuklemez; bunu tarayiciya da soyle.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; img-src data:; "
                         "connect-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _host_ok(self) -> bool:
        """Host basligi bu sunucuya mi ait?

        DNS rebinding: uzaktaki bir site, kendi alan adini 127.0.0.1'e
        cozdurup tarayicidan bu sunucuya istek attirabilir. Baglanti
        gercekten localhost'tan gelir, yani IP kontrolu bunu yakalamaz --
        ama Host basligi saldirganin alan adini tasir. "Panel yalniz bu
        bilgisayardan gorulur" sozunun gecerli olmasi icin gereken kontrol.
        """
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else \
            host.split("]")[0] + "]"
        izin = {"127.0.0.1", "localhost", "[::1]", "::1"}
        cfg_host = getattr(self.server, "allowed_host", "")
        if cfg_host:
            izin.add(str(cfg_host).lower())
        return name in izin

    def _authorized(self) -> bool:
        token = os.getenv("DASHBOARD_TOKEN", "")
        if not token:
            return True   # localhost'a bagliyken token istenmez
        got = self.headers.get("Authorization", "")
        if got.startswith("Bearer "):
            got = got[7:]
        else:
            got = self.path.split("token=")[-1] if "token=" in self.path else ""
        # sabit zamanli karsilastirma
        import hmac
        return hmac.compare_digest(got, token)

    # ---- yollar
    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send(403, b"gecersiz Host basligi", "text/plain; charset=utf-8")
            return
        if not self._authorized():
            self._send(401, b"yetkisiz", "text/plain; charset=utf-8")
            return
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            try:
                state = self.server.state_fn()
            except Exception as exc:
                log.exception("panel durumu uretilemedi")
                self._send(500, json.dumps({"error": str(exc)}).encode(),
                           "application/json")
                return
            self._send(200, json.dumps(state).encode("utf-8"), "application/json")
        else:
            self._send(404, b"yok", "text/plain; charset=utf-8")

    # Panel SALT OKUNUR. Yazma yok, kontrol yok. Sebep: modul basligi.
    def do_POST(self) -> None:  # noqa: N802
        self._send(405, b"panel salt okunur; kontrol icin Telegram kullan",
                   "text/plain; charset=utf-8")

    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, fmt: str, *args) -> None:
        # Token sorgu dizesinde gelebiliyor (tarayicidan erisim icin).
        # Yolu oldugu gibi loglamak onu logs/bot.log'a yazar.
        temiz = tuple(
            a.split("token=")[0] + "token=***" if isinstance(a, str) and "token=" in a
            else a for a in args
        )
        log.debug("[panel] " + fmt, *temiz)


class DashboardServer:
    """Motorun icinde arka planda calisan HTTP sunucusu."""

    def __init__(self, cfg: Config, state_fn):
        self.cfg = cfg
        self.state_fn = state_fn
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.cfg.dashboard.host in ("0.0.0.0", "::") \
            else self.cfg.dashboard.host
        return f"http://{host}:{self.cfg.dashboard.port}"

    def start(self) -> bool:
        d = self.cfg.dashboard
        if not d.enabled:
            return False
        try:
            httpd = ThreadingHTTPServer((d.host, d.port), _Handler)
        except OSError as exc:
            # Panel acilamadiysa BOT DURMAZ. Islem yapmak izlemekten onemli.
            log.warning("Panel %s:%s adresinde acilamadi (%s) - bot devam ediyor",
                        d.host, d.port, exc)
            return False
        httpd.state_fn = self.state_fn  # type: ignore[attr-defined]
        httpd.allowed_host = d.host  # type: ignore[attr-defined]
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        name="edith-panel", daemon=True)
        self._thread.start()
        log.info("Panel acildi: %s", self.url)
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


# ==================================================================== sayfa
# Tek parca, disa bagimliligi sifir. CDN yok: bot internetsiz bir makinede
# de calisabilmeli ve panel, uctan gelen bir script'in guncellenmesiyle
# degisebilecek bir yuzey olmamali.
PAGE = r"""<title>EDITH</title>
<style>
:root{
  --bg:#0b0e13; --panel:#141922; --panel2:#1b212c; --line:#252c3a;
  --fg:#e6ebf2; --dim:#8a94a6; --faint:#5c6577;
  --up:#2ecc8f; --down:#ff5f6d; --warn:#f5b544; --accent:#5b9dff;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
.wrap{max-width:1280px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:15px;margin:0;letter-spacing:.14em;font-weight:600}
h2{font-size:11px;margin:0 0 12px;color:var(--dim);letter-spacing:.12em;
  text-transform:uppercase;font-weight:600}

/* ust bar */
.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding-bottom:14px;border-bottom:1px solid var(--line);margin-bottom:18px}
.pill{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;
  letter-spacing:.06em;border:1px solid transparent}
.pill.ok{background:rgba(46,204,143,.12);color:var(--up);border-color:rgba(46,204,143,.3)}
.pill.paused{background:rgba(245,181,68,.12);color:var(--warn);border-color:rgba(245,181,68,.3)}
.pill.halted{background:rgba(245,181,68,.12);color:var(--warn);border-color:rgba(245,181,68,.3)}
.pill.shadow{background:rgba(91,157,255,.12);color:var(--accent);border-color:rgba(91,157,255,.3)}
.pill.live{background:rgba(255,95,109,.14);color:var(--down);border-color:rgba(255,95,109,.35)}
.spacer{flex:1}
.stamp{color:var(--faint);font-size:11px;font-family:var(--mono)}

/* kartlar */
.grid{display:grid;gap:12px}
.cards{grid-template-columns:repeat(5,1fr);margin-bottom:18px}
@media(max-width:1080px){.cards{grid-template-columns:repeat(3,1fr)}}
@media(max-width:660px){.cards{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.card .k{font-size:10px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase}
.card .v{font-family:var(--mono);font-size:21px;margin-top:5px;font-variant-numeric:tabular-nums}
.card .s{font-size:11px;color:var(--faint);margin-top:2px;font-family:var(--mono)}
.big .v{font-size:27px}
.up{color:var(--up)} .down{color:var(--down)} .warnc{color:var(--warn)}
.dim{color:var(--dim)}

section{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:16px;margin-bottom:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:860px){.two{grid-template-columns:1fr}}

/* tablolar */
.tw{overflow-x:auto}
.tw.scroll{max-height:420px;overflow-y:auto}
.tw.scroll thead th{position:sticky;top:0;background:var(--panel);z-index:1}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;
  font-variant-numeric:tabular-nums}
th{text-align:right;padding:7px 9px;color:var(--dim);font-weight:500;font-size:10px;
  letter-spacing:.08em;text-transform:uppercase;border-bottom:1px solid var(--line);
  white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:7px 9px;border-bottom:1px solid rgba(37,44,58,.5);text-align:right;white-space:nowrap}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--panel2)}
.side{font-size:10px;padding:1px 6px;border-radius:4px;font-weight:600}
.side.LONG{background:rgba(46,204,143,.15);color:var(--up)}
.side.SHORT{background:rgba(255,95,109,.15);color:var(--down)}
.bar{height:4px;border-radius:2px;background:var(--panel2);overflow:hidden;margin-top:5px}
.bar>i{display:block;height:100%;background:var(--accent)}

/* saglik */
.chk{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid rgba(37,44,58,.5);
  align-items:flex-start}
.chk:last-child{border-bottom:none}
.dot{width:7px;height:7px;border-radius:50%;margin-top:6px;flex:none}
.dot.info{background:var(--up)} .dot.warn{background:var(--warn)}
.dot.critical{background:var(--down)}
.chk .n{font-size:11px;color:var(--dim);width:150px;flex:none;font-family:var(--mono)}
.chk .m{flex:1;font-size:12px}
.chk .a{color:var(--warn);font-size:11px;margin-top:3px}
pre{font-family:var(--mono);font-size:11.5px;white-space:pre-wrap;margin:0;
  color:var(--dim);line-height:1.6}
.empty{color:var(--faint);font-size:12px;padding:14px 0;text-align:center}
.floorwrap{margin-bottom:14px}
.floorbar{display:flex;height:22px;border-radius:4px;overflow:hidden;
  border:1px solid var(--line);background:var(--panel2)}
.floorbar>span{display:block}
.floorbar .lock{background:var(--accent);opacity:.32}
.floorbar .cush{background:var(--warn);opacity:.55}
.floorlegend{display:flex;flex-wrap:wrap;gap:16px;margin-top:8px;
  font-family:var(--mono);font-size:11.5px;color:var(--dim)}
.sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}
.sw.lock{background:var(--accent);opacity:.32}
.sw.cush{background:var(--warn);opacity:.55}
svg{display:block;width:100%}
.ro{font-size:11px;color:var(--faint);border-top:1px solid var(--line);
  margin-top:22px;padding-top:14px;line-height:1.7}
</style>

<div class="wrap">
  <div class="top">
    <h1>EDITH</h1>
    <span class="pill" id="mode"></span>
    <span class="pill" id="status"></span>
    <span class="spacer"></span>
    <span class="stamp" id="stamp"></span>
  </div>

  <div class="grid cards" id="kpi"></div>

  <section>
    <h2>Bakiye egrisi</h2>
    <div id="eqchart"></div>
  </section>

  <section id="floorBox" hidden>
    <h2>Sermaye tabani</h2>
    <div id="floor"></div>
  </section>

  <section>
    <h2>Acik pozisyonlar</h2>
    <div class="tw" id="pos"></div>
  </section>

  <div class="two">
    <section>
      <h2>Islem dagilimi (R)</h2>
      <div id="hist"></div>
    </section>
    <section>
      <h2>Sembol basina</h2>
      <div class="tw" id="syms"></div>
    </section>
  </div>

  <section>
    <h2>Son islemler</h2>
    <div class="tw scroll" id="trades"></div>
  </section>

  <div class="two">
    <section>
      <h2>Saglik kontrolleri</h2>
      <div id="health"></div>
    </section>
    <section>
      <h2>Ogrenme</h2>
      <pre id="learning"></pre>
    </section>
  </div>

  <div class="ro">
    Bu panel <b>salt okunurdur</b> ve yalniz bu bilgisayardan erisilebilir.
    Icinde dugme yok: localhost'ta dinleyen bir sunucuya, tarayicinda acik
    olan herhangi bir web sitesi istek gonderebilir; kimlik dogrulamasi
    olmayan bir "pozisyonu kapat" ucu, rastgele bir sekmenin islemlerini
    kapatabilmesi demek olurdu.
    Kontrol Telegram'da: <code>/durum /dur /devam /kapat</code>
  </div>
</div>

<script>
const F=(x,d=2)=>(x==null||isNaN(x))?"-":Number(x).toFixed(d);
/* Fiyat ondaligi buyuklukle degisir: BTC'de 4 hane gurultu, SHIB'de 2 hane
   bilgi kaybi. Esikler Binance tick_size dagilimina yakin secildi. */
const P=x=>x==null||isNaN(x)?"-":Number(x).toFixed(
  x>=1000?1: x>=100?2: x>=1?3: x>=0.01?5:8);
const S=(x,d=2)=>(x>=0?"+":"")+F(x,d);
const cls=x=>x>0?"up":(x<0?"down":"dim");
const el=(t,a={},...c)=>{const e=document.createElement(t);
  for(const k in a){k=="class"?e.className=a[k]:k=="html"?e.innerHTML=a[k]:e.setAttribute(k,a[k]);}
  c.flat().forEach(x=>e.append(x));return e;};
const dt=ms=>new Date(ms).toLocaleString("tr-TR",{day:"2-digit",month:"2-digit",
  hour:"2-digit",minute:"2-digit"});

function table(head,rows,align){
  if(!rows.length) return el("div",{class:"empty"},"kayit yok");
  const t=el("table");
  t.append(el("thead",{},el("tr",{},head.map(h=>el("th",{},h)))));
  const tb=el("tbody");
  rows.forEach(r=>tb.append(el("tr",{},r.map(c=>
    typeof c=="object"&&c.html!==undefined?el("td",{class:c.cls||"",html:c.html}):el("td",{},String(c))))));
  t.append(tb);return t;
}

/* ---------- bakiye egrisi: cizgi + altinda dusus golgesi ---------- */
function equityChart(pts){
  const W=1000,H=240,P={t:14,r:52,b:22,l:6};
  if(pts.length<2) return el("div",{class:"empty"},"grafik icin yeterli veri yok");
  const xs=pts.map(p=>p.ts),ys=pts.map(p=>p.eq);
  const x0=Math.min(...xs),x1=Math.max(...xs);
  let y0=Math.min(...ys),y1=Math.max(...ys);
  const pad=(y1-y0)*0.12||Math.abs(y1)*0.05||1; y0-=pad; y1+=pad;
  const X=v=>P.l+(v-x0)/((x1-x0)||1)*(W-P.l-P.r);
  const Y=v=>P.t+(1-(v-y0)/((y1-y0)||1))*(H-P.t-P.b);
  const maxdd=Math.max(...pts.map(p=>p.dd),0.001);
  const DY=v=>H-P.b-(v/maxdd)*(H-P.t-P.b)*0.3;

  const line=pts.map((p,i)=>(i?"L":"M")+X(p.ts).toFixed(1)+" "+Y(p.eq).toFixed(1)).join(" ");
  const area=line+` L${X(x1).toFixed(1)} ${H-P.b} L${X(x0).toFixed(1)} ${H-P.b} Z`;
  const ddArea=pts.map((p,i)=>(i?"L":"M")+X(p.ts).toFixed(1)+" "+DY(p.dd).toFixed(1)).join(" ")
    +` L${X(x1).toFixed(1)} ${H-P.b} L${X(x0).toFixed(1)} ${H-P.b} Z`;
  const first=ys[0],last=ys[ys.length-1],good=last>=first;
  const col=good?"var(--up)":"var(--down)";

  let g="";
  for(let i=0;i<=3;i++){
    const v=y0+(y1-y0)*i/3, y=Y(v).toFixed(1);
    g+=`<line x1="${P.l}" x2="${W-P.r}" y1="${y}" y2="${y}" stroke="var(--line)" stroke-width="1"/>`
     +`<text x="${W-P.r+7}" y="${+y+4}" fill="var(--faint)" font-size="11"
        font-family="var(--mono)">${v.toFixed(v<100?1:0)}</text>`;
  }
  return el("div",{html:`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:240px">
    <defs><linearGradient id="fillg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${col}" stop-opacity=".22"/>
      <stop offset="100%" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    ${g}
    <path d="${ddArea}" fill="var(--down)" opacity=".10"/>
    <path d="${area}" fill="url(#fillg)"/>
    <path d="${line}" fill="none" stroke="${col}" stroke-width="1.8"
      stroke-linejoin="round" stroke-linecap="round"/>
    <text x="${P.l}" y="${H-6}" fill="var(--faint)" font-size="11"
      font-family="var(--mono)">${dt(x0)}</text>
    <text x="${W-P.r}" y="${H-6}" fill="var(--faint)" font-size="11"
      font-family="var(--mono)" text-anchor="end">${dt(x1)}</text>
    <text x="${P.l}" y="${P.t+2}" fill="var(--faint)" font-size="10"
      font-family="var(--mono)">golgeli alan = zirveden dusus</text>
  </svg>`});
}

/* ---------- R dagilimi ---------- */
function histChart(bins){
  if(!bins.length) return el("div",{class:"empty"},"islem yok");
  const W=1000,H=200,P={t:12,r:8,b:26,l:8};
  const mx=Math.max(...bins.map(b=>b.n))||1;
  const bw=(W-P.l-P.r)/bins.length;
  let bars="",labels="";
  bins.forEach((b,i)=>{
    const h=(b.n/mx)*(H-P.t-P.b);
    const x=P.l+i*bw, y=H-P.b-h;
    const c=b.to<=0?"var(--down)":"var(--up)";
    bars+=`<rect x="${(x+1).toFixed(1)}" y="${y.toFixed(1)}" width="${(bw-2).toFixed(1)}"
      height="${Math.max(h,b.n?1.5:0).toFixed(1)}" fill="${c}" opacity="${b.n?.75:.15}" rx="2"/>`;
    if(b.n) bars+=`<text x="${(x+bw/2).toFixed(1)}" y="${(y-4).toFixed(1)}"
      fill="var(--faint)" font-size="10" text-anchor="middle" font-family="var(--mono)">${b.n}</text>`;
    if(i%2==0) labels+=`<text x="${(x+bw/2).toFixed(1)}" y="${H-9}" fill="var(--faint)"
      font-size="10" text-anchor="middle" font-family="var(--mono)">${b.from.toFixed(1)}</text>`;
  });
  const zi=bins.findIndex(b=>b.from>=0);
  const zx=P.l+(zi<0?bins.length:zi)*bw;
  return el("div",{html:`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:200px">
    ${bars}<line x1="${zx}" x2="${zx}" y1="${P.t}" y2="${H-P.b}"
      stroke="var(--dim)" stroke-width="1" stroke-dasharray="3 3"/>${labels}</svg>`});
}

/* ---------------------------- cizim ---------------------------- */
function render(d){
  const m=d.meta,a=d.account,s=d.stats,r=d.risk;

  const mode=document.getElementById("mode");
  mode.textContent=m.mode.toUpperCase();
  mode.className="pill "+(m.mode=="live"?"live":"shadow");
  const st=document.getElementById("status");
  st.textContent=d.status.text; st.className="pill "+d.status.kind;
  document.getElementById("stamp").textContent=
    dt(m.generated_at)+"  ·  "+m.timeframe+"  ·  "+m.symbols.length+" sembol"
    +"  ·  giris: "+m.entry_order_type;

  const K=(k,v,sub,c)=>el("div",{class:"card"+(k=="BAKIYE"?" big":"")},
    el("div",{class:"k"},k),el("div",{class:"v "+(c||"")},v),
    sub?el("div",{class:"s"},sub):"");
  const kpi=document.getElementById("kpi");kpi.textContent="";
  kpi.append(
    K("BAKIYE",F(a.equity)+" $",
      "zirveden -"+F(a.current_drawdown_pct,1)+"%"),
    K("BUGUN",S(a.day_pnl)+" $",S(a.day_pnl_pct,2)+"%  ·  limit -"+r.day_loss_limit_pct+"%",
      cls(a.day_pnl)),
    K("ISLEM",s.trades,r.trades_today+"/"+r.max_trades+" bugun"),
    K("ISABET",F(s.win_rate,1)+"%",
      s.breakeven_wr?"basabas "+F(s.breakeven_wr,1)+"%":"",
      s.breakeven_wr&&s.win_rate>s.breakeven_wr?"up":(s.trades?"down":"")),
    K("BEKLENTI",S(s.expectancy_r,3)+"R","islem basi",cls(s.expectancy_r)),
    K("t","t = "+F(s.t_stat,2),s.t_stat>2?"anlamli":"henuz zayif",
      s.t_stat>2?"up":"warnc"),
    K("PROFIT FACTOR",s.profit_factor?F(s.profit_factor,2):"-",
      "kazanc/kayip orani",s.profit_factor>1?"up":"down"),
    K("MAKS DUSUS","-"+F(a.peak_drawdown_pct,1)+"%","gorulen en kotu","warnc"),
    K("SERI",(s.streak>0?"+":"")+s.streak,
      s.streak>0?"kazanc":"kayip",cls(s.streak)),
    K("KOMISYON",F(s.fees)+" $","toplam odenen","dim"),
  );

  /* sermaye tabani */
  const fb=document.getElementById("floorBox");
  if(!d.floor){ fb.hidden=true; }
  else{
    fb.hidden=false;
    const fl=d.floor;
    const kullanilan=fl.cushion>0?Math.min(100,fl.open_risk/fl.risk_cap*100):100;
    const oran=a.equity>0?Math.max(0,Math.min(100,fl.cushion/a.equity*100)):0;
    document.getElementById("floor").replaceChildren(el("div",{html:`
      <div class="floorwrap">
        <div class="floorbar">
          <span class="lock" style="width:${(100-oran).toFixed(1)}%"></span>
          <span class="cush" style="width:${oran.toFixed(1)}%"></span>
        </div>
        <div class="floorlegend">
          <span><i class="sw lock"></i>korunan ${F(fl.floor)}$</span>
          <span><i class="sw cush"></i>riske atilabilir ${F(fl.cushion)}$</span>
          <span class="dim">toplam ${F(a.equity)}$</span>
        </div>
      </div>`}),
      table(["","deger","not"],[
        ["Taban",{html:F(fl.floor)+" $"},
         fl.ratchet_pct?`zirve ${F(fl.peak_equity)}$ x %${fl.ratchet_pct} (asla dusmez)`
                        :"sabit"],
        ["Yastik",{html:F(fl.cushion)+" $",cls:fl.exhausted?"down":"up"},
         fl.exhausted?`TUKENDI (esik ${F(fl.min_cushion)}$) - bot yeni islem ACMIYOR`
                     :`bakiyenin %${F(fl.cushion_pct,1)}'i`],
        ["Acik risk",{html:F(fl.open_risk)+" $"},
         `tavan ${F(fl.risk_cap)}$ - %${kullanilan.toFixed(0)} dolu`],
      ]));
  }

  document.getElementById("eqchart").replaceChildren(equityChart(d.equity));
  document.getElementById("hist").replaceChildren(histChart(d.histogram));

  /* acik pozisyonlar */
  const prows=d.positions.map(p=>[
    {html:`${p.symbol} <span class="side ${p.side}">${p.side}</span>`},
    P(p.entry),P(p.price),P(p.stop),P(p.tp2),
    {html:S(p.pnl)+" $",cls:cls(p.pnl)},
    {html:S(p.r,2)+"R",cls:cls(p.r)},
    p.bars+" bar"+(p.breakeven?" · BE":""),
  ]);
  d.pending.forEach(p=>prows.push([
    {html:`${p.symbol} <span class="side ${p.side}">${p.side}</span>`},
    {html:'<span class="dim">limit tahtada, dolmadi</span>'},"","","","","",""]));
  document.getElementById("pos").replaceChildren(
    table(["sembol","giris","fiyat","stop","hedef","pnl","R","sure"],prows));

  document.getElementById("syms").replaceChildren(table(
    ["sembol","islem","isabet","ort R","pnl"],
    d.symbols.map(x=>[x.symbol,x.n,F(x.win_rate,0)+"%",
      {html:S(x.avg_r,3),cls:cls(x.avg_r)},
      {html:S(x.pnl),cls:cls(x.pnl)}])));

  document.getElementById("trades").replaceChildren(table(
    ["sembol","yon","giris","cikis","pnl","R","sebep","zaman"],
    d.trades.map(t=>[t.symbol,
      {html:`<span class="side ${t.side}">${t.side}</span>`},
      P(t.entry_price),P(t.exit_price),
      {html:S(t.pnl),cls:cls(t.pnl)},
      {html:S(t.r,2),cls:cls(t.r)},
      t.exit||"-",dt(t.closed_at)])));

  const h=document.getElementById("health");h.textContent="";
  if(!d.health.length) h.append(el("div",{class:"empty"},"kontrol yok"));
  d.health.forEach(c=>h.append(el("div",{class:"chk"},
    el("span",{class:"dot "+c.severity}),
    el("span",{class:"n"},c.name),
    el("span",{class:"m"},c.message,
      c.action?el("div",{class:"a"},"-> "+c.action):""))));

  document.getElementById("learning").textContent=
    d.learning||"Henuz cikarilmis bir ders yok.";
}

let timer=null;
async function tick(){
  try{
    const r=await fetch("/api/state",{cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    const d=await r.json();
    render(d);
    clearTimeout(timer);
    timer=setTimeout(tick,(d.meta.refresh||20)*1000);
  }catch(e){
    document.getElementById("stamp").textContent="baglanti yok: "+e.message;
    clearTimeout(timer); timer=setTimeout(tick,5000);
  }
}
tick();
</script>
"""
