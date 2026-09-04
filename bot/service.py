"""Botu isletim sistemi servisi olarak kurma.

Amac: makine yeniden baslasa, bot cokse, internet kesilse bile sistem
kendi kendine ayaga kalksin. Insan mudahalesi gerekmesin.

Uc platform icin dosya uretir:
  Linux  -> systemd user service (loginctl enable-linger ile oturumdan bagimsiz)
  macOS  -> launchd plist (KeepAlive)
  Windows -> Task Scheduler XML + .bat sarmalayici

Hicbiri sifre ya da yonetici yetkisi istemez; hepsi kullanici seviyesinde calisir.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Tuple

SERVICE_NAME = "edith-bot"


def _paths(mode: str) -> Tuple[Path, str, str]:
    project = Path(__file__).resolve().parent.parent
    python = sys.executable
    return project, python, mode


def systemd_unit(mode: str) -> str:
    project, python, mode = _paths(mode)
    return f"""[Unit]
Description=EDITH trading bot ({mode})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={project}
ExecStart={python} -m bot {mode}
# Cokerse 30 saniye sonra tekrar baslat, sonsuza kadar dene.
Restart=always
RestartSec=30
StartLimitIntervalSec=0
# Kapatma sinyalinde bot durumu SQLite'a yazar; 60 saniye ver.
KillSignal=SIGTERM
TimeoutStopSec=60
StandardOutput=append:{project}/logs/service.log
StandardError=append:{project}/logs/service.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def launchd_plist(mode: str) -> str:
    project, python, mode = _paths(mode)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.edith.{SERVICE_NAME}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string><string>-m</string><string>bot</string>
    <string>{mode}</string>
  </array>
  <key>WorkingDirectory</key><string>{project}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{project}/logs/service.log</string>
  <key>StandardErrorPath</key><string>{project}/logs/service.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
"""


def windows_bat(mode: str) -> str:
    project, python, mode = _paths(mode)
    return f"""@echo off
REM EDITH bot - cokerse kendini yeniden baslatan sarmalayici
cd /d "{project}"
:loop
"{python}" -m bot {mode} >> "{project}\\logs\\service.log" 2>&1
echo [%date% %time%] bot durdu, 30 saniye sonra yeniden baslatiliyor >> "{project}\\logs\\service.log"
timeout /t 30 /nobreak > nul
goto loop
"""


def windows_task_xml(mode: str) -> str:
    project, _python, mode = _paths(mode)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>EDITH trading bot ({mode}) - acilista baslar</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{project}\\run-bot.bat</Command>
      <WorkingDirectory>{project}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(mode: str, write: bool = True) -> str:
    """Platforma uygun servis dosyasini yazar ve calistirilacak komutlari doner."""
    project, _python, mode = _paths(mode)
    (project / "logs").mkdir(exist_ok=True)
    system = platform.system()

    if system == "Linux":
        target = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(systemd_unit(mode), encoding="utf-8")
        return f"""Servis dosyasi yazildi: {target}

Simdi su iki komutu calistir (kopyala-yapistir):

  systemctl --user daemon-reload && systemctl --user enable --now {SERVICE_NAME}
  loginctl enable-linger $USER

Birincisi botu baslatir ve acilista otomatik baslamasini saglar.
Ikincisi oturumu kapatsan bile calismaya devam etmesini saglar.

Kontrol:      systemctl --user status {SERVICE_NAME}
Loglar:       journalctl --user -u {SERVICE_NAME} -f
Durdurmak:    systemctl --user stop {SERVICE_NAME}

Bundan sonra makineyi yeniden baslatsan da bot kendiliginden acilir.
Cokerse 30 saniye icinde kendini toparlar."""

    if system == "Darwin":
        target = Path.home() / "Library" / "LaunchAgents" / f"com.edith.{SERVICE_NAME}.plist"
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(launchd_plist(mode), encoding="utf-8")
        return f"""Servis dosyasi yazildi: {target}

Simdi su komutu calistir:

  launchctl load -w {target}

Kontrol:      launchctl list | grep edith
Durdurmak:    launchctl unload {target}

Makine yeniden baslatildiginda bot kendiliginden acilir."""

    if system == "Windows":
        bat = project / "run-bot.bat"
        xml = project / "edith-bot-task.xml"
        if write:
            bat.write_text(windows_bat(mode), encoding="utf-8")
            xml.write_text(windows_task_xml(mode), encoding="utf-16")
        return f"""Iki dosya yazildi:
  {bat}   (cokerse yeniden baslatan sarmalayici)
  {xml}   (acilista baslatma gorevi)

Simdi PowerShell'de su komutu calistir:

  schtasks /Create /TN "EDITH Bot" /XML "{xml}" /F

Kontrol:      schtasks /Query /TN "EDITH Bot"
Durdurmak:    schtasks /End /TN "EDITH Bot"
Kaldirmak:    schtasks /Delete /TN "EDITH Bot" /F

Bilgisayari yeniden baslattiginda bot kendiliginden acilir.
Cokerse .bat sarmalayici 30 saniye icinde tekrar baslatir."""

    return f"Bu isletim sistemi ({system}) icin otomatik kurulum yok."
