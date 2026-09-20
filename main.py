"""
EZUnlocker — Windows Recovery Multitool
Real-Time Background Health Check & Auto-Refresh Edition

Architecture:
• AutoRefreshDispatcher — central timer via after(). Every 2500ms calls
  auto_refresh() on the active page. force_refresh() fires immediately
  and resets the timer. Used after tab switches and action buttons.
• Each page's auto_refresh() uses threading.Lock (non-blocking) + daemon
  thread for I/O, then posts to main thread via self.after(0, callback).
• ProcessManagerPage  — preserves selected PID + yview fraction.
• StartupManagerPage  — entry-level diff, green highlight on new entries.
• RegistryUnlockerPage — live Locked/Unlocked dot indicators.
• ToolsRescuePage     — os.listdir watcher, auto-shows new .exe drops.
"""

import ctypes
import os
import subprocess
import sys
import threading
import winreg
from typing import Optional

import customtkinter as ctk
import psutil
from tkinter import messagebox, ttk
import tkinter as tk


# ─── DESIGN TOKENS ──────────────────────────────────────────────────────────────────────────────────
BG_DEEP          = "#0B0C10"
BG_CARD          = "#111318"
BG_SIDEBAR       = "#0E0F14"
BORDER           = "#1E2030"
ACCENT           = "#6C63FF"
ACCENT_HOVER     = "#7B74FF"
TEXT_PRIMARY     = "#F0F2FF"
TEXT_MUTED       = "#6B7280"
TEXT_DESC        = "#9CA3AF"
DANGER           = "#EF4444"
DANGER_HOVER     = "#F87171"
SUCCESS          = "#22C55E"
WARNING          = "#F59E0B"
SIDEBAR_W        = 220
REFRESH_INTERVAL_MS = 2500


# ─── ADMIN ────────────────────────────────────────────────────────────────────────────────────
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def request_elevation() -> None:
    try:
        script = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}" {params}', None, 1
        )
    except Exception as exc:
        messagebox.showerror("Elevation Failed", str(exc))
    sys.exit(0)


