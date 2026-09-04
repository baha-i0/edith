"""Ogrenme katmani: hatadan ders cikarma, ama istatistikle.

## Neden bu kadar temkinli

Beklenti +0.11R, islem basi standart sapma ~1.3R. Yani 10 islemlik bir
serinin ortalamasinin standart hatasi 1.3/sqrt(10) = 0.41R -- gercek
beklentinin ~4 kati. Kisacasi kisa serilerde **hicbir sey ogrenilemez.**

Her zarardan sonra parametre oynatan bir bot, gurultuye uyum saglar ve
kendi edge'ini yok eder. Bu, calisan sistemleri bozmanin en guvenilir
yoludur. O yuzden ogrenme burada **asimetrik**:

  HIZLI ogrenilen (deterministik, tek ornek yeter):
    - emir reddi, min notional yetersizligi, yuvarlama hatasi,
      marj yetersizligi, koruma emri kurulamamasi
    - bunlar sans degil, kural. Ayni hatayi ikinci kez yapmanin bahanesi yok.

  YAVAS ogrenilen (istatistiksel, yuzlerce ornek gerek):
    - bir sembolun/rejimin beklentisinin negatif olmasi
    - stop mesafesinin sistematik olarak cok dar olmasi
    - bunlar ancak anlamlilik testini gectiginde uygulanir

  HIC ogrenilmeyen:
    - "son 3 islem zarardi, stratejiyi degistir" -- bu ogrenme degil, panik

## Ne uygulanir

1. Sembol/rejim saglik takibi  -> negatifligi KANITLANAN kova banklanir
2. Bayesci kucultme (shrinkage) -> canli beklenti, backtest onseline cekilir
3. Dususe gore risk olcegi      -> zararda kucul, toparlaninca geri buyu
4. Stop kalibrasyonu            -> "stop avlanmasi" olcuup stop genisletilir
5. Hata defteri                 -> operasyonel hatalar tekrarlanmaz
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DAY_MS = 86_400_000


# ===========================================================================
# Istatistik cekirdegi
# ===========================================================================
def shrunk_mean(n: int, observed_mean: float, prior_mean: float,
                prior_strength: float) -> float:
    """Bayesci kucultme: az veri varken onsele (backtest beklentisi) yakin kal.

    prior_strength, onselin "kac islemlik guven" degeri. 40 ise, 40 canli
    islem toplanana kadar tahmin agirlikli olarak backtest'e dayanir.
    """
    if n <= 0:
        return prior_mean
    return (n * observed_mean + prior_strength * prior_mean) / (n + prior_strength)


def mean_and_sd(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var)


def upper_confidence_bound(values: List[float], z: float, fallback_sd: float) -> float:
    """Ortalamanin ust guven siniri.

    Bu sinir sifirin ALTINDA ise, "beklenti negatif" iddiasi tek yonlu
    testi gecmis demektir. Sadece ortalamanin negatif olmasi yetmez --
    kucuk orneklerde ortalama surekli isaret degistirir.
    """
    n = len(values)
    if n < 2:
        return float("inf")
    mean, sd = mean_and_sd(values)
    sd = sd or fallback_sd
    return mean + z * sd / math.sqrt(n)


# ===========================================================================
# Kova (bucket) istatistikleri
# ===========================================================================
@dataclass
class BucketStats:
    key: str
    r_values: List[float] = field(default_factory=list)
    benched_until_ms: int = 0
    bench_count: int = 0
    stop_hunts: int = 0          # stop yedi, sonra hedefe gitti
    stop_widen_mult: float = 1.0  # stop_atr_mult icin ogrenilmis carpan

    @property
    def n(self) -> int:
        return len(self.r_values)

    @property
    def mean_r(self) -> float:
        return sum(self.r_values) / self.n if self.n else 0.0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "r_values": self.r_values[-500:],  # sinirli hafiza
            "benched_until_ms": self.benched_until_ms,
            "bench_count": self.bench_count,
            "stop_hunts": self.stop_hunts,
            "stop_widen_mult": self.stop_widen_mult,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BucketStats":
        return cls(
            key=d["key"], r_values=list(d.get("r_values", [])),
            benched_until_ms=int(d.get("benched_until_ms", 0)),
            bench_count=int(d.get("bench_count", 0)),
            stop_hunts=int(d.get("stop_hunts", 0)),
            stop_widen_mult=float(d.get("stop_widen_mult", 1.0)),
        )


# ===========================================================================
# Hata defteri -- operasyonel, deterministik ogrenme
# ===========================================================================
@dataclass
class MistakeLedger:
    """Ayni operasyonel hatayi ikinci kez yapmayi engeller.

    Buradaki hatalar istatistik gerektirmez: min notional yetmiyorsa
    yetmiyordur, bir daha denemek zaman kaybi ve log kirliligi.
    """

    counts: Dict[str, int] = field(default_factory=dict)
    last_seen_ms: Dict[str, int] = field(default_factory=dict)
    blocked: Dict[str, int] = field(default_factory=dict)  # imza -> bitis zamani

    def prune(self, now_ms: int) -> None:
        """Suresi dolan blokeleri ve ILGILI SAYACLARI temizler.

        Sayaci temizlemek sart: aksi halde cezasini cekmis bir sembol,
        yasak bittikten sonraki ILK hatada aninda tekrar bloke olur ve
        gecici bir sorun kalici yasaga donusur.
        """
        for sig, until in list(self.blocked.items()):
            if now_ms < until:
                continue
            del self.blocked[sig]
            self.counts.pop(sig, None)
            # sembol seviyesindeki bloke kalkiyorsa o sembolun tum hata
            # sayaclarina temiz sayfa ver
            if sig.startswith("symbol:"):
                symbol = sig.split(":", 1)[1]
                for k in [k for k in self.counts if k.endswith(f":{symbol}")]:
                    del self.counts[k]

    def record(self, signature: str, now_ms: int, threshold: int,
               block_ms: int) -> bool:
        """Hatayi kaydeder. Esik asildiysa imzayi bloke eder ve True doner."""
        self.prune(now_ms)
        self.counts[signature] = self.counts.get(signature, 0) + 1
        self.last_seen_ms[signature] = now_ms
        if self.counts[signature] >= threshold:
            self.blocked[signature] = now_ms + block_ms
            log.warning("Tekrarlayan hata blokelendi: %s (%dx)",
                        signature, self.counts[signature])
            return True
        return False

    def is_blocked(self, signature: str, now_ms: int) -> bool:
        self.prune(now_ms)
        return signature in self.blocked

    def to_dict(self) -> dict:
        return {"counts": self.counts, "last_seen_ms": self.last_seen_ms,
                "blocked": self.blocked}

    @classmethod
    def from_dict(cls, d: dict) -> "MistakeLedger":
        return cls(counts=dict(d.get("counts", {})),
                   last_seen_ms=dict(d.get("last_seen_ms", {})),
                   blocked={k: int(v) for k, v in d.get("blocked", {}).items()})


# ===========================================================================
# Ogrenici
# ===========================================================================
class Learner:
    """Islem sonuclarindan ders cikaran, ama acele etmeyen katman."""

    def __init__(self, cfg, store=None):
        self.cfg = cfg
        self.lc = cfg.learning
        self.store = store
        self.buckets: Dict[str, BucketStats] = {}
        self.mistakes = MistakeLedger()
        self.peak_equity: float = 0.0
        self.total_r: List[float] = []
        if store is not None:
            self.load()

    # --------------------------------------------------------------- kalici
    def load(self) -> None:
        data = self.store.get_kv(f"learning:{self.store.mode}") if self.store else None
        if not data:
            return
        self.buckets = {k: BucketStats.from_dict(v)
                        for k, v in data.get("buckets", {}).items()}
        self.mistakes = MistakeLedger.from_dict(data.get("mistakes", {}))
        self.peak_equity = float(data.get("peak_equity", 0.0))
        self.total_r = list(data.get("total_r", []))[-1000:]

    def save(self) -> None:
        if not self.store:
            return
        self.store.set_kv(f"learning:{self.store.mode}", {
            "buckets": {k: b.to_dict() for k, b in self.buckets.items()},
            "mistakes": self.mistakes.to_dict(),
            "peak_equity": self.peak_equity,
            "total_r": self.total_r[-1000:],
        })

    # ------------------------------------------------------------- kovalar
    @staticmethod
    def regime_bucket(context: Dict[str, float]) -> str:
        """Islemi kaba bir rejim kovasina yerlestirir.

        Kova sayisi kasten az: her ek boyut ornek basina veriyi bolerek
        istatistigi kullanilamaz hale getirir. 3 ADX x 2 volatilite = 6 kova.
        """
        adx = context.get("adx", 0.0)
        atr_pct = context.get("atr_pct", 0.0)
        adx_band = "adx_dusuk" if adx < 25 else ("adx_orta" if adx < 35 else "adx_yuksek")
        vol_band = "vol_dusuk" if atr_pct < 1.2 else "vol_yuksek"
        return f"{adx_band}|{vol_band}"

    def _bucket(self, key: str) -> BucketStats:
        if key not in self.buckets:
            self.buckets[key] = BucketStats(key=key)
        return self.buckets[key]

    # ------------------------------------------------------ giris oncesi kapi
    def allow_entry(self, symbol: str, context: Dict[str, float],
                    now_ms: int) -> Tuple[bool, str]:
        """Bu sembol/rejim su an islem yapmaya uygun mu?"""
        if not self.lc.enabled:
            return True, "ogrenme kapali"

        if self.mistakes.is_blocked(f"symbol:{symbol}", now_ms):
            return False, f"{symbol} operasyonel hata nedeniyle gecici olarak devre disi"

        for key in (f"sym:{symbol}", f"reg:{self.regime_bucket(context)}"):
            b = self.buckets.get(key)
            if b and b.benched_until_ms > now_ms:
                kalan = int((b.benched_until_ms - now_ms) / DAY_MS) + 1
                return False, f"{key} banklandi (kanitlanmis negatif beklenti, {kalan} gun kaldi)"
        return True, "ok"

    def risk_multiplier(self, symbol: str, context: Dict[str, float],
                        equity: float) -> Tuple[float, str]:
        """Risk butcesi carpani. 1.0 = normal, 0.4 = en kucuk.

        Iki bagimsiz kaynak:
          - dusus (drawdown): mekanik, dusuk overfit riski, hemen uygulanir
          - kanitlanmis edge: Bayesci kucultme, yavas hareket eder
        """
        if not self.lc.enabled:
            return 1.0, ""
        lc = self.lc
        reasons = []
        mult = 1.0

        if lc.drawdown_scaling and self.peak_equity > 0 and equity < self.peak_equity:
            dd = 100.0 * (self.peak_equity - equity) / self.peak_equity
            if dd > 1.0:
                cut = min(1.0, dd / max(lc.drawdown_full_cut_pct, 1e-9))
                mult *= (1.0 - cut * (1.0 - lc.min_risk_multiplier))
                reasons.append(f"dusus %{dd:.1f}")

        key = f"sym:{symbol}"
        b = self.buckets.get(key)
        if b and b.n >= lc.min_trades_for_sizing:
            est = shrunk_mean(b.n, b.mean_r, lc.prior_expectancy_r, lc.prior_strength)
            if lc.prior_expectancy_r > 0:
                ratio = est / lc.prior_expectancy_r
                edge_mult = max(lc.min_risk_multiplier, min(lc.max_risk_multiplier, ratio))
                mult *= edge_mult
                reasons.append(f"{symbol} kanitlanmis edge {est:+.3f}R (n={b.n})")

        # Probation: bank suresi bitmis ama gecmisi kotu olan kova kucuk baslar
        if b and b.bench_count > 0 and b.benched_until_ms <= 0:
            mult *= lc.probation_multiplier
            reasons.append("gozetim altinda")

        mult = max(lc.min_risk_multiplier, min(lc.max_risk_multiplier, mult))
        return mult, "; ".join(reasons)

    def stop_multiplier(self, symbol: str) -> float:
        """Ogrenilmis stop genisletme carpani (stop avlanmasi olcumune dayali)."""
        if not self.lc.enabled or not self.lc.stop_calibration:
            return 1.0
        b = self.buckets.get(f"sym:{symbol}")
        return b.stop_widen_mult if b else 1.0

    # -------------------------------------------------------- islem sonrasi
    def record_trade(self, trade, now_ms: Optional[int] = None) -> List[str]:
        """Kapanan islemi isler ve ogrenilen dersleri metin olarak doner."""
        if not self.lc.enabled:
            return []
        now = now_ms or trade.closed_at
        lessons: List[str] = []
        self.total_r.append(trade.r_multiple)

        keys = [f"sym:{trade.symbol}", f"reg:{self.regime_bucket(trade.context or {})}"]
        for key in keys:
            b = self._bucket(key)
            b.r_values.append(trade.r_multiple)
            lessons.extend(self._maybe_bench(b, now))

        return lessons

    def note_stop_hunt(self, symbol: str, now_ms: int) -> List[str]:
        """'Stop avlandi' gozlemi.

        Stop yedikten sonra fiyat, N mum icinde orijinal hedefe ulastiysa
        yon dogruydu ama stop cok dardi. Tek ornek hicbir sey ifade etmez;
        oran anlamli hale gelince stop genisletilir. Bu olcum islem
        KAPANDIKTAN sonra yapilir, karar aninda degil -- yani lookahead degil,
        geriye donuk performans analizidir.
        """
        if not self.lc.enabled or not self.lc.stop_calibration:
            return []
        lc = self.lc
        b = self._bucket(f"sym:{symbol}")
        b.stop_hunts += 1
        stops = sum(1 for r in b.r_values if r < 0)
        if stops < lc.min_trades_per_bucket:
            return []
        hunt_rate = b.stop_hunts / max(stops, 1)
        if hunt_rate > lc.stop_hunt_rate_threshold and b.stop_widen_mult < lc.stop_widen_max:
            b.stop_widen_mult = min(lc.stop_widen_max,
                                    b.stop_widen_mult + lc.stop_widen_step)
            b.stop_hunts = 0  # olcumu sifirla, yeni stop genisligiyle tekrar ol
            msg = (f"{symbol}: zararli cikislarin %{hunt_rate*100:.0f}'i stop "
                   f"avlanmasi -> stop carpani {b.stop_widen_mult:.2f}x")
            log.warning(msg)
            return [msg]
        return []

    def _maybe_bench(self, b: BucketStats, now_ms: int) -> List[str]:
        """Kovayi SADECE negatifligi istatistiksel olarak kanitlandiysa bankla.

        Iki koruma var, ikisi de olculerek secildi (200 islemlik simulasyon,
        3000 tekrar, gercek beklenti +0.11R olan bir sembol uzerinde):

          z=1.64, her islemde test  -> yanlis bank %6.1
          z=2.33, her 10 islemde    -> yanlis bank %0.7   <-- secilen
          z=3.09, her 10 islemde    -> yanlis bank %0.1 ama guc %98'den %66'ya duser

        Her islemden sonra test etmek nominal %5 hatayi cok asar (optional
        stopping / coklu karsilastirma). Testi seyrekleştirmek ve esigi
        yukseltmek, iyi bir sembolu 14 gun bosuna banklama riskini 9 kat
        azaltirken gercekten bozuk bir sembolu (-0.6R) hala %100 yakaliyor.
        """
        lc = self.lc
        if b.n < lc.min_trades_per_bucket or b.benched_until_ms > now_ms:
            return []
        if lc.bench_eval_every > 1 and \
                (b.n - lc.min_trades_per_bucket) % lc.bench_eval_every:
            return []
        ub = upper_confidence_bound(b.r_values, lc.significance_z, lc.trade_r_sd)
        if ub >= 0:
            return []   # negatif oldugu KANITLANMADI -> dokunma
        b.benched_until_ms = now_ms + lc.bench_days * DAY_MS
        b.bench_count += 1
        b.r_values = b.r_values[-lc.min_trades_per_bucket:]  # yeni donem icin taze baslangic
        msg = (f"{b.key} banklandi: n={b.n}, ortalama {b.mean_r:+.3f}R, "
               f"ust guven siniri {ub:+.3f} < 0 ({lc.bench_days} gun)")
        log.warning(msg)
        return [msg]

    def record_equity(self, equity: float) -> None:
        self.peak_equity = max(self.peak_equity, equity)

    # ----------------------------------------------------- operasyonel hata
    def record_mistake(self, symbol: str, kind: str, detail: str,
                       now_ms: int) -> Optional[str]:
        """Operasyonel hatayi kaydeder; tekrarlanirsa sembolu devre disi birakir."""
        if not self.lc.enabled:
            return None
        sig = f"{kind}:{symbol}"
        blocked = self.mistakes.record(sig, now_ms, self.lc.mistake_repeat_threshold,
                                       self.lc.mistake_block_days * DAY_MS)
        if blocked:
            self.mistakes.blocked[f"symbol:{symbol}"] = \
                now_ms + self.lc.mistake_block_days * DAY_MS
            return (f"{symbol}: '{kind}' hatasi "
                    f"{self.lc.mistake_repeat_threshold} kez tekrarlandi ({detail}). "
                    f"{self.lc.mistake_block_days} gun devre disi.")
        return None

    # ------------------------------------------------------------- rapor
    def report(self, now_ms: Optional[int] = None) -> str:
        now = now_ms or int(time.time() * 1000)
        lc = self.lc
        lines = [f"Ogrenme: {'AKTIF' if lc.enabled else 'KAPALI'}",
                 f"Toplam islem: {len(self.total_r)} | zirve equity: {self.peak_equity:.2f}"]
        if self.total_r:
            mean, sd = mean_and_sd(self.total_r)
            n = len(self.total_r)
            se = (sd or lc.trade_r_sd) / math.sqrt(max(n, 1))
            lines.append(f"Canli beklenti: {mean:+.3f}R  (+/- {1.96*se:.3f} 95% guven)")
            lines.append(f"Kucultulmus tahmin: "
                         f"{shrunk_mean(n, mean, lc.prior_expectancy_r, lc.prior_strength):+.3f}R "
                         f"(onsel {lc.prior_expectancy_r:+.3f}R)")
            if n < lc.min_trades_per_bucket:
                lines.append(f"  UYARI: {lc.min_trades_per_bucket} islemin altinda "
                             f"hicbir sonuc cikarilmaz. Bu sayilar henuz gurultu.")

        if self.buckets:
            lines.append("\nKovalar:")
            lines.append(f"  {'kova':<28}{'n':>5}{'ort R':>9}{'ust sinir':>11}  durum")
            for key, b in sorted(self.buckets.items(), key=lambda kv: kv[1].mean_r):
                ub = upper_confidence_bound(b.r_values, lc.significance_z, lc.trade_r_sd)
                if b.benched_until_ms > now:
                    durum = f"BANKLI ({int((b.benched_until_ms-now)/DAY_MS)+1}g)"
                elif b.n < lc.min_trades_per_bucket:
                    durum = f"veri yetersiz ({lc.min_trades_per_bucket - b.n} eksik)"
                elif b.bench_count:
                    durum = "gozetimde"
                else:
                    durum = "aktif"
                ubs = "inf" if ub == float("inf") else f"{ub:+.3f}"
                lines.append(f"  {key:<28}{b.n:>5}{b.mean_r:>+9.3f}{ubs:>11}  {durum}")

        widened = {k: b.stop_widen_mult for k, b in self.buckets.items()
                   if b.stop_widen_mult != 1.0}
        if widened:
            lines.append("\nOgrenilmis stop carpanlari:")
            for k, v in widened.items():
                lines.append(f"  {k}: {v:.2f}x")

        active_blocks = {s: t for s, t in self.mistakes.blocked.items() if t > now}
        if active_blocks:
            lines.append("\nOperasyonel hata blokeleri:")
            for sig, until in active_blocks.items():
                lines.append(f"  {sig} -> {int((until-now)/DAY_MS)+1} gun")
        if self.mistakes.counts:
            lines.append("\nHata sayaclari: " + json.dumps(self.mistakes.counts,
                                                           ensure_ascii=True))
        return "\n".join(lines)
