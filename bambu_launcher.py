#!/usr/bin/env python3
"""
Bambu Studio Launcher - GUI

Run with pythonw.exe (or "Bambu Studio Launcher.lnk") for no console window:

    pythonw bambu_launcher.py

See bambu_core.py for what each action actually does.

Layout: the window opens showing only stats, four collapsed section headings,
and Launch. Clicking a heading rolls that section down and rolls any other one
up, and the window resizes to fit. Nothing is more than one click away, but
nothing is on screen until you ask for it.
"""

from __future__ import annotations

import ctypes
import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import bambu_core as core
import bambu_tray

COFFEE_URL = "http://buymeacoffee.com/DjjC13"

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

BG = "#15171b"
RAISED = "#22262d"
RAISED_HI = "#2c313a"
FG = "#e8eaee"
DIM = "#9aa1ac"
FAINT = "#6c737e"
RULE = "#282d35"
ACCENT = "#25c95e"          # Bambu-ish green, matching the icon
ACCENT_HI = "#37e074"
ACCENT_DARK = "#0e3a20"
ACCENT_TEXT = "#04220f"
WARN = "#e8a33d"
DANGER = "#e5555a"
BLUE = "#5b9df0"
COFFEE = "#e0a33a"

VK_SHIFT = 0x10


def shift_held() -> bool:
    """True if Shift is down right now.

    Unattended start hides the window, so holding Shift during startup is the
    escape hatch that brings the launcher up normally and lets the option be
    switched off again.
    """
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
    except Exception:
        return False


