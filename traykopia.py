# -*- coding: utf-8 -*-
"""
TrayKopia – wskaźnik stanu kopii zapasowych Kopia (KopiaUI) w zasobniku systemowym.

Sam odczytuje wszystkie repozytoria podłączone w KopiaUI (%APPDATA%\\kopia\\*.config),
ich foldery i harmonogramy (polityki w repozytoriach), śledzi działające procesy
KopiaUI/kopia i ich logi, a do tego robi okresowe sprawdzanie kopii
(kopia snapshot verify) według własnego harmonogramu.

Autor: IT Guy (github.com/nyorbee)
"""

import os
import re
import sys
import json
import glob
import time
import queue
import zlib
import shutil
import threading
import subprocess
import webbrowser
from datetime import datetime, timedelta, timezone

import psutil

try:
    from croniter import croniter  # opcjonalne – tylko dla harmonogramów cron
except Exception:  # pragma: no cover
    croniter = None

APP_NAME = "TrayKopia"
APP_VERSION = "1.4.3"
URL_FACEBOOK = "https://www.facebook.com/ajtiguy"
URL_GITHUB = "https://github.com/nyorbee"

IS_WIN = sys.platform == "win32"
APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
KOPIA_CFG_DIR = os.path.join(APPDATA, "kopia")
KOPIA_LOG_DIR = os.path.join(LOCALAPPDATA, "kopia", "cli-logs")
APP_DIR = os.path.join(APPDATA, APP_NAME)
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
STATE_FILE = os.path.join(APP_DIR, "state.json")
APP_LOG_FILE = os.path.join(APP_DIR, "traykopia.log")

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

# --- poziomy stanu (im wyższy, tym gorszy) ---
OK, INFO, WAIT, WARN, ERROR = 0, 1, 2, 4, 5

# --- język (domyślnie angielski; wybór zapamiętany w ustawieniach) ---
# Słownik: polski szablon -> angielski
EN = {
    'Kopia: {v} (testowana: {t})': 'Kopia: {v} (tested: {t})',
    'Nie znaleziono logów KopiaUI dla: {r}. Postęp kopii i błędy snapshotów będą widoczne tylko częściowo.': 'KopiaUI logs not found for: {r}. Backup progress and snapshot errors will only be partly visible.',
    'TrayKopia może pokazywać niepełne informacje – szczegóły w oknie stanu.': 'TrayKopia may show incomplete information – see the status window.',
    'TrayKopia – diagnostyka': 'TrayKopia – diagnostics',
    'W logach KopiaUI nie ma rozpoznawalnego wpisu o ostatnim snapshocie: {r}. Możliwe, że po aktualizacji Kopii zmienił się format logów – postęp, błędy i „bez zmian” mogą być niedokładne. (Jeśli robiłeś snapshot z wiersza poleceń, zignoruj.)': 'KopiaUI logs have no recognizable entry for the latest snapshot: {r}. The log format may have changed after a Kopia update – progress, errors and “no changes” may be inaccurate. (If you made the snapshot from the command line, ignore this.)',
    '  (alarmów w innych repozytoriach: {n})': '  (alarms in other repositories: {n})',
    ' (od {t})': ' (since {t})',
    ' (trwał {d})': ' (took {d})',
    ' ({n} bł.)': ' ({n} err.)',
    ' · problemy: {n}': ' · problems: {n}',
    ' · uwagi: {n}': ' · warnings: {n}',
    ' · w trakcie: {n}': ' · in progress: {n}',
    ' · wczytuję: {n}': ' · loading: {n}',
    ' – przeczytano {d}': ' – read {d}',
    ' – {d} z ~{t} ({p}%)': ' – {d} of ~{t} ({p}%)',
    '% plików': '% of files',
    '(brak alarmów)': '(no alarms)',
    '(brak repozytoriów)': '(no repositories)',
    '(nie znaleziono repozytoriów)': '(no repositories found)',
    '(nieobsługiwany)': '(not supported)',
    '(nowo dodane pliki się nie liczą; minimum 50 plików)': "(newly added files don't count; minimum 50 files)",
    ', automatyczne': ', automatic',
    ', nowe/zmienione pliki: {n}': ', new/changed files: {n}',
    ', odczyt {r}, zapis {w}': ', read {r}, write {w}',
    ', rozmiar {s}, plików {f}': ', size {s}, files {f}',
    'Aktualne {a} z {b}: ': 'Up to date {a} of {b}: ',
    'Alarm, gdy w jednym snapshocie zmieni się lub zniknie co najmniej': 'Alarm when one snapshot changes or removes at least',
    'Alarm: podejrzanie dużo zmian (np. zaszyfrowanie plików)': 'Alarm: suspiciously many changes (e.g. encrypted files)',
    'Anuluj': 'Cancel',
    'Bez błędów. Przeczytano {n} plików ({b}).': 'No errors. Read {n} files ({b}).',
    'Brak repozytoriów': 'No repositories',
    'Brak zakończonego snapshotu.': 'No completed snapshot.',
    'Błąd konserwacji {w}: {e}': 'Maintenance error {w}: {e}',
    'Błąd odczytu: {e}': 'Read error: {e}',
    'Błąd: ': 'Error: ',
    'Błędy:': 'Errors:',
    'Co miesiąc, dnia:': 'Monthly, on day:',
    'Czeka na kopię': 'Waiting for backup',
    'Dane jeszcze się wczytują – spróbuj za chwilę.': 'Data is still loading – try again in a moment.',
    'Dni:': 'Days:',
    'Do sprawdzenia': 'Needs attention',
    'Dzień miesiąca: 1–28.': 'Day of month: 1–28.',
    'Folder: {p}': 'Folder: {p}',
    'Godzina:': 'Time:',
    'Harmonogram działa tylko, gdy KopiaUI jest uruchomiona.': 'The schedule only works while KopiaUI is running.',
    'Harmonogram: {s}': 'Schedule: {s}',
    'Historia sprawdzania: ': 'Verification history: ',
    'Identyczne snapshoty nie są zapisywane (Ignore Identical Snapshots).': 'Identical snapshots are not saved (Ignore Identical Snapshots).',
    'Jeśli to Ty – kliknij „To ja – zatwierdź zmiany”.': 'If it was you, click “It was me – accept changes”.',
    'Kopia nie zapisała nowego snapshotu, bo nic się nie zmieniło.': 'Kopia did not save a new snapshot because nothing changed.',
    'Kopia pracuje': 'Kopia is working',
    'Kopia pracuje (odczyt {r}, zapis {w})': 'Kopia is working (read {r}, write {w})',
    'Kopia: problemy ({n})': 'Backup: problems ({n})',
    'Kopia: {n}': 'Backup: {n}',
    'KopiaUI działa · repozytoriów: {n} · OK: {k}': 'KopiaUI running · repositories: {n} · OK: {k}',
    'KopiaUI nie działa': 'KopiaUI is not running',
    'KopiaUI nie działa – kopie według harmonogramu się nie robią': 'KopiaUI is not running – scheduled backups are not happening',
    'KopiaUI nie działa!': 'KopiaUI is not running!',
    'KopiaUI ponawia nieudany snapshot co 5 minut.': 'KopiaUI retries a failed snapshot every 5 minutes.',
    'Kopie według harmonogramu nie będą się robić, dopóki nie uruchomisz KopiaUI.': 'Scheduled backups will not run until you start KopiaUI.',
    'Mało miejsca na dysku repozytorium: wolne {f} z {t}.': 'Low space on the repository disk: {f} free of {t}.',
    'Montowanie… (pierwsze zamontowanie może potrwać kilkanaście sekund)': 'Mounting… (the first mount may take a few seconds)',
    'NIE ZNALEZIONO kopia.exe – ustaw ścieżkę w ustawieniach': 'kopia.exe NOT FOUND – set the path in settings',
    'Nadrób po włączeniu komputera, jeśli termin minął': 'Catch up after the PC starts if the time was missed',
    'Następne sprawdzanie częściowe: {w}': 'Next partial verification: {w}',
    'Następny': 'Next',
    'Następny planowany: {w}': 'Next scheduled: {w}',
    'Nie ma folderu {p}.': 'Folder {p} does not exist.',
    'Nie można odczytać repozytorium: {e}': 'Cannot read repository: {e}',
    'Nie udało się odczytać polityki: {e}': 'Could not read policy: {e}',
    'Nie udało się uruchomić KopiaUI: {e}': 'Could not start KopiaUI: {e}',
    'Nie udało się zamontować: {m}': 'Mount failed: {m}',
    'Nie udało się zmienić autostartu: {e}': 'Could not change autostart: {e}',
    'Nie znaleziono KopiaUI.exe.': 'KopiaUI.exe not found.',
    'Nie znaleziono kopia.exe': 'kopia.exe not found',
    'Nie znaleziono kopia.exe (zainstaluj KopiaUI albo wskaż plik w ustawieniach).': 'kopia.exe not found (install KopiaUI or set the path in settings).',
    'Nie znaleziono kopia.exe.': 'kopia.exe not found.',
    'Nieprawidłowa godzina: „{t}” (format GG:MM).': 'Invalid time: “{t}” (format HH:MM).',
    'O programie': 'About',
    'O programie {a}': 'About {a}',
    'OK {a}/{b}, czeka {c}': 'OK {a}/{b}, waiting {c}',
    'Odmontuj': 'Unmount',
    'Odmontuj wszystkie': 'Unmount all',
    'Odśwież': 'Refresh',
    'Odśwież teraz': 'Refresh now',
    'Ostatni planowany termin: {w}': 'Last scheduled time: {w}',
    'Ostatni snapshot': 'Last snapshot',
    'Ostatni snapshot: {w}': 'Last snapshot: {w}',
    'Ostatnia kopia: {w}': 'Last backup: {w}',
    'Otwórz KopiaUI': 'Open KopiaUI',
    'PODEJRZANIE DUŻO ZMIAN {w}: ': 'SUSPICIOUSLY MANY CHANGES {w}: ',
    'PROBLEM': 'PROBLEM',
    'Pełne sprawdzanie': 'Full verification',
    'Pełne sprawdzanie (100%) – wybrane repozytoria': 'Full verification (100%) – selected repositories',
    'Plik konfiguracji: {p}': 'Config file: {p}',
    'Pliki': 'Files',
    'Pokaż stan kopii': 'Show backup status',
    'Pominięte błędy:': 'Ignored errors:',
    'Powiadomienia': 'Notifications',
    'Problemy: {n}': 'Problems: {n}',
    'Procent losowo czytanych plików:': 'Percent of files read at random:',
    'Procent musi być liczbą od 1 do 100.': 'Percent must be a number from 1 to 100.',
    'Proces KopiaUI dla tego repozytorium działa od {w}': 'KopiaUI process for this repository running since {w}',
    'Proces KopiaUI dla tego repozytorium nie działa.': 'KopiaUI process for this repository is not running.',
    'Proces KopiaUI intensywnie pracuje – kopia albo konserwacja repozytorium.': 'The KopiaUI process is busy – a backup or repository maintenance.',
    'Programy': 'Programs',
    'Przerwij sprawdzanie': 'Cancel verification',
    'Próg alarmu: liczba od 5 do 100.': 'Alarm threshold: a number from 5 to 100.',
    'Puste = automatycznie. Używany: ': 'Empty = automatic. In use: ',
    'Repozytoria:': 'Repositories:',
    'Repozytorium': 'Repository',
    'Repozytorium niedostępne: {p}': 'Repository unavailable: {p}',
    'Repozytorium niedostępne: {p} (dysk odłączony?)': 'Repository unavailable: {p} (disk disconnected?)',
    'Repozytorium:': 'Repository:',
    'Repozytorium: {p}': 'Repository: {p}',
    'Robi się kopia': 'Backing up',
    'Rozmiar': 'Size',
    'Snapshot zostanie zamontowany jako dysk tylko do odczytu i otworzy się w Eksploratorze.': 'The snapshot will be mounted as a read-only drive and opened in Explorer.',
    'Sprawdzanie częściowe': 'Partial verification',
    'Sprawdzanie częściowe – wszystkie repozytoria': 'Partial verification – all repositories',
    'Sprawdzanie kopii': 'Verifying backups',
    'Sprawdź całość': 'Verify fully',
    'Sprawdź całość (100%)': 'Verify fully (100%)',
    'Sprawdź częściowo': 'Verify partially',
    'Sprawdź częściowo ({p}%)': 'Verify partially ({p}%)',
    'Sprawdź wszystkie częściowo': 'Verify all partially',
    'Stan': 'Status',
    'Stan kopii zapasowych Kopia w zasobniku systemowym': 'Kopia backup status in the system tray',
    'Ten snapshot nie ma identyfikatora katalogu.': 'This snapshot has no directory ID.',
    'To ja – zatwierdź zmiany': 'It was me – accept changes',
    'To repozytorium nie ma jeszcze snapshotów.': 'This repository has no snapshots yet.',
    'Uruchamiaj z systemem': 'Start with Windows',
    'Uruchamianie…': 'Starting…',
    'Ustawienia sprawdzania…': 'Verification settings…',
    'Ustawienia…': 'Settings…',
    'Uwaga: ': 'Warning: ',
    'Uwagi': 'Notes',
    'W folderze nic się nie zmieniło, więc Kopia nie zapisuje nowego snapshotu.': 'Nothing changed in the folder, so Kopia does not save a new snapshot.',
    'Wolne na dysku': 'Free space',
    'Wskazany kopia.exe nie istnieje.': 'The selected kopia.exe does not exist.',
    'Wskaż kopia.exe': 'Select kopia.exe',
    'Wszystkie kopie aktualne ({a} z {b})': 'All backups up to date ({a} of {b})',
    'Wszystkie repozytoria': 'All repositories',
    'Wszystko aktualne': 'All up to date',
    'Włączone': 'Enabled',
    'Włączony': 'Enabled',
    'Zakończ': 'Exit',
    'Zamknij': 'Close',
    'Zamontowano jako {d}. Odmontujesz z menu w zasobniku (Odmontuj) albo przy zamknięciu programu.': 'Mounted as {d}. Unmount it from the tray menu (Unmount) or by exiting the program.',
    'Zamontowany: {n}': 'Mounted: {n}',
    'Zamontuj i otwórz': 'Mount and open',
    'Zamontuj snapshot': 'Mount snapshot',
    'Zamontuj snapshot…': 'Mount snapshot…',
    'Zapisz': 'Save',
    'bez zmian od {w}': 'no changes since {w}',
    'bez zmian – sprawdzone {w}': 'no changes – checked {w}',
    'brak danych': 'no data',
    'brak folderów – dodaj snapshot w KopiaUI': 'no folders – add a snapshot in KopiaUI',
    'brak harmonogramu': 'no schedule',
    'błąd snapshotu: {e}': 'snapshot error: {e}',
    'błąd: ': 'error: ',
    'co {d}': 'every {d}',
    'codziennie': 'daily at',
    'czeka na pierwszy snapshot': 'waiting for the first snapshot',
    'czeka na start (plan {w})': 'waiting to start (scheduled {w})',
    'dziś {t}': 'today {t}',
    'folder źródłowy niedostępny': 'source folder unavailable',
    'jeszcze nie było snapshotu': 'no snapshot yet',
    'jutro {t}': 'tomorrow {t}',
    'kod wyjścia {c}': 'exit code {c}',
    'kopia mount zakończyła się kodem {c}': 'kopia mount exited with code {c}',
    'kopia nie odpowiedziała w {n} s': 'kopia did not respond within {n} s',
    'kopia zakończyła się kodem {rc}': 'kopia exited with code {rc}',
    'mało miejsca na dysku kopii': 'low space on backup disk',
    'mało miejsca na {d}: {f}': 'low space on {d}: {f}',
    'montowanie': 'mount',
    'nie działa – kopie według harmonogramu się nie robią': 'not running – scheduled backups are not happening',
    'nie udało się odczytać harmonogramu': 'could not read the schedule',
    'nie wybrano repozytoriów': 'no repositories selected',
    'nie wykonano (plan {w})': 'not done (scheduled {w})',
    'nie znaleziono': 'not found',
    'nie znaleziono repozytoriów KopiaUI': 'no KopiaUI repositories found',
    'niepełny ({r})': 'incomplete ({r})',
    'o godzinie:': 'at:',
    'od {t} ({d})': 'since {t} ({d})',
    'odświeżono {t}': 'refreshed {t}',
    'pełne': 'full',
    'pełne sprawdzanie': 'full verification',
    'pełne: {w}': 'full: {w}',
    'pominięta ({w}) – KopiaUI nie działa': 'missed ({w}) – KopiaUI is not running',
    'pominięta ({w}) – repozytorium nie jest połączone w KopiaUI': 'missed ({w}) – repository is not connected in KopiaUI',
    'pracuje (kopia lub konserwacja)': 'working (backup or maintenance)',
    'przeczytano {d}': 'read {d}',
    'przegapiona ({w}), nadrabianie wyłączone – następna {n}': 'missed ({w}), catch-up disabled – next {n}',
    'przerwane': 'cancelled',
    'przerwane {w}': 'cancelled {w}',
    'robi się kopia': 'backing up',
    'ręcznie': 'manual',
    'ręczny – jeszcze nie było snapshotu': 'manual – no snapshot yet',
    'ręczny – ostatni {w}': 'manual – last {w}',
    'sprawdzanie wykryło problem ({w})': 'verification found a problem ({w})',
    'sprawdzanie {k}': 'verifying {k}',
    'sprawdzanie {p}%': '{p}% verification',
    'sprawdzanie: {w}': 'verification: {w}',
    'stan kopii zapasowych': 'backup status',
    'trwa': 'running',
    'tylko ręcznie': 'manual only',
    'uruchamianie…': 'starting…',
    'ustawienia sprawdzania': 'verification settings',
    'w kolejce': 'queued',
    'w kolejce: {n}': 'queued: {n}',
    'wczoraj {t}': 'yesterday {t}',
    'wczytywanie…': 'loading…',
    'wszystko OK ({a}/{b})': 'all OK ({a}/{b})',
    'wykryto błędy': 'errors found',
    'wyłączone': 'off',
    'zaległa – Kopia zrobi ją o {t}': 'overdue – Kopia will run it at {t}',
    'zamontowany: {n} {d}': 'mounted: {n} {d}',
    'zamontuj snapshot': 'mount snapshot',
    'zmienionych {m} z {b} plików ({p}%)': '{m} of {b} files changed ({p}%)',
    'zniknęło {d} plików ({p}%)': '{d} files disappeared ({p}%)',
    'zrobiona {w}': 'done {w}',
    '{b}, ale {n} plików się nie skopiowało': '{b}, but {n} files were not backed up',
    '{b}, pominięto {n} błędów': '{b}, {n} errors ignored',
    '{b}; późniejszy snapshot przerwany ({r})': '{b}; a later snapshot was interrupted ({r})',
    '{d} z ~{t}': '{d} of ~{t}',
    '{n} błędów': '{n} errors',
    '{r}, {w}, trwało {d}, przeczytano {n} plików ({b})': '{r}, {w}, took {d}, read {n} files ({b})',
    '{w}: zmienione {m}, usunięte {d} (poprzednio {b} plików, snapshot {pw})': '{w}: changed {m}, deleted {d} (previously {b} files, snapshot {pw})',
    '⚠ Alarm zmian – sprawdź, czy to Ty (np. porządki, zmiana nazw) czy coś złego (np. zaszyfrowanie):': '⚠ Change alarm – check whether it was you (tidying up, renaming) or something bad (e.g. encryption):',
}

