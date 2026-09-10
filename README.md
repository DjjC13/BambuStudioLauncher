# Bambu Studio Launcher

A small Windows control panel that launches Bambu Studio pinned to the CPU
cores you choose, keeps its network plugin healthy, and surfaces the documented
NVIDIA fixes for Bambu Studio's slicing crashes.

![Bambu Studio Launcher](docs/screenshot.png)

## Download

Grab **BambuStudioLauncher.exe** from the
[latest release](https://github.com/DjjC13/BambuStudioLauncher/releases/latest).
Nothing to install - it is a single file and needs no Python, no admin rights,
and writes only to your own user profile.

> **SmartScreen will warn you the first time.** The exe is unsigned, because
> code-signing certificates cost money. Click *More info* then *Run anyway*, or
> build it yourself from source with `python build_exe.py` if you would rather
> not take my word for it.

## Running from source

```
python bambu_launcher.py
```

Python 3.10+ with tkinter, which the standard python.org installer includes.
No pip installs - the app itself is stdlib only. Pillow is needed only to
regenerate the icon (`python make_icon.py`), never at runtime.

## What it does

- **Pins Bambu Studio to chosen CPU cores** before it executes a single
  instruction, with presets built from your machine's real P-core/E-core layout
- **Catches half-applied plugin updates**, the cause of a repeating crash that
  is otherwise very hard to diagnose
- **Applies the Windows per-app GPU preference** and points you at the NVIDIA
  setting that fixes crash-on-slice
- **Reports crashes** from the Windows event log, and decodes the exit code
  when Bambu Studio dies so you know it happened
- **Cleans up** the crash dumps Bambu Studio never deletes

---

## Why it exists

This started as a one-off diagnosis. Bambu Studio was crashing on one machine
roughly every session — 33 crashes over three weeks, every one of them carrying
an identical signature:

```
Faulting module: bambu_networking.dll
Exception:       0xC0000005 access violation, READ
Fault offset:    +0xD5447A          (identical every time)
```

The cause was a **half-applied plugin update**. Bambu Studio had staged an OTA
plugin package in `%APPDATA%\BambuStudio\ota\plugins` and applied it to every
DLL *except* `bambu_networking.dll`, which stayed on an older build while the
app and its sibling plugins moved forward. The mismatched networking core
dereferenced a dangling pointer on a worker thread.

This launcher checks for that condition before every launch, so a future
half-applied update gets caught rather than costing another day of crashes.

---

## What each control does

### CPU affinity

Replaces the old shortcut:

```
cmd /c start "" /affinity 55 "C:\Program Files\Bambu Studio\bambu-studio.exe"
```

The mask is applied with `CREATE_SUSPENDED` → `SetProcessAffinityMask` →
`ResumeThread`, so the process is pinned **before it executes a single
instruction**. `start /affinity` sets the mask on an already-running process,
leaving a brief window in which startup worker threads can be scheduled onto
cores you meant to exclude. This closes that window.

Presets are computed from **your** machine's actual topology via
`GetLogicalProcessorInformationEx`, which reports each core's efficiency class,
so the masks below differ per CPU. This example is an i9-14900KF - 8 P-cores
(CPUs 0-15, hyperthreaded) plus 16 E-cores (CPUs 16-31):

| Preset | Mask | CPUs |
|---|---|---|
| `P-cores` | `0x5555` | 0, 2, 4, 6, 8, 10, 12, 14 — all 8 P-cores, no HT siblings |
| `P + HT` | `0xFFFF` | 0–15 — 8 P-cores including hyperthreads |
| `E-cores` | `0xFFFF0000` | 16–31 |
| `All 32` | `0xFFFFFFFF` | everything |

The original `0x55` preset is gone; `P-cores` supersedes it with twice the
threads and the same "stay off the E-cores" behaviour. Any mask still works —
click individual CPU chips or type hex directly, and the two stay in sync. The
preset row highlights whichever preset the current mask matches, and the
blue/orange bar under each chip marks P-core vs E-core.

**Priority** sets the process priority class. Leave it on Normal unless you
want slicing to stay out of the way of something else.

### The window

It opens at 760x300: a status line, four collapsed headings, and Launch.
Clicking a heading rolls that section down and rolls any other one up, and the
window resizes to fit. Nothing is more than one click away, and nothing is on
screen until you ask for it.

| Section | Holds |
|---|---|
| **CPU & Performance** | affinity presets, hex mask, priority, per-CPU grid |
| **Graphics & NVIDIA** | adapter list, GPU preference, Threaded Optimization |
| **Plugin & maintenance** | plugin state, launch checks, every repair action |
| **Activity** | running log of what the launcher did |

Each heading carries a summary on the right when collapsed — the active CPU
preset, the plugin state, the number of log events — so the common case needs
no clicks at all.

### Status detail

- **Network plugin** — compares every installed plugin DLL against the staged
  OTA copies by SHA-256. `OUT OF DATE` in red is the exact condition that
  caused the crashes.
- **Crashes (7 days)** — reads the Windows Application event log for Bambu
  faults. Should be `0`.
- **Log folder** — total size and crash dump count. Dumps are ~50 MB each and
  Bambu Studio never deletes them, so this folder grows without limit. On the
  machine this tool was written for it had reached 572 MB.

### Graphics & NVIDIA

Two separate things live here, and the difference matters.

**Applied by the launcher.** *Force the high-performance GPU for Bambu Studio*
writes the Windows per-app GPU preference
(`HKCU\Software\Microsoft\DirectX\UserGpuPreferences`) — a documented,
user-scope, reversible registry value. It matters most on laptops and on
desktops carrying a virtual display adapter (Parsec, Sunshine, OBS, a KVM)
alongside a real GPU, since Bambu Studio is known to pick the wrong adapter
when more than one is present. Untick it to clear the value and hand the
choice back to Windows.

**Not applied by the launcher.** *Threaded Optimization* is the documented
NVIDIA fix — the driver feature fights Bambu Studio's threading and kills it
mid-slice. Both Bambu's own wiki and the community thread say to turn it off
per-program. There is **no supported API** for writing NVIDIA's driver profile
database; it is an undocumented binary blob meant only for the control panel or
NVIDIA Profile Inspector. So the launcher opens the right app and prints the
exact steps rather than pretending to have set it:

> NVIDIA Control Panel → Manage 3D Settings → Program Settings → add
> `bambu-studio.exe` → set **Threaded Optimization** to **Off**.

The section also warns when the GeForce overlay (`nvspcap64.dll`) is installed,
since it injects itself into Bambu Studio and is a known source of instability
in OpenGL apps.

Sources: [Bambu wiki: win crash when slicing](https://wiki.bambulab.com/en/bambu-studio/troubleshoot/win-crash-when-slicing),
[forum: crashes after slicing – solved (NVIDIA problem)](https://forum.bambulab.com/t/bambu-studio-crashes-after-slicing-solved-nvidia-problem/162392)

### Maintenance

Everything below lives under **Plugin & maintenance**.

| Action | What it does |
|---|---|
| **Sync plugin from OTA** | Copies any stale/missing staged DLL over the installed one. Backs up first. Also refreshes `plugins\backup\` so a failed load can't roll back to the broken build. |
| **Delete plugin (force reinstall)** | Backs up, then deletes the whole plugins folder. Bambu Studio offers to download a fresh copy on next launch. Use when the plugin is broken and there's no OTA copy to sync from. |
| **Clean logs & crash dumps** | Deletes all but the 2 newest crash dumps, plus logs older than a day. |
| **Restore plugin backup…** | Puts back any previous plugins snapshot. |
| **Save diagnostics report** | Writes a `diagnostics_*.txt` with version, plugin inventory, log sizes and a 30-day crash breakdown — paste-ready for a Bambu bug report. |
| **Close Bambu Studio** | `taskkill /F`, for when it hangs. |
| **Open data folder** | Opens `%APPDATA%\BambuStudio`, which holds both `plugins\` and `log\`. |

Maintenance actions refuse to run while Bambu Studio is open, since the DLLs
are locked — that lock is what caused the original half-applied update.

### Launch options

- **Check plugin before launch** — verifies plugin state and reports it in the
  Activity log, but launches either way.
- **Auto-repair the plugin if it is stale** — additionally syncs from OTA
  before launching. Off by default; turn it on if you'd rather it just fix
  itself.

Both live under **Plugin & maintenance**.

### Crash detection

The launcher keeps a handle on the process and reports the exit code when it
ends. A clean quit logs *"exited cleanly"*; a fault logs the decoded status,
e.g. `0xC0000005 - access violation`. That means you find out a crash happened
even if you weren't watching.

---

## Files

```
bambu_launcher.py    GUI
bambu_core.py        logic - launching, plugin checks, log cleanup, event log
build_exe.py         builds the single-file exe with PyInstaller
Bambu Launcher.bat   run from source with no console window
make_icon.py         regenerates icon.ico / icon.png (needs Pillow)
logo_options.py      renders candidate marks side by side, for iterating
icon.ico, icon*.png  app icon, 16-256px
```

### Where your settings live

Running the **exe**, user data goes to `%LOCALAPPDATA%\BambuStudioLauncher\`.
Running **from source**, it sits beside the scripts. Either way it is:

```
config.json          written on exit; remembers mask, priority, toggles
backups/             timestamped plugin snapshots, created on first backup
diagnostics_*.txt    reports you generate
```

A one-file exe unpacks itself to a temp directory that Windows deletes on
exit, which is why the two are kept apart — settings written next to the
executable would evaporate on every run.

To point at a different Bambu Studio install, edit `exe` in `config.json`.

---

## Notes

- **A pinned mask is not free.** Limiting cores trades slicing speed for
  stability and a quieter machine. If slicing feels slow, step up a preset or
  untick *Limit CPUs* entirely.
- **`plugins\backup\`** is Bambu's own rollback copy. Sync refreshes it; a
  manual DLL swap does not, so a rollback there could reintroduce an old build.
- The launcher never touches `BambuStudio.conf`, so your printer profiles,
  access codes and preferences are untouched by any button here.
- **The icon** is generated, not hand-drawn — `make_icon.py` renders it at 8x
  and downsamples, so edit that file rather than the PNGs to change it. The
  mark is a bamboo shoot: a thick three-segment stalk in mid green with a
  lighter leaf at the crown. Three things keep it legible when small, and all
  three were arrived at by rendering candidates at 16px rather than guessing:
  the stalk segments are taller than they are wide with a tight corner radius
  (square segments with a big radius read as three stacked pills); the leaf is
  a lighter tone so it never merges with the stalk under downsampling; and the
  leaf sits just clear of the stalk rather than touching it, because a leaf
  whose pointed base lands on the stalk fuses into a hook shape.
  **Below 24px the leaf is dropped entirely** and the stalk grows to fill the
  tile — a three-pixel leaf is not a leaf. `SIMPLIFY_BELOW` controls that.
- `logo_options.py` renders candidate marks side by side at 96/48/32/24/16 on
  both dark and light grounds. It is how the current mark was chosen; keep it
  if you want to iterate further, delete it if you don't.
- **Buy me a coffee** at the bottom left opens <http://buymeacoffee.com/DjjC13>.
