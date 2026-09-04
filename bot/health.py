"""Saglik kontrolu ve kendini denetleme.

## Neden var

Bu botu calistiran kisinin istatistik bilmesi gerekmiyor. Ama birinin
su soruyu cevaplamasi gerekiyor: **"su an her sey yolunda mi?"**

Modul iki isi yapiyor:

  1. Operasyonel saglik  -- bot calisiyor mu, veri geliyor mu, pozisyonlar
     tutarli mi, tekrarlayan hata var mi
  2. Istatistiksel saglik -- canli performans backtest'e benziyor mu

Ikincisi kritik. Bir stratejinin edge'i zamanla yok olabilir (piyasa
degisir, baskalari ayni seyi yapar, rejim doner). O zaman dogru davranis
**parametreleri yeniden ayarlamak degil, durmak ve insana haber vermektir.**

Kendini yeniden optimize eden bir sistem, bozuldugunu asla soylemez --
cunku her seferinde gecmise uyan yeni bir parametre bulur. Bu yuzden
buradaki kural tek yonlu: kanit kotuyse DUR, iyi diye kendini buyutme.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Config
from .learning import mean_and_sd

INFO, WARN, CRITICAL = "bilgi", "dikkat", "mudahale"
DAY_MS = 86_400_000


@dataclass
class Check:
    name: str
    severity: str
    message: str
    action: str = ""

    @property
    def ok(self) -> bool:
        return self.severity == INFO


@dataclass
class HealthReport:
    checks: List[Check] = field(default_factory=list)
    halt_required: bool = False
    halt_reason: str = ""

    @property
    def worst(self) -> str:
        for level in (CRITICAL, WARN):
            if any(c.severity == level for c in self.checks):
                return level
        return INFO

    def verdict(self) -> str:
        return {
            INFO: "HER SEY YOLUNDA - yapman gereken bir sey yok",
            WARN: "DIKKAT - izlemeye deger bir sey var, acil degil",
            CRITICAL: "MUDAHALE GEREKIYOR - asagidaki adimi at",
        }[self.worst]

    def render(self) -> str:
        icon = {INFO: "  ok  ", WARN: " !!!  ", CRITICAL: " >>>  "}
        lines = ["=" * 64, self.verdict(), "=" * 64, ""]
        for c in sorted(self.checks, key=lambda x: (x.severity != CRITICAL,
                                                    x.severity != WARN)):
            lines.append(f"{icon[c.severity]}{c.name}")
            lines.append(f"        {c.message}")
            if c.action:
                lines.append(f"        YAP: {c.action}")
            lines.append("")
        return "\n".join(lines)


def run_health_checks(cfg: Config, store, learner=None, broker=None,
                      now_ms: Optional[int] = None) -> HealthReport:
    now = now_ms or int(time.time() * 1000)
    rep = HealthReport()
    rep.checks.append(_check_alive(cfg, store, now))
    rep.checks.append(_check_daily_state(cfg, store))
    rep.checks.extend(_check_performance(cfg, store))
    rep.checks.append(_check_drawdown(cfg, store, learner))
    rep.checks.append(_check_fee_drag(cfg, store))
    rep.checks.append(_check_positions(cfg, store, broker, now))
    cek = _check_cushion(cfg, store, broker)
    if cek is not None:
        rep.checks.append(cek)
    if learner is not None:
        rep.checks.append(_check_operational_errors(learner, now))

    edge = _check_edge_alive(cfg, store)
    rep.checks.append(edge)
    if edge.severity == CRITICAL:
        rep.halt_required = True
        rep.halt_reason = edge.message
    return rep


# --------------------------------------------------------------------- checks
def _check_cushion(cfg: Config, store, broker) -> Optional["Check"]:
    """Sermaye tabani yastigi. Taban kapaliysa kontrol yok.

    Bu kontrol olmadan bot yastik tukendiginde SESSIZCE durur: hata yok,
    uyari yok, sadece hicbir islem acilmaz. Kullanici gunlerce "neden
    calismiyor" diye bakar. Sessiz durma, gorunur durma olmali.
    """
    from .risk import effective_floor

    rs = store.load_risk_state()
    floor = effective_floor(cfg.risk, rs)
    if floor <= 0:
        return None
    try:
        equity = broker.equity() if broker is not None else 0.0
    except Exception:
        return None
    if equity <= 0:
        return None

    yastik = max(0.0, equity - floor)
    esik = cfg.risk.min_cushion_usdt
    if yastik < esik:
        # Iki farkli durum, iki farkli tavsiye. Karistirmak kullaniciyi
        # yanlis yone gonderir: "sermayen korundu" demek, aslinda parasini
        # cekmis birine sistemin bozuldugunu dusundurur.
        if equity < floor:
            return Check(
                "Sermaye tabani", CRITICAL,
                f"Bakiye ({equity:.2f}) tabanin ({floor:.2f}) ALTINDA. "
                "Bot yeni islem ACMIYOR.",
                "Bu genellikle zarardan degil, hesaptan para cekmekten ya da "
                "ayar degisikliginden olur. Bot para yatirma/cekme islemlerini "
                "kendi algilar ve tabani kaydirir; algilamadiysa 'python -m bot "
                "floor --reset' ile tabani su anki bakiyeye gore yeniden kur.",
            )
        return Check(
            "Sermaye tabani", CRITICAL,
            f"Yastik tukendi: bakiye {equity:.2f}, taban {floor:.2f} -> "
            f"riske atilabilir {yastik:.2f} (esik {esik:.2f}). "
            "Bot yeni islem ACMIYOR.",
            "Taban isini yapti: zarar tabanda durduruldu, sermayenin geri "
            "kalani korundu. Devam etmek istersen hesaba para ekle. Once "
            "NEDEN buraya gelindigine bak -- strateji bozulmus olabilir.",
        )
    if yastik < esik * 2:
        return Check(
            "Sermaye tabani", WARN,
            f"Yastik azaliyor: {yastik:.2f} (durma esigi {esik:.2f}). "
            f"Bakiye {equity:.2f}, taban {floor:.2f}.",
            "Pozisyonlar kuculerek devam ediyor. Mudahale gerekmiyor ama "
            "yastik biterse bot duracak.",
        )
    return Check("Sermaye tabani", INFO,
                 f"Taban {floor:.2f} korunuyor, yastik {yastik:.2f} "
                 f"(bakiyenin %{yastik/equity*100:.0f}'i).")


def _check_alive(cfg: Config, store, now: int) -> Check:
    last = store.last_equity()
    if not last:
        return Check("Bot calisiyor mu", INFO,
                     "Henuz hic kayit yok. Bot ilk kez baslatilacaksa normal.")
    age_min = (now - last[0]) / 60_000
    limit_min = max(10.0, cfg.loop_seconds * 10 / 60)
    if age_min > limit_min * 6:
        return Check("Bot calisiyor mu", CRITICAL,
                     f"Son kayit {age_min:.0f} dakika once. Bot durmus gorunuyor.",
                     "Botu yeniden baslat: python -m bot paper (veya live)")
    if age_min > limit_min:
        return Check("Bot calisiyor mu", WARN,
                     f"Son kayit {age_min:.0f} dakika once. Beklenenden eski.",
                     "Loglara bak: tail -50 logs/bot.log")
    return Check("Bot calisiyor mu", INFO,
                 f"Calisiyor. Son kayit {age_min:.0f} dakika once, "
                 f"equity {last[1]:.2f}")


def _check_daily_state(cfg: Config, store) -> Check:
    rs = store.load_risk_state()
    if rs.halted:
        return Check("Gunluk durum", INFO,
                     f"Bot bugun kendini durdurdu: {rs.halt_reason}. "
                     f"Bugunku sonuc: {rs.realized_pnl_today:+.2f}",
                     "Bir sey yapma. Yarin (UTC) kendiliginden acilir.")
    return Check("Gunluk durum", INFO,
                 f"Aktif. Bugun {rs.trades_today} islem, "
                 f"sonuc {rs.realized_pnl_today:+.2f}")


def _check_performance(cfg: Config, store) -> List[Check]:
    trades = store.all_trades()
    n = len(trades)
    prior = cfg.learning.prior_expectancy_r
    if n == 0:
        return [Check("Performans", INFO, "Henuz kapanmis islem yok.")]

    rs = [t["r_multiple"] for t in trades]
    mean, sd = mean_and_sd(rs)
    se = (sd or cfg.learning.trade_r_sd) / math.sqrt(n)
    net = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)

    out = [Check("Performans", INFO,
                 f"{n} islem | isabet %{100*wins/n:.0f} | net {net:+.2f} | "
                 f"islem basi {mean:+.3f}R (backtest beklentisi {prior:+.3f}R)")]

    if n < 30:
        out.append(Check("Ornek boyu", INFO,
                         f"{n} islem. Sonuc cikarmak icin en az 30 gerekiyor -- "
                         f"bu sayilar simdilik gurultu.",
                         "Sabret. Az islemle karar vermek en pahali hata."))
    else:
        out.append(Check("Ornek boyu", INFO,
                         f"{n} islem. Guven araligi: {mean-1.96*se:+.3f}R ile "
                         f"{mean+1.96*se:+.3f}R arasi."))
    return out


def _check_edge_alive(cfg: Config, store) -> Check:
    """Canli performans, stratejinin OLDUGUNU soyleyecek kadar kotu mu?

    Esik kasten yuksek. Amac kotu bir aya tepki vermek degil, edge'in
    gercekten yok oldugunu yakalamak. Kanit varsa bot durur ve seni cagirir --
    kendini yeniden ayarlamaz.
    """
    # KAYAN PENCERE, tum gecmis degil. Iki sebep:
    #
    # 1) Istatistiksel: iki yil once olmus bir edge, bugunku sorunun kaniti
    #    degil. Tum gecmisi kullanmak eski verinin bugunku karari yutmasi
    #    demek -- her iki yonde de.
    # 2) Golge modu KILITLENIYORDU: golgede kanit uretip canliya donuldugunde
    #    bu kontrol hala AYNI omurluk ortalamayi goruyor ve bir saat icinde
    #    tekrar golgeye atiyordu. ShadowTracker.enter() ise golge gecmisini
    #    sifirliyor -- yani 40 sanal islem bosa gidiyor ve dongu sonsuza
    #    kadar tekrarliyordu. Bot "kendini toparliyorum" derken hicbir zaman
    #    islem yapmiyordu.
    min_n = cfg.health.min_trades_for_edge_check
    trades = store.recent_trades(limit=max(min_n * 4, 200))
    n = len(trades)
    if n < min_n:
        return Check("Strateji hala calisiyor mu", INFO,
                     f"Karar icin {min_n} islem gerekiyor, su an {n} var. "
                     f"Bu kontrol henuz aktif degil.")

    rs = [t["r_multiple"] for t in trades]
    mean, sd = mean_and_sd(rs)
    sd = sd or cfg.learning.trade_r_sd
    ucb = mean + cfg.health.edge_z * sd / math.sqrt(n)

    if ucb < 0:
        return Check("Strateji hala calisiyor mu", CRITICAL,
                     f"{n} islemde ortalama {mean:+.3f}R ve ust guven siniri "
                     f"{ucb:+.3f} < 0. Beklentinin negatif oldugu istatistiksel "
                     f"olarak KANITLANDI. Bu kotu bir seri degil, bozulmus bir sistem.",
                     "Bot yeni pozisyon acmayi durdurdu. Once paper moda gec, "
                     "sonra backtest'i guncel veriyle tekrar calistir: "
                     "python -m bot backtest --portfolio")
    if mean < 0:
        return Check("Strateji hala calisiyor mu", WARN,
                     f"{n} islemde ortalama {mean:+.3f}R (negatif) ama ust guven "
                     f"siniri {ucb:+.3f} > 0 -- yani bu HENUZ kanit degil, "
                     f"kotu bir seri de olabilir.",
                     "Bir sey yapma, izle. Karar icin daha cok islem gerekiyor.")
    return Check("Strateji hala calisiyor mu", INFO,
                 f"{n} islemde ortalama {mean:+.3f}R. Backtest beklentisiyle "
                 f"tutarli.")


def _check_drawdown(cfg: Config, store, learner) -> Check:
    series = store.equity_series(limit=2)
    if len(series) < 2:
        return Check("Dusus", INFO, "Yeterli equity gecmisi yok.")
    # Zirve TUM gecmisten okunur. equity_series(limit=N) son N satiri doner;
    # dakikada bir kayitla varsayilan 5000 satir sadece ~3.5 gundur. O
    # pencereyle bakinca aylara yayilan bir erime hep "%1 dusus" gorunur ve
    # %15/%22 esikleri hicbir zaman tetiklenmez -- yani koruma korlesir.
    peak = store.peak_equity()
    cur = series[-1][1]
    dd = 100.0 * (peak - cur) / peak if peak > 0 else 0.0
    hc = cfg.health
    if dd >= hc.drawdown_critical_pct:
        return Check("Dusus", CRITICAL,
                     f"Zirveden %{dd:.1f} asagidasin ({peak:.2f} -> {cur:.2f}). "
                     f"Backtest'te gorulen en kotu dusus %21 idi.",
                     "Bunu bekliyorduk ama sinirdayiz. Riski yariya indirmeyi "
                     "dusun: config.yaml -> risk_per_trade_pct")
    if dd >= hc.drawdown_warn_pct:
        return Check("Dusus", WARN,
                     f"Zirveden %{dd:.1f} asagidasin. Bu NORMAL -- backtest'te "
                     f"%21'e kadar dususler vardi.",
                     "Bir sey yapma. Dususte botu kapatmak, kaybi kalici hale "
                     "getirmenin en yaygin yolu.")
    return Check("Dusus", INFO,
                 f"Zirveden %{dd:.1f} asagida. Zirve {peak:.2f}, su an {cur:.2f}")


def _check_fee_drag(cfg: Config, store) -> Check:
    trades = store.all_trades()
    if len(trades) < 10:
        return Check("Komisyon yuku", INFO, "Olcum icin yeterli islem yok.")
    fees = sum(t["fees"] for t in trades)
    gross = sum(t["pnl"] for t in trades) + fees
    if gross <= 0:
        return Check("Komisyon yuku", WARN,
                     f"Komisyon oncesi bile zarardasın. Odenen komisyon: {fees:.2f}",
                     "Strateji kontrolune bak (asagida).")
    ratio = 100.0 * fees / gross
    if ratio > cfg.health.fee_drag_warn_pct:
        return Check("Komisyon yuku", WARN,
                     f"Brut karin %{ratio:.0f}'i komisyona gidiyor ({fees:.2f}). "
                     f"Cok sik islem yapiliyor olabilir.",
                     "Zaman dilimini buyut (4h -> 6h/12h) ya da min_adx'i yukselt.")
    return Check("Komisyon yuku", INFO,
                 f"Brut karin %{ratio:.0f}'i komisyon ({fees:.2f}). Makul.")


def _check_positions(cfg: Config, store, broker, now: int) -> Check:
    positions = broker.positions() if broker else store.load_positions()
    if not positions:
        return Check("Acik pozisyonlar", INFO, "Acik pozisyon yok.")
    limit = cfg.risk.max_concurrent_positions
    stale = []
    bar_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
              "4h": 14_400_000}.get(cfg.timeframe, 14_400_000)
    for sym, p in positions.items():
        bars = (now - p.opened_at) / bar_ms
        if bars > cfg.strategy.max_bars_in_trade * 1.5:
            stale.append(f"{sym} ({bars:.0f} mumdur acik)")
    if len(positions) > limit:
        return Check("Acik pozisyonlar", CRITICAL,
                     f"{len(positions)} pozisyon acik ama limit {limit}. "
                     f"Yerel kayit ile borsa uyusmuyor olabilir.",
                     "Binance'te pozisyonlari kontrol et, gerekirse botu "
                     "durdurup elle kapat.")
    if stale:
        return Check("Acik pozisyonlar", WARN,
                     f"Cok uzun suredir acik: {', '.join(stale)}",
                     "Zaman stopu calismamis olabilir; loglara bak.")
    return Check("Acik pozisyonlar", INFO,
                 f"{len(positions)}/{limit} pozisyon acik: {', '.join(positions)}")


def _check_operational_errors(learner, now: int) -> Check:
    learner.mistakes.prune(now)
    active = {s: t for s, t in learner.mistakes.blocked.items() if t > now}
    if not active:
        return Check("Operasyonel hatalar", INFO, "Tekrarlayan hata yok.")
    items = [f"{s} ({int((t-now)/DAY_MS)+1}g)" for s, t in active.items()]
    return Check("Operasyonel hatalar", WARN,
                 f"Su anda devre disi: {', '.join(items)}",
                 "Genelde bakiye kucuklugu ya da sembol kurali. "
                 "python -m bot learn ile detayina bak.")