LANG = {"code": "en"}


def _(text, **kw):
    """Tłumaczenie: polski szablon -> angielski (EN), potem format()."""
    t = EN.get(text, text) if LANG["code"] == "en" else text
    return t.format(**kw) if kw else t


DAY_NAMES_PL = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
DAY_NAMES_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def day_names():
    return DAY_NAMES_EN if LANG["code"] == "en" else DAY_NAMES_PL

TESTED_KOPIA = (0, 23)        # wersja Kopii, pod którą program był pisany i testowany
GRACE_MINUTES = 20            # ile czekać po zaplanowanej godzinie, zanim uznamy snapshot za pominięty
SERVER_WARMUP_MINUTES = 10    # tyle czasu dajemy świeżo uruchomionemu KopiaUI na nadrobienie
FULL_REFRESH_EVERY = 600      # pełne odświeżenie danych z repozytoriów (s)
PROC_SCAN_EVERY = 3           # skanowanie procesów i logów (s)
LOW_SPACE_PCT = 5.0           # ostrzeżenie o małej ilości miejsca na dysku repozytorium
LOW_SPACE_BYTES = 50 * 1024 ** 3


# ============================================================================
#  Narzędzia
# ============================================================================

def app_log(msg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(APP_LOG_FILE) and os.path.getsize(APP_LOG_FILE) > 2_000_000:
            shutil.move(APP_LOG_FILE, APP_LOG_FILE + ".old")
        with open(APP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def now_local():
    return datetime.now().astimezone()


_TS_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?")


def parse_ts(s):
    """Czas z Kopii (RFC3339, nanosekundy, zwykle UTC) -> lokalny datetime ze strefą."""
    if not s or not isinstance(s, str):
        return None
    m = _TS_RE.match(s.strip())
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    base = base.replace(microsecond=int(frac))
    tz = m.group(3)
    if tz in (None, "Z"):
        tzinfo = timezone.utc
    else:
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        tzinfo = timezone(sign * timedelta(hours=hh, minutes=mm))
    return base.replace(tzinfo=tzinfo).astimezone()


def to_iso(dt):
    return dt.isoformat() if dt else None


def from_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).astimezone()
    except Exception:
        return None


def fmt_bytes(n):
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            v = f"{n:.1f} {unit}"
            return v if LANG["code"] == "en" else v.replace(".", ",")
        n /= 1024
    return f"{n:.1f} TB"


def fmt_rate(bps):
    return fmt_bytes(bps) + "/s"


def fmt_when(dt, now=None):
    if not dt:
        return "—"
    now = now or now_local()
    d = dt.date()
    t = dt.strftime("%H:%M")
    if d == now.date():
        return _("dziś {t}", t=t)
    if d == (now - timedelta(days=1)).date():
        return _("wczoraj {t}", t=t)
    if d == (now + timedelta(days=1)).date():
        return _("jutro {t}", t=t)
    if abs((now - dt).days) < 7:
        return f"{day_names()[dt.weekday()]} {dt:%d.%m} {t}"
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_duration(td):
    s = int(td.total_seconds())
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} s" if m < 10 else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def norm(p):
    return os.path.normcase(os.path.abspath(p)) if p else ""


def resource_path(name):
    """Plik dołączony do exe (Nuitka --include-data-files) albo leżący obok skryptu."""
    for base in (os.path.dirname(os.path.abspath(__file__)),
                 os.path.dirname(os.path.abspath(sys.argv[0]))):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


def is_compiled():
    return "__compiled__" in globals()


# ============================================================================
#  Ustawienia i stan trwały
# ============================================================================

DEFAULT_SETTINGS = {
    "language": "en",                     # en / pl
    "notifications": True,
    "alarm": {                            # alarm „podejrzanie dużo zmian” (np. ransomware)
        "enabled": True,
        "percent": 30,                    # % zmienionych lub usuniętych plików względem poprzedniego snapshotu
        "min_files": 50,
    },
    "kopia_exe": "",                      # pusty = szukaj automatycznie
    "verify": {                           # okresowe sprawdzanie częściowe (wszystkie repozytoria)
        "enabled": True,
        "percent": 5,
        "days": [3],                      # 0=pon ... 6=nd
        "time": "16:30",
        "catch_up": True,                 # nadrób, jeśli komputer był wyłączony
    },
    "full": {                             # okresowe pełne sprawdzanie wybranych repozytoriów
        "enabled": True,
        "day": 1,                         # dzień miesiąca 1–28
        "time": "16:30",
        "catch_up": True,
        "repos": [],                      # identyfikatory configów (np. "repository-1764...")
    },
}


def deep_merge(defaults, data):
    out = {}
    for k, v in defaults.items():
        if isinstance(v, dict):
            out[k] = deep_merge(v, data.get(k, {}) if isinstance(data.get(k), dict) else {})
        else:
            out[k] = data.get(k, v)
    for k, v in data.items():
        if k not in out:
            out[k] = v
    return out


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Store:
    """Ustawienia + stan trwały (wyniki sprawdzania, zdarzenia z logów)."""

    def __init__(self):
        self.lock = threading.RLock()
        self.settings = deep_merge(DEFAULT_SETTINGS, load_json(SETTINGS_FILE, {}))
        self.state = load_json(STATE_FILE, {})
        self.state.setdefault("verify", {})       # repo_id -> {"quick": {...}, "full": {...}}
        self.state.setdefault("events", {})       # repo_id -> zdarzenia z logów
        self.state.setdefault("auto", {})         # znaczniki wykonanych terminów automatycznych
        self.state.setdefault("acks", {})         # repo_id -> [id snapshotów z zatwierdzonym alarmem]

    def save_settings(self):
        with self.lock:
            save_json(SETTINGS_FILE, self.settings)

    def save_state(self):
        with self.lock:
            try:
                save_json(STATE_FILE, self.state)
            except Exception as e:
                app_log(f"save_state: {e}")


# ============================================================================
#  Harmonogramy
# ============================================================================

def _strip_cron(expr):
    return expr.split("#", 1)[0].strip()


def last_due(sched, now, last_snapshot=None):
    """Ostatni zaplanowany termin <= now (albo None, gdy brak harmonogramu)."""
    if not sched or sched.get("manual"):
        return None
    cands = []
    for tod in sched.get("timeOfDay") or []:
        d = now.replace(hour=int(tod.get("hour", 0)), minute=int(tod.get("min", 0)),
                        second=0, microsecond=0)
        if d > now:
            d -= timedelta(days=1)
        cands.append(d)
    if croniter:
        for expr in sched.get("cron") or []:
            try:
                cands.append(croniter(_strip_cron(expr), now).get_prev(datetime))
            except Exception:
                pass
    iv = int(sched.get("intervalSeconds") or 0)
    if iv > 0 and last_snapshot:
        d = last_snapshot + timedelta(seconds=iv)
        if d <= now:
            cands.append(d)
    return max(cands) if cands else None


def next_due(sched, now, last_snapshot=None):
    if not sched or sched.get("manual"):
        return None
    cands = []
    for tod in sched.get("timeOfDay") or []:
        d = now.replace(hour=int(tod.get("hour", 0)), minute=int(tod.get("min", 0)),
                        second=0, microsecond=0)
        if d <= now:
            d += timedelta(days=1)
        cands.append(d)
    if croniter:
        for expr in sched.get("cron") or []:
            try:
                cands.append(croniter(_strip_cron(expr), now).get_next(datetime))
            except Exception:
                pass
    iv = int(sched.get("intervalSeconds") or 0)
    if iv > 0:
        d = (last_snapshot + timedelta(seconds=iv)) if last_snapshot else now
        cands.append(max(d, now))
    return min(cands) if cands else None


def has_schedule(sched):
    if not sched or sched.get("manual"):
        return False
    return bool(sched.get("timeOfDay") or sched.get("cron") or sched.get("intervalSeconds"))


def describe_schedule(sched):
    if not sched:
        return _("brak danych")
    if sched.get("manual"):
        return _("tylko ręcznie")
    parts = []
    tods = sched.get("timeOfDay") or []
    if tods:
        parts.append(_("codziennie") + " " + ", ".join(f"{int(t.get('hour', 0)):02d}:{int(t.get('min', 0)):02d}" for t in tods))
    iv = int(sched.get("intervalSeconds") or 0)
    if iv:
        parts.append(_("co {d}", d=fmt_duration(timedelta(seconds=iv))))
    for c in sched.get("cron") or []:
        parts.append(f"cron „{_strip_cron(c)}”" + ("" if croniter else " " + _("(nieobsługiwany)")))
    return "; ".join(parts) if parts else _("brak harmonogramu")


def weekly_last_due(days, hhmm, now):
    if not days:
        return None
    h, m = parse_hhmm(hhmm)
    for back in range(0, 8):
        d = (now - timedelta(days=back)).replace(hour=h, minute=m, second=0, microsecond=0)
        if d.weekday() in days and d <= now:
            return d
    return None


def weekly_next_due(days, hhmm, now):
    if not days:
        return None
    h, m = parse_hhmm(hhmm)
    for fwd in range(0, 8):
        d = (now + timedelta(days=fwd)).replace(hour=h, minute=m, second=0, microsecond=0)
        if d.weekday() in days and d > now:
            return d
    return None


def monthly_last_due(day, hhmm, now):
    h, m = parse_hhmm(hhmm)
    day = max(1, min(28, int(day)))
    d = now.replace(day=day, hour=h, minute=m, second=0, microsecond=0)
    if d > now:
        prev_month_end = now.replace(day=1) - timedelta(days=1)
        d = prev_month_end.replace(day=day, hour=h, minute=m, second=0, microsecond=0)
    return d


def monthly_next_due(day, hhmm, now):
    h, m = parse_hhmm(hhmm)
    day = max(1, min(28, int(day)))
    d = now.replace(day=day, hour=h, minute=m, second=0, microsecond=0)
    if d <= now:
        nm = (now.replace(day=28) + timedelta(days=5)).replace(day=day)
        d = nm.replace(hour=h, minute=m, second=0, microsecond=0)
    return d


def valid_hhmm(s):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s))
    return bool(m) and int(m.group(1)) < 24 and int(m.group(2)) < 60


def parse_hhmm(s):
    try:
        h, m = str(s).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:
        pass
    return 16, 30


# ============================================================================
#  Repozytoria (configi KopiaUI)
# ============================================================================

class RepoInfo:
    def __init__(self, cfg_path, data):
        self.cfg_path = cfg_path
        self.id = os.path.splitext(os.path.basename(cfg_path))[0]
        self.key = norm(cfg_path)
        self.description = (data.get("description") or "").strip()
        self.hostname = data.get("hostname") or ""
        self.username = data.get("username") or ""
        st = data.get("storage") or {}
        self.storage_type = st.get("type") or ("server" if data.get("apiServer") else "?")
        self.storage_path = (st.get("config") or {}).get("path") or ""
        if data.get("apiServer"):
            self.storage_path = (data.get("apiServer") or {}).get("url", "")

    @property
    def name(self):
        if self.description and self.description.lower() not in ("repository", "my repository"):
            return self.description
        if self.storage_path and self.storage_type == "filesystem":
            return os.path.basename(self.storage_path.rstrip("\\/")) or self.storage_path
        return self.description or self.id


def discover_repos(cfg_dir=KOPIA_CFG_DIR):
    repos = []
    for p in sorted(glob.glob(os.path.join(cfg_dir, "*.config"))):
        data = load_json(p, None)
        if not isinstance(data, dict):
            continue
        if not data.get("storage") and not data.get("apiServer"):
            continue
        repos.append(RepoInfo(p, data))
    return repos


# ============================================================================
#  Wywołania kopia.exe
# ============================================================================

def default_kopia_candidates():
    c = []
    for base in (os.path.join(LOCALAPPDATA, "Programs", "KopiaUI"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "KopiaUI"),
                 os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "KopiaUI")):
        c.append(os.path.join(base, "resources", "server", "kopia.exe"))
    w = shutil.which("kopia")
    if w:
        c.append(w)
    return c


