"""Telegram bildirim ve komut katmani. Token yoksa sessizce devre disi kalir.

Iki yon var:
  GIDEN  (send)          -> giris/cikis, uyari, gunluk rapor
  GELEN  (poll_commands) -> /durum, /bakiye, /dur, /devam, /kapat

GUVENLIK: gelen komutlar SADECE TELEGRAM_CHAT_ID ile eslesen sohbetten
kabul edilir. Bot token'i sizarsa baskasi bota "hepsini kapat" diyebilir;
chat_id kontrolu bunun tek savunmasi. Asla gevsetme.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org"


class Notifier:
    def __init__(self, store=None) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        self.store = store
        # Telegram getUpdates offset'i: ayni komutu iki kez islememek icin.
        # Kalici saklanir, yoksa yeniden baslatmada eski "/kapat" tekrar isler.
        self._offset = 0
        if store is not None:
            rec = store.get_kv("telegram_offset") or {}
            self._offset = int(rec.get("offset", 0))

    # ----------------------------------------------------------------- giden
    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            requests.post(
                f"{API}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:4000]},
                timeout=8,
            )
        except Exception:  # bildirim hatasi islemi durdurmamali
            log.warning("Telegram bildirimi gonderilemedi", exc_info=True)

    # ----------------------------------------------------------------- gelen
    def poll_commands(self) -> List[str]:
        """Bekleyen komutlari doner. Bloklamaz (timeout=0, tek atis).

        Islem dongusunu bekletmemek icin long-polling KULLANILMIYOR: bot
        her turda bir kez bakar, komut varsa isler. Gecikme en fazla bir
        dongu (varsayilan 60 sn) -- acil kapatma icin yeterince hizli,
        fiyat takibini yavaslatmayacak kadar ucuz.
        """
        if not self.enabled:
            return []
        try:
            resp = requests.get(
                f"{API}/bot{self.token}/getUpdates",
                params={"offset": self._offset, "timeout": 0, "limit": 20},
                timeout=10,
            )
            data = resp.json()
        except Exception:
            log.warning("Telegram komutlari okunamadi", exc_info=True)
            return []
        if not data.get("ok"):
            return []

        out: List[str] = []
        last = self._offset
        for upd in data.get("result", []):
            last = max(last, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            # Sahibi disindaki herkes yok sayilir. Sessizce: yabanciya
            # "yanlis sohbet" demek botun varligini dogrular.
            if chat != str(self.chat_id):
                log.warning("Yetkisiz Telegram komutu (chat=%s) yok sayildi", chat)
                continue
            out.append(text)
        if last != self._offset:
            self._offset = last
            if self.store is not None:
                self.store.set_kv("telegram_offset", {"offset": last})
        return out


class CommandRouter:
    """Metin komutlarini calistirilabilir eylemlere baglar.

    Yikici komutlar (hepsini kapat) TEK mesajla calismaz: once onay istenir.
    Sebep: cebindeki telefonda yanlisliga basmak, gercek parayi piyasadan
    kotu bir anda cikarmak demek. Onay penceresi kisa tutulur.
    """

    CONFIRM_WINDOW_MS = 120_000

    def __init__(self) -> None:
        self._handlers: Dict[str, Tuple[Callable[[], str], bool]] = {}
        self._pending: Optional[Tuple[str, int]] = None

    def register(self, name: str, fn: Callable[[], str], *,
                 confirm: bool = False, aliases: Tuple[str, ...] = ()) -> None:
        for key in (name,) + aliases:
            self._handlers[key.lower()] = (fn, confirm)

    def dispatch(self, text: str, now_ms: int) -> Optional[str]:
        cmd = text.split()[0].lower().lstrip("/")
        cmd = cmd.split("@")[0]  # grup sohbetinde /dur@botadi

        if cmd == "onayla":
            if not self._pending:
                return "Onaylanacak bir sey yok."
            pend, ts = self._pending
            self._pending = None
            if now_ms - ts > self.CONFIRM_WINDOW_MS:
                return "Onay suresi doldu. Komutu bastan yaz."
            fn, _ = self._handlers[pend]
            return fn()

        if cmd == "iptal":
            self._pending = None
            return "Iptal edildi."

        entry = self._handlers.get(cmd)
        if entry is None:
            return None
        fn, needs_confirm = entry
        if needs_confirm:
            self._pending = (cmd, now_ms)
            return (f"/{cmd} GERI ALINAMAZ.\n\n"
                    "Onaylamak icin /onayla yaz (2 dakika icinde).\n"
                    "Vazgecmek icin /iptal.")
        return fn()

    @property
    def commands(self) -> List[str]:
        return sorted(set(self._handlers))
