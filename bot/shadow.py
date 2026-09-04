"""Golge modu: para riske atmadan islem yapmaya devam etmek.

## Problem

Bir stratejinin edge'i olebilir. O an ne yapmali?

  a) Dur ve insani bekle       -> otonom degil; insan uyuyorsa sistem oludur
  b) Parametreleri yeniden ayarla -> bozulmayi gizlemenin en kolay yolu;
                                     olculdu, beklentiyi yariya dusuruyor
  c) GOLGE MODU                -> islem yapmaya devam et, ama kagit uzerinde

(c) tek otonom ve guvenli cevap. Bot sinyal uretmeye, pozisyon acmaya,
yonetmeye ve kapatmaya devam eder -- sadece borsaya emir gitmez. Sonuclar
gercekmis gibi olculur.

Kanit geri gelirse (golge performansi istatistiksel olarak POZITIF olursa)
bot kendiliginden canliya doner. Gelmezse sonsuza kadar golgede kalir ve
sermaye korunur. Insan mudahalesi gerekmez.

## Neden bu, "dur ve bekle"den iyi

Durmak bilgi uretmez. Golge modu bilgi uretmeye devam eder: strateji
gercekten mi bozuldu, yoksa gecici bir rejim mi? Durmus bir bot bunu asla
ogrenemez, cunku olcecek veri toplamaz.

## Neden bu, "yeniden optimize et"ten iyi

Golge modu parametreleri DEGISTIRMEZ. Ayni strateji, ayni ayarlar, sadece
para yok. Yani geri donus karari gercek bir sinavdir, gecmise uydurulmus
bir parametre degil.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .config import Config
from .learning import mean_and_sd
from .models import LONG, Candle, Position, Trade

log = logging.getLogger(__name__)


@dataclass
class ShadowState:
    r_values: List[float] = field(default_factory=list)
    positions: Dict[str, dict] = field(default_factory=dict)
    entered_at_ms: int = 0
    resumed_count: int = 0

    def to_dict(self) -> dict:
        return {"r_values": self.r_values[-500:], "positions": self.positions,
                "entered_at_ms": self.entered_at_ms, "resumed_count": self.resumed_count}

    @classmethod
    def from_dict(cls, d: dict) -> "ShadowState":
        return cls(r_values=list(d.get("r_values", [])),
                   positions=dict(d.get("positions", {})),
                   entered_at_ms=int(d.get("entered_at_ms", 0)),
                   resumed_count=int(d.get("resumed_count", 0)))


class ShadowTracker:
    """Sanal pozisyonlari gercek strateji kurallariyla yurutur.

    Ayni `strategy.manage` cagrilir, ayni stop/hedef/iz suren stop mantigi
    calisir. Tek fark: emir gonderilmez. Boylece golge sonuclari gercek
    sonuclarla karsilastirilabilir olur.
    """

    def __init__(self, cfg: Config, strategy, store=None):
        self.cfg = cfg
        self.strategy = strategy
        self.store = store
        self.state = ShadowState()
        if store is not None:
            data = store.get_kv(f"shadow:{store.mode}")
            if data:
                self.state = ShadowState.from_dict(data)

    def save(self) -> None:
        if self.store:
            self.store.set_kv(f"shadow:{self.store.mode}", self.state.to_dict())

    # ------------------------------------------------------------------ giris
    def open(self, signal, now_ms: int) -> None:
        if signal.symbol in self.state.positions:
            return
        if len(self.state.positions) >= self.cfg.risk.max_concurrent_positions:
            return
        self.state.positions[signal.symbol] = {
            "side": signal.side, "entry": signal.entry, "stop": signal.stop,
            "tp1": signal.tp1, "tp2": signal.tp2, "opened_at": now_ms,
            "tp1_filled": False, "realized_r": 0.0, "qty_frac": 1.0,
        }
        log.info("[GOLGE] sanal giris %s %s @ %.4f (para riske atilmadi)",
                 signal.side, signal.symbol, signal.entry)
        self.save()

    def has_position(self, symbol: str) -> bool:
        return symbol in self.state.positions

    # ---------------------------------------------------------------- yonetim
    def update(self, symbol: str, candle: Candle, current_atr: float,
               now_ms: int) -> Optional[float]:
        """Sanal pozisyonu bir mum ilerletir. Kapandiysa R degerini doner."""
        d = self.state.positions.get(symbol)
        if not d:
            return None

        risk = abs(d["entry"] - d["stop"]) or 1e-9
        pos = Position(
            symbol=symbol, side=d["side"], qty=d["qty_frac"], entry_price=d["entry"],
            stop=d["stop"], tp1=d["tp1"], tp2=d["tp2"], initial_risk_per_unit=risk,
            opened_at=d["opened_at"], leverage=1, initial_qty=1.0,
            tp1_filled=d["tp1_filled"],
        )
        bar_ms = _tf_ms(self.cfg.timeframe)
        pos.bars_held = max(0, int((now_ms - d["opened_at"]) / bar_ms))

        for act in self.strategy.manage(pos, candle, current_atr):
            if act["type"] == "move_stop":
                d["stop"] = act["price"]
            elif act["type"] == "partial":
                frac = act["portion"] * d["qty_frac"]
                d["realized_r"] += (act["price"] - d["entry"]) * pos.direction / risk * frac
                d["qty_frac"] -= frac
                d["tp1_filled"] = True
            elif act["type"] == "exit":
                d["realized_r"] += ((act["price"] - d["entry"]) * pos.direction
                                    / risk * d["qty_frac"])
                # gercek islemdeki komisyon yukunu golgede de uygula
                cost_r = self._cost_in_r(d["entry"], risk)
                total_r = d["realized_r"] - cost_r
                self.state.r_values.append(total_r)
                del self.state.positions[symbol]
                log.info("[GOLGE] sanal cikis %s @ %.4f -> %+.2fR (%s) | "
                         "golge ornegi: %d", symbol, act["price"], total_r,
                         act["reason"], len(self.state.r_values))
                self.save()
                return total_r
        self.save()
        return None

    def _cost_in_r(self, entry: float, risk_per_unit: float) -> float:
        e = self.cfg.execution
        round_trip = 2 * e.taker_fee + 2 * (e.slippage_bps / 10_000.0)
        return (round_trip * entry) / risk_per_unit if risk_per_unit > 0 else 0.0

    # ---------------------------------------------------------------- geri don
    def enter(self, reason: str, now_ms: int) -> None:
        self.state = ShadowState(entered_at_ms=now_ms,
                                 resumed_count=self.state.resumed_count)
        self.save()
        log.error("GOLGE MODUNA GECILDI: %s. Bot islem yapmaya devam ediyor "
                  "ama para riske atilmiyor.", reason)

    def stats(self) -> tuple:
        n = len(self.state.r_values)
        if n == 0:
            return 0, 0.0, 0.0
        mean, sd = mean_and_sd(self.state.r_values)
        sd = sd or self.cfg.learning.trade_r_sd
        lcb = mean - self.cfg.shadow.resume_z * sd / math.sqrt(n)
        return n, mean, lcb

    def should_resume(self) -> tuple:
        """Canliya donus icin KANIT var mi?

        Simetrik degil: canliya donmek icin beklentinin pozitif oldugunun
        kanitlanmasi gerekiyor (alt guven siniri > 0). Sadece ortalamanin
        pozitif olmasi yetmez -- golgede kotu bir seriden sonra gelen sansli
        bir seri, sistemin duzeldigi anlamina gelmez.
        """
        sc = self.cfg.shadow
        n, mean, lcb = self.stats()
        if n < sc.min_trades_to_resume:
            return False, (f"golgede {n}/{sc.min_trades_to_resume} islem "
                           f"(ortalama {mean:+.3f}R)")
        if lcb <= 0:
            return False, (f"golgede {n} islem, ortalama {mean:+.3f}R ama alt "
                           f"guven siniri {lcb:+.3f} <= 0 -- kanit yetersiz")
        return True, (f"golgede {n} islem, ortalama {mean:+.3f}R, alt guven "
                      f"siniri {lcb:+.3f} > 0 -- edge geri geldi")

    def report(self) -> str:
        n, mean, lcb = self.stats()
        sc = self.cfg.shadow
        if n == 0:
            return "Golge modu: henuz kapanmis sanal islem yok."
        return (f"Golge modu: {n} sanal islem | ortalama {mean:+.3f}R | "
                f"alt guven siniri {lcb:+.3f} | canliya donus icin "
                f"{sc.min_trades_to_resume} islem ve alt sinir > 0 gerekiyor")


def _tf_ms(tf: str) -> int:
    return {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
            "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
            "4h": 14_400_000}.get(tf, 14_400_000)