def default_kopiaui_candidates():
    return [os.path.join(LOCALAPPDATA, "Programs", "KopiaUI", "KopiaUI.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "KopiaUI", "KopiaUI.exe")]


class Kopia:
    def __init__(self, store):
        self.store = store
        self.found_exe = None  # z działającego procesu serwera

    def exe(self):
        s = (self.store.settings.get("kopia_exe") or "").strip()
        if s and os.path.exists(s):
            return s
        if self.found_exe and os.path.exists(self.found_exe):
            return self.found_exe
        for p in default_kopia_candidates():
            if p and os.path.exists(p):
                return p
        return None

    def base_args(self, repo):
        return ["--config-file", repo.cfg_path, "--disable-file-logging", "--disable-content-log"]

    def run(self, repo, args, timeout=300):
        exe = self.exe()
        if not exe:
            raise RuntimeError(_("Nie znaleziono kopia.exe (zainstaluj KopiaUI albo wskaż plik w ustawieniach)."))
        cmd = [exe] + self.base_args(repo) + args
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(_("kopia nie odpowiedziała w {n} s", n=timeout))
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        return r.returncode, out, err

    def run_json(self, repo, args, timeout=300):
        rc, out, err = self.run(repo, args + ["--json"], timeout)
        if rc != 0:
            raise RuntimeError(clean_error(err) or _("kopia zakończyła się kodem {rc}", rc=rc))
        out = out.strip()
        if not out:
            return []
        return json.loads(out)


def clean_error(err):
    lines = [l.strip() for l in (err or "").splitlines() if l.strip()]
    # najciekawsze są linie z ERROR / error, inaczej ostatnia
    for l in reversed(lines):
        if "ERROR" in l or l.lower().startswith("error"):
            return re.sub(r"^.*?ERROR\s*", "", l)[:300]
    return lines[-1][:300] if lines else ""


# ============================================================================
#  Dane repozytorium (z CLI)
# ============================================================================

class Snap:
    __slots__ = ("start", "end", "incomplete", "failed", "ignored", "size", "files",
                 "noncached", "errors", "id", "root_obj")

    def __init__(self, m):
        self.id = m.get("id")
        self.root_obj = (m.get("rootEntry") or {}).get("obj")
        self.start = parse_ts(m.get("startTime"))
        self.end = parse_ts(m.get("endTime"))
        self.incomplete = m.get("incomplete") or ""
        st = m.get("stats") or {}
        summ = ((m.get("rootEntry") or {}).get("summ") or {})
        self.failed = int(summ.get("numFailed") or 0) or int(st.get("errorCount") or 0)
        self.ignored = int(summ.get("numIgnoredErrors") or 0) or int(st.get("ignoredErrorCount") or 0)
        self.size = summ.get("size", st.get("totalSize"))
        self.files = summ.get("files", st.get("fileCount"))
        self.noncached = st.get("nonCachedFiles")
        self.errors = [f"{e.get('path', '?')}: {e.get('error', '?')}" for e in (summ.get("errors") or [])][:20]


class SourceData:
    def __init__(self, host, user, path):
        self.host, self.user, self.path = host, user, path
        self.snaps = []
        self.sched = None
        self.ignore_identical = False
        self.run_missed = True
        self.policy_error = None
        self.changed_since_last = None   # dla ignoreIdentical: czy coś się zmieniło od ostatniego snapshotu
        self.changed_checked_for = None
        self.est_size = None             # szacowany rozmiar folderu (do postępu pierwszego snapshotu)

    @property
    def target(self):
        return f"{self.user}@{self.host}:{self.path}"

    @property
    def complete(self):
        return [s for s in self.snaps if not s.incomplete and s.start]

    @property
    def last_complete(self):
        c = self.complete
        return c[-1] if c else None


class RepoData:
    def __init__(self):
        self.error = None
        self.sources = []
        self.refreshed = None
        self.free = None
        self.total = None


def fetch_repo_data(kopia, repo, prev=None):
    d = RepoData()
    d.refreshed = now_local()
    if repo.storage_type == "filesystem" and repo.storage_path:
        if not os.path.isdir(repo.storage_path):
            d.error = _("Repozytorium niedostępne: {p} (dysk odłączony?)", p=repo.storage_path)
            return d
        try:
            du = shutil.disk_usage(repo.storage_path)
            d.free, d.total = du.free, du.total
        except Exception:
            pass
    try:
        snaps = kopia.run_json(repo, ["snapshot", "list", "--no-retention"])
        pols = kopia.run_json(repo, ["policy", "list"])
    except Exception as e:
        d.error = _("Nie można odczytać repozytorium: {e}", e=e)
        return d

    sources = {}

    def get_src(host, user, path):
        k = (host, user, path)
        if k not in sources:
            sources[k] = SourceData(host, user, path)
        return sources[k]

    for m in snaps or []:
        s = m.get("source") or {}
        if not s.get("path"):
            continue
        get_src(s.get("host", ""), s.get("userName", ""), s["path"]).snaps.append(Snap(m))
    for p in pols or []:
        t = p.get("target") or {}
        if t.get("path"):
            get_src(t.get("host", ""), t.get("userName", ""), t["path"])

    local = [s for s in sources.values()
             if (not repo.hostname or s.host == repo.hostname) and (not repo.username or s.user == repo.username)]
    d.sources = sorted(local or list(sources.values()), key=lambda s: s.path.lower())

    for src in d.sources:
        src.snaps.sort(key=lambda s: s.start or datetime.min.replace(tzinfo=timezone.utc))
        try:
            pol = kopia.run_json(repo, ["policy", "show", src.target])
            if isinstance(pol, list):
                pol = pol[0] if pol else {}
            src.sched = pol.get("scheduling") or {}
            ret = pol.get("retention") or {}
            src.ignore_identical = bool(ret.get("ignoreIdenticalSnapshots"))
            rm = src.sched.get("runMissed")
            src.run_missed = True if rm is None else bool(rm)
        except Exception as e:
            src.policy_error = str(e)
        # zachowaj poprzedni wynik kontroli zmian
        if prev:
            for ps in prev.sources:
                if ps.path == src.path:
                    src.changed_since_last = ps.changed_since_last
                    src.changed_checked_for = ps.changed_checked_for
                    src.est_size = ps.est_size
    return d


def folder_size(path, max_entries=3_000_000, max_seconds=90):
    """Rozmiar folderu (do szacowania postępu pierwszego snapshotu). None = nie udało się."""
    if not os.path.isdir(path):
        return None
    total, n, t0 = 0, 0, time.monotonic()
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    n += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
        if n > max_entries or time.monotonic() - t0 > max_seconds:
            return None
    return total


def looks_local(path):
    return bool(re.match(r"^[A-Za-z]:[\\/]", path or "")) or (path or "").startswith("\\\\")


def changed_since(path, since, max_entries=400_000, max_seconds=30):
    """Czy w folderze coś zmieniło się po `since` (mtime plików i folderów). None = nie wiadomo."""
    if not since or not os.path.isdir(path):
        return None
    ts = since.timestamp()
    t0 = time.monotonic()
    n = 0
    stack = [path]
    try:
        if os.stat(path).st_mtime > ts:
            return True
    except OSError:
        return None
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    n += 1
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if st.st_mtime > ts:
                        return True
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
        except OSError:
            continue
        if n > max_entries or time.monotonic() - t0 > max_seconds:
            return None
    return False


# ============================================================================
#  Procesy KopiaUI / serwerów kopia i ich logi
# ============================================================================

class ServerProc:
    def __init__(self, pid, cfg_key, started, exe, log_dir=None):
        self.log_dir = log_dir            # cli-logs tego procesu (KopiaUI portable podaje własny --log-dir)
        self.pid = pid
        self.cfg_key = cfg_key
        self.started = started
        self.exe = exe
        self.prev_io = None
        self.prev_t = None
        self.read_rate = 0.0
        self.write_rate = 0.0
        self.busy_since = None
        self.last_busy = None
        self.read_total = 0


class LogEvents:
    """Zdarzenia z logu serwera kopia dla jednego repozytorium."""

    def __init__(self):
        self.snap_running = False
        self.snap_started = None
        self.snap_read_base = None        # read_bytes procesu w chwili startu snapshotu
        self.last_end = None
        self.last_end_kind = None         # created / identical / error
        self.last_identical = None
        self.last_error = None
        self.last_error_msg = None
        self.maint_error = None
        self.maint_error_msg = None
        self.end_times = []               # czasy rozpoznanych zakończeń snapshotów (tylko w pamięci)
        self.log_files = None             # czy znaleziono pliki logu procesu (None = jeszcze nie sprawdzano)

    def to_dict(self):
        return {"last_identical": to_iso(self.last_identical), "last_error": to_iso(self.last_error),
                "last_error_msg": self.last_error_msg, "maint_error": to_iso(self.maint_error),
                "maint_error_msg": self.maint_error_msg, "last_end": to_iso(self.last_end),
                "last_end_kind": self.last_end_kind}

    @classmethod
    def from_dict(cls, d):
        e = cls()
        e.last_identical = from_iso(d.get("last_identical"))
        e.last_error = from_iso(d.get("last_error"))
        e.last_error_msg = d.get("last_error_msg")
        e.maint_error = from_iso(d.get("maint_error"))
        e.maint_error_msg = d.get("maint_error_msg")
        e.last_end = from_iso(d.get("last_end"))
        e.last_end_kind = d.get("last_end_kind")
        return e


_RE_SNAPSHOTTING = re.compile(r"(?<!not )\bsnapshotting(?:\s*\{|\s*$)")


def parse_log_line(line):
    """Zwraca (czas, rodzaj, tekst) albo None. Rodzaje: start, created, identical, error, maint_error."""
    line = line.rstrip("\r\n")
    if not line or not line[0].isdigit():
        return None
    sp = line.find(" ")
    if sp < 0:
        return None
    ts = parse_ts(line[:sp])
    if not ts:
        return None
    rest = line[sp + 1:]
    if "Not saving snapshot because no files have been changed" in rest:
        return ts, "identical", ""
    i = rest.find("snapshot error:")
    if i >= 0:
        return ts, "error", rest[i + len("snapshot error:"):].strip()[:400]
    if "created snapshot " in rest and "Created snapshot" not in rest:
        return ts, "created", ""
    if "maintenance task failed" in rest:
        m = re.search(r'"err"\s*:\s*"([^"]*)"', rest)
        return ts, "maint_error", (m.group(1) if m else rest)[:300]
    if _RE_SNAPSHOTTING.search(rest) and "not snapshotting" not in rest:
        return ts, "start", ""
    return None


class LogTail:
    def __init__(self):
        self.offsets = {}   # plik -> (pozycja, niedokończona linia)

    def has_files(self, pid, log_dir=None):
        return bool(glob.glob(os.path.join(log_dir or KOPIA_LOG_DIR, f"kopia-*-{pid}-server-start*.log")))

    def read_new(self, pid, log_dir=None):
        pattern = os.path.join(log_dir or KOPIA_LOG_DIR, f"kopia-*-{pid}-server-start*.log")
        files = glob.glob(pattern)

        def seg(f):
            m = re.search(r"\.(\d+)\.log$", f)
            return int(m.group(1)) if m else -1

        out = []
        for f in sorted(files, key=seg):
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            pos, partial = self.offsets.get(f, (0, ""))
            if size < pos:
                pos, partial = 0, ""
            if size == pos:
                continue
            try:
                with open(f, "rb") as fh:
                    fh.seek(pos)
                    data = fh.read(size - pos)
            except OSError:
                continue
            text = partial + data.decode("utf-8", "replace")
            lines = text.split("\n")
            partial = lines.pop()  # ostatnia może być niepełna
            self.offsets[f] = (size, partial)
            for ln in lines:
                ev = parse_log_line(ln)
                if ev:
                    out.append(ev)
        return out


# ============================================================================
#  Ocena stanu
# ============================================================================

def suspicious_changes(src, now, cfg, acked=(), days=7):
    """Pary kolejnych snapshotów z ostatnich dni, w których zmieniło/zniknęło podejrzanie dużo plików."""
    if not cfg or not cfg.get("enabled", True):
        return []
    pct_lim = float(cfg.get("percent", 30))
    min_files = int(cfg.get("min_files", 50))
    comp = src.complete
    out = []
    for prev, cur in zip(comp, comp[1:]):
        if not cur.start or now - cur.start > timedelta(days=days) or cur.id in acked:
            continue
        base = prev.files or 0
        if base < 20 or cur.noncached is None or cur.files is None:
            continue
        added = max(0, cur.files - base)
        modified = max(0, int(cur.noncached) - added)
        deleted = max(0, base - cur.files)
        lim = max(min_files, base * pct_lim / 100.0)
        if modified >= lim or deleted >= lim:
            out.append({"id": cur.id, "time": cur.start, "prev": prev.start, "base": base,
                        "modified": modified, "deleted": deleted,
                        "pct_mod": modified * 100.0 / base, "pct_del": deleted * 100.0 / base})
    return out


class Status:
    def __init__(self, level, text, running=False, detail=None):
        self.level = level
        self.text = text
        self.running = running
        self.detail = detail or []
        self.alarms = []
        self.progress = None   # dla dymku: {"kind","pct","done","total","rate","since"}


def eval_source(src, ev, proc, kopiaui_running, now, server_age=None, alarm_cfg=None, acked=()):
    """Stan jednego folderu + alarm podejrzanych zmian."""
    st = _eval_source(src, ev, proc, kopiaui_running, now, server_age)
    alarms = suspicious_changes(src, now, alarm_cfg, acked)
    if alarms:
        a = alarms[-1]
        what = []
        if a["modified"]:
            what.append(_("zmienionych {m} z {b} plików ({p}%)", m=a['modified'], b=a['base'], p=f"{a['pct_mod']:.0f}"))
        if a["deleted"]:
            what.append(_("zniknęło {d} plików ({p}%)", d=a['deleted'], p=f"{a['pct_del']:.0f}"))
        msg = _("PODEJRZANIE DUŻO ZMIAN {w}: ", w=fmt_when(a['time'], now)) + ", ".join(what)
        lines = ["", _("⚠ Alarm zmian – sprawdź, czy to Ty (np. porządki, zmiana nazw) czy coś złego (np. zaszyfrowanie):")]
        for x in alarms:
            lines.append("   " + _("{w}: zmienione {m}, usunięte {d} (poprzednio {b} plików, snapshot {pw})",
                                   w=fmt_when(x['time'], now), m=x['modified'], d=x['deleted'], b=x['base'],
                                   pw=fmt_when(x['prev'], now)))
        lines.append("   " + _("Jeśli to Ty – kliknij „To ja – zatwierdź zmiany”."))
        if st.running:
            st.detail += lines
        else:
            st = Status(ERROR, msg, detail=st.detail + lines)
        st.alarms = alarms
    return st


def _eval_source(src, ev, proc, kopiaui_running, now, server_age=None):
    """Stan jednego folderu w repozytorium."""
    last = src.last_complete
    latest = src.snaps[-1] if src.snaps else None
    last_t = last.start if last else None
    det = [_("Folder: {p}", p=src.path), _("Harmonogram: {s}", s=describe_schedule(src.sched))]
    if src.ignore_identical:
        det.append(_("Identyczne snapshoty nie są zapisywane (Ignore Identical Snapshots)."))
    if src.policy_error:
        det.append(_("Nie udało się odczytać polityki: {e}", e=src.policy_error))
    if last:
        extra = ""
        if last.noncached is not None:
            extra = _(", nowe/zmienione pliki: {n}", n=last.noncached)
        det.append(_("Ostatni snapshot: {w}", w=fmt_when(last.start, now))
                   + (_(" (trwał {d})", d=fmt_duration(last.end - last.start)) if last.end else "")
                   + _(", rozmiar {s}, plików {f}", s=fmt_bytes(last.size), f=last.files) + extra)
    else:
        det.append(_("Brak zakończonego snapshotu."))

    if looks_local(src.path) and not os.path.isdir(src.path):
        return Status(ERROR, _("folder źródłowy niedostępny"), detail=det + [_("Nie ma folderu {p}.", p=src.path)])

    # --- trwa snapshot ---
    if ev and ev.snap_running and proc:
        txt = _("robi się kopia")
        if ev.snap_started:
            txt += _(" (od {t})", t=f"{ev.snap_started:%H:%M}")
        if proc.read_rate > 256 * 1024:
            txt += f", {fmt_rate(proc.read_rate)}"
        base = ev.snap_read_base
        if base is None and ev.snap_started and ev.snap_started - proc.started < timedelta(minutes=5):
            base = 0  # snapshot ruszył zaraz po starcie procesu – liczymy od zera
        prog = {"kind": "snapshot", "pct": None, "done": None, "total": None,
                "rate": proc.read_rate, "since": ev.snap_started}
        if base is not None:
            done_b = max(0, proc.read_total - base)
            prog["done"] = done_b
            total = None
            if not last:
                total = src.est_size or (latest.size if latest and latest.size else None)
            if total:
                pct = min(99.0, done_b * 100.0 / total)
                prog.update(pct=pct, total=total)
                txt += _(" – {d} z ~{t} ({p}%)", d=fmt_bytes(done_b), t=fmt_bytes(total), p=f"{pct:.0f}")
            elif done_b > 50 * 1024 ** 2:
                txt += _(" – przeczytano {d}", d=fmt_bytes(done_b))
        st = Status(INFO, txt, running=True, detail=det)
        st.progress = prog
        return st
    if proc and proc.busy_since and now - proc.busy_since > timedelta(seconds=10):
        st = Status(INFO, _("Kopia pracuje (odczyt {r}, zapis {w})", r=fmt_rate(proc.read_rate), w=fmt_rate(proc.write_rate)),
                    running=True, detail=det + [_("Proces KopiaUI intensywnie pracuje – kopia albo konserwacja repozytorium.")])
        st.progress = {"kind": "busy", "pct": None, "done": None, "total": None,
                       "rate": proc.read_rate + proc.write_rate, "since": proc.busy_since}
        return st

    sched = src.sched
    if sched is None and src.policy_error:
        return Status(WARN, _("nie udało się odczytać harmonogramu"), detail=det)

    due = last_due(sched, now, last_t)
    nxt = next_due(sched, now, last_t)
    if nxt:
        det.append(_("Następny planowany: {w}", w=fmt_when(nxt, now)))

    def snap_quality(base_level, base_text):
        if last and last.failed:
            return Status(ERROR, _("{b}, ale {n} plików się nie skopiowało", b=base_text, n=last.failed),
                          detail=det + [_("Błędy:")] + last.errors)
        if last and last.ignored:
            return Status(WARN, _("{b}, pominięto {n} błędów", b=base_text, n=last.ignored),
                          detail=det + [_("Pominięte błędy:")] + last.errors)
        if latest and latest.incomplete and last and latest.start and latest.start > last.start:
            return Status(WARN, _("{b}; późniejszy snapshot przerwany ({r})", b=base_text, r=latest.incomplete), detail=det)
        return Status(base_level, base_text, detail=det)

    # --- tylko ręcznie / brak harmonogramu ---
    if not has_schedule(sched):
        if not last:
            return Status(WARN, _("ręczny – jeszcze nie było snapshotu"), detail=det)
        return snap_quality(OK, _("ręczny – ostatni {w}", w=fmt_when(last.start, now)))

    # harmonogram tylko co X (bez godzin) i brak snapshotu
    if due is None and not last:
        if proc and server_age is not None and server_age < timedelta(minutes=SERVER_WARMUP_MINUTES):
            return Status(WAIT, _("czeka na pierwszy snapshot"), detail=det)
        return Status(WARN, _("jeszcze nie było snapshotu"), detail=det)
    if due is None:
        return snap_quality(OK, f"OK – {fmt_when(last.start, now)}")

    det.append(_("Ostatni planowany termin: {w}", w=fmt_when(due, now)))
    tol = timedelta(seconds=5)
    done = last is not None and last.start >= due - tol
    identical = bool(src.ignore_identical and ev and ev.last_identical and ev.last_identical >= due - tol)

    if done:
        return snap_quality(OK, _("zrobiona {w}", w=fmt_when(last.start, now)))
    if identical:
        return Status(OK, _("bez zmian – sprawdzone {w}", w=fmt_when(ev.last_identical, now)),
                      detail=det + [_("Kopia nie zapisała nowego snapshotu, bo nic się nie zmieniło.")])

    # --- nie zrobione ---
    if ev and ev.last_error and ev.last_error >= due - tol:
        return Status(ERROR, _("błąd snapshotu: {e}", e=ev.last_error_msg),
                      detail=det + [f"{fmt_when(ev.last_error, now)}: {ev.last_error_msg}",
                                    _("KopiaUI ponawia nieudany snapshot co 5 minut.")])
    if not kopiaui_running:
        return Status(ERROR, _("pominięta ({w}) – KopiaUI nie działa", w=fmt_when(due, now)),
                      detail=det + [_("Harmonogram działa tylko, gdy KopiaUI jest uruchomiona.")])
    if not proc:
        return Status(ERROR, _("pominięta ({w}) – repozytorium nie jest połączone w KopiaUI", w=fmt_when(due, now)),
                      detail=det)
    if now - due < timedelta(minutes=GRACE_MINUTES) or (
            server_age is not None and server_age < timedelta(minutes=SERVER_WARMUP_MINUTES)):
        return Status(WAIT, _("czeka na start (plan {w})", w=fmt_when(due, now)), detail=det)
    if src.ignore_identical and last:
        if src.changed_since_last is False and src.changed_checked_for == to_iso(last.start):
            return Status(OK, _("bez zmian od {w}", w=fmt_when(last.start, now)),
                          detail=det + [_("W folderze nic się nie zmieniło, więc Kopia nie zapisuje nowego snapshotu.")])
    if src.run_missed and nxt and nxt - now <= timedelta(minutes=30) and due < nxt:
        return Status(WAIT, _("zaległa – Kopia zrobi ją o {t}", t=f"{nxt:%H:%M}"), detail=det)
    if not src.run_missed:
        return Status(ERROR, _("przegapiona ({w}), nadrabianie wyłączone – następna {n}", w=fmt_when(due, now), n=fmt_when(nxt, now)),
                      detail=det)
    return Status(ERROR, _("nie wykonano (plan {w})", w=fmt_when(due, now)), detail=det)


def low_space(data):
    if not data or data.free is None or not data.total:
        return False
    return data.free * 100 / data.total < LOW_SPACE_PCT or data.free < LOW_SPACE_BYTES


def eval_repo(repo, data, ev, proc, kopiaui_running, now, verify_rec, verify_running, alarm_cfg=None, acked=()):
    """Łączny stan repozytorium (najgorszy z folderów + sprawdzanie + miejsce)."""
    detail = [_("Repozytorium: {p}", p=repo.storage_path or repo.id), _("Plik konfiguracji: {p}", p=repo.cfg_path)]
    if verify_running:
        vr = verify_running
        pct = f" {vr['pct']:.0f}%" if vr.get("pct") is not None else ""
        st = Status(INFO, _("sprawdzanie {k}", k=vr['kind_name']) + (f":{pct}" if pct else " – start"), running=True, detail=detail)
        st.progress = {"kind": "verify", "label": vr["kind_name"], "pct": vr.get("pct"), "done": vr.get("read"),
                       "total": None, "rate": None, "since": vr.get("started")}
        return st, []
    if data is None:
        return Status(INFO, _("wczytywanie…"), detail=detail), []
    if data.error:
        return Status(ERROR, data.error, detail=detail), []
    if not data.sources:
        if ev and ev.snap_running and proc:
            st = Status(INFO, _("robi się kopia") + (_(" (od {t})", t=f"{ev.snap_started:%H:%M}") if ev.snap_started else ""),
                        running=True, detail=detail)
            st.progress = {"kind": "snapshot", "pct": None, "done": proc.read_total if ev.snap_started and ev.snap_started - proc.started < timedelta(minutes=5) else None,
                           "total": None, "rate": proc.read_rate, "since": ev.snap_started}
            return st, []
        return Status(WARN, _("brak folderów – dodaj snapshot w KopiaUI"), detail=detail), []
    server_age = (now - proc.started) if proc else None
    srcs = [(s, eval_source(s, ev, proc, kopiaui_running, now, server_age, alarm_cfg, acked)) for s in data.sources]
    worst = max(srcs, key=lambda x: (x[1].running, x[1].level))[1]
    level = max(s.level for _src, s in srcs)
    running = any(s.running for _src, s in srcs)
    if len(srcs) == 1:
        text = srcs[0][1].text
    else:
        bad = [f"{os.path.basename(s.path) or s.path}: {st.text}" for s, st in srcs if st.level >= WARN or st.running]
        text = "; ".join(bad) if bad else worst.text
    # sprawdzanie
    q = (verify_rec or {}).get("quick") or {}
    f = (verify_rec or {}).get("full") or {}
    last_v = max([r for r in (q, f) if r.get("end")], key=lambda r: r["end"], default=None)
    if last_v and not last_v.get("ok") and not last_v.get("cancelled"):
        vmsg = _("sprawdzanie wykryło problem ({w})", w=fmt_when(from_iso(last_v['end']), now))
        text = vmsg + ("; " + text if level >= WARN or running else "")
        level = ERROR
    # miejsce na dysku repozytorium – pokazywane w nagłówku i w kolumnie „Wolne”
    low = low_space(data)
    if low:
        detail.append(_("Mało miejsca na dysku repozytorium: wolne {f} z {t}.", f=fmt_bytes(data.free), t=fmt_bytes(data.total)))
    if ev and ev.maint_error and now - ev.maint_error < timedelta(days=2):
        detail.append(_("Błąd konserwacji {w}: {e}", w=fmt_when(ev.maint_error, now), e=ev.maint_error_msg))
        level = max(level, WARN)
    st = Status(level, text, running=running, detail=detail)
    st.alarms = [a for _src, s in srcs for a in s.alarms]
    st.progress = next((s.progress for _src, s in srcs if s.running and s.progress), None)
    return st, srcs


# ============================================================================
#  Sprawdzanie (kopia snapshot verify)
# ============================================================================

class VerifyManager:
    def __init__(self, app):
        self.app = app
        self.q = []
        self.lock = threading.Lock()
        self.current = None    # {"repo_id","kind","kind_name","pct","started","proc"}
        self.cancel_flag = False
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="verify").start()

    def enqueue(self, repo_id, kind, percent=None, auto=False):
        with self.lock:
            if self.current and self.current["repo_id"] == repo_id and self.current["kind"] == kind:
                return
            if any(j["repo_id"] == repo_id and j["kind"] == kind for j in self.q):
                return
            self.q.append({"repo_id": repo_id, "kind": kind, "percent": percent, "auto": auto})
        self.wake.set()

    def queued(self):
        with self.lock:
            return list(self.q)

    def cancel_all(self):
        with self.lock:
            self.q.clear()
            cur = self.current
        if cur and cur.get("proc"):
            self.cancel_flag = True
            try:
                cur["proc"].terminate()
            except Exception:
                pass

    def running_for(self, repo_id):
        cur = self.current
        if cur and cur["repo_id"] == repo_id:
            return cur
        return None

    def _loop(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            while True:
                with self.lock:
                    job = None
                    for j in self.q:
                        if not self.app.repo_busy(j["repo_id"]):
                            job = j
                            break
                    if job:
                        self.q.remove(job)
                if not job:
                    break
                try:
                    self._run(job)
                except Exception as e:
                    app_log(f"verify error: {e}")

    def _run(self, job):
        app = self.app
        repo = app.repo_by_id(job["repo_id"])
        if not repo:
            return
        kind = job["kind"]
        pct = 100 if kind == "full" else (job["percent"] or app.store.settings["verify"]["percent"])
        kind_name = _("pełne") if kind == "full" else f"{pct}%"
        rec = {"start": to_iso(now_local()), "percent": pct, "auto": job["auto"]}
        exe = app.kopia.exe()
        if not exe:
            rec.update(end=to_iso(now_local()), ok=False, error=_("Nie znaleziono kopia.exe"))
            self._save(repo, kind, rec)
            return
        if repo.storage_type == "filesystem" and repo.storage_path and not os.path.isdir(repo.storage_path):
            rec.update(end=to_iso(now_local()), ok=False, error=_("Repozytorium niedostępne: {p}", p=repo.storage_path))
            self._save(repo, kind, rec)
            return
        cmd = [exe] + app.kopia.base_args(repo) + [
            "snapshot", "verify", f"--verify-files-percent={pct}", "--max-errors=50", "--json"]
        app_log(f"verify start {repo.name} {kind_name}")
        self.cancel_flag = False
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
        cur = {"repo_id": repo.id, "kind": kind, "kind_name": kind_name, "pct": None,
               "started": now_local(), "proc": proc, "read": 0}
        self.current = cur
        app.request_ui_update()
        err_lines = []

        def read_err():
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("{"):
                    try:
                        st = json.loads(line)
                        exp = st.get("expectedTotalBytes") or 0
                        done = st.get("processedBytes") or 0
                        if exp:
                            cur["pct"] = min(100.0, done * 100.0 / exp)
                        cur["read"] = st.get("readBytes") or 0
                        continue
                    except Exception:
                        pass
                if line:
                    err_lines.append(line)
                    del err_lines[:-200]

        t = threading.Thread(target=read_err, daemon=True)
        t.start()
        out = proc.stdout.read().decode("utf-8", "replace")
        proc.wait()
        t.join(5)
        result = {}
        try:
            result = json.loads(out.strip()) if out.strip() else {}
        except Exception:
            pass
        stats = result.get("stats") or {}
        rec.update(end=to_iso(now_local()),
                   errors=int(result.get("errorCount") or 0),
                   error_strings=(result.get("errorStrings") or [])[:20],
                   read_files=stats.get("readFileCount"), read_bytes=stats.get("readBytes"),
                   objects=stats.get("processedObjectCount"))
        if self.cancel_flag:
            rec.update(ok=False, cancelled=True)
        elif proc.returncode == 0 and rec["errors"] == 0:
            rec.update(ok=True)
        else:
            rec.update(ok=False)
            if not rec["error_strings"]:
                rec["error"] = clean_error("\n".join(err_lines)) or _("kod wyjścia {c}", c=proc.returncode)
        self.current = None
        self._save(repo, kind, rec)
        app_log(f"verify end {repo.name} {kind_name}: {rec}")
        app.on_verify_finished(repo, kind, rec)

    def _save(self, repo, kind, rec):
        st = self.app.store
        with st.lock:
            v = st.state["verify"].setdefault(repo.id, {})
            v[kind] = rec
            hist = v.setdefault("history", [])
            hist.append({"kind": kind, **{k: rec.get(k) for k in ("start", "end", "ok", "cancelled", "errors", "percent")}})
            del hist[:-30]
        st.save_state()
        self.app.request_ui_update()


# ============================================================================
#  Montowanie snapshotów (kopia mount -> WebDAV + net use, litera dysku)
# ============================================================================

class MountManager:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.items = []   # dict: id, repo_id, name, desc, proc, drive, state, error

    def list(self):
        with self.lock:
            return [dict(m) for m in self.items]

    def mount(self, repo, snap, src_path, on_ready=None, on_error=None):
        exe = self.app.kopia.exe()
        if not exe:
            if on_error:
                on_error(_("Nie znaleziono kopia.exe."))
            return
        if not snap.root_obj:
            if on_error:
                on_error(_("Ten snapshot nie ma identyfikatora katalogu."))
            return
        cmd = [exe] + self.app.kopia.base_args(repo) + ["mount", snap.root_obj, "*"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
        desc = f"{os.path.basename(src_path.rstrip(chr(92) + '/')) or src_path} · {fmt_when(snap.start)}"
        m = {"id": f"{repo.id}:{snap.id}:{time.time()}", "repo_id": repo.id, "name": repo.name, "desc": desc,
             "proc": proc, "drive": None, "state": "montowanie", "error": None}
        with self.lock:
            self.items.append(m)
        self.app.request_ui_update()

        def reader():
            out = []
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                out.append(line)
                del out[:-50]
                mm = re.search(r"Mounted '.*?' on (\S+)", line)
                if mm:
                    m["drive"] = mm.group(1).rstrip(".")
                    m["state"] = "zamontowany"
                    self.app.request_ui_update()
                    if on_ready:
                        on_ready(m["drive"])
            proc.wait()
            if m["state"] != "zamontowany" and m["state"] != "odmontowywanie":
                m["error"] = clean_error("\n".join(out)) or _("kopia mount zakończyła się kodem {c}", c=proc.returncode)
                if on_error:
                    on_error(m["error"])
            with self.lock:
                if m in self.items:
                    self.items.remove(m)
            if m.get("drive"):
                _net_use_delete(m["drive"])
            self.app.request_ui_update()

        threading.Thread(target=reader, daemon=True, name="mount").start()

    def unmount(self, mid):
        with self.lock:
            m = next((x for x in self.items if x["id"] == mid), None)
        if not m:
            return
        m["state"] = "odmontowywanie"
        p = m["proc"]
        try:
            p.terminate()
            p.wait(5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        if m.get("drive"):
            _net_use_delete(m["drive"])
        self.app.request_ui_update()

    def unmount_all(self):
        for m in self.list():
            self.unmount(m["id"])


def _net_use_delete(drive):
    """Po zabiciu procesu kopia mount usuń mapowanie dysku (inaczej zostaje martwa litera)."""
    if not IS_WIN or not drive:
        return
    try:
        subprocess.run(["net", "use", drive, "/delete", "/y"], capture_output=True, timeout=20,
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


# ============================================================================
#  Rdzeń aplikacji (bez GUI)
# ============================================================================

class Core:
    def __init__(self):
        self.store = Store()
        self.kopia = Kopia(self.store)
        self.lock = threading.RLock()
        self.repos = []                 # [RepoInfo]
        self.data = {}                  # repo_id -> RepoData
        self.events = {}                # repo_id -> LogEvents
        self.procs = {}                 # cfg_key -> ServerProc
        self.kopiaui_running = False
        self.kopiaui_exe = None
        self.kopiaui_since = None
        self.tail = LogTail()
        self.refresh_pending = set()
        self.refresh_all_at = 0
        self.refresh_wake = threading.Event()
        self.refreshing = None
        self.ui_dirty = threading.Event()
        self.listeners = []
        for rid, d in self.store.state.get("events", {}).items():
            self.events[rid] = LogEvents.from_dict(d)
        self.verify = VerifyManager(self)
        self.mounts = MountManager(self)
        self.app_start = now_local()
        self.extra_cfg_dirs = set()        # dodatkowe katalogi configów (KopiaUI portable)
        self._rediscover_now = False
        self.kopia_version = None          # (major, minor, patch, tekst)
        self._version_for_exe = None
        self._due_refresh_done = {}

    # --- pomocnicze ---
    def repo_by_id(self, rid):
        with self.lock:
            for r in self.repos:
                if r.id == rid:
                    return r
        return None

    def repo_busy(self, rid):
        r = self.repo_by_id(rid)
        if not r:
            return False
        ev = self.events.get(rid)
        return bool(ev and ev.snap_running and self.procs.get(r.key))

    def request_ui_update(self):
        self.ui_dirty.set()

    def request_refresh(self, rid=None):
        with self.lock:
            if rid:
                self.refresh_pending.add(rid)
            else:
                self.refresh_pending.update(r.id for r in self.repos)
        self.refresh_wake.set()

    def start(self):
        threading.Thread(target=self._monitor_loop, daemon=True, name="monitor").start()
        threading.Thread(target=self._refresh_loop, daemon=True, name="refresh").start()

    # --- skanowanie procesów i logów ---
    def scan_processes(self):
        now = now_local()
        found_ui = False
        ui_exe = None
        servers = {}
        cfg_dirs = set()
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                name = (p.info.get("name") or "").lower()
                if name in ("kopiaui.exe", "kopiaui", "kopia-ui"):
                    found_ui = True
                    try:
                        ui_exe = p.exe()
                    except Exception:
                        pass
                    continue
                if name not in ("kopia.exe", "kopia"):
                    continue
                cl = p.info.get("cmdline") or []
                if "server" not in cl or "start" not in cl:
                    continue
                cfg = None
                log_dir = None
                for i, a in enumerate(cl):
                    if a == "--config-file" and i + 1 < len(cl):
                        cfg = cl[i + 1]
                    elif a.startswith("--config-file="):
                        cfg = a.split("=", 1)[1]
                    elif a == "--log-dir" and i + 1 < len(cl):
                        log_dir = os.path.join(cl[i + 1], "cli-logs")
                    elif a.startswith("--log-dir="):
                        log_dir = os.path.join(a.split("=", 1)[1], "cli-logs")
                if not cfg:
                    cfg = os.path.join(KOPIA_CFG_DIR, "repository.config")
                key = norm(cfg)
                started = datetime.fromtimestamp(p.info.get("create_time") or time.time()).astimezone()
                sp = self.procs.get(key)
                if not sp or sp.pid != p.pid:
                    exe = None
                    try:
                        exe = p.exe()
                    except Exception:
                        pass
                    sp = ServerProc(p.pid, key, started, exe, log_dir)
                    cfg_dirs.add(os.path.dirname(os.path.abspath(cfg)))
                try:
                    io = p.io_counters()
                    t = time.monotonic()
                    rb = io.read_bytes
                    wb = io.write_bytes
                    if sp.prev_io is not None:
                        dt = max(0.001, t - sp.prev_t)
                        sp.read_rate = max(0.0, (rb - sp.prev_io[0]) / dt)
                        sp.write_rate = max(0.0, (wb - sp.prev_io[1]) / dt)
                    sp.prev_io, sp.prev_t = (rb, wb), t
                    sp.read_total = rb
                    if sp.read_rate + sp.write_rate > 2 * 1024 ** 2:
                        sp.busy_since = sp.busy_since or now
                        sp.last_busy = now
                    elif sp.last_busy and now - sp.last_busy > timedelta(seconds=8):
                        sp.busy_since = None
                except Exception:
                    pass
                servers[key] = sp
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        with self.lock:
            self.procs = servers
            if found_ui and not self.kopiaui_running:
                self.kopiaui_since = now
            self.kopiaui_running = found_ui
            if ui_exe:
                self.kopiaui_exe = ui_exe
            # KopiaUI portable: configi w folderze „repositories” obok KopiaUI.exe – zapamiętaj ten katalog
            if ui_exe:
                for cand in (os.path.join(os.path.dirname(ui_exe), "repositories"),
                             os.path.join(os.path.dirname(ui_exe), "..", "repositories")):
                    if os.path.isdir(cand):
                        cfg_dirs.add(os.path.normpath(cand))
            new_dirs = {norm(d) for d in cfg_dirs} - {norm(KOPIA_CFG_DIR)}
            if new_dirs - self.extra_cfg_dirs:
                self.extra_cfg_dirs |= new_dirs
                self._rediscover_now = True
            for sp in servers.values():
                if sp.exe and os.path.basename(sp.exe).lower().startswith("kopia"):
                    self.kopia.found_exe = sp.exe
                    break

    def scan_logs(self):
        changed = False
        with self.lock:
            procs = list(self.procs.values())
            repos = {r.key: r for r in self.repos}
        for sp in procs:
            repo = repos.get(sp.cfg_key)
            if not repo:
                continue
            ev = self.events.setdefault(repo.id, LogEvents())
            if not ev.log_files and (ev.log_files is None or now_local() - sp.started > timedelta(minutes=2)):
                ev.log_files = self.tail.has_files(sp.pid, sp.log_dir)
            for ts, kind, txt in self.tail.read_new(sp.pid, sp.log_dir):
                if ts < sp.started - timedelta(seconds=5):
                    continue
                changed = True
                if kind == "start":
                    ev.snap_running = True
                    ev.snap_started = ts
                    self.request_refresh(repo.id)
                    ev.snap_read_base = sp.read_total if (now_local() - ts) < timedelta(seconds=30) else None
                elif kind in ("created", "identical", "error"):
                    ev.snap_running = False
                    ev.last_end, ev.last_end_kind = ts, kind
                    ev.end_times.append(ts)
                    del ev.end_times[:-100]
                    if kind == "identical":
                        ev.last_identical = ts
                    if kind == "error":
                        ev.last_error, ev.last_error_msg = ts, txt
                    self.request_refresh(repo.id)
                elif kind == "maint_error":
                    ev.maint_error, ev.maint_error_msg = ts, txt
            # proces padł w trakcie snapshotu
        for rid, ev in self.events.items():
            r = self.repo_by_id(rid)
            if ev.snap_running and (not r or r.key not in self.procs):
                ev.snap_running = False
                changed = True
        if changed:
            with self.store.lock:
                self.store.state["events"] = {k: v.to_dict() for k, v in self.events.items()}
            self.store.save_state()
            self.request_ui_update()

    def rediscover(self):
        new = discover_repos()
        seen = {r.key for r in new}
        for d in sorted(self.extra_cfg_dirs):
            for r in discover_repos(d):
                if r.key not in seen:
                    seen.add(r.key)
                    if any(x.id == r.id for x in new):      # ta sama nazwa pliku w innym katalogu
                        r.id = f"{r.id}@{zlib.crc32(norm(d).encode()) % 100000}"
                    new.append(r)
        with self.lock:
            old_ids = {r.id for r in self.repos}
            self.repos = new
            added = [r.id for r in new if r.id not in old_ids]
            removed = old_ids - {r.id for r in new}
            for rid in removed:
                self.data.pop(rid, None)
        for rid in added:
            self.request_refresh(rid)
        if added or removed:
            self.request_ui_update()

    def check_kopia_version(self):
        exe = self.kopia.exe()
        if not exe or exe == self._version_for_exe:
            return
        self._version_for_exe = exe
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
            out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
            if m:
                self.kopia_version = (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(0))
                app_log(f"kopia version: {out.splitlines()[0] if out else '?'}")
        except Exception as e:
            app_log(f"kopia --version: {e!r}")

    def diagnostics(self, now=None):
        """Ostrzeżenia, że TrayKopia może pokazywać niepełne informacje (np. po aktualizacji Kopii)."""
        now = now or now_local()
        out = []
        # sama zmiana wersji Kopii nie jest problemem – ostrzegamy tylko, gdy coś faktycznie przestaje pasować
        with self.lock:
            repos = list(self.repos)
            procs = dict(self.procs)
        no_logs, mismatch = [], []
        for r in repos:
            proc = procs.get(r.key)
            ev = self.events.get(r.id)
            if not proc or not ev:
                continue
            if ev.log_files is False and now - proc.started > timedelta(minutes=3):
                no_logs.append(r.name)
                continue
            d = self.data.get(r.id)
            if not d or d.error:
                continue
            for src in d.sources:
                last = src.last_complete
                if not last or not last.start or not last.end:
                    continue
                # snapshot zrobiony, gdy TrayKopia i ten proces KopiaUI już działały – w logu musi być ślad
                if last.start < self.app_start or last.start < proc.started or now - last.end < timedelta(minutes=3):
                    continue
                lo, hi = last.start - timedelta(seconds=60), last.end + timedelta(minutes=3)
                if not any(lo <= t <= hi for t in ev.end_times):
                    mismatch.append(r.name)
                    break
        if no_logs:
            out.append(("nologs", _("Nie znaleziono logów KopiaUI dla: {r}. Postęp kopii i błędy snapshotów będą widoczne tylko częściowo.",
                                    r=", ".join(no_logs))))
        if mismatch:
            out.append(("logfmt", _("W logach KopiaUI nie ma rozpoznawalnego wpisu o ostatnim snapshocie: {r}. Możliwe, że po aktualizacji Kopii zmienił się format logów – postęp, błędy i „bez zmian” mogą być niedokładne. (Jeśli robiłeś snapshot z wiersza poleceń, zignoruj.)",
                                    r=", ".join(mismatch))))
        return out

    def _monitor_loop(self):
        last_disc = 0
        last_auto = 0
        while True:
            try:
                if time.monotonic() - last_disc > 30 or self._rediscover_now:
                    self._rediscover_now = False
                    self.rediscover()
                    last_disc = time.monotonic()
                self.scan_processes()
                self.scan_logs()
                self.check_kopia_version()
                if time.monotonic() - self.refresh_all_at > FULL_REFRESH_EVERY:
                    self.refresh_all_at = time.monotonic()
                    self.request_refresh()
                self._refresh_after_due_times()
                if time.monotonic() - last_auto > 30:
                    last_auto = time.monotonic()
                    self.check_auto_verify()
                self.request_ui_update()
            except Exception as e:
                app_log(f"monitor: {e!r}")
            time.sleep(PROC_SCAN_EVERY)

    def _refresh_after_due_times(self):
        """Po minięciu terminu snapshotu odśwież dane (żeby szybko pokazać ptaszek / problem)."""
        now = now_local()
        for r in list(self.repos):
            d = self.data.get(r.id)
            if not d or d.error:
                continue
            for s in d.sources:
                last = s.last_complete
                due = last_due(s.sched, now, last.start if last else None)
                if not due:
                    continue
                for delay in (GRACE_MINUTES + 1, 2):
                    mark = (r.id, s.path, to_iso(due), delay)
                    if now >= due + timedelta(minutes=delay) and mark not in self._due_refresh_done \
                            and now - due < timedelta(hours=6):
                        self._due_refresh_done[mark] = True
                        self.request_refresh(r.id)

    def _refresh_loop(self):
        while True:
            self.refresh_wake.wait(10)
            self.refresh_wake.clear()
            while True:
                with self.lock:
                    if not self.refresh_pending:
                        break
                    rid = sorted(self.refresh_pending)[0]
                    self.refresh_pending.discard(rid)
                repo = self.repo_by_id(rid)
                if not repo:
                    continue
                self.refreshing = rid
                self.request_ui_update()
                try:
                    d = fetch_repo_data(self.kopia, repo, self.data.get(rid))
                    self._check_identical_changes(repo, d)
                    for s in d.sources:
                        if not s.last_complete and s.est_size is None:
                            s.est_size = folder_size(s.path)
                except Exception as e:
                    d = RepoData()
                    d.error = _("Błąd odczytu: {e}", e=e)
                    d.refreshed = now_local()
                with self.lock:
                    self.data[rid] = d
                self.refreshing = None
                self.request_ui_update()

    def _check_identical_changes(self, repo, d):
        """Folder z Ignore Identical: czy coś się zmieniło od ostatniego snapshotu (gdy brak info z logów)."""
        if d.error:
            return
        now = now_local()
        ev = self.events.get(repo.id)
        for s in d.sources:
            last = s.last_complete
            if not s.ignore_identical or not last:
                continue
            due = last_due(s.sched, now, last.start)
            if not due or last.start >= due or (ev and ev.last_identical and ev.last_identical >= due):
                continue
            key = to_iso(last.start)
            if s.changed_checked_for == key and s.changed_since_last is False:
                continue
            s.changed_since_last = changed_since(s.path, last.start)
            s.changed_checked_for = key

    # --- automatyczne sprawdzanie ---
    def check_auto_verify(self):
        now = now_local()
        st = self.store.settings
        auto = self.store.state.setdefault("auto", {})
        v = st["verify"]
        if "quick_due" not in auto:
            auto["quick_due"] = to_iso(weekly_last_due(v.get("days") or [], v.get("time", "16:30"), now) or now)
            auto["full_due"] = to_iso(monthly_last_due(st["full"].get("day", 1), st["full"].get("time", "16:30"), now))
            self.store.save_state()
        if v.get("enabled") and v.get("days"):
            due = weekly_last_due(v["days"], v.get("time", "16:30"), now)
            if due and (from_iso(auto.get("quick_due")) or datetime.min.replace(tzinfo=timezone.utc)) < due:
                if v.get("catch_up", True) or now - due < timedelta(hours=2):
                    for r in list(self.repos):
                        self.verify.enqueue(r.id, "quick", v.get("percent", 5), auto=True)
                auto["quick_due"] = to_iso(due)
                self.store.save_state()
        f = st["full"]
        if f.get("enabled") and f.get("repos"):
            due = monthly_last_due(f.get("day", 1), f.get("time", "16:30"), now)
            if due and (from_iso(auto.get("full_due")) or datetime.min.replace(tzinfo=timezone.utc)) < due:
                if f.get("catch_up", True) or now - due < timedelta(hours=2):
                    ids = {r.id for r in self.repos}
                    for rid in f["repos"]:
                        if rid in ids:
                            self.verify.enqueue(rid, "full", 100, auto=True)
                auto["full_due"] = to_iso(due)
                self.store.save_state()

    def next_auto_verify(self):
        now = now_local()
        v = self.store.settings["verify"]
        f = self.store.settings["full"]
        q = weekly_next_due(v.get("days") or [], v.get("time", "16:30"), now) if v.get("enabled") else None
        fu = monthly_next_due(f.get("day", 1), f.get("time", "16:30"), now) if f.get("enabled") and f.get("repos") else None
        return q, fu

    def on_verify_finished(self, repo, kind, rec):
        for cb in self.listeners:
            try:
                cb("verify", repo, kind, rec)
            except Exception:
                pass

    # --- migawka stanu dla GUI ---
    def snapshot(self):
        now = now_local()
        with self.lock:
            repos = list(self.repos)
            kopiaui = self.kopiaui_running
            procs = dict(self.procs)
        rows = []
        for r in repos:
            ev = self.events.get(r.id)
            proc = procs.get(r.key)
            vrec = self.store.state["verify"].get(r.id)
            vrun = self.verify.running_for(r.id)
            st, srcs = eval_repo(r, self.data.get(r.id), ev, proc, kopiaui, now, vrec, vrun,
                                 self.store.settings.get("alarm"), set(self.store.state["acks"].get(r.id, [])))
            rows.append({"repo": r, "status": st, "sources": srcs, "data": self.data.get(r.id),
                         "verify": vrec or {}, "verify_running": vrun, "proc": proc, "events": ev})
        return {"now": now, "kopiaui": kopiaui, "rows": rows, "kopia_exe": self.kopia.exe(),
                "queued": self.verify.queued(), "refreshing": self.refreshing,
                "mounts": self.mounts.list(), "diag": self.diagnostics(now)}

    def ack_alarms(self, rid):
        """„To ja” – zatwierdź wszystkie bieżące alarmy zmian w repozytorium."""
        d = self.data.get(rid)
        if not d:
            return
        ids = set(self.store.state["acks"].get(rid, []))
        now = now_local()
        for s in d.sources:
            for a in suspicious_changes(s, now, self.store.settings.get("alarm"), ()):
                ids.add(a["id"])
        with self.store.lock:
            self.store.state["acks"][rid] = sorted(ids)[-200:]
        self.store.save_state()
        self.request_ui_update()


# ============================================================================
#  Autostart (rejestr Windows)
# ============================================================================

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if is_compiled() or getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.argv[0])}"'
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        cand = pyw[:-10] + "pythonw.exe"
        if os.path.exists(cand):
            pyw = cand
    return f'"{pyw}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# ============================================================================
#  Ikony
# ============================================================================

COLORS = {"ok": (46, 160, 67), "run": (43, 123, 214), "wait": (130, 130, 140), "warn": (230, 150, 20),
          "error": (215, 55, 55), "off": (120, 120, 120)}


ANIM_FRAMES = 36          # klatek na pełny obrót
ANIM_INTERVAL_MS = 50    # ~20 kl./s -> obrót w 1,8 s


def make_icon(kind, frame=0, badge=None, size=64):
    from PIL import Image
    big = _draw_icon(kind, frame, badge, size * 4)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def _draw_icon(kind, frame, badge, size):
    from PIL import Image, ImageDraw
    import math
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = COLORS.get(kind, COLORS["off"])
    d.ellipse((s * .03, s * .03, s * .97, s * .97), fill=col + (255,), outline=(255, 255, 255, 230), width=max(3, s // 22))
    w = max(4, s // 10)
    white = (255, 255, 255, 255)
    c = s / 2
    if kind == "ok":
        d.line([(s * .27, s * .52), (s * .44, s * .69), (s * .74, s * .34)], fill=white, width=w, joint="curve")
    elif kind == "error":
        d.line([(s * .32, s * .32), (s * .68, s * .68)], fill=white, width=w)
        d.line([(s * .68, s * .32), (s * .32, s * .68)], fill=white, width=w)
    elif kind == "warn":
        d.line([(c, s * .24), (c, s * .58)], fill=white, width=w)
        d.ellipse((c - w * .7, s * .68, c + w * .7, s * .68 + w * 1.4), fill=white)
    elif kind == "off":
        d.rectangle((s * .34, s * .3, s * .44, s * .7), fill=white)
        d.rectangle((s * .56, s * .3, s * .66, s * .7), fill=white)
    elif kind == "wait":
        r = s * .26
        d.ellipse((c - r, c - r, c + r, c + r), outline=white, width=max(3, w - 2))
        d.line([(c, c), (c, c - r * .7)], fill=white, width=max(3, w - 2))
        d.line([(c, c), (c + r * .55, c)], fill=white, width=max(3, w - 2))
    elif kind == "run":
        r = s * .27
        start = (frame * 360.0 / ANIM_FRAMES) % 360
        d.arc((c - r, c - r, c + r, c + r), start=start, end=start + 270, fill=white, width=w)
        a = math.radians(start + 270)
        x, y = c + r * math.cos(a), c + r * math.sin(a)
        ah = s * .13
        a1 = a + math.radians(90)
        tip = (x + ah * math.cos(a1), y + ah * math.sin(a1))
        d.polygon([(x + ah * .9 * math.cos(a), y + ah * .9 * math.sin(a)),
                   (x - ah * .9 * math.cos(a), y - ah * .9 * math.sin(a)), tip], fill=white)
    if badge:
        bc = COLORS[badge]
        br = s * .2
        d.ellipse((s - 2 * br - 1, 1, s - 1, 2 * br + 1), fill=bc + (255,), outline=(255, 255, 255, 255), width=max(2, s // 32))
    return img


# ============================================================================
#  Windows: szybka podmiana ikony w zasobniku i obsługa najechania myszą
# ============================================================================

class WinTray:
    """Omija pystray przy podmianie ikony (gotowe HICON-y = płynna animacja)
    i daje znać, gdy kursor jest nad ikoną (własny dymek zamiast migającego systemowego)."""

    WM_MOUSEMOVE, WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDOWN, WM_RBUTTONDOWN = 0x200, 0x202, 0x205, 0x201, 0x204

    def __init__(self, icon, on_hover, on_click, on_recreated):
        import ctypes
        from ctypes import wintypes
        from pystray._util import win32
        self.ct, self.wt, self.w32 = ctypes, wintypes, win32
        self.icon = icon
        self.handles = {}
        self.on_hover, self.on_click, self.on_recreated = on_hover, on_click, on_recreated
        u = ctypes.windll.user32
        self.small = u.GetSystemMetrics(49) or 16   # SM_CXSMICON
        h = icon._message_handlers
        orig_notify = h[win32.WM_NOTIFY]
        orig_created = h.get(win32.WM_TASKBARCREATED)

        def notify(wparam, lparam):
            msg = lparam & 0xFFFF
            if msg == self.WM_MOUSEMOVE:
                self.on_hover()
            elif msg in (self.WM_LBUTTONDOWN, self.WM_RBUTTONDOWN, self.WM_LBUTTONUP, self.WM_RBUTTONUP):
                self.on_click()
            return orig_notify(wparam, lparam)

        def created(wparam, lparam):
            r = orig_created(wparam, lparam) if orig_created else 0
            self.on_recreated()
            return r

        h[win32.WM_NOTIFY] = notify
        if orig_created:
            h[win32.WM_TASKBARCREATED] = created

        class NOTIFYICONIDENTIFIER(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("guidItem", ctypes.c_byte * 16)]
        self.NII = NOTIFYICONIDENTIFIER

    def hicon(self, key, pil_img):
        hd = self.handles.get(key)
        if hd:
            return hd
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), f"traykopia_{os.getpid()}.ico")
        sizes = sorted({16, 20, 24, 32, 40, 48, 64, self.small})
        pil_img.save(tmp, format="ICO", sizes=[(x, x) for x in sizes])
        u = self.ct.WinDLL("user32")   # osobna instancja – nie ruszamy typów używanych przez pystray
        u.LoadImageW.restype = self.wt.HANDLE
        u.LoadImageW.argtypes = (self.wt.HINSTANCE, self.wt.LPCWSTR, self.wt.UINT, self.ct.c_int, self.ct.c_int, self.wt.UINT)
        hd = u.LoadImageW(None, tmp, 1, self.small, self.small, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        try:
            os.remove(tmp)
        except OSError:
            pass
        if hd:
            self.handles[key] = hd
        return hd

    def set_icon(self, key, pil_img):
        hd = self.hicon(key, pil_img)
        if not hd or not self.icon._hwnd:
            return False
        w32 = self.w32
        w32.Shell_NotifyIcon(w32.NIM_MODIFY, w32.NOTIFYICONDATAW(
            cbSize=self.ct.sizeof(w32.NOTIFYICONDATAW), hWnd=self.icon._hwnd, uID=0,
            uFlags=w32.NIF_ICON, hIcon=hd))
        return True

    def icon_rect(self):
        try:
            nii = self.NII(cbSize=self.ct.sizeof(self.NII), hWnd=self.icon._hwnd, uID=0)
            r = self.wt.RECT()
            res = self.ct.windll.shell32.Shell_NotifyIconGetRect(self.ct.byref(nii), self.ct.byref(r))
            if res == 0 and r.right > r.left:
                return (r.left, r.top, r.right, r.bottom)
        except Exception:
            pass
        return None

    def cursor(self):
        pt = self.wt.POINT()
        self.ct.windll.user32.GetCursorPos(self.ct.byref(pt))
        return pt.x, pt.y


class TrayTip:
    """Własny dymek przy ikonie: czytelne „karty”, odświeżane na żywo, bez migania."""

    BG = "#ffffff"
    FG = "#1f1f1f"
    GREY = "#6b6b6b"
    BORDER = "#c9c9c9"
    TRACK = "#e4e8ef"
    COL = {"run": "#1f6fc5", "error": "#c42b2b", "warn": "#b86e00", "wait": "#6b6b75", "ok": "#1e8a3a"}
    WIDTH = 340

    def __init__(self, app):
        import ctypes
        tk = app.tk
        self.app = app
        self.tk = tk
        self.ct = ctypes
        w = tk.Toplevel(app.root)
        self.w = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(background=self.BORDER)
        self.inner = tk.Frame(w, background=self.BG, padx=14, pady=11)
        self.inner.pack(padx=1, pady=1)
        self.sig = None
        self.widgets = {}
        w.geometry("+-5000+-5000")
        w.update_idletasks()
        w.deiconify()
        w.update_idletasks()
        from ctypes import wintypes as wt
        # własna instancja user32 z poprawnymi typami (64-bit: uchwyty to wskaźniki, HWND_TOPMOST = -1 jako HWND)
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
        u.SetWindowPos.restype = wt.BOOL
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.ShowWindow.restype = wt.BOOL
        u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        u.GetAncestor.restype = wt.HWND
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        self.u = u
        self.HWND_TOPMOST = wt.HWND(-1)
        frame = int(w.wm_frame(), 16)
        self.hwnd = u.GetAncestor(frame, 2) or frame        # GA_ROOT
        ex = u.GetWindowLongW(self.hwnd, -20)
        u.SetWindowLongW(self.hwnd, -20, ex | 0x08000000 | 0x00000080 | 0x00000008)  # NOACTIVATE|TOOLWINDOW|TOPMOST
        u.ShowWindow(self.hwnd, 0)
        self.visible = False
        self.anchor_rect = None
        self.last_inside = 0
        self.last_move = None
        self.logged = False
        self.pos = None

    # --- budowa treści ---
    def _font(self, size=9, bold=False):
        return ("Segoe UI", size, "bold" if bold else "normal")

    def _lbl(self, parent, text="", size=9, bold=False, fg=None, **kw):
        return self.tk.Label(parent, text=text, font=self._font(size, bold), background=self.BG,
                             foreground=fg or self.FG, anchor="w", justify="left", **kw)

    def _build(self, model):
        tk = self.tk
        for c in self.inner.winfo_children():
            c.destroy()
        W = {}
        # nagłówek
        head = tk.Frame(self.inner, background=self.BG)
        head.pack(fill="x")
        W["dot"] = tk.Canvas(head, width=12, height=12, background=self.BG, highlightthickness=0)
        W["dot_item"] = W["dot"].create_oval(1, 1, 11, 11, fill=self.COL["ok"], outline="")
        W["dot"].pack(side="left", padx=(0, 7))
        W["state"] = self._lbl(head, size=10, bold=True)
        W["state"].pack(side="left")
        self._lbl(head, APP_NAME, size=8, fg=self.GREY).pack(side="right")
        # karty „w trakcie”
        for i, r in enumerate(model["running"]):
            card = tk.Frame(self.inner, background=self.BG)
            card.pack(fill="x", pady=(10, 0))
            row = tk.Frame(card, background=self.BG)
            row.pack(fill="x")
            W[f"r{i}_name"] = self._lbl(row, size=10, bold=True)
            W[f"r{i}_name"].pack(side="left")
            W[f"r{i}_pct"] = self._lbl(row, size=15, bold=True, fg=self.COL["run"])
            W[f"r{i}_pct"].pack(side="right")
            W[f"r{i}_what"] = self._lbl(card, size=8, fg=self.COL["run"])
            W[f"r{i}_what"].pack(fill="x")
            if r["pct"] is not None:
                c = tk.Canvas(card, width=self.WIDTH, height=8, background=self.BG, highlightthickness=0)
                c.pack(fill="x", pady=(4, 2))
                c.create_rectangle(0, 1, self.WIDTH, 7, fill=self.TRACK, outline="")
                W[f"r{i}_bar"] = (c, c.create_rectangle(0, 1, 0, 7, fill=self.COL["run"], outline=""))
            W[f"r{i}_info"] = self._lbl(card, size=9, fg=self.GREY)
            W[f"r{i}_info"].pack(fill="x")
        # problemy / oczekujące
        if model["issues"]:
            box = tk.Frame(self.inner, background=self.BG)
            box.pack(fill="x", pady=(10, 0))
            for i, it in enumerate(model["issues"]):
                row = tk.Frame(box, background=self.BG)
                row.pack(fill="x", pady=(0, 3))
                W[f"i{i}_sym"] = self._lbl(row, size=10, bold=True, width=2)
                W[f"i{i}_sym"].pack(side="left", anchor="n")
                col = tk.Frame(row, background=self.BG)
                col.pack(side="left", fill="x")
                W[f"i{i}_name"] = self._lbl(col, size=9, bold=True)
                W[f"i{i}_name"].pack(fill="x")
                W[f"i{i}_text"] = self._lbl(col, size=9, fg=self.GREY, wraplength=self.WIDTH - 30)
                W[f"i{i}_text"].pack(fill="x")
        # OK
        if model["ok"]:
            row = tk.Frame(self.inner, background=self.BG)
            row.pack(fill="x", pady=(10, 0))
            self._lbl(row, "✔", size=10, bold=True, fg=self.COL["ok"], width=2).pack(side="left", anchor="n")
            W["ok"] = self._lbl(row, size=9, wraplength=self.WIDTH - 30)
            W["ok"].pack(side="left", fill="x")
        # stopka
        sep = tk.Frame(self.inner, background="#ececec", height=1)
        sep.pack(fill="x", pady=(10, 6))
        W["foot"] = self._lbl(self.inner, size=8, fg=self.GREY, wraplength=self.WIDTH)
        W["foot"].pack(fill="x")
        W["spacer"] = tk.Frame(self.inner, background=self.BG, width=self.WIDTH, height=0)
        W["spacer"].pack()
        self.widgets = W

    @staticmethod
    def _set(widget, **kw):
        cur = {k: widget.cget(k) for k in kw}
        if any(str(cur[k]) != str(v) for k, v in kw.items()):
            widget.configure(**kw)

    def _fill(self, model):
        W = self.widgets
        self._set(W["state"], text=model["state"], foreground=self.COL[model["state_kind"]])
        W["dot"].itemconfigure(W["dot_item"], fill=self.COL[model["state_kind"]])
        for i, r in enumerate(model["running"]):
            self._set(W[f"r{i}_name"], text=r["name"])
            self._set(W[f"r{i}_pct"], text=(f"{r['pct']:.0f}%" if r["pct"] is not None else ""))
            self._set(W[f"r{i}_what"], text=r["what"])
            self._set(W[f"r{i}_info"], text=r["info"])
            if f"r{i}_bar" in W and r["pct"] is not None:
                c, item = W[f"r{i}_bar"]
                c.coords(item, 0, 1, max(4, self.WIDTH * r["pct"] / 100.0), 7)
        for i, it in enumerate(model["issues"]):
            self._set(W[f"i{i}_sym"], text=it["sym"], foreground=self.COL[it["kind"]])
            self._set(W[f"i{i}_name"], text=it["name"], foreground=self.COL[it["kind"]])
            self._set(W[f"i{i}_text"], text=it["text"])
        if "ok" in W:
            self._set(W["ok"], text=model["ok"])
        self._set(W["foot"], text=model["foot"])

    def update_text(self):
        model = self.app.tip_model()
        sig = (tuple((r["key"], r["pct"] is not None) for r in model["running"]),
               len(model["issues"]), bool(model["ok"]))
        if sig != self.sig:
            self.sig = sig
            self._build(model)
        self._fill(model)
        if self.visible:
            self._place(show=False)

    # --- pokazywanie / chowanie ---
    def hover(self, rect, cursor):
        now = time.monotonic()
        self.last_inside = now
        self.last_move = cursor
        cx, cy = cursor
        if rect and rect[0] - 4 <= cx <= rect[2] + 4 and rect[1] - 4 <= cy <= rect[3] + 4:
            self.anchor_rect = rect
        else:
            self.anchor_rect = (cx - 12, cy - 12, cx + 12, cy + 12)
        if not self.visible:
            self.update_text()
            self._place(show=True)

    def hide(self):
        if self.visible:
            self.u.ShowWindow(self.hwnd, 0)
            self.visible = False

    def _place(self, show):
        self.w.update_idletasks()
        tw, th = self.w.winfo_reqwidth(), self.w.winfo_reqheight()
        l, t, r, b = self.anchor_rect
        sw, sh = self.w.winfo_screenwidth(), self.w.winfo_screenheight()
        x = int((l + r) / 2 - tw / 2)
        y = t - th - 10 if t - th - 10 > 0 else b + 10       # pasek zadań na dole albo na górze
        if l < 120 and t > 120 and b < sh - 120:               # pasek z boku
            x, y = r + 10, int((t + b) / 2 - th / 2)
        elif r > sw - 120 and t > 120 and b < sh - 120:
            x, y = l - tw - 10, int((t + b) / 2 - th / 2)
        x = max(4, min(x, sw - tw - 4))
        y = max(4, min(y, sh - th - 4))
        if (x, y) != self.pos:
            self.w.geometry(f"+{x}+{y}")
            self.pos = (x, y)
        if show:
            ok = self.u.SetWindowPos(self.hwnd, self.HWND_TOPMOST, x, y, 0, 0, 0x0001 | 0x0010 | 0x0040)
            self.u.ShowWindow(self.hwnd, 4)
            if not ok and not self.logged:
                self.logged = True
                app_log(f"tip: SetWindowPos nie zadziałało, błąd {self.ct.get_last_error()}")
            self.visible = True

    def poll(self, tray):
        """Wołane co ~80 ms, gdy dymek jest widoczny."""
        if not self.visible:
            return
        cx, cy = tray.cursor()
        inside = False
        if self.last_move and abs(cx - self.last_move[0]) <= 3 and abs(cy - self.last_move[1]) <= 3:
            inside = True
        rect = tray.icon_rect()
        if rect and rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]:
            inside = True
        if inside:
            self.last_inside = time.monotonic()
        elif time.monotonic() - self.last_inside > 0.35:
            self.hide()


# ============================================================================
#  GUI
# ============================================================================

def level_color(level, running=False):
    if running:
        return "#1f6fc5"
    return {OK: "#1e8a3a", INFO: "#555555", WAIT: "#6b6b75", WARN: "#b86e00", ERROR: "#c42b2b"}.get(level, "#333")


def level_symbol(level, running=False):
    if running:
        return "⟳"
    return {OK: "✔", INFO: "•", WAIT: "⏳", WARN: "⚠", ERROR: "✖"}.get(level, "•")


def verify_cell(rec, now, running=None, queued=False):
    if running:
        p = f" {running['pct']:.0f}%" if running.get("pct") is not None else ""
        return "⟳ " + _("trwa") + p
    if not rec or not rec.get("end"):
        return _("w kolejce") if queued else "—"
    end = from_iso(rec["end"])
    if rec.get("cancelled"):
        return _("przerwane {w}", w=fmt_when(end, now))
    if rec.get("ok"):
        return f"✔ {fmt_when(end, now)}"
    n = rec.get("errors") or 0
    return f"✖ {fmt_when(end, now)}" + (_(" ({n} bł.)", n=n) if n else "")


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.ttk = ttk
        self.core = Core()
        self.core.listeners.append(self._on_core_event)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        ico = resource_path("traykopia.ico")
        if ico and IS_WIN:
            try:
                self.root.iconbitmap(default=ico)
            except Exception:
                pass
        self.uiq = queue.Queue()
        self.status_win = None
        self.settings_win = None
        self.about_win = None
        self.icon = None
        self.anim_frame = 0
        self.last_icon_key = None
        self.notified = {}
        self.kopiaui_off_since = None
        self.snap = None
        self.icons = {}
        self.mount_win = None
        self.mute_notify_once = False
        LANG["code"] = self.core.store.settings.get("language", "en") if self.core.store.settings.get("language") in ("en", "pl") else "en"

    # --- uruchomienie ---
    def run(self):
        import pystray
        self.pystray = pystray
        self.core.start()
        title = "" if IS_WIN else f"{APP_NAME} – uruchamianie…"
        self.icon = pystray.Icon(APP_NAME, make_icon("wait"), title, menu=self._menu())
        self.wintray = None
        self.tip = None
        if IS_WIN:
            try:
                self.wintray = WinTray(self.icon, on_hover=lambda: self.ui(self._on_hover),
                                       on_click=lambda: self.ui(self._on_tray_click),
                                       on_recreated=lambda: self.ui(self._on_tray_recreated))
                self.tip = TrayTip(self)
            except Exception:
                import traceback
                app_log("wintray: " + traceback.format_exc())
                self.wintray = None
                self.tip = None
                self.icon.title = APP_NAME
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()
        self.root.after(30, self._pump)
        self.root.after(200, self._tick)
        self.root.after(400, self._anim)
        self.root.after(80, self._tip_poll)
        self.root.mainloop()

    def _on_hover(self):
        if self.tip and self.wintray:
            self.tip.hover(self.wintray.icon_rect(), self.wintray.cursor())

    def _on_tray_click(self):
        if self.tip:
            self.tip.hide()

    def _on_tray_recreated(self):
        self.last_icon_key = None
        self._update_tray()

    def _tip_poll(self):
        try:
            if self.tip and self.tip.visible:
                self.tip.update_text()
                self.tip.poll(self.wintray)
        except Exception:
            import traceback
            app_log("tip: " + traceback.format_exc())
        self.root.after(80, self._tip_poll)

    def tip_model(self):
        """Dane do dymku – na żywo."""
        s = self.snap
        model = {"state": _("Uruchamianie…"), "state_kind": "wait", "running": [], "issues": [], "ok": "", "foot": ""}
        if not s:
            return model
        now = s["now"]
        rows = s["rows"]
        running = [r for r in rows if r["status"].running]
        errs = [r for r in rows if r["status"].level >= ERROR and not r["status"].running]
        warns = [r for r in rows if r["status"].level == WARN and not r["status"].running]
        waits = [r for r in rows if r["status"].level == WAIT and not r["status"].running]
        oks = [r for r in rows if r["status"].level <= INFO and not r["status"].running]

        if not s["kopiaui"]:
            model.update(state=_("KopiaUI nie działa"), state_kind="error")
        elif errs:
            model.update(state=_("Problemy: {n}", n=len(errs)), state_kind="error")
        elif running:
            model.update(state=_("Robi się kopia") if any((r["status"].progress or {}).get("kind") == "snapshot" for r in running)
                         else (_("Sprawdzanie kopii") if any((r["status"].progress or {}).get("kind") == "verify" for r in running)
                               else _("Kopia pracuje")), state_kind="run")
        elif warns:
            model.update(state=_("Do sprawdzenia"), state_kind="warn")
        elif waits:
            model.update(state=_("Czeka na kopię"), state_kind="wait")
        elif rows:
            model.update(state=_("Wszystko aktualne"), state_kind="ok")
        else:
            model.update(state=_("Brak repozytoriów"), state_kind="warn")

        for r in running:
            p = r["status"].progress or {}
            kind = p.get("kind")
            info = []
            if kind == "snapshot":
                what = _("robi się kopia")
                if p.get("total") and p.get("done") is not None:
                    info.append(_("{d} z ~{t}", d=fmt_bytes(p['done']), t=fmt_bytes(p['total'])))
                elif p.get("done"):
                    info.append(_("przeczytano {d}", d=fmt_bytes(p['done'])))
            elif kind == "verify":
                what = _("sprawdzanie {k}", k=p.get('label', '')).strip()
                if p.get("done"):
                    info.append(_("przeczytano {d}", d=fmt_bytes(p['done'])))
            else:
                what = _("pracuje (kopia lub konserwacja)")
            if p.get("rate") and p["rate"] > 256 * 1024:
                info.append(fmt_rate(p["rate"]))
            if p.get("since"):
                info.append(_("od {t} ({d})", t=f"{p['since']:%H:%M}", d=fmt_duration(now - p['since'])))
            model["running"].append({"key": r["repo"].id, "name": r["repo"].name, "what": what,
                                     "pct": p.get("pct"), "info": " · ".join(info)})

        def short(t, n=90):
            t = re.sub(r"^(błąd snapshotu: |snapshot error: )?(upload error: )?", lambda m: _("błąd: ") if m.group(0) else "", t)
            return t if len(t) <= n else t[:n - 1] + "…"

        if not s["kopiaui"]:
            model["issues"].append({"sym": "⏸", "kind": "error", "name": "KopiaUI",
                                    "text": _("nie działa – kopie według harmonogramu się nie robią")})
        for r in errs:
            model["issues"].append({"sym": "✖", "kind": "error", "name": r["repo"].name, "text": short(r["status"].text)})
        for r in warns:
            model["issues"].append({"sym": "⚠", "kind": "warn", "name": r["repo"].name, "text": short(r["status"].text)})
        for r in waits:
            model["issues"].append({"sym": "⏳", "kind": "wait", "name": r["repo"].name, "text": short(r["status"].text)})
        for m in s.get("mounts") or []:
            model["issues"].append({"sym": "⏏", "kind": "run", "name": _("Zamontowany: {n}", n=m['name']),
                                    "text": m["desc"] + (f" → {m['drive']}" if m.get("drive") else " …")})

        if oks:
            if len(oks) == len(rows):
                model["ok"] = _("Wszystkie kopie aktualne ({a} z {b})", a=len(oks), b=len(rows))
            else:
                model["ok"] = _("Aktualne {a} z {b}: ", a=len(oks), b=len(rows)) + ", ".join(r["repo"].name for r in oks)
        foot = []
        lasts = [sd.last_complete.start for r in rows if r["data"] and not r["data"].error
                 for sd in r["data"].sources if sd.last_complete]
        if lasts:
            foot.append(_("Ostatnia kopia: {w}", w=fmt_when(max(lasts), now)))
        q, f = self.core.next_auto_verify()
        if q:
            foot.append(_("sprawdzanie: {w}", w=fmt_when(q, now)))
        model["foot"] = " · ".join(foot)
        if s.get("diag"):
            model["foot"] += ("\n" if model["foot"] else "") + "ⓘ " + _("TrayKopia może pokazywać niepełne informacje – szczegóły w oknie stanu.")
        return model

    def _menu_state(self):
        s = self.snap or {}
        return (len(s.get("mounts") or []), tuple(r["repo"].id for r in s.get("rows", []) if r["status"].alarms),
                tuple(r.id for r in self.core.repos))

    def ui(self, fn, *a):
        self.uiq.put((fn, a))

    def _pump(self):
        try:
            while True:
                fn, a = self.uiq.get_nowait()
                try:
                    fn(*a)
                except Exception:
                    import traceback
                    app_log("ui: " + traceback.format_exc())
        except queue.Empty:
            pass
        self.root.after(30, self._pump)

    def _tick(self):
        if self.core.ui_dirty.is_set() or self.snap is None:
            self.core.ui_dirty.clear()
            try:
                prev_menu = self._menu_state() if self.snap else None
                self.snap = self.core.snapshot()
                self._update_tray()
                if self.icon and self._menu_state() != prev_menu:
                    try:
                        self.icon.update_menu()
                    except Exception:
                        pass
                self._notify_changes()
                if self.status_win:
                    self.status_win.refresh(self.snap)
            except Exception as e:
                app_log(f"tick: {e!r}")
        self.root.after(500, self._tick)

    def _anim(self):
        if self.snap and any(r["status"].running for r in self.snap["rows"]):
            self.anim_frame = int(time.monotonic() * 1000 / ANIM_INTERVAL_MS) % ANIM_FRAMES
            self._update_tray()
        self.root.after(ANIM_INTERVAL_MS, self._anim)

    # --- ikona i dymek ---
    def _overall(self):
        s = self.snap
        if not s:
            return "wait", None, _("uruchamianie…")
        rows = s["rows"]
        if not rows:
            return "warn", None, _("nie znaleziono repozytoriów KopiaUI")
        running = [r for r in rows if r["status"].running]
        errs = [r for r in rows if r["status"].level >= ERROR]
        warns = [r for r in rows if r["status"].level == WARN]
        waits = [r for r in rows if r["status"].level == WAIT]
        ok = [r for r in rows if r["status"].level <= INFO and not r["status"].running]
        badge = "error" if errs else ("warn" if warns else None)
        if not s["kopiaui"]:
            kind = "off"
            badge = "error"
        elif running:
            kind = "run"
        elif errs:
            kind = "error"
        elif warns:
            kind = "warn"
        elif waits:
            kind = "wait"
            badge = None
        else:
            kind = "ok"
            badge = None
        parts = []
        if not s["kopiaui"]:
            parts.append(_("KopiaUI nie działa!"))
        for r in running[:2]:
            parts.append(f"{r['repo'].name}: {r['status'].text}")
        if errs:
            parts.append(_("Błąd: ") + ", ".join(r["repo"].name for r in errs))
        if warns:
            parts.append(_("Uwaga: ") + ", ".join(r["repo"].name for r in warns))
        if any(low_space(r["data"]) for r in rows):
            parts.append(_("mało miejsca na dysku kopii"))
        if not parts:
            parts.append(_("wszystko OK ({a}/{b})", a=len(ok), b=len(rows)) if len(ok) == len(rows) else
                         _("OK {a}/{b}, czeka {c}", a=len(ok), b=len(rows), c=len(waits)))
        tip = f"{APP_NAME}: " + " | ".join(parts)
        return kind, badge, tip

    def _update_tray(self):
        if not self.icon:
            return
        kind, badge, tip = self._overall()
        frame = self.anim_frame % ANIM_FRAMES if kind == "run" else 0
        key = (kind, badge, frame)
        if key != self.last_icon_key:
            if key not in self.icons:
                self.icons[key] = make_icon(kind, frame, badge)
            done = False
            if self.wintray:
                try:
                    done = self.wintray.set_icon(key, self.icons[key])
                except Exception as e:
                    app_log(f"set_icon: {e!r}")
            if not done:
                self.icon.icon = self.icons[key]
            self.last_icon_key = key
        if not self.wintray:   # poza Windows zwykły dymek systemowy
            tip = tip if len(tip) <= 127 else tip[:124] + "…"
            if self.icon.title != tip:
                self.icon.title = tip

    def notify(self, title, msg):
        if not self.core.store.settings.get("notifications", True) or not self.icon:
            return
        try:
            self.icon.notify(msg[:250], title[:60] or APP_NAME)
        except Exception:
            pass

    def _notify_changes(self):
        s = self.snap
        now = s["now"]
        if not s["kopiaui"]:
            if self.kopiaui_off_since is None:
                self.kopiaui_off_since = now
            elif now - self.kopiaui_off_since > timedelta(minutes=2) and not self.notified.get("kopiaui_off"):
                self.notified["kopiaui_off"] = True
                self.notify(_("KopiaUI nie działa"), _("Kopie według harmonogramu nie będą się robić, dopóki nie uruchomisz KopiaUI."))
        else:
            self.kopiaui_off_since = None
            self.notified.pop("kopiaui_off", None)
        new_err = []
        loaded = all(r["data"] is not None for r in s["rows"])
        for r in s["rows"]:
            st = r["status"]
            k = ("repo", r["repo"].id)
            if st.level >= ERROR and not st.running:
                if r["data"] is None:
                    continue
                if self.notified.get(k) != st.text:
                    self.notified[k] = st.text
                    new_err.append((r["repo"].name, st.text))
            elif st.level <= WAIT:
                self.notified.pop(k, None)
        if not loaded and not self.notified.get("startup_done"):
            return
        self.notified["startup_done"] = True
        st_diag = self.core.store.state.setdefault("diag_notified", [])
        for key, text in s.get("diag") or []:
            if key not in st_diag:
                st_diag.append(key)
                self.core.store.save_state()
                self.notify(_("TrayKopia – diagnostyka"), text)
        if self.mute_notify_once:      # po zmianie języka teksty się zmieniły – nie powiadamiaj ponownie
            self.mute_notify_once = False
            return
        if len(new_err) == 1:
            self.notify(_("Kopia: {n}", n=new_err[0][0]), new_err[0][1])
        elif new_err:
            self.notify(_("Kopia: problemy ({n})", n=len(new_err)), "; ".join(f"{n}: {t}" for n, t in new_err))

    def _on_core_event(self, what, repo, kind, rec):
        if what == "verify":
            name = _("pełne sprawdzanie") if kind == "full" else _("sprawdzanie {p}%", p=rec.get('percent'))
            if rec.get("cancelled"):
                return
            if rec.get("ok"):
                if not rec.get("auto"):
                    self.ui(self.notify, f"{repo.name}: {name} OK",
                            _("Bez błędów. Przeczytano {n} plików ({b}).", n=rec.get('read_files') or 0, b=fmt_bytes(rec.get('read_bytes') or 0)))
            else:
                msg = rec.get("error") or "; ".join(rec.get("error_strings") or [])[:200] or _("wykryto błędy")
                self.ui(self.notify, f"{repo.name}: {name} – " + _("PROBLEM"), msg)

    # --- menu w zasobniku ---
    def _menu(self):
        pystray = self.pystray
        MI, M = pystray.MenuItem, pystray.Menu

        def repo_items(kind):
            def gen():
                items = []
                repos = list(self.core.repos)
                if kind == "quick":
                    items.append(MI(_("Wszystkie repozytoria"), lambda: self.verify_all()))
                    items.append(M.SEPARATOR)
                for r in repos:
                    items.append(MI(r.name, (lambda rid: (lambda: self.verify_repo(rid, kind)))(r.id)))
                if not repos:
                    items.append(MI(_("(brak repozytoriów)"), None, enabled=False))
                return items
            return M(gen)

        pct = lambda: self.core.store.settings["verify"].get("percent", 5)
        T = lambda text: (lambda item: _(text))   # tekst liczony przy każdym otwarciu menu (zmiana języka)
        return M(
            MI(T("Pokaż stan kopii"), lambda: self.ui(self.show_status), default=True),
            MI(T("Odśwież teraz"), lambda: self.core.request_refresh()),
            M.SEPARATOR,
            MI(lambda item: _("Sprawdź częściowo ({p}%)", p=pct()), repo_items("quick")),
            MI(T("Sprawdź całość (100%)"), repo_items("full")),
            MI(T("Przerwij sprawdzanie"), lambda: self.core.verify.cancel_all(),
               enabled=lambda item: bool(self.core.verify.current or self.core.verify.queued())),
            M.SEPARATOR,
            MI(T("Zamontuj snapshot"), M(lambda: [
                MI(r.name, (lambda rid: (lambda: self.ui(self.show_mount, rid)))(r.id)) for r in list(self.core.repos)
            ] or [MI(_("(brak repozytoriów)"), None, enabled=False)])),
            MI(T("Odmontuj"), M(lambda: [
                MI(f"{m['name']} – {m['desc']}" + (f" ({m['drive']})" if m.get("drive") else ""),
                   (lambda mid: (lambda: threading.Thread(target=self.core.mounts.unmount, args=(mid,), daemon=True).start()))(m["id"]))
                for m in self.core.mounts.list()
            ] + [M.SEPARATOR, MI(_("Odmontuj wszystkie"), lambda: threading.Thread(target=self.core.mounts.unmount_all, daemon=True).start())]),
               visible=lambda item: bool(self.core.mounts.list())),
            MI(T("To ja – zatwierdź zmiany"), M(lambda: [
                MI(r["repo"].name, (lambda rid: (lambda: self.core.ack_alarms(rid)))(r["repo"].id))
                for r in (self.snap or {}).get("rows", []) if r["status"].alarms
            ] or [MI(_("(brak alarmów)"), None, enabled=False)]),
               visible=lambda item: any(r["status"].alarms for r in (self.snap or {}).get("rows", []))),
            M.SEPARATOR,
            MI(T("Ustawienia sprawdzania…"), lambda: self.ui(self.show_settings)),
            MI(T("Otwórz KopiaUI"), lambda: self.open_kopiaui()),
            MI(T("Uruchamiaj z systemem"), lambda: self.toggle_autostart(),
               checked=lambda item: autostart_enabled(), visible=IS_WIN),
            MI(T("Powiadomienia"), lambda: self.toggle_notifications(),
               checked=lambda item: self.core.store.settings.get("notifications", True)),
            MI("Language / Język", M(
                MI("English", lambda: self.ui(self.set_language, "en"),
                   checked=lambda item: LANG["code"] == "en", radio=True),
                MI("Polski", lambda: self.ui(self.set_language, "pl"),
                   checked=lambda item: LANG["code"] == "pl", radio=True))),
            M.SEPARATOR,
            MI(T("O programie"), lambda: self.ui(self.show_about)),
            MI(T("Zakończ"), lambda: self.ui(self.quit)),
        )

    def set_language(self, code):
        if code == LANG["code"]:
            return
        LANG["code"] = code
        st = self.core.store
        st.settings["language"] = code
        st.save_settings()
        self.mute_notify_once = True
        # okna zbudowane w starym języku – zamknij i otwórz ponownie te, które były otwarte
        reopen = []
        for attr, opener in (("status_win", self.show_status), ("settings_win", self.show_settings),
                             ("about_win", self.show_about)):
            win = getattr(self, attr, None)
            if win and win.alive():
                win.close()
                reopen.append(opener)
        if self.mount_win and self.mount_win.alive():
            self.mount_win.close()
        if self.tip:
            self.tip.sig = None
        self.snap = None
        self.core.request_ui_update()
        for opener in reopen:
            opener()
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def verify_all(self):
        p = self.core.store.settings["verify"].get("percent", 5)
        for r in list(self.core.repos):
            self.core.verify.enqueue(r.id, "quick", p)
        self.core.request_ui_update()

    def verify_repo(self, rid, kind):
        p = 100 if kind == "full" else self.core.store.settings["verify"].get("percent", 5)
        self.core.verify.enqueue(rid, kind, p)
        self.core.request_ui_update()

    def toggle_autostart(self):
        try:
            set_autostart(not autostart_enabled())
        except Exception as e:
            self.notify(APP_NAME, _("Nie udało się zmienić autostartu: {e}", e=e))
        if self.icon:
            self.icon.update_menu()

    def toggle_notifications(self):
        st = self.core.store
        st.settings["notifications"] = not st.settings.get("notifications", True)
        st.save_settings()
        if self.icon:
            self.icon.update_menu()

    def open_kopiaui(self):
        exe = self.core.kopiaui_exe
        if not exe or not os.path.exists(exe):
            exe = next((p for p in default_kopiaui_candidates() if os.path.exists(p)), None)
        if not exe:
            self.notify(APP_NAME, _("Nie znaleziono KopiaUI.exe."))
            return
        try:
            subprocess.Popen([exe], creationflags=0, close_fds=True)
        except Exception as e:
            self.notify(APP_NAME, _("Nie udało się uruchomić KopiaUI: {e}", e=e))

    def quit(self):
        try:
            self.core.verify.cancel_all()
        except Exception:
            pass
        try:
            self.core.mounts.unmount_all()
        except Exception:
            pass
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        self.root.after(100, self.root.destroy)

    # --- okna ---
    def show_status(self):
        if self.status_win and self.status_win.alive():
            self.status_win.focus()
        else:
            self.status_win = StatusWindow(self)
        if self.snap:
            self.status_win.refresh(self.snap)

    def show_mount(self, rid=None):
        if getattr(self, "mount_win", None) and self.mount_win.alive():
            self.mount_win.close()
        self.mount_win = MountWindow(self, rid)

    def show_settings(self):
        if self.settings_win and self.settings_win.alive():
            self.settings_win.focus()
        else:
            self.settings_win = SettingsWindow(self)

    def show_about(self):
        if self.about_win and self.about_win.alive():
            self.about_win.focus()
        else:
            self.about_win = AboutWindow(self)


class Banner:
    """Baner „Created by IT Guy”: lewa połowa → Facebook, prawa → GitHub, podświetlenie połówki pod myszą."""

    def __init__(self, parent, tk, max_width=None):
        self.widget = None
        path = resource_path("created.png")
        if not path:
            return
        from PIL import Image, ImageEnhance, ImageTk
        img = Image.open(path).convert("RGBA")
        if max_width and img.width > max_width:
            img = img.resize((max_width, int(img.height * max_width / img.width)), Image.Resampling.LANCZOS)
        w, h = img.size
        rgb = ImageEnhance.Brightness(img.convert("RGB")).enhance(1.35).convert("RGBA")
        rgb.putalpha(img.getchannel("A"))
        bright = rgb
        left = img.copy()
        left.paste(bright.crop((0, 0, w // 2, h)), (0, 0))
        right = img.copy()
        right.paste(bright.crop((w // 2, 0, w, h)), (w // 2, 0))
        self.imgs = {None: ImageTk.PhotoImage(img), "L": ImageTk.PhotoImage(left), "R": ImageTk.PhotoImage(right)}
        self.w = w
        c = tk.Canvas(parent, width=w, height=h, highlightthickness=0, bd=0, cursor="hand2")
        self.item = c.create_image(0, 0, anchor="nw", image=self.imgs[None])
        c.bind("<Motion>", self._move)
        c.bind("<Leave>", lambda e: c.itemconfigure(self.item, image=self.imgs[None]))
        c.bind("<Button-1>", self._click)
        self.widget = c
        self.c = c

    def _move(self, e):
        self.c.itemconfigure(self.item, image=self.imgs["L" if e.x < self.w // 2 else "R"])

    def _click(self, e):
        webbrowser.open(URL_FACEBOOK if e.x < self.w // 2 else URL_GITHUB)


class StatusWindow:
    COLS = ("state", "last", "next", "quick", "full", "space")

    def __init__(self, app):
        self.app = app
        tk, ttk = app.tk, app.ttk
        w = tk.Toplevel(app.root)
        self.w = w
        w.title(f"{APP_NAME} – " + _("stan kopii zapasowych"))
        w.geometry("1200x680")
        w.minsize(900, 420)
        w.protocol("WM_DELETE_WINDOW", self.close)

        top = ttk.Frame(w, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        self.head = ttk.Label(top, text="", font=("Segoe UI", 11, "bold"))
        self.head.pack(anchor="w")
        self.sub = ttk.Label(top, text="", foreground="#555")
        self.sub.pack(anchor="w")

        self.alarm = tk.Frame(w, background="#c42b2b", padx=10, pady=6)
        self.alarm_lbl = tk.Label(self.alarm, text="", background="#c42b2b", foreground="white",
                                  font=("Segoe UI", 10, "bold"), anchor="w", justify="left", wraplength=900)
        self.alarm_lbl.pack(side="left", fill="x", expand=True)
        self.alarm_btn = ttk.Button(self.alarm, text=_("To ja – zatwierdź zmiany"), command=self._ack)
        self.alarm_btn.pack(side="right")
        self.alarm_rid = None
        self.diag = tk.Frame(w, background="#fff4e0", padx=10, pady=5, highlightthickness=1, highlightbackground="#e0b060")
        self.diag_lbl = tk.Label(self.diag, text="", background="#fff4e0", foreground="#7a4a00",
                                 font=("Segoe UI", 9), anchor="w", justify="left", wraplength=1100)
        self.diag_lbl.pack(fill="x")
        mid = ttk.Frame(w, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        self.mid = mid
        style = ttk.Style(w)
        style.configure("TrayKopia.Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("TrayKopia.Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.tree = ttk.Treeview(mid, columns=self.COLS, style="TrayKopia.Treeview", selectmode="browse")
        self.tree.heading("#0", text=_("Repozytorium"))
        heads = {"state": _("Stan"), "last": _("Ostatni snapshot"), "next": _("Następny"),
                 "quick": _("Sprawdzanie częściowe"), "full": _("Pełne sprawdzanie"), "space": _("Wolne na dysku")}
        widths = {"state": 360, "last": 130, "next": 135, "quick": 150, "full": 140, "space": 95}
        self.tree.column("#0", width=175, stretch=False)
        for c in self.COLS:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], stretch=(c == "state"))
        for lvl in (OK, INFO, WAIT, WARN, ERROR):
            self.tree.tag_configure(f"l{lvl}", foreground=level_color(lvl))
        self.tree.tag_configure("run", foreground=level_color(0, True))
        self.last_detail = None
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())

        det = ttk.Frame(w, padding=(10, 4))
        det.pack(fill="both")
        self.detail = tk.Text(det, height=9, wrap="word", font=("Segoe UI", 9), relief="flat",
                              background="#f6f6f6", padx=8, pady=6)
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")

        bar = ttk.Frame(w, padding=(10, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text=_("Odśwież"), command=lambda: app.core.request_refresh()).pack(side="left")
        self.b_quick = ttk.Button(bar, text=_("Sprawdź częściowo"), command=lambda: self._verify("quick"))
        self.b_quick.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=_("Sprawdź wszystkie częściowo"), command=app.verify_all).pack(side="left", padx=(8, 0))
        self.b_full = ttk.Button(bar, text=_("Sprawdź całość"), command=lambda: self._verify("full"))
        self.b_full.pack(side="left", padx=(8, 0))
        self.b_cancel = ttk.Button(bar, text=_("Przerwij sprawdzanie"), command=lambda: app.core.verify.cancel_all())
        self.b_cancel.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=_("Zamontuj snapshot…"), command=lambda: app.show_mount(self._sel())).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=_("Ustawienia…"), command=app.show_settings).pack(side="right")
        ttk.Button(bar, text=_("Otwórz KopiaUI"), command=app.open_kopiaui).pack(side="right", padx=(0, 8))

        bot = ttk.Frame(w, padding=(10, 0, 10, 8))
        bot.pack(fill="x")
        self.banner = Banner(bot, tk, max_width=300)
        if self.banner.widget:
            self.banner.widget.pack(side="right")
        self.foot = ttk.Label(bot, text="", foreground="#666")
        self.foot.pack(side="left", anchor="s")

        self.rows = {}
        self.snap = None
        w.lift()
        w.focus_force()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except Exception:
            return False

    def focus(self):
        self.w.deiconify()
        self.w.lift()
        self.w.focus_force()

    def close(self):
        self.w.destroy()
        self.app.status_win = None

    def _sel(self):
        s = self.tree.selection()
        return s[0] if s else None

    def _ack(self):
        if self.alarm_rid:
            self.app.core.ack_alarms(self.alarm_rid)

    def _verify(self, kind):
        rid = self._sel()
        if rid:
            self.app.verify_repo(rid, kind)

    def refresh(self, snap):
        if not self.alive():
            return
        self.snap = snap
        now = snap["now"]
        rows = snap["rows"]
        kind, badge, tip = self.app._overall()
        if not snap["kopiaui"]:
            self.head.configure(text="⚠ " + _("KopiaUI nie działa – kopie według harmonogramu się nie robią"), foreground="#c42b2b")
        else:
            n_ok = sum(1 for r in rows if r["status"].level <= INFO and not r["status"].running)
            n_run = sum(1 for r in rows if r["status"].running)
            n_err = sum(1 for r in rows if r["status"].level >= ERROR)
            n_warn = sum(1 for r in rows if r["status"].level == WARN)
            t = _("KopiaUI działa · repozytoriów: {n} · OK: {k}", n=len(rows), k=n_ok)
            if n_run:
                t += _(" · w trakcie: {n}", n=n_run)
            if n_warn:
                t += _(" · uwagi: {n}", n=n_warn)
            if n_err:
                t += _(" · problemy: {n}", n=n_err)
            self.head.configure(text=t, foreground=level_color(ERROR if n_err else (WARN if n_warn else OK)))
        q, f = self.app.core.next_auto_verify()
        fs = self.app.core.store.settings["full"]
        sub = []
        sub.append(_("Następne sprawdzanie częściowe: {w}", w=fmt_when(q, now) if q else _("wyłączone")))
        if f:
            sub.append(_("pełne: {w}", w=fmt_when(f, now)))
        else:
            sub.append(_("pełne: {w}", w=_("nie wybrano repozytoriów") if fs.get("enabled") else _("wyłączone")))
        lows = {}
        for r in rows:
            if low_space(r["data"]):
                drive = os.path.splitdrive(r["repo"].storage_path)[0] or r["repo"].storage_path
                lows[drive] = r["data"].free
        for drv, free in lows.items():
            sub.append("⚠ " + _("mało miejsca na {d}: {f}", d=drv, f=fmt_bytes(free)))
        if snap["queued"]:
            sub.append(_("w kolejce: {n}", n=len(snap['queued'])))
        for m in snap.get("mounts") or []:
            sub.append(_("zamontowany: {n} {d}", n=m['name'], d=m['desc']) + (f" → {m['drive']}" if m.get("drive") else " …"))
        if not snap["kopia_exe"]:
            sub.append(_("NIE ZNALEZIONO kopia.exe – ustaw ścieżkę w ustawieniach"))
        self.sub.configure(text=" · ".join(sub))

        queued = {(j["repo_id"], j["kind"]) for j in snap["queued"]}
        seen = set()
        for r in rows:
            repo = r["repo"]
            st = r["status"]
            data = r["data"]
            seen.add(repo.id)
            last_s, next_s = "—", "—"
            if data and not data.error and data.sources:
                lasts = [s.last_complete.start for s in data.sources if s.last_complete]
                if lasts:
                    last_s = fmt_when(max(lasts), now)
                nx = []
                for s in data.sources:
                    lc = s.last_complete
                    n = next_due(s.sched, now, lc.start if lc else None)
                    if n:
                        nx.append(n)
                next_s = fmt_when(min(nx), now) if nx else (_("ręcznie") if data.sources else "—")
            v = r["verify"]
            vr = r["verify_running"]
            quick = verify_cell(v.get("quick"), now, vr if vr and vr["kind"] == "quick" else None,
                                (repo.id, "quick") in queued)
            full = verify_cell(v.get("full"), now, vr if vr and vr["kind"] == "full" else None,
                               (repo.id, "full") in queued)
            space = "—"
            if data and data.free is not None:
                space = fmt_bytes(data.free) + (" ⚠" if low_space(data) else "")
            state = f"{level_symbol(st.level, st.running)} {st.text}"
            if snap["refreshing"] == repo.id and data is None:
                state = "• " + _("wczytywanie…")
            vals = (state, last_s, next_s, quick, full, space)
            tag = "run" if st.running else f"l{st.level}"
            if self.tree.exists(repo.id):
                self.tree.item(repo.id, text=repo.name, values=vals, tags=(tag,))
            else:
                self.tree.insert("", "end", iid=repo.id, text=repo.name, values=vals, tags=(tag,))
        for iid in self.tree.get_children():
            if iid not in seen:
                self.tree.delete(iid)
        if not self._sel() and rows:
            self.tree.selection_set(rows[0]["repo"].id)
        self.b_cancel.state(["!disabled"] if (snap["queued"] or self.app.core.verify.current) else ["disabled"])
        diag = snap.get("diag") or []
        if diag:
            self.diag_lbl.configure(text="\n".join("ⓘ " + t for _k, t in diag))
            if not self.diag.winfo_ismapped():
                self.diag.pack(fill="x", padx=10, pady=(0, 4), before=self.mid)
        elif self.diag.winfo_ismapped():
            self.diag.pack_forget()
        alarmed = [r for r in rows if r["status"].alarms]
        selrow = next((r for r in alarmed if r["repo"].id == self._sel()), None) or (alarmed[0] if alarmed else None)
        if selrow:
            self.alarm_rid = selrow["repo"].id
            more = _("  (alarmów w innych repozytoriach: {n})", n=len(alarmed) - 1) if len(alarmed) > 1 else ""
            self.alarm_lbl.configure(text=f"⚠ {selrow['repo'].name}: {selrow['status'].text}{more}")
            if not self.alarm.winfo_ismapped():
                self.alarm.pack(fill="x", padx=10, pady=(0, 4), before=self.diag if self.diag.winfo_ismapped() else self.mid)
        else:
            self.alarm_rid = None
            if self.alarm.winfo_ismapped():
                self.alarm.pack_forget()
        self.foot.configure(text=f"{APP_NAME} {APP_VERSION} · " + _("odświeżono {t}", t=f"{now:%H:%M:%S}")
                                 + (_(" · wczytuję: {n}", n=self.app.core.repo_by_id(snap['refreshing']).name)
                                    if snap["refreshing"] and self.app.core.repo_by_id(snap["refreshing"]) else ""))
        self._show_detail()

    def _show_detail(self):
        if not self.snap:
            return
        rid = self._sel()
        row = next((r for r in self.snap["rows"] if r["repo"].id == rid), None)
        lines = []
        if row:
            now = self.snap["now"]
            st = row["status"]
            lines.append(f"{row['repo'].name}: {st.text}")
            lines += st.detail
            proc = row["proc"]
            if proc:
                lines.append(_("Proces KopiaUI dla tego repozytorium działa od {w}", w=fmt_when(proc.started, now))
                             + (_(", odczyt {r}, zapis {w}", r=fmt_rate(proc.read_rate), w=fmt_rate(proc.write_rate))
                                if proc.read_rate + proc.write_rate > 64 * 1024 else ""))
            else:
                lines.append(_("Proces KopiaUI dla tego repozytorium nie działa."))
            for src, sst in row["sources"]:
                lines.append("")
                lines.append(f"[{level_symbol(sst.level, sst.running)}] {sst.text}")
                lines += ["   " + l for l in sst.detail]
            v = row["verify"]
            for k, nm in (("quick", _("Sprawdzanie częściowe")), ("full", _("Pełne sprawdzanie"))):
                rec = v.get(k)
                if rec and rec.get("end"):
                    s = from_iso(rec["start"])
                    e = from_iso(rec["end"])
                    res = _("przerwane") if rec.get("cancelled") else ("OK" if rec.get("ok") else _("PROBLEM"))
                    lines.append("")
                    lines.append(f"{nm} ({rec.get('percent')}%{_(', automatyczne') if rec.get('auto') else ''}): "
                                 + _("{r}, {w}, trwało {d}, przeczytano {n} plików ({b})", r=res, w=fmt_when(e, now),
                                     d=fmt_duration(e - s), n=rec.get('read_files') or 0, b=fmt_bytes(rec.get('read_bytes') or 0)))
                    if rec.get("error"):
                        lines.append("   " + rec["error"])
                    for es in rec.get("error_strings") or []:
                        lines.append("   " + es)
            hist = v.get("history") or []
            if hist:
                lines.append("")
                lines.append(_("Historia sprawdzania: ") + ", ".join(
                    f"{fmt_when(from_iso(h.get('end')), now)} {h.get('percent')}% "
                    f"{'–' if h.get('cancelled') else ('✔' if h.get('ok') else '✖')}" for h in hist[-8:][::-1]))
        text = "\n".join(lines)
        if text == self.last_detail:
            return
        self.last_detail = text
        y = self.detail.yview()[0]
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
        self.detail.yview_moveto(y)


class MountWindow:
    """Wybór snapshotu do zamontowania jako dysk (tylko do odczytu)."""

    def __init__(self, app, rid=None):
        self.app = app
        tk, ttk = app.tk, app.ttk
        w = tk.Toplevel(app.root)
        self.w = w
        w.title(f"{APP_NAME} – " + _("zamontuj snapshot"))
        w.geometry("760x460")
        w.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text=_("Repozytorium:")).pack(side="left")
        self.repos = list(app.core.repos)
        self.repo_var = tk.StringVar()
        names = [r.name for r in self.repos]
        self.cb = ttk.Combobox(top, values=names, state="readonly", textvariable=self.repo_var, width=30)
        self.cb.pack(side="left", padx=(6, 0))
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.fill())
        cols = ("when", "folder", "size", "files", "info")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, t, wd in (("when", "Snapshot", 150), ("folder", "Folder", 230), ("size", _("Rozmiar"), 90),
                         ("files", _("Pliki"), 80), ("info", _("Uwagi"), 170)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wd, stretch=(c in ("folder", "info")))
        self.tree.pack(fill="both", expand=True, pady=(10, 0))
        self.tree.bind("<Double-1>", lambda e: self.mount())
        self.msg = ttk.Label(f, text=_("Snapshot zostanie zamontowany jako dysk tylko do odczytu i otworzy się w Eksploratorze."),
                             foreground="#555", wraplength=720)
        self.msg.pack(anchor="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text=_("Zamontuj i otwórz"), command=self.mount).pack(side="right")
        ttk.Button(b, text=_("Zamknij"), command=self.close).pack(side="right", padx=(0, 8))
        self.snaps = {}
        idx = next((i for i, r in enumerate(self.repos) if r.id == rid), 0)
        if self.repos:
            self.cb.current(idx)
            self.fill()
        w.lift()
        w.focus_force()

    def repo(self):
        i = self.cb.current()
        return self.repos[i] if 0 <= i < len(self.repos) else None

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        self.snaps = {}
        r = self.repo()
        if not r:
            return
        d = self.app.core.data.get(r.id)
        if not d or d.error:
            self.msg.configure(text=(d.error if d else _("Dane jeszcze się wczytują – spróbuj za chwilę.")), foreground="#c42b2b")
            return
        now = now_local()
        items = []
        for s in d.sources:
            for sn in s.snaps:
                items.append((sn, s))
        items.sort(key=lambda x: x[0].start or now, reverse=True)
        for i, (sn, s) in enumerate(items):
            iid = f"s{i}"
            self.snaps[iid] = (sn, s)
            info = []
            if sn.incomplete:
                info.append(_("niepełny ({r})", r=sn.incomplete))
            if sn.failed:
                info.append(_("{n} błędów", n=sn.failed))
            self.tree.insert("", "end", iid=iid, values=(
                fmt_when(sn.start, now), s.path, fmt_bytes(sn.size), sn.files or "", ", ".join(info)))
        if items:
            self.tree.selection_set("s0")
        else:
            self.msg.configure(text=_("To repozytorium nie ma jeszcze snapshotów."), foreground="#c42b2b")

    def mount(self):
        sel = self.tree.selection()
        r = self.repo()
        if not sel or not r:
            return
        sn, s = self.snaps[sel[0]]
        self.msg.configure(text=_("Montowanie… (pierwsze zamontowanie może potrwać kilkanaście sekund)"), foreground="#1f6fc5")

        def ready(drive):
            self.app.ui(self._opened, drive)

        def error(msg):
            self.app.ui(self._failed, msg)

        self.app.core.mounts.mount(r, sn, s.path, on_ready=ready, on_error=error)

    def _opened(self, drive):
        try:
            if IS_WIN:
                os.startfile(drive if drive.endswith("\\") else drive + "\\")
        except Exception as e:
            app_log(f"startfile: {e!r}")
        if self.alive():
            self.msg.configure(text=_("Zamontowano jako {d}. Odmontujesz z menu w zasobniku (Odmontuj) albo przy zamknięciu programu.", d=drive),
                               foreground="#1e8a3a")

    def _failed(self, msg):
        if self.alive():
            self.msg.configure(text=_("Nie udało się zamontować: {m}", m=msg), foreground="#c42b2b")
        self.app.notify(f"{APP_NAME}: " + _("montowanie"), msg)

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except Exception:
            return False

    def close(self):
        self.w.destroy()
        self.app.mount_win = None


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        tk, ttk = app.tk, app.ttk
        st = app.core.store.settings
        w = tk.Toplevel(app.root)
        self.w = w
        w.title(f"{APP_NAME} – " + _("ustawienia sprawdzania"))
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)

        # --- częściowe ---
        g1 = ttk.LabelFrame(f, text=" " + _("Sprawdzanie częściowe – wszystkie repozytoria") + " ", padding=10)
        g1.pack(fill="x")
        v = st["verify"]
        self.q_on = tk.BooleanVar(value=v.get("enabled", True))
        ttk.Checkbutton(g1, text=_("Włączone"), variable=self.q_on).grid(row=0, column=0, columnspan=8, sticky="w")
        ttk.Label(g1, text=_("Procent losowo czytanych plików:")).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.q_pct = tk.StringVar(value=str(v.get("percent", 5)))
        ttk.Spinbox(g1, from_=1, to=100, width=5, textvariable=self.q_pct).grid(row=1, column=4, sticky="w", pady=(6, 0))
        ttk.Label(g1, text=_("Dni:")).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.q_days = []
        for i, dn in enumerate(day_names()):
            var = tk.BooleanVar(value=i in (v.get("days") or []))
            self.q_days.append(var)
            ttk.Checkbutton(g1, text=dn, variable=var).grid(row=2, column=1 + i, sticky="w", pady=(6, 0))
        ttk.Label(g1, text=_("Godzina:")).grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.q_time = tk.StringVar(value=v.get("time", "16:30"))
        ttk.Entry(g1, width=7, textvariable=self.q_time).grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.q_catch = tk.BooleanVar(value=v.get("catch_up", True))
        ttk.Checkbutton(g1, text=_("Nadrób po włączeniu komputera, jeśli termin minął"),
                        variable=self.q_catch).grid(row=4, column=0, columnspan=8, sticky="w", pady=(6, 0))

        # --- pełne ---
        g2 = ttk.LabelFrame(f, text=" " + _("Pełne sprawdzanie (100%) – wybrane repozytoria") + " ", padding=10)
        g2.pack(fill="x", pady=(12, 0))
        fu = st["full"]
        self.f_on = tk.BooleanVar(value=fu.get("enabled", True))
        ttk.Checkbutton(g2, text=_("Włączone"), variable=self.f_on).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(g2, text=_("Co miesiąc, dnia:")).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.f_day = tk.StringVar(value=str(fu.get("day", 1)))
        ttk.Spinbox(g2, from_=1, to=28, width=4, textvariable=self.f_day).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(g2, text=_("o godzinie:")).grid(row=1, column=2, sticky="e", pady=(6, 0), padx=(10, 4))
        self.f_time = tk.StringVar(value=fu.get("time", "16:30"))
        ttk.Entry(g2, width=7, textvariable=self.f_time).grid(row=1, column=3, sticky="w", pady=(6, 0))
        self.f_catch = tk.BooleanVar(value=fu.get("catch_up", True))
        ttk.Checkbutton(g2, text=_("Nadrób po włączeniu komputera, jeśli termin minął"),
                        variable=self.f_catch).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(g2, text=_("Repozytoria:")).grid(row=3, column=0, sticky="nw", pady=(6, 0))
        rf = ttk.Frame(g2)
        rf.grid(row=3, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.f_repos = {}
        sel = set(fu.get("repos") or [])
        repos = list(app.core.repos)
        if not repos:
            ttk.Label(rf, text=_("(nie znaleziono repozytoriów)")).pack(anchor="w")
        for i, r in enumerate(repos):
            var = tk.BooleanVar(value=r.id in sel)
            self.f_repos[r.id] = var
            ttk.Checkbutton(rf, text=r.name, variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 14))

        # --- alarm zmian ---
        ga = ttk.LabelFrame(f, text=" " + _("Alarm: podejrzanie dużo zmian (np. zaszyfrowanie plików)") + " ", padding=10)
        ga.pack(fill="x", pady=(12, 0))
        al = st.get("alarm") or {}
        self.a_on = tk.BooleanVar(value=al.get("enabled", True))
        ttk.Checkbutton(ga, text=_("Włączony"), variable=self.a_on).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(ga, text=_("Alarm, gdy w jednym snapshocie zmieni się lub zniknie co najmniej")).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.a_pct = tk.StringVar(value=str(al.get("percent", 30)))
        ttk.Spinbox(ga, from_=5, to=100, width=5, textvariable=self.a_pct).grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(ga, text=_("% plików")).grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Label(ga, text=_("(nowo dodane pliki się nie liczą; minimum 50 plików)"), foreground="#666").grid(
            row=2, column=0, columnspan=3, sticky="w")

        # --- kopia.exe ---
        g3 = ttk.LabelFrame(f, text=" kopia.exe ", padding=10)
        g3.pack(fill="x", pady=(12, 0))
        self.exe = tk.StringVar(value=st.get("kopia_exe", ""))
        ttk.Entry(g3, width=58, textvariable=self.exe).grid(row=0, column=0, sticky="w")
        ttk.Button(g3, text="…", width=3, command=self._browse).grid(row=0, column=1, padx=(6, 0))
        found = app.core.kopia.exe()
        ttk.Label(g3, text=(_("Puste = automatycznie. Używany: ") + (found or _("nie znaleziono"))),
                  foreground="#666", wraplength=460).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.msg = ttk.Label(f, text="", foreground="#c42b2b")
        self.msg.pack(anchor="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(6, 0))
        ttk.Button(b, text=_("Zapisz"), command=self.save).pack(side="right")
        ttk.Button(b, text=_("Anuluj"), command=self.close).pack(side="right", padx=(0, 8))
        w.lift()
        w.focus_force()

    def _browse(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self.w, title=_("Wskaż kopia.exe"),
                                       filetypes=[("kopia.exe", "kopia.exe"), (_("Programy"), "*.exe")])
        if p:
            self.exe.set(os.path.normpath(p))

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except Exception:
            return False

    def focus(self):
        self.w.lift()
        self.w.focus_force()

    def close(self):
        self.w.destroy()
        self.app.settings_win = None

    def save(self):
        try:
            pct = float(self.q_pct.get().replace(",", "."))
            if not 0 < pct <= 100:
                raise ValueError
        except ValueError:
            self.msg.configure(text=_("Procent musi być liczbą od 1 do 100."))
            return
        for t in (self.q_time.get(), self.f_time.get()):
            if not valid_hhmm(t):
                self.msg.configure(text=_("Nieprawidłowa godzina: „{t}” (format GG:MM).", t=t))
                return
        try:
            day = int(self.f_day.get())
            if not 1 <= day <= 28:
                raise ValueError
        except ValueError:
            self.msg.configure(text=_("Dzień miesiąca: 1–28."))
            return
        try:
            apct = int(float(self.a_pct.get().replace(",", ".")))
            if not 5 <= apct <= 100:
                raise ValueError
        except ValueError:
            self.msg.configure(text=_("Próg alarmu: liczba od 5 do 100."))
            return
        exe = self.exe.get().strip()
        if exe and not os.path.exists(exe):
            self.msg.configure(text=_("Wskazany kopia.exe nie istnieje."))
            return
        days = [i for i, v in enumerate(self.q_days) if v.get()]
        st = self.app.core.store
        h, m = parse_hhmm(self.q_time.get())
        fh, fm = parse_hhmm(self.f_time.get())
        old_q = dict(st.settings["verify"])
        old_f = dict(st.settings["full"])
        st.settings["verify"] = {"enabled": self.q_on.get(), "percent": int(pct) if pct == int(pct) else pct,
                                 "days": days, "time": f"{h:02d}:{m:02d}", "catch_up": self.q_catch.get()}
        st.settings["full"] = {"enabled": self.f_on.get(), "day": day, "time": f"{fh:02d}:{fm:02d}",
                               "catch_up": self.f_catch.get(),
                               "repos": [rid for rid, v in self.f_repos.items() if v.get()]}
        st.settings["kopia_exe"] = exe
        st.settings["alarm"] = {"enabled": self.a_on.get(), "percent": apct,
                                "min_files": (st.settings.get("alarm") or {}).get("min_files", 50)}
        st.save_settings()
        # zmiana harmonogramu: nie odpalaj od razu terminu, który minął przed zmianą
        now = now_local()
        auto = st.state.setdefault("auto", {})
        if (old_q.get("days"), old_q.get("time")) != (days, f"{h:02d}:{m:02d}"):
            d = weekly_last_due(days, f"{h:02d}:{m:02d}", now)
            if d:
                auto["quick_due"] = to_iso(d)
        if (old_f.get("day"), old_f.get("time")) != (day, f"{fh:02d}:{fm:02d}"):
            auto["full_due"] = to_iso(monthly_last_due(day, f"{fh:02d}:{fm:02d}", now))
        st.save_state()
        self.app.core.request_ui_update()
        if self.app.icon:
            self.app.icon.update_menu()
        self.close()


class AboutWindow:
    def __init__(self, app):
        self.app = app
        tk, ttk = app.tk, app.ttk
        w = tk.Toplevel(app.root)
        self.w = w
        w.title(_("O programie {a}", a=APP_NAME))
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(w, padding=16)
        f.pack()
        ttk.Label(f, text=f"{APP_NAME} {APP_VERSION}", font=("Segoe UI", 14, "bold")).pack()
        ttk.Label(f, text=_("Stan kopii zapasowych Kopia w zasobniku systemowym"), foreground="#555").pack(pady=(2, 2))
        kv = app.core.kopia_version
        ttk.Label(f, text=_("Kopia: {v} (testowana: {t})", v=kv[3] if kv else "?", t=f"{TESTED_KOPIA[0]}.{TESTED_KOPIA[1]}"),
                  foreground="#777").pack(pady=(0, 10))
        self.banner = Banner(f, tk, max_width=360)
        if self.banner.widget:
            self.banner.widget.pack()
        ttk.Button(f, text=_("Zamknij"), command=self.close).pack(pady=(12, 0))
        w.lift()
        w.focus_force()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except Exception:
            return False

    def focus(self):
        self.w.lift()
        self.w.focus_force()

    def close(self):
        self.w.destroy()
        self.app.about_win = None


# ============================================================================
#  Start
# ============================================================================

def single_instance():
    if not IS_WIN:
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TrayKopia_nyorbee_single_instance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if not single_instance():
        return
    app_log(f"start {APP_VERSION}")
    App().run()


if __name__ == "__main__":
    main()
