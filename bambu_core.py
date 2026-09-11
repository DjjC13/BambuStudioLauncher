#!/usr/bin/env python3
r"""
Bambu Studio Launcher - core logic
==================================

Why this exists
---------------
Bambu Studio crashes on this machine were traced to a stale
``bambu_networking.dll``: an OTA plugin update staged in
``%APPDATA%\BambuStudio\ota\plugins`` had been applied to every plugin DLL
*except* the networking one, which stayed on an older build. The mismatch
produced a repeating access violation inside the plugin. This launcher checks
for that condition before every launch and can repair it in one click.

It also replaces the old shortcut::

    cmd /c start "" /affinity 55 "C:\Program Files\Bambu Studio\bambu-studio.exe"

with a real CreateProcess call that applies the CPU affinity mask *before the
process runs a single instruction* (CREATE_SUSPENDED -> SetProcessAffinityMask
-> ResumeThread), so the worker threads spawned during startup inherit it too.

Stdlib only - no pip installs.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# A frozen (PyInstaller one-file) build runs from a temp directory that is
# deleted on exit, so bundled resources and user data cannot share a path:
# settings written next to the executable would evaporate every run. Running
# from source, both are simply the project folder.
if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", HERE))
    USER_DIR = Path(os.environ.get("LOCALAPPDATA", str(HERE))) / "BambuStudioLauncher"
    USER_DIR.mkdir(parents=True, exist_ok=True)
else:
    RESOURCE_DIR = HERE
    USER_DIR = HERE

CFG_PATH = USER_DIR / "config.json"
BACKUP_DIR = USER_DIR / "backups"

DATA = Path(os.environ.get("APPDATA", "")) / "BambuStudio"
PLUGINS = DATA / "plugins"
OTA = DATA / "ota" / "plugins"
LOGDIR = DATA / "log"

DEFAULT_EXE = r"C:\Program Files\Bambu Studio\bambu-studio.exe"

# The plugin that actually went stale, kept explicit so the UI can name it.
KEY_PLUGIN = "bambu_networking.dll"

DEFAULTS = {
    "exe": DEFAULT_EXE,
    "use_affinity": True,
    "affinity_mask": 0x5555,        # one thread per P-core
    "priority": "Normal",
    "check_plugin_on_launch": True,
    "auto_sync_plugin": False,
    "keep_dumps": 2,
    "minimize_to_tray": True,
    "close_with_bambu": True,
}

PRIORITY_CLASSES = {
    "Idle": 0x00000040,
    "Below normal": 0x00004000,
    "Normal": 0x00000020,
    "Above normal": 0x00008000,
    "High": 0x00000080,
}


def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    # Guard against a hand-edited config with a nonsense mask.
    if not isinstance(cfg.get("affinity_mask"), int) or cfg["affinity_mask"] <= 0:
        cfg["affinity_mask"] = DEFAULTS["affinity_mask"]
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def human(n: float) -> str:
    if n < 1024:
        return f"{n:.0f} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} GB"


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Win32 plumbing
# --------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
INFINITE = 0xFFFFFFFF


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
kernel32.SetProcessAffinityMask.restype = wintypes.BOOL
kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.SetPriorityClass.restype = wintypes.BOOL
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def cpu_topology() -> list[tuple[int, int]]:
    """Return ``[(efficiency_class, mask), ...]``, one entry per physical core.

    Uses GetLogicalProcessorInformationEx so P-cores and E-cores can be told
    apart on hybrid Intel parts. Falls back to a flat list if the call fails.
    """
    RelationProcessorCore = 0
    fn = kernel32.GetLogicalProcessorInformationEx
    fn.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    fn.restype = wintypes.BOOL

    flat = [(0, 1 << i) for i in range(os.cpu_count() or 1)]

    size = wintypes.DWORD(0)
    fn(RelationProcessorCore, None, ctypes.byref(size))
    if size.value == 0:
        return flat

    buf = ctypes.create_string_buffer(size.value)
    if not fn(RelationProcessorCore, buf, ctypes.byref(size)):
        return flat

    cores: list[tuple[int, int]] = []
    raw = buf.raw
    off = 0
    while off + 8 <= size.value:
        rel = int.from_bytes(raw[off:off + 4], "little")
        rec = int.from_bytes(raw[off + 4:off + 8], "little")
        if rec == 0:
            break
        if rel == RelationProcessorCore:
            eff = raw[off + 9]                      # EfficiencyClass
            # GROUP_AFFINITY is 8-byte aligned at offset 32 of the record.
            mask = int.from_bytes(raw[off + 32:off + 40], "little")
            cores.append((eff, mask))
        off += rec
    return cores or flat


def mask_to_cpus(mask: int) -> list[int]:
    return [i for i in range(64) if mask & (1 << i)]


def cpus_to_mask(cpus) -> int:
    m = 0
    for c in cpus:
        m |= 1 << c
    return m


def describe_mask(mask: int) -> str:
    """Human summary like '4 CPUs: 0, 2, 4, 6'."""
    cpus = mask_to_cpus(mask)
    if not cpus:
        return "no CPUs selected"
    shown = ", ".join(str(c) for c in cpus[:12])
    if len(cpus) > 12:
        shown += f", +{len(cpus) - 12} more"
    return f"{len(cpus)} CPU{'s' if len(cpus) != 1 else ''}: {shown}"


def is_running(exe_name: str = "bambu-studio.exe") -> list[int]:
    """PIDs of running Bambu Studio processes, via tasklist (no deps)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == exe_name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def kill_running() -> tuple[bool, str]:
    pids = is_running()
    if not pids:
        return True, "Bambu Studio is not running."
    subprocess.run(["taskkill", "/IM", "bambu-studio.exe", "/F"],
                   capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    time.sleep(0.6)
    left = is_running()
    if left:
        return False, f"Could not terminate PID(s): {left}"
    return True, f"Terminated {len(pids)} process(es)."


def launch(exe: Path, mask: int | None, priority: str, on_exit=None):
    """Start Bambu Studio suspended, pin it, then let it run.

    Returns ``(pid, error_or_None)``. Setting affinity while the process is
    suspended is what makes this better than ``start /affinity``: every thread
    the app creates during startup inherits the mask, with no window in which
    work escapes onto cores you meant to exclude.
    """
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer('"{}"'.format(exe))

    ok = kernel32.CreateProcessW(
        str(exe), cmdline, None, None, False,
        CREATE_SUSPENDED, None, str(exe.parent),
        ctypes.byref(si), ctypes.byref(pi),
    )
    if not ok:
        return None, ctypes.WinError(ctypes.get_last_error()).strerror

    warning = None
    if mask:
        if not kernel32.SetProcessAffinityMask(pi.hProcess, mask):
            warning = "affinity not applied: " + ctypes.WinError(
                ctypes.get_last_error()).strerror

    cls = PRIORITY_CLASSES.get(priority)
    if cls and priority != "Normal":
        kernel32.SetPriorityClass(pi.hProcess, cls)

    kernel32.ResumeThread(pi.hThread)
    kernel32.CloseHandle(pi.hThread)

    if on_exit is not None:
        def _watch(h):
            kernel32.WaitForSingleObject(h, INFINITE)
            code = wintypes.DWORD(0)
            kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            kernel32.CloseHandle(h)
            on_exit(code.value)
        threading.Thread(target=_watch, args=(pi.hProcess,), daemon=True).start()
    else:
        kernel32.CloseHandle(pi.hProcess)

    return pi.dwProcessId, warning


def explain_exit_code(code: int) -> tuple[bool, str]:
    """Return (clean, description) for a process exit code."""
    known = {
        0xC0000005: "access violation (the crash signature we were chasing)",
        0xC0000006: "in-page error",
        0xC000001D: "illegal instruction",
        0xC0000094: "integer divide by zero",
        0xC00000FD: "stack overflow",
        0xC0000409: "stack buffer overrun / __fastfail",
        0xC0000374: "heap corruption",
        0xC000013A: "terminated by Ctrl+C",
    }
    if code == 0:
        return True, "exited cleanly (code 0)"
    if code == 1:
        return True, "closed (code 1 - forced close or taskkill)"
    if code in known:
        return False, f"crashed: 0x{code:08X} - {known[code]}"
    if code >= 0xC0000000:
        return False, f"crashed: 0x{code:08X} - unhandled exception"
    return True, f"exited with code {code}"


# --------------------------------------------------------------------------
# Maintenance operations
# --------------------------------------------------------------------------

def plugin_status() -> dict:
    """Compare the installed plugins against the staged OTA copies."""
    st = {"state": "unknown", "detail": "", "stale": [], "missing": [],
          "have_ota": OTA.is_dir(), "installed": 0}

    if not PLUGINS.is_dir() or not any(PLUGINS.glob("*.dll")):
        st["state"] = "absent"
        st["detail"] = "No plugin DLLs installed - Bambu Studio will offer to download them."
        return st

    st["installed"] = len(list(PLUGINS.glob("*.dll")))

    if not OTA.is_dir() or not any(OTA.glob("*.dll")):
        st["state"] = "no-ota"
        newest = max((f.stat().st_mtime for f in PLUGINS.glob("*.dll")), default=0)
        st["detail"] = ("{} plugin DLLs installed, newest {:%Y-%m-%d}. No update is "
                        "staged - this is the normal state once an OTA update has "
                        "been applied.".format(
                            st["installed"], datetime.fromtimestamp(newest)))
        return st

    for src in sorted(OTA.glob("*.dll")):
        dst = PLUGINS / src.name
        if not dst.exists():
            st["missing"].append(src.name)
        elif src.stat().st_size != dst.stat().st_size or sha256(src) != sha256(dst):
            st["stale"].append(src.name)

    if st["stale"] or st["missing"]:
        st["state"] = "stale"
        bits = []
        if st["stale"]:
            bits.append("out of date: " + ", ".join(st["stale"]))
        if st["missing"]:
            bits.append("missing: " + ", ".join(st["missing"]))
        st["detail"] = "; ".join(bits)
    else:
        st["state"] = "ok"
        st["detail"] = f"All {st['installed']} plugin DLLs match the staged OTA package."
    return st


def list_backups() -> list[str]:
    if not BACKUP_DIR.is_dir():
        return []
    return sorted((p.name for p in BACKUP_DIR.iterdir() if p.is_dir()), reverse=True)


def backup_plugins(tag: str) -> Path | None:
    if not PLUGINS.is_dir():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / "plugins_{}_{:%Y%m%d_%H%M%S}".format(tag, datetime.now())
    shutil.copytree(PLUGINS, dest)
    return dest


def sync_plugins() -> tuple[bool, str]:
    """Copy staged OTA plugin DLLs over the installed ones."""
    if is_running():
        return False, "Bambu Studio is running - close it first."
    st = plugin_status()
    if st["state"] == "ok":
        return True, "Already up to date; nothing to do."
    if not (st["stale"] or st["missing"]):
        return False, "No staged OTA plugins available to sync from."

    bak = backup_plugins("presync")
    copied = []
    for name in st["stale"] + st["missing"]:
        shutil.copy2(OTA / name, PLUGINS / name)
        copied.append(name)

    # plugins\backup holds the pre-update copies. If it still contains the old
    # file, a failed load could roll straight back to the broken build, so
    # refresh it too.
    nested = PLUGINS / "backup"
    if nested.is_dir():
        for name in copied:
            if (nested / name).exists():
                shutil.copy2(OTA / name, nested / name)

    where = " Backup: {}".format(bak.name) if bak else ""
    return True, "Synced {} file(s): {}.{}".format(len(copied), ", ".join(copied), where)


def remove_plugins() -> tuple[bool, str]:
    """Delete the installed plugins so Bambu Studio reinstalls them on launch."""
    if is_running():
        return False, "Bambu Studio is running - close it first."
    if not PLUGINS.is_dir():
        return True, "Plugins folder already absent."
    bak = backup_plugins("removed")
    shutil.rmtree(PLUGINS, ignore_errors=True)
    if PLUGINS.is_dir():
        return False, "Could not fully remove the plugins folder (file in use?)."
    return True, ("Plugins removed (backup: {}). Bambu Studio will prompt to "
                  "reinstall the network plugin on next launch - accept it, then "
                  "restart the app once it finishes."
                  .format(bak.name if bak else "none"))


def restore_backup(name: str) -> tuple[bool, str]:
    src = BACKUP_DIR / name
    if not src.is_dir():
        return False, "Backup {} not found.".format(name)
    if is_running():
        return False, "Bambu Studio is running - close it first."
    if PLUGINS.is_dir():
        shutil.rmtree(PLUGINS, ignore_errors=True)
    shutil.copytree(src, PLUGINS)
    return True, "Restored plugins from {}.".format(name)


def log_stats() -> tuple[int, int, int]:
    """``(total_bytes, dump_count, dump_bytes)`` for the Bambu log folder."""
    if not LOGDIR.is_dir():
        return 0, 0, 0
    total = dumps = dump_bytes = 0
    for f in LOGDIR.rglob("*"):
        try:
            if f.is_file():
                sz = f.stat().st_size
                total += sz
                if f.suffix.lower() == ".dmp":
                    dumps += 1
                    dump_bytes += sz
        except OSError:
            pass
    return total, dumps, dump_bytes


def clean_logs(keep_dumps: int = 2, keep_log_days: int = 1) -> tuple[bool, str]:
    """Delete old crash dumps and logs. The newest ``keep_dumps`` dumps survive."""
    if not LOGDIR.is_dir():
        return True, "No log folder."
    before, _, _ = log_stats()

    dumps = sorted(LOGDIR.glob("*.dmp"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for d in dumps[keep_dumps:]:
        try:
            d.unlink()
            removed += 1
        except OSError:
            pass

    cutoff = time.time() - keep_log_days * 86400
    logs_removed = 0
    for pat in ("*.log.0", "*.log.enc", "*.log"):
        for f in LOGDIR.glob(pat):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    logs_removed += 1
            except OSError:
                pass

    after, _, _ = log_stats()
    return True, ("Removed {} dump(s) and {} old log(s). {} -> {} (freed {})."
                  .format(removed, logs_removed, human(before), human(after),
                          human(max(before - after, 0))))


def crash_history(days: int = 30) -> list[dict]:
    """Bambu crash records pulled from the Windows Application event log."""
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-WinEvent -FilterHashtable @{LogName='Application';"
        "ProviderName='Application Error';StartTime=(Get-Date).AddDays(-" + str(days) + ")}"
        " | Where-Object { $_.Message -match 'bambu' } | ForEach-Object {"
        " $m=$_.Message;"
        " $mod = if ($m -match 'Faulting module name: ([^,]+)') { $Matches[1] } else { '?' };"
        " $exc = if ($m -match 'Exception code: (\\S+)') { $Matches[1] } else { '?' };"
        " $off = if ($m -match 'Fault offset: (\\S+)') { $Matches[1] } else { '?' };"
        " $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + '|' + $mod + '|' + $exc + '|' + $off }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=90,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4 and parts[0]:
            rows.append({"time": parts[0], "module": parts[1],
                         "exception": parts[2], "offset": parts[3]})
    return rows


def app_version(exe: Path) -> str:
    """Read FileVersion out of the executable's PE version resource."""
    try:
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return "unknown"
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(exe), 0, size, buf):
            return "unknown"
        val = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(val), ctypes.byref(length)):
            return "unknown"
        # VS_FIXEDFILEINFO: dwFileVersionMS at +8, dwFileVersionLS at +12
        data = ctypes.string_at(val, length.value)
        ms = int.from_bytes(data[8:12], "little")
        ls = int.from_bytes(data[12:16], "little")
        return "{}.{}.{}.{}".format(ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return "unknown"


def diagnostics_report(exe: Path) -> str:
    """A plain-text summary suitable for pasting into a Bambu bug report."""
    lines = []
    add = lines.append
    add("Bambu Studio diagnostics - {:%Y-%m-%d %H:%M:%S}".format(datetime.now()))
    add("=" * 62)
    add("")
    add("Executable : {}".format(exe))
    add("Version    : {}".format(app_version(exe)))
    add("Data dir   : {}".format(DATA))
    add("")

    st = plugin_status()
    add("PLUGIN STATE: {}".format(st["state"].upper()))
    add("  {}".format(st["detail"]))
    for folder, label in ((PLUGINS, "installed"), (OTA, "staged OTA")):
        add("  {}:".format(label))
        if folder.is_dir():
            for f in sorted(folder.glob("*.dll")):
                add("    {:<26} {:>12,}  {:%Y-%m-%d}".format(
                    f.name, f.stat().st_size,
                    datetime.fromtimestamp(f.stat().st_mtime)))
        else:
            add("    (folder absent)")
    add("")

    total, dumps, dump_bytes = log_stats()
    add("LOGS: {} total, {} crash dump(s) using {}".format(
        human(total), dumps, human(dump_bytes)))
    add("")

    hist = crash_history(30)
    add("CRASHES (last 30 days): {}".format(len(hist)))
    by_mod: dict[str, int] = {}
    for row in hist:
        key = "{} {} +{}".format(row["module"], row["exception"], row["offset"])
        by_mod[key] = by_mod.get(key, 0) + 1
    for key, count in sorted(by_mod.items(), key=lambda kv: -kv[1]):
        add("  {:>4} x  {}".format(count, key))
    if hist:
        add("")
        add("  Most recent 10:")
        for row in hist[:10]:
            add("    {}  {}  {}".format(row["time"], row["module"], row["exception"]))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Graphics / NVIDIA
#
# Documented Bambu Studio crash fix: NVIDIA's "Threaded Optimization" driver
# feature is incompatible with Bambu Studio's threading and crashes it during
# slicing. Bambu's own wiki and the community thread both say to turn it off
# per-program in the NVIDIA Control Panel.
#
# There is no supported API for writing NVIDIA's driver profile database - it
# lives in an undocumented binary blob only meant to be touched by the control
# panel or NVIDIA Profile Inspector. So the launcher opens the right panel and
# says exactly what to change, rather than pretending to have set it.
#
# What it *can* genuinely apply is the Windows per-application GPU preference,
# a documented user-scope registry value. That matters on machines where a
# virtual display adapter sits alongside the real GPU, and Bambu Studio is
# known to pick the wrong adapter on multi-adapter systems.
# --------------------------------------------------------------------------

GPU_PREF_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
GPU_PREF_LABELS = {0: "Windows decides", 1: "Power saving", 2: "High performance"}

NVIDIA_OVERLAY_DLL = (
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "nvspcap64.dll")

NVIDIA_APP = Path(
    r"C:\Program Files\NVIDIA Corporation\NVIDIA app\CEF\NVIDIA app.exe")
NVIDIA_LEGACY_CPL = Path(
    r"C:\Program Files\NVIDIA Corporation\Control Panel Client\nvcplui.exe")

THREADED_OPT_STEPS = (
    "NVIDIA Control Panel \u2192 Manage 3D Settings \u2192 Program Settings \u2192 "
    "add bambu-studio.exe \u2192 set \u201cThreaded Optimization\u201d to Off."
)


def gpu_list() -> list[tuple[str, str]]:
    """[(adapter name, driver version), ...] for every display adapter."""
    ps = ("Get-CimInstance Win32_VideoController | ForEach-Object { "
          "$_.Name + '|' + $_.DriverVersion }")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW).stdout
    except Exception:
        return []
    gpus = []
    for line in out.splitlines():
        if "|" in line:
            name, _, ver = line.strip().partition("|")
            if name:
                gpus.append((name, ver))
    return gpus


def has_nvidia(gpus=None) -> bool:
    rows = gpu_list() if gpus is None else gpus
    return any("nvidia" in name.lower() for name, _ in rows)


def nvidia_overlay_installed() -> bool:
    """The GeForce overlay injects nvspcap64.dll into OpenGL apps like this one."""
    return NVIDIA_OVERLAY_DLL.exists()


def get_gpu_preference(exe: Path) -> int | None:
    """Windows per-app GPU preference, or None when unset."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GPU_PREF_KEY) as key:
            val, _ = winreg.QueryValueEx(key, str(exe))
    except OSError:
        return None
    for part in str(val).split(";"):
        if part.startswith("GpuPreference="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def set_gpu_preference(exe: Path, pref: int | None) -> tuple[bool, str]:
    """Set the per-app GPU preference, or clear it with pref=None."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, GPU_PREF_KEY) as key:
            if pref is None:
                try:
                    winreg.DeleteValue(key, str(exe))
                except FileNotFoundError:
                    pass
                return True, "GPU preference cleared - Windows decides again."
            winreg.SetValueEx(key, str(exe), 0, winreg.REG_SZ,
                              "GpuPreference={};".format(pref))
        return True, "GPU preference set to {}.".format(GPU_PREF_LABELS[pref])
    except OSError as exc:
        return False, "Could not write GPU preference: {}".format(exc)


def open_nvidia_settings() -> tuple[bool, str]:
    """Open whichever NVIDIA settings UI this machine actually has."""
    if NVIDIA_APP.exists():
        subprocess.Popen([str(NVIDIA_APP)], creationflags=CREATE_NO_WINDOW)
        return True, "Opened the NVIDIA app. " + THREADED_OPT_STEPS

    # The modern Control Panel is a Store app, reachable through the shell.
    try:
        appid = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Where-Object { $_.Name -match 'NVIDIA Control Panel' }"
             " | Select-Object -First 1 -ExpandProperty AppID"],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW).stdout.strip()
    except Exception:
        appid = ""
    if appid:
        subprocess.Popen(["explorer.exe", "shell:appsFolder\\" + appid])
        return True, "Opened NVIDIA Control Panel. " + THREADED_OPT_STEPS

    if NVIDIA_LEGACY_CPL.exists():
        subprocess.Popen([str(NVIDIA_LEGACY_CPL)], creationflags=CREATE_NO_WINDOW)
        return True, "Opened NVIDIA Control Panel. " + THREADED_OPT_STEPS
    return False, "No NVIDIA settings app found on this machine."


def open_url(url: str) -> None:
    webbrowser.open(url, new=2)