# ─── PATH HELPERS ─────────────────────────────────────────────────────────────────────────────────
def get_exe_dir() -> str:
    """
    Always returns the directory of the actual running .exe.
    When frozen by PyInstaller, sys.executable points to EZUnlocker.exe.
    sys._MEIPASS is the TEMP extraction dir — never use it for runtime assets.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_tools_dir() -> str:
    return os.path.join(get_exe_dir(), "tools")


def scan_custom_tools() -> list:
    d = get_tools_dir()
    if not os.path.isdir(d):
        return []
    return [
        os.path.join(d, f)
        for f in sorted(os.listdir(d))
        if f.lower().endswith(".exe")
    ]


# ─── CONSTANTS ────────────────────────────────────────────────────────────────────────────────────
CRITICAL_PROCS = {"system", "csrss.exe", "lsass.exe", "smss.exe", "wininit.exe"}

REG_UNLOCK_TARGETS = {
    "Task Manager (DisableTaskMgr)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
        "DisableTaskMgr",
    ),
    "Registry Editor (DisableRegistryTools)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
        "DisableRegistryTools",
    ),
    "Command Prompt (DisableCMD)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Policies\Microsoft\Windows\System",
        "DisableCMD",
    ),
    "Run Dialog (NoRun)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer",
        "NoRun",
    ),
}

AUTORUN_KEYS = [
    (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Run",     "HKCU\\Run"),
    (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU\\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run",     "HKLM\\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM\\RunOnce"),
]

SYSTEM_TOOLS = {
    "Command Prompt":  ("cmd.exe",        ["/K", "echo EZUnlocker ready"]),
    "PowerShell":      ("powershell.exe", ["-NoExit", "-Command", "Write-Host EZUnlocker"]),
    "Registry Editor": ("regedit.exe",    []),
    "MSConfig":        ("msconfig.exe",   []),
}


# ─── REGISTRY HELPERS ─────────────────────────────────────────────────────────────────────────────
def bytes_to_mb(b: int) -> str:
    return f"{b / 1_048_576:.1f} MB"


def delete_reg_value(hive, subkey: str, value_name: str):
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, value_name)
        return True, "OK"
    except FileNotFoundError:
        return True, "Already absent"
    except PermissionError:
        return False, "Access denied — run as Administrator"
    except Exception as exc:
        return False, str(exc)


def set_reg_value(hive, subkey: str, value_name: str, value: str):
    try:
        with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, value)
        return True, "OK"
    except PermissionError:
        return False, "Access denied — run as Administrator"
    except Exception as exc:
        return False, str(exc)


# ─── CUSTOM WIDGETS ───────────────────────────────────────────────────────────────────────────────────
class GlowButton(ctk.CTkButton):
    def __init__(self, master, danger=False, **kwargs):
        kwargs.setdefault("fg_color",      DANGER if danger else ACCENT)
        kwargs.setdefault("hover_color",   DANGER_HOVER if danger else ACCENT_HOVER)
        kwargs.setdefault("text_color",    TEXT_PRIMARY)
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("height",        36)
        kwargs.setdefault("font",          ctk.CTkFont(family="Segoe UI", size=13, weight="bold"))
        super().__init__(master, **kwargs)


class CardFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color",      BG_CARD)
        kwargs.setdefault("corner_radius", 14)
        kwargs.setdefault("border_width",  1)
        kwargs.setdefault("border_color",  BORDER)
        super().__init__(master, **kwargs)


class SectionLabel(ctk.CTkLabel):
    def __init__(self, master, text, **kwargs):
        kwargs.setdefault("text_color", TEXT_PRIMARY)
        kwargs.setdefault("font", ctk.CTkFont(family="Segoe UI", size=16, weight="bold"))
        super().__init__(master, text=text, **kwargs)


class MutedLabel(ctk.CTkLabel):
    def __init__(self, master, text, **kwargs):
        kwargs.setdefault("text_color", TEXT_MUTED)
        kwargs.setdefault("font", ctk.CTkFont(family="Segoe UI", size=12))
        super().__init__(master, text=text, **kwargs)


class StatusBar(ctk.CTkLabel):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("text",       "  Ready.")
        kwargs.setdefault("text_color", TEXT_MUTED)
        kwargs.setdefault("font",       ctk.CTkFont(family="Segoe UI", size=11))
        kwargs.setdefault("anchor",     "w")
        super().__init__(master, **kwargs)

    def set(self, msg: str, color: str = TEXT_MUTED):
        self.configure(text=f"  {msg}", text_color=color)


class LiveDot(ctk.CTkLabel):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("text",       "\u25cf Live")
        kwargs.setdefault("text_color", SUCCESS)
        kwargs.setdefault("font",       ctk.CTkFont(family="Segoe UI", size=11))
        super().__init__(master, **kwargs)

    def set_active(self, active: bool):
        self.configure(
            text="\u25cf Live" if active else "\u25cb Paused",
            text_color=SUCCESS if active else TEXT_MUTED,
        )


# ─── THEMED TREEVIEW ──────────────────────────────────────────────────────────────────────────────────
def apply_tree_style(tree, style_name: str):
    style = ttk.Style()
    style.theme_use("default")
    style.configure(f"{style_name}.Treeview",
        background=BG_CARD, foreground=TEXT_PRIMARY, fieldbackground=BG_CARD,
        borderwidth=0, rowheight=28, font=("Segoe UI", 11))
    style.configure(f"{style_name}.Treeview.Heading",
        background="#16171F", foreground=TEXT_DESC,
        font=("Segoe UI", 11, "bold"), relief="flat", borderwidth=0)
    style.map(f"{style_name}.Treeview",
        background=[("selected", ACCENT)], foreground=[("selected", "#FFFFFF")])
    tree.configure(style=f"{style_name}.Treeview")


# ─── AUTO-REFRESH DISPATCHER ────────────────────────────────────────────────────────────────────────────
class AutoRefreshDispatcher:
    """
    Central reactive dispatcher. Runs on Tk main thread via after().

    API:
        start()         — begin the loop after window is ready
        stop()          — clean shutdown on WM_DELETE_WINDOW
        force_refresh() — cancel pending tick, fire now, re-arm
                          Call after every action button and tab switch.

    Page contract:
        Each page implements auto_refresh() which MUST:
          1. Acquire self._fetch_lock non-blocking (return if busy).
          2. Spawn a daemon thread for I/O (psutil / winreg).
          3. Post UI updates via self.after(0, callback) — never touch
             Tkinter widgets from background threads.
    """

    def __init__(self, app):
        self._app      = app
        self._timer_id = None
        self._running  = False

    def start(self):
        self._running = True
        self._schedule_next()

    def stop(self):
        self._running = False
        self._cancel_pending()

    def force_refresh(self):
        self._cancel_pending()
        self._tick()

    def _cancel_pending(self):
        if self._timer_id is not None:
            try:
                self._app.after_cancel(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def _schedule_next(self):
        if self._running:
            self._timer_id = self._app.after(REFRESH_INTERVAL_MS, self._tick)

    def _tick(self):
        page = self._app.pages.get(self._app.current_page_name)
        if page is not None and hasattr(page, "auto_refresh"):
            try:
                page.auto_refresh()
            except Exception:
                pass  # Never crash the loop on page errors
        self._schedule_next()


# ─── PAGE: PROCESS MANAGER ──────────────────────────────────────────────────────────────────────────────
class ProcessManagerPage(ctk.CTkFrame):
    """
    _smart_populate() preserves:
        selected PID  — re-selected and scrolled into view after repopulation
        yview fraction — restored when nothing is selected
    This eliminates visible jump / flicker during auto-refresh.
    """

    COLUMNS    = ("pid", "name", "ram", "path")
    COL_LABELS = {"pid": "PID", "name": "Name", "ram": "RAM", "path": "Path"}
    COL_WIDTHS = {"pid": 70, "name": 180, "ram": 90, "path": 420}

    def __init__(self, master, status_bar, dispatcher, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._status     = status_bar
        self._dispatcher = dispatcher
        self._all_rows   = []
        self._fetch_lock = threading.Lock()
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\u2699  Process Manager").pack(side="left")
        MutedLabel(hdr, text="Live view \u00b7 auto-refreshes every 2.5 s").pack(
            side="left", padx=(12, 0), pady=(4, 0))

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=20, pady=(0, 8))
        GlowButton(toolbar, text="\u27f3  Refresh Now",
            command=self._manual_refresh, width=130).pack(side="left", padx=(0, 8))
        GlowButton(toolbar, text="\u2715  Kill Process",
            command=self._kill_selected, danger=True, width=130).pack(side="left", padx=(0, 8))
        GlowButton(toolbar, text="\U0001f4c2  Open Folder",
            command=self._open_folder, width=130).pack(side="left", padx=(0, 8))
        self._live_dot = LiveDot(toolbar)
        self._live_dot.pack(side="left", padx=(12, 0))

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(toolbar, textvariable=self._search_var,
            placeholder_text="Filter by name or PID\u2026",
            width=220, fg_color=BG_CARD, border_color=BORDER,
            text_color=TEXT_PRIMARY, placeholder_text_color=TEXT_MUTED,
            corner_radius=10).pack(side="right")

        card = CardFrame(self)
        card.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        frame = ctk.CTkFrame(card, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        self._tree = ttk.Treeview(frame, columns=self.COLUMNS, show="headings",
            yscrollcommand=vsb.set, xscrollcommand=hsb.set, selectmode="browse")
        apply_tree_style(self._tree, "Proc")

        for col in self.COLUMNS:
            self._tree.heading(col, text=self.COL_LABELS[col],
                               command=lambda c=col: self._sort(c, False))
            self._tree.column(col, width=self.COL_WIDTHS[col],
                              minwidth=60, stretch=(col == "path"))
        vsb.config(command=self._tree.yview)
        hsb.config(command=self._tree.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True)

    def auto_refresh(self):
        if not self._fetch_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._fetch_procs, daemon=True).start()

    def _fetch_procs(self):
        rows = []
        try:
            for proc in psutil.process_iter(["pid", "name", "memory_info", "exe"]):
                try:
                    info = proc.info
                    rows.append((
                        info["pid"],
                        info["name"] or "\u2014",
                        bytes_to_mb(info["memory_info"].rss) if info["memory_info"] else "\u2014",
                        info["exe"] or "\u2014",
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass  # Vanished mid-iteration
        finally:
            self._fetch_lock.release()
        self.after(0, self._smart_populate, rows)

    def _smart_populate(self, rows):
        self._all_rows = rows
        # 1. Snapshot state before clearing
        sel_items = self._tree.selection()
        sel_pid = None
        if sel_items:
            try:
                sel_pid = str(self._tree.item(sel_items[0], "values")[0])
            except (IndexError, tk.TclError):
                pass
        yview_fraction = self._tree.yview()[0]

        # 2. Clear and repopulate with filter
        flt = self._search_var.get().lower()
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        new_sel_iid = None
        for pid, name, ram, path in rows:
            if flt and flt not in name.lower() and flt not in str(pid):
                continue
            iid = self._tree.insert("", "end", values=(pid, name, ram, path))
            if str(pid) == sel_pid:
                new_sel_iid = iid

        # 3. Restore selection or scroll position
        if new_sel_iid:
            self._tree.selection_set(new_sel_iid)
            self._tree.see(new_sel_iid)
        elif yview_fraction > 0.001:
            self._tree.yview_moveto(yview_fraction)

        shown = len(self._tree.get_children())
        self._status.set(
            f"Processes: {shown} of {len(rows)}  \u2022  Auto-refresh active", TEXT_MUTED)

    def _apply_filter(self):
        self._smart_populate(self._all_rows)

    def _manual_refresh(self):
        self._dispatcher.force_refresh()

    def _sort(self, col, reverse):
        data = [(self._tree.set(k, col), k) for k in self._tree.get_children("")]
        try:
            data.sort(key=lambda t: float(t[0].replace(" MB", "")), reverse=reverse)
        except ValueError:
            data.sort(key=lambda t: t[0].lower(), reverse=reverse)
        for idx, (_, k) in enumerate(data):
            self._tree.move(k, "", idx)
        self._tree.heading(col, command=lambda: self._sort(col, not reverse))

    def _selected_values(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a process first.")
            return None
        return self._tree.item(sel[0], "values")

    def _kill_selected(self):
        vals = self._selected_values()
        if vals is None:
            return
        pid, name = int(vals[0]), vals[1]
        if name.lower() in CRITICAL_PROCS:
            messagebox.showwarning("\u26a0 Critical Process",
                f'"{name}" is a critical Windows system process.\n'
                "Terminating it will cause a BSOD.\n\nOperation cancelled.")
            return
        if not messagebox.askyesno("Confirm Kill",
            f"Forcefully terminate PID {pid} \u2014 {name}?\nUnsaved data will be lost."):
            return
        try:
            psutil.Process(pid).kill()
            self._status.set(f"Killed {name} (PID {pid}).", DANGER)
        except psutil.AccessDenied:
            messagebox.showerror("Access Denied", "Run EZUnlocker as Administrator.")
            return
        except psutil.NoSuchProcess:
            messagebox.showinfo("Already Gone", "Process no longer exists.")
        self._dispatcher.force_refresh()

    def _open_folder(self):
        vals = self._selected_values()
        if vals is None:
            return
        path = vals[3]
        if path == "\u2014" or not os.path.exists(path):
            messagebox.showwarning("Path Unavailable", "Could not resolve executable path.")
            return
        subprocess.Popen(["explorer", "/select,", path])


# ─── PAGE: STARTUP MANAGER ──────────────────────────────────────────────────────────────────────────────
class StartupManagerPage(ctk.CTkFrame):
    """
    Entry-level diff on each tick:
    - New entries are inserted with a 3-second green highlight.
    - Vanished entries are removed.
    - Unchanged entries are left alone (no scroll/selection reset).
    A virus re-registering in Run/RunOnce appears within 2.5 s.
    """

    COLUMNS    = ("name", "command", "regpath")
    COL_LABELS = {"name": "Name", "command": "Command / Path", "regpath": "Registry Key"}
    COL_WIDTHS = {"name": 200, "command": 380, "regpath": 220}

    def __init__(self, master, status_bar, dispatcher, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._status     = status_bar
        self._dispatcher = dispatcher
        self._fetch_lock = threading.Lock()
        self._entry_map  = {}   # iid -> (hive, subkey, name)
        self._displayed  = set()
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\U0001f680  Startup Manager").pack(side="left")
        MutedLabel(hdr,
            text="HKCU/HKLM Run/RunOnce \u00b7 new entries appear within 2.5 s"
        ).pack(side="left", padx=(12, 0), pady=(4, 0))

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=20, pady=(0, 8))
        GlowButton(toolbar, text="\u27f3  Refresh Now",
            command=self._manual_refresh, width=130).pack(side="left", padx=(0, 8))
        GlowButton(toolbar, text="\U0001f5d1  Remove Entry",
            command=self._remove_selected, danger=True, width=140).pack(side="left")
        self._live_dot = LiveDot(toolbar)
        self._live_dot.pack(side="left", padx=(12, 0))

        card = CardFrame(self)
        card.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        frame = ctk.CTkFrame(card, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        self._tree = ttk.Treeview(frame, columns=self.COLUMNS, show="headings",
            yscrollcommand=vsb.set, xscrollcommand=hsb.set, selectmode="browse")
        apply_tree_style(self._tree, "Startup")
        for col in self.COLUMNS:
            self._tree.heading(col, text=self.COL_LABELS[col])
            self._tree.column(col, width=self.COL_WIDTHS[col],
                              minwidth=80, stretch=(col == "command"))
        vsb.config(command=self._tree.yview)
        hsb.config(command=self._tree.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True)

    def auto_refresh(self):
        if not self._fetch_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._fetch_entries, daemon=True).start()

    def _fetch_entries(self):
        entries = []
        try:
            for hive, subkey, label in AUTORUN_KEYS:
                hive_id = id(hive)
                try:
                    with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as k:
                        i = 0
                        while True:
                            try:
                                name, data, _ = winreg.EnumValue(k, i)
                                iid = f"{hive_id}::{subkey}::{name}"
                                entries.append((iid, name, data, label, hive, subkey))
                                i += 1
                            except OSError:
                                break
                except (FileNotFoundError, PermissionError):
                    pass
        finally:
            self._fetch_lock.release()
        self.after(0, self._apply_diff, entries)

    def _apply_diff(self, entries):
        incoming  = {e[0] for e in entries}
        removed   = self._displayed - incoming
        added     = incoming - self._displayed
        is_first  = len(self._displayed) == 0

        for iid in removed:
            try:
                self._tree.delete(iid)
            except tk.TclError:
                pass
            self._entry_map.pop(iid, None)

        for iid, name, data, label, hive, subkey in entries:
            if iid in added:
                try:
                    self._tree.insert("", "end", iid=iid, values=(name, data, label))
                    self._entry_map[iid] = (hive, subkey, name)
                    if not is_first:
                        self._tree.item(iid, tags=("new",))
                        self._tree.tag_configure("new", background="#1B2E1B")
                        self.after(3000, self._clear_tag, iid)
                except tk.TclError:
                    pass

        self._displayed = incoming
        self._status.set(
            f"Startup entries: {len(incoming)}  \u2022  Monitoring Run/RunOnce\u2026",
            TEXT_MUTED)

    def _clear_tag(self, iid):
        try:
            if self._tree.exists(iid):
                self._tree.item(iid, tags=())
        except tk.TclError:
            pass

    def _manual_refresh(self):
        self._dispatcher.force_refresh()

    def _remove_selected(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select an entry to remove.")
            return
        iid   = sel[0]
        entry = self._entry_map.get(iid)
        if not entry:
            return
        hive, subkey, name = entry
        label = self._tree.item(iid, "values")[2]
        if not messagebox.askyesno("Confirm Remove",
            f"Remove {name!r} from {label}?\nIt will no longer run at startup."):
            return
        ok, msg = delete_reg_value(hive, subkey, name)
        if ok:
            self._status.set(f"Removed {name!r} from {label}.", SUCCESS)
            self._dispatcher.force_refresh()
        else:
            messagebox.showerror("Error", msg)
            self._status.set(f"Failed: {msg}", DANGER)


# ─── PAGE: REGISTRY UNLOCKER ────────────────────────────────────────────────────────────────────────────
class RegistryUnlockerPage(ctk.CTkFrame):
    """
    _fetch_states() reads keys in a daemon thread.
    _apply_scan() updates each row indicator on the main thread.
    If a virus re-locks a key the indicator turns red within 2.5 s.
    """

    def __init__(self, master, status_bar, dispatcher, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._status     = status_bar
        self._dispatcher = dispatcher
        self._fetch_lock = threading.Lock()
        self._rows       = {}
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\U0001f513  Registry Unlocker").pack(side="left")
        MutedLabel(hdr,
            text="Live restriction status \u00b7 auto-scans every 2.5 s"
        ).pack(side="left", padx=(12, 0), pady=(4, 0))

        GlowButton(self, text="\u26a1  Unlock Everything \u2014 One Click",
            command=self._unlock_all, height=42,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        ).pack(fill="x", padx=20, pady=(0, 16))

        for label, (hive, subkey, value_name) in REG_UNLOCK_TARGETS.items():
            self._add_row(label, hive, subkey, value_name, is_explorer=False)
        self._add_row(
            "Explorer Shell (Winlogon Shell)",
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
            "Shell", is_explorer=True)

    def _add_row(self, label, hive, subkey, value_name, is_explorer):
        card  = CardFrame(self)
        card.pack(fill="x", padx=20, pady=4)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(info, text=label, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            anchor="w").pack(anchor="w")
        hint = "Resets Shell= to explorer.exe" if is_explorer \
            else f"{subkey} \u2192 {value_name}"
        ctk.CTkLabel(info, text=hint, text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI Mono", size=10),
            anchor="w").pack(anchor="w")

        status_lbl = ctk.CTkLabel(inner, text="\u25cf  Checking\u2026",
            text_color=TEXT_MUTED, font=ctk.CTkFont(family="Segoe UI", size=12))
        status_lbl.pack(side="right", padx=(12, 0))

        cmd = self._restore_shell if is_explorer \
            else (lambda h=hive, s=subkey, v=value_name, lbl=label:
                  self._unlock_one(h, s, v, lbl))
        btn = GlowButton(inner, text="Restore" if is_explorer else "Unlock",
            width=90, command=cmd)
        btn.pack(side="right", padx=(8, 0))

        self._rows[label] = {
            "hive": hive, "subkey": subkey, "value": value_name,
            "status_lbl": status_lbl, "btn": btn, "is_explorer": is_explorer,
        }

    def auto_refresh(self):
        if not self._fetch_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._fetch_states, daemon=True).start()

    def _fetch_states(self):
        results = {}
        try:
            for label, row in self._rows.items():
                results[label] = self._check_row(row)
        finally:
            self._fetch_lock.release()
        self.after(0, self._apply_scan, results)

    def _check_row(self, row):
        try:
            with winreg.OpenKey(row["hive"], row["subkey"], 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, row["value"])
                if row["is_explorer"]:
                    return "LOCKED" if val.lower().strip() != "explorer.exe" else "OK"
                return "LOCKED" if val else "OK"
        except FileNotFoundError:
            return "OK"
        except PermissionError:
            return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _apply_scan(self, results):
        locked_count = 0
        for label, state in results.items():
            row = self._rows[label]
            lbl = row["status_lbl"]
            btn = row["btn"]
            if state == "OK":
                lbl.configure(text="\u25cf  Unlocked", text_color=SUCCESS)
                btn.configure(state="disabled", fg_color="#1A2E22")
            elif state == "LOCKED":
                lbl.configure(text="\u25cf  LOCKED",   text_color=DANGER)
                btn.configure(state="normal",   fg_color=ACCENT)
                locked_count += 1
            else:
                lbl.configure(text="\u25cf  Unknown",  text_color=WARNING)
                btn.configure(state="normal",   fg_color=ACCENT)
        if locked_count:
            self._status.set(
                f"\u26a0  {locked_count} active restriction(s) detected!", DANGER)
        else:
            self._status.set("All registry restrictions are clear.", SUCCESS)

    def _unlock_one(self, hive, subkey, value_name, label):
        ok, msg = delete_reg_value(hive, subkey, value_name)
        if ok:
            self._status.set(f"Unlocked: {label}", SUCCESS)
        else:
            messagebox.showerror("Error", msg)
            self._status.set(f"Failed: {msg}", DANGER)
        self._dispatcher.force_refresh()

    def _restore_shell(self):
        ok, msg = set_reg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
            "Shell", "explorer.exe")
        if ok:
            self._status.set("Shell restored to explorer.exe", SUCCESS)
        else:
            messagebox.showerror("Error", msg)
            self._status.set(f"Failed: {msg}", DANGER)
        self._dispatcher.force_refresh()

    def _unlock_all(self):
        if not messagebox.askyesno("Unlock All",
            "Remove ALL virus-imposed restrictions?\n\n"
            "\u2022 Enable Task Manager\n"
            "\u2022 Enable Registry Editor\n"
            "\u2022 Enable Command Prompt\n"
            "\u2022 Enable Run dialog\n"
            "\u2022 Restore Explorer shell"):
            return
        errors = []
        for label, row in self._rows.items():
            if row["is_explorer"]:
                ok, msg = set_reg_value(
                    row["hive"], row["subkey"], row["value"], "explorer.exe")
            else:
                ok, msg = delete_reg_value(
                    row["hive"], row["subkey"], row["value"])
            if not ok:
                errors.append(f"{label}: {msg}")
        if errors:
            messagebox.showerror("Partial Success",
                "Some operations failed:\n" + "\n".join(errors))
            self._status.set("Partial unlock \u2014 see error dialog.", WARNING)
        else:
            self._status.set("All restrictions removed successfully!", SUCCESS)
        self._dispatcher.force_refresh()


# ─── PAGE: TOOLS & RESCUE ───────────────────────────────────────────────────────────────────────────────
class ToolsRescuePage(ctk.CTkFrame):
    """
    auto_refresh() calls scan_custom_tools() (os.listdir, < 1 ms).
    Only rebuilds the widget list when the file list changes.
    Dropping a .exe into ./tools/ makes it appear within 2.5 s.
    """

    def __init__(self, master, status_bar, dispatcher, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._status     = status_bar
        self._dispatcher = dispatcher
        self._last_tools = []
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\U0001f6e0  Tools & Rescue").pack(side="left")
        MutedLabel(hdr,
            text="Quick-launch tools \u00b7 drop .exe into tools/ to auto-detect"
        ).pack(side="left", padx=(12, 0), pady=(4, 0))

        # Built-in system tools
        builtins = CardFrame(self)
        builtins.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(builtins, text="Built-in System Tools", text_color=TEXT_DESC,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 6))
        btn_row = ctk.CTkFrame(builtins, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        for label, (exe, args) in SYSTEM_TOOLS.items():
            GlowButton(btn_row, text=label, width=140,
                command=lambda e=exe, a=args: self._launch(e, a)).pack(side="left", padx=4)

        # Explorer rescue
        rescue = CardFrame(self)
        rescue.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(rescue, text="Explorer Rescue", text_color=TEXT_DESC,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 6))
        inner = ctk.CTkFrame(rescue, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=(0, 12))
        GlowButton(inner, text="\u27f3  Restart Explorer",
            command=self._restart_explorer, width=160).pack(side="left", padx=(0, 12))
        MutedLabel(inner,
            text="Kills all explorer.exe instances and starts fresh.\n"
                 "Use when the desktop or taskbar is frozen.").pack(side="left")

        # Custom tools (dynamic)
        self._custom_card = CardFrame(self)
        self._custom_card.pack(fill="x", padx=20, pady=(0, 20))
        custom_hdr = ctk.CTkFrame(self._custom_card, fg_color="transparent")
        custom_hdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(custom_hdr, text="Custom Tools  (./tools/)",
            text_color=TEXT_DESC,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        ).pack(side="left")
        self._live_dot = LiveDot(custom_hdr)
        self._live_dot.pack(side="left", padx=(10, 0))
        GlowButton(custom_hdr, text="\U0001f4c2  Open Folder",
            command=self._open_tools_dir, width=130).pack(side="right")

        self._custom_inner = ctk.CTkFrame(self._custom_card, fg_color="transparent")
        self._custom_inner.pack(fill="x", padx=12, pady=(0, 12))

    def auto_refresh(self):
        """os.listdir scan on main thread (< 1 ms). Rebuilds only on change."""
        current = scan_custom_tools()
        if current != self._last_tools:
            self._last_tools = current
            self._render_custom_tools(current)

    def _render_custom_tools(self, tools):
        for w in self._custom_inner.winfo_children():
            w.destroy()
        if not tools:
            MutedLabel(self._custom_inner,
                text="No .exe files in ./tools/.\n"
                     "Drop Process Hacker, AVZ, CureIt, Autoruns etc. there \u2014 "
                     "they appear automatically within 2.5 s."
            ).pack(anchor="w", pady=6)
            return
        for path in tools:
            row = ctk.CTkFrame(self._custom_inner, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=os.path.basename(path), text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                anchor="w", width=240).pack(side="left")
            MutedLabel(row, text=path).pack(side="left", padx=8)
            GlowButton(row, text="\u25b6  Launch", width=100,
                command=lambda p=path: self._launch_custom(p)).pack(side="right")
        self._status.set(f"{len(tools)} custom tool(s) in ./tools/", SUCCESS)

    def _launch(self, exe, args):
        try:
            subprocess.Popen([exe] + args, shell=True)
            self._status.set(f"Launched: {exe}", SUCCESS)
        except Exception as exc:
            messagebox.showerror("Launch Error", str(exc))

    def _launch_custom(self, path):
        try:
            subprocess.Popen([path])
            self._status.set(f"Launched: {os.path.basename(path)}", SUCCESS)
        except Exception as exc:
            messagebox.showerror("Launch Error", str(exc))

    def _open_tools_dir(self):
        d = get_tools_dir()
        os.makedirs(d, exist_ok=True)
        subprocess.Popen(["explorer", d])

    def _restart_explorer(self):
        if not messagebox.askyesno("Restart Explorer",
            "Kill all explorer.exe and restart one.\n"
            "Desktop will briefly disappear. Continue?"):
            return
        try:
            subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"],
                capture_output=True, check=False)
            import time as _t; _t.sleep(0.8)
            subprocess.Popen(["explorer.exe"])
            self._status.set("Explorer restarted successfully.", SUCCESS)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self._status.set(f"Explorer restart failed: {exc}", DANGER)


# ─── SIDEBAR NAV BUTTON ───────────────────────────────────────────────────────────────────────────────────
class NavButton(ctk.CTkButton):
    def __init__(self, master, text, icon, command, **kwargs):
        super().__init__(master,
            text=f"  {icon}  {text}", command=command, anchor="w",
            fg_color="transparent", hover_color="#1A1C24", text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            corner_radius=10, height=40, **kwargs)

    def set_active(self, active):
        if active:
            self.configure(fg_color="#1E2238", text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"))
        else:
            self.configure(fg_color="transparent", text_color=TEXT_MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="normal"))


# ─── MAIN APPLICATION WINDOW ──────────────────────────────────────────────────────────────────────────────
class EZUnlockerApp(ctk.CTk):
    """
    Public attrs used by AutoRefreshDispatcher:
        pages              -- dict[str, CTkFrame]
        current_page_name  -- str
    """

    PAGES = [
        ("Process Manager",   "\u2699",  ProcessManagerPage),
        ("Startup Manager",   "\U0001f680", StartupManagerPage),
        ("Registry Unlocker", "\U0001f513", RegistryUnlockerPage),
        ("Tools & Rescue",    "\U0001f6e0", ToolsRescuePage),
    ]

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("EZUnlocker \u2014 Windows Recovery Tool")
        self.geometry("1080x700")
        self.minsize(900, 600)
        self.configure(fg_color=BG_DEEP)

        self.pages             = {}
        self.current_page_name = ""
        self._nav_buttons      = {}

        self._build_layout()
        self._dispatcher = AutoRefreshDispatcher(self)
        self._check_admin_status()
        self._show_page(self.PAGES[0][0])
        self._dispatcher.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._dispatcher.stop()
        self.destroy()

    def _build_layout(self):
        self._sidebar = ctk.CTkFrame(self, width=SIDEBAR_W,
            fg_color=BG_SIDEBAR, corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        content_col = ctk.CTkFrame(self, fg_color="transparent")
        content_col.pack(side="left", fill="both", expand=True)

        self._content = ctk.CTkFrame(content_col, fg_color="transparent")
        self._content.pack(fill="both", expand=True)

        status_frame = ctk.CTkFrame(content_col, height=28,
            fg_color="#0D0E13", corner_radius=0)
        status_frame.pack(fill="x", side="bottom")
        status_frame.pack_propagate(False)
        self._status_bar = StatusBar(status_frame)
        self._status_bar.pack(fill="x", padx=4, pady=4)

        self._build_sidebar()

    def _build_sidebar(self):
        logo = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        logo.pack(fill="x", padx=14, pady=(22, 18))
        
        nc = ctk.CTkFrame(logo, fg_color="transparent")
        nc.pack(side="left", padx=(4, 0))
        ctk.CTkLabel(nc, text="EZUnlocker", text_color=TEXT_PRIMARY,
                     font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(nc, text="Recovery Multitool", text_color=TEXT_MUTED,
                     font=ctk.CTkFont(family="Segoe UI", size=11)).pack(anchor="w")

        ctk.CTkFrame(self._sidebar, height=1, fg_color=BORDER
        ).pack(fill="x", padx=14, pady=(0, 12))

        nav_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", padx=10)
        for name, icon, _ in self.PAGES:
            btn = NavButton(nav_frame, text=name, icon=icon,
                command=lambda n=name: self._show_page(n))
            btn.pack(fill="x", pady=2)
            self._nav_buttons[name] = btn

        ctk.CTkFrame(self._sidebar, fg_color="transparent"
        ).pack(fill="both", expand=True)

        self._admin_badge = ctk.CTkLabel(self._sidebar, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=8, padx=10, pady=4)
        self._admin_badge.pack(padx=14, pady=(0, 6), fill="x")

        ctk.CTkLabel(self._sidebar, text="v1.1.0  |  Live Refresh",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10)).pack(pady=(0, 10))

    def _show_page(self, name):
        if self.current_page_name == name:
            return
        if self.current_page_name:
            self.pages[self.current_page_name].pack_forget()
            self._nav_buttons[self.current_page_name].set_active(False)

        if name not in self.pages:
            page_cls = next(cls for n, _, cls in self.PAGES if n == name)
            self.pages[name] = page_cls(
                self._content,
                status_bar=self._status_bar,
                dispatcher=self._dispatcher,
            )

        self.pages[name].pack(fill="both", expand=True)
        self._nav_buttons[name].set_active(True)
        self.current_page_name = name

        # Force immediate refresh on tab switch
        if hasattr(self, "_dispatcher"):
            self._dispatcher.force_refresh()

    def _check_admin_status(self):
        if is_admin():
            self._admin_badge.configure(
                text="  \u2713 Administrator",
                text_color=SUCCESS, fg_color="#0F2218")
            self._status_bar.set("Running with Administrator privileges.", SUCCESS)
        else:
            self._admin_badge.configure(
                text="  \u26a0 Not Admin",
                text_color=WARNING, fg_color="#2A1F08")
            self._status_bar.set(
                "Not running as Administrator \u2014 some features may fail.", WARNING)
            self.after(800, self._show_admin_warning)

    def _show_admin_warning(self):
        if messagebox.askyesno(
            "Administrator Rights Required",
            "EZUnlocker is not running as Administrator.\n\n"
            "Registry edits and killing protected processes require elevation.\n\n"
            "Restart as Administrator now?",
            icon="warning"
        ):
            request_elevation()


if __name__ == "__main__":
    app = EZUnlockerApp()
    app.mainloop()