HERE = Path(__file__).resolve().parent
# Icons are bundled resources; in a frozen build they live in the temp
# extraction directory, not beside the executable. See bambu_core.RESOURCE_DIR.
ICON_ICO = core.RESOURCE_DIR / "icon.ico"
ICON_PNG = core.RESOURCE_DIR / "icon_64.png"


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = core.load_cfg()
        self.exe = Path(self.cfg["exe"])
        self.topology = core.cpu_topology()
        self.ncpu = os.cpu_count() or 1
        self.cpu_vars: dict[int, tk.BooleanVar] = {}
        self.cpu_chips: dict[int, tk.Label] = {}
        self.seg_buttons: list[tuple[ttk.Button, int]] = []
        self.sections: dict[str, dict] = {}
        self._syncing = False
        self._gfx_loaded = False
        self._tray: bambu_tray.TrayIcon | None = None
        self._hidden = False

        self.title("Bambu Studio Launcher")
        self.configure(bg=BG)
        self.minsize(720, 300)
        if ICON_ICO.exists():
            try:
                self.iconbitmap(default=str(ICON_ICO))
            except tk.TclError:
                pass

        self._init_style()
        self._build()
        self._apply_mask(self.cfg["affinity_mask"])
        self._on_use_aff()
        self._fit_window(width=760)
        self.refresh_status()
        self._load_graphics()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.cfg["auto_start"] and not shift_held():
            self.withdraw()
            self._hidden = True
            self.after(700, self._auto_start)
        elif self.cfg["auto_start"]:
            self.log("Shift held — unattended start skipped for this run.", "warn")

    # ---------------------------------------------------------------- style

    def _init_style(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, borderwidth=0, focuscolor=BG)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG)

        s.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
        s.configure("Sub.TLabel", foreground=DIM, font=("Segoe UI", 9))
        s.configure("Faint.TLabel", foreground=FAINT, font=("Segoe UI", 9))
        s.configure("Stat.TLabel", font=("Segoe UI", 9))
        s.configure("Body.TLabel", foreground=DIM, font=("Segoe UI", 9))

        s.configure("TButton", background=RAISED, foreground=FG, relief="flat",
                    padding=(12, 6), font=("Segoe UI", 9))
        s.map("TButton", background=[("active", RAISED_HI), ("disabled", "#1c1f25")],
              foreground=[("disabled", FAINT)])

        s.configure("Seg.TButton", background=RAISED, foreground=DIM,
                    padding=(12, 6), font=("Segoe UI", 9))
        s.map("Seg.TButton", background=[("active", RAISED_HI), ("disabled", "#1c1f25")],
              foreground=[("active", FG), ("disabled", FAINT)])
        s.configure("SegOn.TButton", background=ACCENT_DARK, foreground=ACCENT,
                    padding=(12, 6), font=("Segoe UI Semibold", 9))
        s.map("SegOn.TButton", background=[("active", ACCENT_DARK), ("disabled", "#1c1f25")],
              foreground=[("active", ACCENT), ("disabled", FAINT)])

        s.configure("Go.TButton", background=ACCENT, foreground=ACCENT_TEXT,
                    font=("Segoe UI Semibold", 11), padding=(24, 11))
        s.map("Go.TButton", background=[("active", ACCENT_HI), ("disabled", "#1f4a2e")],
              foreground=[("disabled", "#5d7966")])

        s.configure("TEntry", fieldbackground=RAISED, foreground=FG,
                    insertcolor=FG, padding=5)
        s.configure("TCombobox", fieldbackground=RAISED, background=RAISED,
                    foreground=FG, arrowcolor=DIM, padding=4)
        # Readonly comboboxes ignore `configure` colours on clam; the state has
        # to be mapped explicitly or the text renders dark-on-dark.
        s.map("TCombobox",
              fieldbackground=[("readonly", RAISED)], foreground=[("readonly", FG)],
              background=[("readonly", RAISED)], selectbackground=[("readonly", RAISED)],
              selectforeground=[("readonly", FG)], arrowcolor=[("readonly", DIM)])
        self.option_add("*TCombobox*Listbox.background", RAISED)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", ACCENT_TEXT)

    # -------------------------------------------------------------- widgets

    def _check(self, parent, text: str, var: tk.BooleanVar, command=None):
        """A checkbox as a clickable label.

        clam draws its ticked indicator as an X, which reads as "rejected"
        rather than "on". A label carrying its own glyph is clearer and far
        easier to colour.
        """
        lab = tk.Label(parent, bg=BG, font=("Segoe UI", 9), cursor="hand2", anchor="w")

        def paint(*_):
            on = var.get()
            lab.configure(text=("☑  " if on else "☐  ") + text,
                          fg=FG if on else DIM)

        def toggle(_e=None):
            var.set(not var.get())
            if command:
                command()

        var.trace_add("write", paint)
        lab.bind("<Button-1>", toggle)
        paint()
        return lab

    def _rule(self, parent, pady=12) -> None:
        tk.Frame(parent, bg=RULE, height=1).pack(fill="x", pady=pady)

    def _label(self, parent, text, style="Body.TLabel", **pack):
        lab = ttk.Label(parent, text=text, style=style, justify="left")
        lab.pack(anchor="w", **pack)
        return lab

    # ---------------------------------------------------------------- build

    def _build(self) -> None:
        self.root = ttk.Frame(self, padding=(22, 16))
        self.root.pack(fill="both", expand=True)

        self._build_header(self.root)
        self._build_stats(self.root)
        self._rule(self.root, pady=(14, 6))

        self.holder = ttk.Frame(self.root)
        self.holder.pack(fill="x")
        self._build_section("cpu", "CPU & Performance", self._body_cpu)
        self._build_section("gfx", "Graphics & NVIDIA", self._body_gfx)
        self._build_section("plugin", "Plugin & Maintenance", self._body_plugin)
        self._build_section("log", "Activity", self._body_log)

        self._build_bottom(self.root)

    def _build_header(self, root) -> None:
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(0, 14))

        if ICON_PNG.exists():
            try:
                # Kept on self: PhotoImage is garbage collected otherwise.
                self.icon_img = tk.PhotoImage(file=str(ICON_PNG)).subsample(2, 2)
                tk.Label(bar, image=self.icon_img, bg=BG).pack(side="left", padx=(0, 12))
            except tk.TclError:
                pass

        text = ttk.Frame(bar)
        text.pack(side="left")
        ttk.Label(text, text="Bambu Studio Launcher",
                  style="Title.TLabel").pack(anchor="w")
        self.sub_lbl = ttk.Label(text, text="checking...", style="Faint.TLabel")
        self.sub_lbl.pack(anchor="w")

        ttk.Button(bar, text="Refresh", command=self.refresh_status).pack(side="right")

    def _build_stats(self, root) -> None:
        row = ttk.Frame(root)
        row.pack(fill="x")
        self.stats: dict[str, tuple[tk.Label, ttk.Label]] = {}
        for key in ("plugin", "crashes", "logs"):
            cell = ttk.Frame(row)
            cell.pack(side="left", padx=(0, 30))
            dot = tk.Label(cell, text="●", bg=BG, fg=FAINT, font=("Segoe UI", 8))
            dot.pack(side="left", padx=(0, 6))
            lbl = ttk.Label(cell, text="checking...", style="Stat.TLabel")
            lbl.pack(side="left")
            self.stats[key] = (dot, lbl)

    # ------------------------------------------------------------- sections

    def _build_section(self, key: str, title: str, build_body) -> None:
        wrap = ttk.Frame(self.holder)
        wrap.pack(fill="x")

        head = tk.Frame(wrap, bg=BG, cursor="hand2")
        head.pack(fill="x")
        chev = tk.Label(head, text="▸", bg=BG, fg=FAINT, font=("Segoe UI", 9),
                        width=2, cursor="hand2")
        chev.pack(side="left")
        name = tk.Label(head, text=title, bg=BG, fg=FG,
                        font=("Segoe UI Semibold", 10), cursor="hand2")
        name.pack(side="left")
        summary = tk.Label(head, text="", bg=BG, fg=FAINT, font=("Segoe UI", 9),
                           cursor="hand2")
        summary.pack(side="right")

        body = ttk.Frame(wrap, padding=(24, 4, 0, 12))
        build_body(body)

        entry = {"wrap": wrap, "head": head, "chev": chev, "name": name,
                 "summary": summary, "body": body, "open": False}
        self.sections[key] = entry

        for w in (head, chev, name, summary):
            w.bind("<Button-1>", lambda _e, k=key: self._toggle_section(k))
            w.bind("<Enter>", lambda _e, e=entry: e["chev"].configure(fg=ACCENT))
            w.bind("<Leave>", lambda _e, e=entry: e["chev"].configure(
                fg=ACCENT if e["open"] else FAINT))

        tk.Frame(wrap, bg=RULE, height=1).pack(fill="x")

    def _toggle_section(self, key: str) -> None:
        target = self.sections[key]
        opening = not target["open"]
        for k, sec in self.sections.items():
            if sec["open"]:
                sec["body"].pack_forget()
                sec["open"] = False
                sec["chev"].configure(text="▸", fg=FAINT)
        if opening:
            target["body"].pack(fill="x", before=target["wrap"].winfo_children()[-1])
            target["open"] = True
            target["chev"].configure(text="▾", fg=ACCENT)
        self._fit_window()

    def _fit_window(self, width: int | None = None) -> None:
        """Resize the window to exactly fit whichever section is open."""
        self.update_idletasks()
        w = width or self.winfo_width()
        h = self.root.winfo_reqheight()
        cap = self.winfo_screenheight() - 140
        self.geometry("{}x{}".format(w, min(h, cap)))

    # ---------------------------------------------------------- section: cpu

    def _body_cpu(self, body) -> None:
        seg = ttk.Frame(body)
        seg.pack(fill="x", pady=(4, 0))
        for label, mask in self._presets():
            btn = ttk.Button(seg, text=label, style="Seg.TButton",
                             command=lambda m=mask: self._apply_mask(m))
            btn.pack(side="left", padx=(0, 4))
            self.seg_buttons.append((btn, mask))

        self.mask_desc = ttk.Label(body, text="", style="Body.TLabel")
        self.mask_desc.pack(anchor="w", pady=(9, 0))

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(12, 0))
        self.use_aff = tk.BooleanVar(value=self.cfg["use_affinity"])
        self._check(row, "Limit CPUs", self.use_aff,
                    self._on_use_aff).pack(side="left", padx=(0, 18))

        ttk.Label(row, text="Mask 0x", style="Body.TLabel").pack(side="left")
        self.mask_var = tk.StringVar()
        self.mask_entry = ttk.Entry(row, textvariable=self.mask_var, width=11,
                                    font=("Consolas", 10))
        self.mask_entry.pack(side="left", padx=(3, 18))
        self.mask_var.trace_add("write", self._on_mask_typed)

        ttk.Label(row, text="Priority", style="Body.TLabel").pack(side="left", padx=(0, 5))
        self.prio_var = tk.StringVar(value=self.cfg["priority"])
        ttk.Combobox(row, textvariable=self.prio_var, width=12, state="readonly",
                     values=list(core.PRIORITY_CLASSES)).pack(side="left")

        self._build_core_grid(body)

    def _presets(self) -> list[tuple[str, int]]:
        """Presets derived from the machine's real P-core / E-core layout."""
        classes = sorted({eff for eff, _ in self.topology}, reverse=True)
        out: list[tuple[str, int]] = []

        if len(classes) > 1:
            perf = [m for eff, m in self.topology if eff == classes[0]]
            slow = [m for eff, m in self.topology if eff != classes[0]]
            out.append(("P-cores", sum(m & -m for m in perf)))   # one thread each
            out.append(("P + HT", sum(perf)))
            if slow:
                out.append(("E-cores", sum(slow)))
        else:
            half = max(self.ncpu // 2, 1)
            out.append(("Half", (1 << half) - 1))

        out.append(("All {}".format(self.ncpu), (1 << self.ncpu) - 1))
        return out

    def _build_core_grid(self, parent) -> None:
        classes = sorted({eff for eff, _ in self.topology}, reverse=True)
        hybrid = len(classes) > 1
        cpu_class = {cpu: eff for eff, mask in self.topology
                     for cpu in core.mask_to_cpus(mask)}

        wrap = ttk.Frame(parent)
        wrap.pack(fill="x", pady=(13, 0))

        if hybrid:
            key = ttk.Frame(wrap)
            key.pack(fill="x", pady=(0, 6))
            for text, colour in (("P-cores", BLUE), ("E-cores", WARN)):
                tk.Label(key, text="▂ " + text, bg=BG, fg=colour,
                         font=("Segoe UI", 8)).pack(side="left", padx=(0, 14))

        grid = ttk.Frame(wrap)
        grid.pack(fill="x")
        for cpu in range(self.ncpu):
            var = tk.BooleanVar(value=False)
            self.cpu_vars[cpu] = var

            cell = tk.Frame(grid, bg=BG)
            cell.grid(row=cpu // 16, column=cpu % 16, padx=1, pady=1)
            chip = tk.Label(cell, text=str(cpu), width=2, bg=RAISED, fg=DIM,
                            font=("Consolas", 9), padx=3, pady=3, cursor="hand2")
            chip.pack(fill="x")
            if hybrid:
                tk.Frame(cell, height=2,
                         bg=BLUE if cpu_class.get(cpu) == classes[0] else WARN
                         ).pack(fill="x")
            self.cpu_chips[cpu] = chip

            chip.bind("<Button-1>", lambda _e, v=var: self._chip_clicked(v))
            var.trace_add("write", lambda *_a, c=cpu: self._paint_chip(c))
            var.trace_add("write", self._on_core_toggled)
            self._paint_chip(cpu)

    def _chip_clicked(self, var: tk.BooleanVar) -> None:
        if self.use_aff.get():
            var.set(not var.get())

    def _paint_chip(self, cpu: int) -> None:
        on = self.cpu_vars[cpu].get()
        enabled = self.use_aff.get()
        if on and enabled:
            self.cpu_chips[cpu].configure(bg=ACCENT_DARK, fg=ACCENT)
        elif on:
            self.cpu_chips[cpu].configure(bg=RAISED_HI, fg=FAINT)
        else:
            self.cpu_chips[cpu].configure(bg=RAISED, fg=DIM if enabled else FAINT)

    # ---------------------------------------------------------- section: gfx

    def _body_gfx(self, body) -> None:
        self.gpu_lbl = self._label(body, "Detecting adapters...", pady=(4, 0))

        self._rule(body, pady=(12, 10))

        self.gpu_pref_var = tk.BooleanVar(value=False)
        self._check(body, "Force the high-performance GPU for Bambu Studio",
                    self.gpu_pref_var, self._on_gpu_pref).pack(anchor="w")
        self._label(body,
                    "Windows per-app GPU preference — Bambu Studio is known to pick\n"
                    "the wrong adapter when more than one is present.", pady=(3, 0))

        self._rule(body, pady=(12, 10))

        self._label(body, "Threaded Optimization — set this one in NVIDIA's app",
                    style="Stat.TLabel")
        self._label(body,
                    "Documented Bambu Studio crash: NVIDIA's threaded optimization fights\n"
                    "its threading and kills it mid-slice. Nothing can set this through a\n"
                    "supported API, so it stays manual —", pady=(4, 0))
        self._label(body, core.THREADED_OPT_STEPS, style="Stat.TLabel", pady=(6, 0))
        ttk.Button(body, text="Open NVIDIA settings",
                   command=self.do_open_nvidia).pack(anchor="w", pady=(9, 0))

        self.overlay_frame = ttk.Frame(body)
        self.overlay_lbl = ttk.Label(self.overlay_frame, text="", style="Body.TLabel",
                                     justify="left")
        self.overlay_lbl.pack(anchor="w")

    def _load_graphics(self) -> None:
        """Populate the graphics section the first time it is opened."""
        self._gfx_loaded = True

        def gather():
            gpus = core.gpu_list()
            return {"gpus": gpus,
                    "nvidia": core.has_nvidia(gpus),
                    "overlay": core.nvidia_overlay_installed(),
                    "pref": core.get_gpu_preference(self.exe)}

        def show(data):
            if data["gpus"]:
                self.gpu_lbl.configure(text="\n".join(
                    "{}   {}".format(n, v) for n, v in data["gpus"]))
            else:
                self.gpu_lbl.configure(text="No display adapters reported.")

            self._syncing = True
            self.gpu_pref_var.set(data["pref"] == 2)
            self._syncing = False

            if data["overlay"]:
                self._rule(self.overlay_frame.master, pady=(12, 10))
                self.overlay_frame.pack(anchor="w", fill="x")
                self.overlay_lbl.configure(
                    text="⚠  The GeForce overlay (nvspcap64.dll) is installed and "
                         "injects itself into\n     Bambu Studio. If crashes continue, "
                         "turn the in-game overlay off in the NVIDIA app.",
                    foreground=WARN)
            self._fit_window()

        self._run_bg(gather, show)

    def _on_gpu_pref(self) -> None:
        if self._syncing:
            return
        pref = 2 if self.gpu_pref_var.get() else None
        ok, msg = core.set_gpu_preference(self.exe, pref)
        self.log(msg, "ok" if ok else "err")
        if ok:
            self.log("Restart Bambu Studio for the GPU preference to take effect.")

    def do_open_nvidia(self) -> None:
        ok, msg = core.open_nvidia_settings()
        self.log(msg, "ok" if ok else "err")

    # ------------------------------------------------------- section: plugin

    def _body_plugin(self, body) -> None:
        self.plugin_lbl = self._label(body, "checking...", pady=(4, 0))

        self._rule(body, pady=(12, 10))

        self.check_var = tk.BooleanVar(value=self.cfg["check_plugin_on_launch"])
        self.autosync_var = tk.BooleanVar(value=self.cfg["auto_sync_plugin"])
        self._check(body, "Check plugin before launch", self.check_var).pack(anchor="w")
        self._check(body, "Auto-repair the plugin if it is stale",
                    self.autosync_var).pack(anchor="w", pady=(4, 0))

        self._rule(body, pady=(12, 10))

        row1 = ttk.Frame(body)
        row1.pack(fill="x", pady=(0, 6))
        row2 = ttk.Frame(body)
        row2.pack(fill="x")
        for parent, text, cmd in (
                (row1, "Sync plugin from OTA", self.do_sync),
                (row1, "Delete plugin (reinstall)", self.do_remove),
                (row1, "Restore backup...", self.do_restore),
                (row2, "Clean logs & dumps", self.do_clean),
                (row2, "Diagnostics report", self.do_report),
                (row2, "Close Bambu Studio", self.do_kill),
                (row2, "Open data folder", self.do_folders)):
            ttk.Button(parent, text=text, command=cmd).pack(side="left", padx=(0, 6))

    def do_folders(self) -> None:
        """Open the Bambu data folder, which holds both the plugins and log dirs."""
        self._open(core.DATA)

    # ---------------------------------------------------------- section: log

    def _body_log(self, body) -> None:
        self.tray_var = tk.BooleanVar(value=self.cfg["minimize_to_tray"])
        self.close_with_var = tk.BooleanVar(value=self.cfg["close_with_bambu"])
        self._check(body, "Minimise to the notification area while Bambu Studio runs",
                    self.tray_var).pack(anchor="w", pady=(4, 0))
        self._check(body, "Close the launcher when Bambu Studio exits normally",
                    self.close_with_var).pack(anchor="w", pady=(4, 0))

        self.auto_var = tk.BooleanVar(value=self.cfg["auto_start"])
        self._check(body, "Start hidden and launch Bambu Studio automatically",
                    self.auto_var).pack(anchor="w", pady=(4, 0))
        self._label(body,
                    "Unattended mode. The launcher starts hidden, runs its checks, and\n"
                    "starts Bambu Studio only if they pass; otherwise it shows itself and\n"
                    "explains why. Hold Shift while starting to bypass it.",
                    style="Faint.TLabel", pady=(3, 0))

        self._rule(body, pady=(12, 10))

        self.console = tk.Text(body, height=9, bg="#101216", fg="#c3c9d2", bd=0,
                               font=("Consolas", 9), wrap="word", padx=12, pady=9,
                               highlightthickness=0, state="disabled")
        self.console.pack(fill="x", pady=(4, 0))
        for tag, colour in (("ok", ACCENT), ("err", DANGER), ("warn", WARN),
                            ("time", FAINT)):
            self.console.tag_configure(tag, foreground=colour)

    # --------------------------------------------------------------- bottom

    def _build_bottom(self, root) -> None:
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(16, 0))

        coffee = tk.Label(bar, text="☕  Buy me a coffee", bg=BG, fg=COFFEE,
                          font=("Segoe UI", 9), cursor="hand2")
        coffee.pack(side="left")
        coffee.bind("<Button-1>", lambda _e: self.do_coffee())
        coffee.bind("<Enter>", lambda _e: coffee.configure(
            font=("Segoe UI", 9, "underline")))
        coffee.bind("<Leave>", lambda _e: coffee.configure(font=("Segoe UI", 9)))

        self.launch_btn = ttk.Button(bar, text="▶  Launch", style="Go.TButton",
                                     command=self.do_launch)
        self.launch_btn.pack(side="right")

    def do_coffee(self) -> None:
        core.open_url(COFFEE_URL)
        self.log("Opened {} - thank you!".format(COFFEE_URL), "ok")

    # ------------------------------------------------------------ utilities

    def log(self, msg: str, tag: str = "") -> None:
        def write():
            self.console.configure(state="normal")
            self.console.insert("end", "{:%H:%M:%S}  ".format(datetime.now()), "time")
            self.console.insert("end", msg + "\n", tag)
            self.console.see("end")
            self.console.configure(state="disabled")
            sec = self.sections.get("log")
            if sec and not sec["open"]:
                self._log_count = getattr(self, "_log_count", 0) + 1
                sec["summary"].configure(text="{} event{}".format(
                    self._log_count, "" if self._log_count == 1 else "s"))
        self.after(0, write)

    def _open(self, path: Path) -> None:
        if path.is_dir():
            os.startfile(str(path))
        else:
            self.log("Folder does not exist: {}".format(path), "warn")

    def _run_bg(self, fn, done=None) -> None:
        """Run a slow call off the UI thread."""
        def worker():
            try:
                result = fn()
            except Exception as exc:                  # surface, never swallow
                self.log("Failed: {}".format(exc), "err")
                return
            if done:
                self.after(0, lambda: done(result))
        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------- affinity

    def _current_mask(self) -> int:
        return core.cpus_to_mask(c for c, v in self.cpu_vars.items() if v.get())

    def _apply_mask(self, mask: int) -> None:
        self._syncing = True
        for cpu, var in self.cpu_vars.items():
            var.set(bool(mask & (1 << cpu)))
        self.mask_var.set("{:X}".format(mask))
        self._syncing = False
        self._refresh_mask_ui(mask)

    def _on_core_toggled(self, *_) -> None:
        if self._syncing:
            return
        mask = self._current_mask()
        self._syncing = True
        self.mask_var.set("{:X}".format(mask))
        self._syncing = False
        self._refresh_mask_ui(mask)

    def _on_mask_typed(self, *_) -> None:
        if self._syncing:
            return
        text = self.mask_var.get().strip().lower().replace("0x", "")
        try:
            mask = int(text, 16) if text else 0
        except ValueError:
            self.mask_desc.configure(text="Not a valid hex mask", foreground=DANGER)
            return
        self._syncing = True
        for cpu, var in self.cpu_vars.items():
            var.set(bool(mask & (1 << cpu)))
        self._syncing = False
        self._refresh_mask_ui(mask)

    def _refresh_mask_ui(self, mask: int) -> None:
        for btn, preset in self.seg_buttons:
            btn.configure(style="SegOn.TButton" if preset == mask else "Seg.TButton")

        if not self.use_aff.get():
            text, colour = "Not limited · all {} CPUs".format(self.ncpu), FAINT
        elif mask & ~((1 << self.ncpu) - 1):
            text, colour = "Mask includes CPUs this machine lacks", DANGER
        elif not mask:
            text, colour = "No CPUs selected", WARN
        else:
            text, colour = core.describe_mask(mask), DIM

        self.mask_desc.configure(text=text, foreground=colour)
        named = [lbl for (b, m) in self.seg_buttons
                 for lbl in [b.cget("text")] if m == mask]
        self.sections["cpu"]["summary"].configure(
            text=named[0] if named else text.split(" ·")[0])

    def _on_use_aff(self) -> None:
        on = self.use_aff.get()
        self.mask_entry.configure(state="normal" if on else "disabled")
        for btn, _ in self.seg_buttons:
            btn.configure(state="normal" if on else "disabled")
        for cpu in self.cpu_vars:
            self._paint_chip(cpu)
        self._refresh_mask_ui(self._current_mask())

    # --------------------------------------------------------------- actions

    def refresh_status(self) -> None:
        for dot, lbl in self.stats.values():
            dot.configure(fg=FAINT)
            lbl.configure(text="checking...", foreground=DIM)

        def gather():
            return {
                "version": core.app_version(self.exe) if self.exe.exists() else "not found",
                "running": core.is_running(),
                "plugin": core.plugin_status(),
                "logs": core.log_stats(),
                "crashes": core.crash_history(7),
            }

        def show(data):
            running = data["running"]
            self.sub_lbl.configure(text="v{}  ·  {}".format(
                data["version"],
                "running (PID {})".format(running[0]) if running else "not running"))

            st = data["plugin"]
            text = {"ok": "Plugin up to date", "stale": "Plugin OUT OF DATE",
                    "absent": "Plugin not installed", "no-ota": "Plugin OK",
                    "unknown": "Plugin unknown"}
            colour = {"ok": ACCENT, "no-ota": ACCENT, "stale": DANGER,
                      "absent": WARN}.get(st["state"], FAINT)
            dot, lbl = self.stats["plugin"]
            dot.configure(fg=colour)
            lbl.configure(text=text.get(st["state"], st["state"]), foreground=FG)
            self.plugin_lbl.configure(text=st["detail"],
                                      foreground=DANGER if st["state"] == "stale" else DIM)
            self.sections["plugin"]["summary"].configure(
                text=text.get(st["state"], ""))

            crashes = data["crashes"]
            dot, lbl = self.stats["crashes"]
            dot.configure(fg=DANGER if crashes else ACCENT)
            lbl.configure(text="{} crash{} in 7 days".format(
                len(crashes), "" if len(crashes) == 1 else "es"), foreground=FG)

            total, dumps, _ = data["logs"]
            dot, lbl = self.stats["logs"]
            dot.configure(fg=WARN if total > 300 * 1024 * 1024 else FAINT)
            lbl.configure(text="{} logs · {} dump{}".format(
                core.human(total), dumps, "" if dumps == 1 else "s"), foreground=FG)

            if st["state"] == "stale":
                self.log(st["detail"], "err")
            if crashes:
                self.log("Most recent crash {} in {}".format(
                    crashes[0]["time"], crashes[0]["module"]), "warn")

        self._run_bg(gather, show)

    def do_launch(self) -> None:
        if not self.exe.exists():
            messagebox.showerror("Bambu Studio not found",
                                 "Could not find:\n{}\n\nEdit config.json to point at "
                                 "the correct path.".format(self.exe))
            return
        if core.is_running() and not messagebox.askyesno(
                "Already running",
                "Bambu Studio is already running. Launch another instance anyway?"):
            return

        mask = self._current_mask() if self.use_aff.get() else 0
        if self.use_aff.get() and not mask:
            messagebox.showwarning("No CPUs selected",
                                   "Pick at least one CPU, or untick 'Limit CPUs'.")
            return

        self._save_state()
        self.launch_btn.configure(state="disabled", text="Starting...")

        def go():
            if self.check_var.get():
                st = core.plugin_status()
                if st["state"] == "stale":
                    self.log("Plugin out of date: {}".format(st["detail"]), "warn")
                    if self.autosync_var.get():
                        ok, msg = core.sync_plugins()
                        self.log(msg, "ok" if ok else "err")
                    else:
                        self.log("Launching anyway - tick Auto-repair under "
                                 "Plugin & Maintenance to fix this automatically.", "warn")
                elif st["state"] == "absent":
                    self.log("No plugins installed; Bambu Studio should offer to "
                             "download them.", "warn")
            return core.launch(self.exe, mask, self.prio_var.get(), on_exit=self._on_exit)

        def done(result):
            pid, err = result
            self.launch_btn.configure(state="normal", text="▶  Launch")
            if pid is None:
                self.log("Launch failed: {}".format(err), "err")
                messagebox.showerror("Launch failed", str(err))
                return
            if err:
                self.log(err, "warn")
            self.log("Launched PID {} · {} · {} priority".format(
                pid, core.describe_mask(mask) if mask else "all CPUs",
                self.prio_var.get().lower()), "ok")
            self.after(1500, self.refresh_status)
            # Long enough for the launch line to be read before the window goes.
            self.after(1800, lambda: self._to_tray(pid))

        self._run_bg(go, done)

    # ------------------------------------------------------ unattended start

    def _auto_start(self) -> None:
        """Verify everything, then start Bambu Studio without ever appearing.

        Anything unexpected reveals the window instead. The point of the mode
        is to be invisible when all is well, not to be silent when it is not.
        """
        self.log("Unattended start: running checks.")

        # Read the Tk variables here; the checks themselves run off-thread.
        use_aff = self.use_aff.get()
        mask = self._current_mask() if use_aff else 0
        autosync = self.autosync_var.get()

        def check():
            if not self.exe.exists():
                return "Bambu Studio was not found at {}".format(self.exe)
            if core.is_running():
                return "Bambu Studio is already running"
            if use_aff and not mask:
                return "No CPUs are selected"

            st = core.plugin_status()
            if st["state"] == "absent":
                return "No network plugin is installed"
            if st["state"] == "stale":
                if not autosync:
                    return "The network plugin is out of date: {}".format(st["detail"])
                ok, msg = core.sync_plugins()
                self.log(msg, "ok" if ok else "err")
                if not ok:
                    return "The network plugin could not be repaired automatically"
            return None

        def done(problem):
            if problem:
                self.log(problem, "warn")
                self.log("Unattended start stopped. Showing the launcher.", "warn")
                self._restore_window()
                self._open_section("plugin" if "plugin" in problem.lower() else "log")
                return
            self.log("Checks passed.", "ok")
            self.do_launch()

        self._run_bg(check, done)

    # ------------------------------------------------------- tray monitoring

    def _to_tray(self, pid: int) -> None:
        """Hide the launcher and keep watch from the notification area."""
        if not self.tray_var.get():
            # An unattended start leaves the window withdrawn. Without a tray
            # icon there would be no way back to it, so show it instead.
            if self._hidden:
                self._restore_window()
            return

        self._tray = bambu_tray.TrayIcon(
            "Bambu Studio running (PID {})".format(pid),
            ICON_ICO,
            # Tray callbacks arrive on the tray's own message-loop thread;
            # hand them to Tk rather than touching widgets from there.
            on_activate=lambda: self.after(0, self._restore_window),
            on_quit=lambda: self.after(0, self._on_close),
        )
        if self._tray.show():
            self._hidden = True
            self.withdraw()
            self.log("Minimised to the notification area. Monitoring Bambu Studio.")
        else:
            self._tray = None
            self._hidden = True
            self.iconify()
            self.log("Notification area unavailable; minimised to the taskbar "
                     "instead. Monitoring continues.", "warn")

    def _close_tray(self) -> None:
        if self._tray is not None:
            self._tray.close()
            self._tray = None

    def _restore_window(self) -> None:
        self._hidden = False
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _open_section(self, key: str) -> None:
        if not self.sections[key]["open"]:
            self._toggle_section(key)

    def _on_exit(self, code: int) -> None:
        """Called on the process-watcher thread when Bambu Studio ends."""
        clean, desc = core.explain_exit_code(code)
        self.after(0, lambda: self._handle_exit(clean, desc))

    def _handle_exit(self, clean: bool, desc: str) -> None:
        self.log("Bambu Studio {}".format(desc), "ok" if clean else "err")

        if clean:
            # A normal quit is the user's decision to stop; follow them out.
            if self._hidden and self.close_with_var.get():
                self._close_tray()
                self._on_close()
                return
            self._close_tray()
            if self._hidden:
                self._restore_window()
            self.refresh_status()
            return

        # A crash is the one case worth interrupting for: surface the window
        # with the activity log already open on the decoded fault.
        if self._tray is not None:
            self._tray.notify("Bambu Studio crashed", desc, "error")
            # Leave the icon up briefly so the balloon is not cut off.
            self.after(8000, self._close_tray)
        self._restore_window()
        self._open_section("log")
        self.log("Plugin & Maintenance → Diagnostics report captures the details.",
                 "warn")
        self.refresh_status()

    def _simple_action(self, fn) -> None:
        def done(result):
            ok, msg = result
            self.log(msg, "ok" if ok else "err")
            self.refresh_status()
        self._run_bg(fn, done)

    def do_sync(self) -> None:
        self._simple_action(core.sync_plugins)

    def do_kill(self) -> None:
        self._simple_action(core.kill_running)

    def do_remove(self) -> None:
        if not messagebox.askyesno(
                "Delete network plugin?",
                "This deletes every DLL in:\n\n{}\n\nA backup is saved first, and "
                "Bambu Studio will offer to download a fresh copy on next launch."
                "\n\nContinue?".format(core.PLUGINS)):
            return

        def done(result):
            ok, msg = result
            self.log(msg, "ok" if ok else "err")
            if ok:
                messagebox.showinfo("Plugin removed", msg)
            self.refresh_status()
        self._run_bg(core.remove_plugins, done)

    def do_clean(self) -> None:
        keep = int(self.cfg.get("keep_dumps", 2))
        total, dumps, dump_bytes = core.log_stats()
        if not messagebox.askyesno(
                "Clean logs?",
                "Log folder is {} with {} crash dump(s) using {}.\n\nDelete all but "
                "the {} newest dump(s), plus logs older than a day?".format(
                    core.human(total), dumps, core.human(dump_bytes), keep)):
            return
        self._simple_action(lambda: core.clean_logs(keep))

    def do_report(self) -> None:
        self.log("Collecting diagnostics (reads the Windows event log)...")

        def build():
            text = core.diagnostics_report(self.exe)
            path = core.USER_DIR / "diagnostics_{:%Y%m%d_%H%M%S}.txt".format(datetime.now())
            path.write_text(text, encoding="utf-8")
            return path

        def done(path):
            self.log("Diagnostics written to {}".format(path.name), "ok")
            if messagebox.askyesno("Diagnostics saved",
                                   "Saved to:\n{}\n\nOpen it now?".format(path)):
                os.startfile(str(path))
        self._run_bg(build, done)

    def do_restore(self) -> None:
        backups = core.list_backups()
        if not backups:
            messagebox.showinfo("No backups", "No plugin backups have been made yet.")
            return

        win = tk.Toplevel(self)
        win.title("Restore plugin backup")
        win.configure(bg=BG)
        win.geometry("480x300")
        win.transient(self)
        win.grab_set()
        if ICON_ICO.exists():
            try:
                win.iconbitmap(str(ICON_ICO))
            except tk.TclError:
                pass

        ttk.Label(win, text="Restore which snapshot over the current plugins?",
                  style="Sub.TLabel").pack(anchor="w", padx=18, pady=(16, 9))
        box = tk.Listbox(win, bg=RAISED, fg=FG, bd=0, highlightthickness=0,
                         font=("Consolas", 9), selectbackground=ACCENT,
                         selectforeground=ACCENT_TEXT, activestyle="none")
        box.pack(fill="both", expand=True, padx=18)
        for name in backups:
            box.insert("end", name)
        box.selection_set(0)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=18, pady=14)

        def confirm():
            sel = box.curselection()
            if not sel:
                return
            name = backups[sel[0]]
            win.destroy()
            self._simple_action(lambda: core.restore_backup(name))

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Restore", style="Go.TButton",
                   command=confirm).pack(side="right", padx=(0, 8))

    # ----------------------------------------------------------------- state

    def _save_state(self) -> None:
        self.cfg.update({
            "use_affinity": self.use_aff.get(),
            "affinity_mask": self._current_mask() or core.DEFAULTS["affinity_mask"],
            "priority": self.prio_var.get(),
            "check_plugin_on_launch": self.check_var.get(),
            "auto_sync_plugin": self.autosync_var.get(),
            "minimize_to_tray": self.tray_var.get(),
            "close_with_bambu": self.close_with_var.get(),
            "auto_start": self.auto_var.get(),
            "exe": str(self.exe),
        })
        core.save_cfg(self.cfg)

    def _on_close(self) -> None:
        self._save_state()
        self._close_tray()
        self.destroy()


def main() -> int:
    import sys
    if not sys.platform.startswith("win"):
        print("This launcher is Windows-only.", file=sys.stderr)
        return 1
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text on HiDPI
    except Exception:
        pass
    Launcher().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
