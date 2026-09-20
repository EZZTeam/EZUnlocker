"""
EZUnlocker v1.2.0 — Windows Recovery Multitool
Features: AutoRefreshDispatcher, HeuristicScanner, Plugin Architecture
"""
import ctypes
import os
import subprocess
import sys
import threading
import winreg
from pathlib import Path
from typing import Optional

import customtkinter as ctk
import psutil
from tkinter import messagebox, ttk
import tkinter as tk

# Optional modules — graceful fallback if files not present
try:
    from heuristic_scanner import HeuristicScanner
    _SCANNER = HeuristicScanner()
except ImportError:
    _SCANNER = None

try:
    from plugin_manager import PluginManager
    from plugin_base import PluginAPI
    _PLUGINS_OK = True
except ImportError:
    _PLUGINS_OK = False


# DESIGN TOKENS
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

# Suspicion row colours
COL_RISK_HIGH_BG = "#3D1515"
COL_RISK_HIGH_FG = "#FF9090"
COL_RISK_MED_BG  = "#3D2A10"
COL_RISK_MED_FG  = "#FFB870"
COL_RISK_LOW_BG  = "#2A2A15"
COL_RISK_LOW_FG  = "#DDDD80"


# ADMIN
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
            None, "runas", sys.executable, f'"{script}" {params}', None, 1)
    except Exception as exc:
        messagebox.showerror("Elevation Failed", str(exc))
    sys.exit(0)


# PATH HELPERS
def get_exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_tools_dir() -> str:
    return os.path.join(get_exe_dir(), "tools")

def get_plugins_dir() -> str:
    return os.path.join(get_exe_dir(), "plugins")

def scan_custom_tools() -> list:
    d = get_tools_dir()
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".exe")]


# CONSTANTS
CRITICAL_PROCS = {"system", "csrss.exe", "lsass.exe", "smss.exe", "wininit.exe"}

REG_UNLOCK_TARGETS = {
    "Task Manager (DisableTaskMgr)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
        "DisableTaskMgr"),
    "Registry Editor (DisableRegistryTools)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
        "DisableRegistryTools"),
    "Command Prompt (DisableCMD)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Policies\Microsoft\Windows\System",
        "DisableCMD"),
    "Run Dialog (NoRun)": (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer",
        "NoRun"),
}

AUTORUN_KEYS = [
    (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Run",     "HKCU\\Run"),
    (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU\\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run",     "HKLM\\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM\\RunOnce"),
]

SYSTEM_TOOLS = {
    "Command Prompt":  ("cmd.exe",        ["/K", "echo EZUnlocker ready"]),
    "PowerShell":      ("powershell.exe", ["-NoExit", "-Command", "Write-Host OK"]),
    "Registry Editor": ("regedit.exe",    []),
    "MSConfig":        ("msconfig.exe",   []),
}


# REGISTRY HELPERS
def bytes_to_mb(b: int) -> str:
    return f"{b / 1_048_576:.1f} MB"

def delete_reg_value(hive, subkey, value_name):
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, value_name)
        return True, "OK"
    except FileNotFoundError:
        return True, "Already absent"
    except PermissionError:
        return False, "Access denied"
    except Exception as e:
        return False, str(e)

def set_reg_value(hive, subkey, value_name, value):
    try:
        with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, value)
        return True, "OK"
    except PermissionError:
        return False, "Access denied"
    except Exception as e:
        return False, str(e)


# CUSTOM WIDGETS
class GlowButton(ctk.CTkButton):
    def __init__(self, master, danger=False, **kw):
        kw.setdefault("fg_color",      DANGER if danger else ACCENT)
        kw.setdefault("hover_color",   DANGER_HOVER if danger else ACCENT_HOVER)
        kw.setdefault("text_color",    TEXT_PRIMARY)
        kw.setdefault("corner_radius", 10)
        kw.setdefault("height",        36)
        kw.setdefault("font", ctk.CTkFont(family="Segoe UI", size=13, weight="bold"))
        super().__init__(master, **kw)

class CardFrame(ctk.CTkFrame):
    def __init__(self, master, **kw):
        kw.setdefault("fg_color",      BG_CARD)
        kw.setdefault("corner_radius", 14)
        kw.setdefault("border_width",  1)
        kw.setdefault("border_color",  BORDER)
        super().__init__(master, **kw)

class SectionLabel(ctk.CTkLabel):
    def __init__(self, master, text, **kw):
        kw.setdefault("text_color", TEXT_PRIMARY)
        kw.setdefault("font", ctk.CTkFont(family="Segoe UI", size=16, weight="bold"))
        super().__init__(master, text=text, **kw)

class MutedLabel(ctk.CTkLabel):
    def __init__(self, master, text, **kw):
        kw.setdefault("text_color", TEXT_MUTED)
        kw.setdefault("font", ctk.CTkFont(family="Segoe UI", size=12))
        super().__init__(master, text=text, **kw)

class StatusBar(ctk.CTkLabel):
    def __init__(self, master, **kw):
        kw.setdefault("text",       "  Ready.")
        kw.setdefault("text_color", TEXT_MUTED)
        kw.setdefault("font",       ctk.CTkFont(family="Segoe UI", size=11))
        kw.setdefault("anchor",     "w")
        super().__init__(master, **kw)
    def set(self, msg, color=TEXT_MUTED):
        self.configure(text=f"  {msg}", text_color=color)

class LiveDot(ctk.CTkLabel):
    def __init__(self, master, **kw):
        kw.setdefault("text",       "\u25cf Live")
        kw.setdefault("text_color", SUCCESS)
        kw.setdefault("font",       ctk.CTkFont(family="Segoe UI", size=11))
        super().__init__(master, **kw)
    def set_active(self, v):
        self.configure(text="\u25cf Live" if v else "\u25cb Paused",
                       text_color=SUCCESS if v else TEXT_MUTED)


# TREEVIEW STYLE
def apply_tree_style(tree, name):
    s = ttk.Style()
    s.theme_use("default")
    s.configure(f"{name}.Treeview",
        background=BG_CARD, foreground=TEXT_PRIMARY, fieldbackground=BG_CARD,
        borderwidth=0, rowheight=28, font=("Segoe UI", 11))
    s.configure(f"{name}.Treeview.Heading",
        background="#16171F", foreground=TEXT_DESC,
        font=("Segoe UI", 11, "bold"), relief="flat", borderwidth=0)
    s.map(f"{name}.Treeview",
        background=[("selected", ACCENT)], foreground=[("selected", "#FFFFFF")])
    tree.configure(style=f"{name}.Treeview")


# AUTO-REFRESH DISPATCHER
class AutoRefreshDispatcher:
    def __init__(self, app):
        self._app      = app
        self._timer_id = None
        self._running  = False

    def start(self):
        self._running = True
        self._schedule_next()

    def stop(self):
        self._running = False
        self._cancel()

    def force_refresh(self):
        self._cancel()
        self._tick()

    def _cancel(self):
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
                pass
        self._schedule_next()


# PAGE: PROCESS MANAGER
class ProcessManagerPage(ctk.CTkFrame):
    COLUMNS    = ("pid", "name", "ram", "path")
    COL_LABELS = {"pid": "PID", "name": "Name", "ram": "RAM", "path": "Path"}
    COL_WIDTHS = {"pid": 70, "name": 200, "ram": 90, "path": 390}

    def __init__(self, master, status_bar, dispatcher, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._status        = status_bar
        self._dispatcher    = dispatcher
        self._all_rows      = []
        self._fetch_lock    = threading.Lock()
        self._scan_active   = False
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\u2699  Process Manager").pack(side="left")
        MutedLabel(hdr, text="Live \u00b7 auto-refresh 2.5 s").pack(side="left", padx=(12, 0), pady=(4, 0))

        tb = ctk.CTkFrame(self, fg_color="transparent")
        tb.pack(fill="x", padx=20, pady=(0, 8))
        GlowButton(tb, text="\u27f3 Refresh", command=self._manual_refresh, width=110).pack(side="left", padx=(0, 6))
        GlowButton(tb, text="\u2715 Kill",    command=self._kill_selected,  danger=True, width=100).pack(side="left", padx=(0, 6))
        GlowButton(tb, text="\U0001f4c2 Folder", command=self._open_folder, width=110).pack(side="left", padx=(0, 6))

        self._susp_btn = ctk.CTkButton(tb,
            text="\U0001f9d0 Suspicious: OFF",
            command=self._toggle_suspicious,
            fg_color=BG_CARD, hover_color="#1E2238",
            text_color=TEXT_MUTED, corner_radius=10, height=36,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        self._susp_btn.pack(side="left", padx=(0, 6))

        LiveDot(tb).pack(side="left", padx=(10, 0))

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(tb, textvariable=self._search_var,
            placeholder_text="Filter\u2026", width=200,
            fg_color=BG_CARD, border_color=BORDER,
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

        # Configure suspicion tags
        self._tree.tag_configure("risk_high", background=COL_RISK_HIGH_BG, foreground=COL_RISK_HIGH_FG)
        self._tree.tag_configure("risk_med",  background=COL_RISK_MED_BG,  foreground=COL_RISK_MED_FG)
        self._tree.tag_configure("risk_low",  background=COL_RISK_LOW_BG,  foreground=COL_RISK_LOW_FG)

    def _toggle_suspicious(self):
        self._scan_active = not self._scan_active
        if self._scan_active:
            self._susp_btn.configure(text="\U0001f9d0 Suspicious: ON",
                fg_color=WARNING, text_color="#000000", border_color=WARNING)
            if _SCANNER:
                _SCANNER.capture_baseline()
        else:
            self._susp_btn.configure(text="\U0001f9d0 Suspicious: OFF",
                fg_color=BG_CARD, text_color=TEXT_MUTED, border_color=BORDER)
            for iid in self._tree.get_children():
                self._tree.item(iid, tags=())
        self._dispatcher.force_refresh()

    def auto_refresh(self):
        if not self._fetch_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._fetch_procs, daemon=True).start()

    def _fetch_procs(self):
        rows = []
        try:
            for proc in psutil.process_iter(["pid", "name", "memory_info", "exe"]):
                try:
                    i = proc.info
                    rows.append((i["pid"], i["name"] or "\u2014",
                        bytes_to_mb(i["memory_info"].rss) if i["memory_info"] else "\u2014",
                        i["exe"] or "\u2014"))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        finally:
            self._fetch_lock.release()
        self.after(0, self._smart_populate, rows)

    def _smart_populate(self, rows):
        self._all_rows = rows
        sel_items = self._tree.selection()
        sel_pid = None
        if sel_items:
            try:
                sel_pid = str(self._tree.item(sel_items[0], "values")[0])
            except (IndexError, tk.TclError):
                pass
        yview = self._tree.yview()[0]

        flt = self._search_var.get().lower()
        for iid in self._tree.get_children():
            self._tree.delete(iid)

        new_sel = None
        for pid, name, ram, path in rows:
            if flt and flt not in name.lower() and flt not in str(pid):
                continue
            iid = self._tree.insert("", "end", values=(pid, name, ram, path))
            if str(pid) == sel_pid:
                new_sel = iid

        # Apply heuristic highlighting if scan is active
        if self._scan_active and _SCANNER:
            self._apply_suspicion_tags()

        if new_sel:
            self._tree.selection_set(new_sel)
            self._tree.see(new_sel)
        elif yview > 0.001:
            self._tree.yview_moveto(yview)

        shown = len(self._tree.get_children())
        self._status.set(f"Processes: {shown} of {len(rows)}  \u2022  {'Scan ON' if self._scan_active else 'Live'}",
                         WARNING if self._scan_active else TEXT_MUTED)

    def _apply_suspicion_tags(self):
        if not _SCANNER:
            return
        for iid in self._tree.get_children():
            vals = self._tree.item(iid, "values")
            try:
                pid  = int(vals[0])
                name = str(vals[1])
                path = str(vals[3])
            except (IndexError, ValueError):
                continue
            result = _SCANNER.scan_process(pid, name, path)
            if result.is_suspicious:
                self._tree.item(iid, tags=(result.tag,))
                # Prefix name with risk badge
                self._tree.set(iid, "name", f"{result.badge} {name}")
            else:
                self._tree.item(iid, tags=())

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

    def _sel_vals(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a process first.")
            return None
        return self._tree.item(sel[0], "values")

    def _kill_selected(self):
        vals = self._sel_vals()
        if vals is None:
            return
        pid, name = int(vals[0]), vals[1]
        # Strip badge prefix if present
        for badge in ("[HIGH]", "[MED]", "[LOW]"):
            name = name.replace(badge + " ", "")
        if name.lower() in CRITICAL_PROCS:
            messagebox.showwarning("Critical",
                f'"{name}" is a critical system process. Terminating it causes BSOD.')
            return
        if not messagebox.askyesno("Kill?", f"Terminate PID {pid} ({name})?\nUnsaved data will be lost."):
            return
        try:
            psutil.Process(pid).kill()
            self._status.set(f"Killed {name} (PID {pid}).", DANGER)
        except psutil.AccessDenied:
            messagebox.showerror("Access Denied", "Run as Administrator.")
            return
        except psutil.NoSuchProcess:
            messagebox.showinfo("Gone", "Process no longer exists.")
        self._dispatcher.force_refresh()

    def _open_folder(self):
        vals = self._sel_vals()
        if vals is None:
            return
        path = vals[3]
        if path == "\u2014" or not os.path.exists(path):
            messagebox.showwarning("Unavailable", "Cannot resolve executable path.")
            return
        subprocess.Popen(["explorer", "/select,", path])


# PAGE: STARTUP MANAGER
class StartupManagerPage(ctk.CTkFrame):
    COLUMNS    = ("name", "command", "regpath")
    COL_LABELS = {"name": "Name", "command": "Command / Path", "regpath": "Registry Key"}
    COL_WIDTHS = {"name": 190, "command": 370, "regpath": 220}

    def __init__(self, master, status_bar, dispatcher, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._status      = status_bar
        self._dispatcher  = dispatcher
        self._fetch_lock  = threading.Lock()
        self._entry_map   = {}
        self._displayed   = set()
        self._scan_active = False
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\U0001f680  Startup Manager").pack(side="left")
        MutedLabel(hdr, text="HKCU/HKLM Run/RunOnce \u00b7 live diff").pack(
            side="left", padx=(12, 0), pady=(4, 0))

        tb = ctk.CTkFrame(self, fg_color="transparent")
        tb.pack(fill="x", padx=20, pady=(0, 8))
        GlowButton(tb, text="\u27f3 Refresh", command=self._manual_refresh, width=110).pack(side="left", padx=(0, 6))
        GlowButton(tb, text="\U0001f5d1 Remove", command=self._remove_selected, danger=True, width=110).pack(side="left", padx=(0, 6))

        self._susp_btn = ctk.CTkButton(tb,
            text="\U0001f9d0 Anomalies: OFF",
            command=self._toggle_suspicious,
            fg_color=BG_CARD, hover_color="#1E2238",
            text_color=TEXT_MUTED, corner_radius=10, height=36,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        self._susp_btn.pack(side="left", padx=(0, 6))

        LiveDot(tb).pack(side="left", padx=(10, 0))

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

        self._tree.tag_configure("risk_high", background=COL_RISK_HIGH_BG, foreground=COL_RISK_HIGH_FG)
        self._tree.tag_configure("risk_med",  background=COL_RISK_MED_BG,  foreground=COL_RISK_MED_FG)
        self._tree.tag_configure("risk_low",  background=COL_RISK_LOW_BG,  foreground=COL_RISK_LOW_FG)
        self._tree.tag_configure("new",       background="#1B2E1B")

    def _toggle_suspicious(self):
        self._scan_active = not self._scan_active
        if self._scan_active:
            self._susp_btn.configure(text="\U0001f9d0 Anomalies: ON",
                fg_color=WARNING, text_color="#000000", border_color=WARNING)
        else:
            self._susp_btn.configure(text="\U0001f9d0 Anomalies: OFF",
                fg_color=BG_CARD, text_color=TEXT_MUTED, border_color=BORDER)
            for iid in self._tree.get_children():
                self._tree.item(iid, tags=())
        self._dispatcher.force_refresh()

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
                    self._entry_map[iid] = (hive, subkey, name, data)
                    if not is_first:
                        self._tree.item(iid, tags=("new",))
                        self.after(3000, self._clear_tag, iid)
                except tk.TclError:
                    pass
            else:
                # Update data column in case command changed
                try:
                    self._tree.set(iid, "command", data)
                    self._entry_map[iid] = (hive, subkey, name, data)
                except tk.TclError:
                    pass

        self._displayed = incoming

        # Apply suspicion scan if active
        if self._scan_active and _SCANNER:
            self._apply_suspicion_tags()

        self._status.set(
            f"Startup entries: {len(incoming)}  \u2022  {'Scan ON' if self._scan_active else 'Monitoring'}",
            WARNING if self._scan_active else TEXT_MUTED)

    def _apply_suspicion_tags(self):
        if not _SCANNER:
            return
        for iid in self._tree.get_children():
            vals = self._tree.item(iid, "values")
            try:
                name    = str(vals[0])
                command = str(vals[1])
            except IndexError:
                continue
            result = _SCANNER.scan_startup_entry(name, command)
            if result.is_suspicious:
                self._tree.item(iid, tags=(result.tag,))
                clean_name = name.replace("[HIGH] ", "").replace("[MED] ", "").replace("[LOW] ", "")
                self._tree.set(iid, "name", f"{result.badge} {clean_name}")
            else:
                self._tree.item(iid, tags=())

    def _clear_tag(self, iid):
        try:
            if self._tree.exists(iid):
                tags = self._tree.item(iid, "tags")
                if "new" in tags:
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
        hive, subkey, name, _ = entry
        label = self._tree.item(iid, "values")[2]
        if not messagebox.askyesno("Confirm",
            f"Remove {name!r} from {label}?\nIt will no longer run at startup."):
            return
        ok, msg = delete_reg_value(hive, subkey, name)
        if ok:
            self._status.set(f"Removed {name!r} from {label}.", SUCCESS)
            self._dispatcher.force_refresh()
        else:
            messagebox.showerror("Error", msg)
            self._status.set(f"Failed: {msg}", DANGER)


# PAGE: REGISTRY UNLOCKER
class RegistryUnlockerPage(ctk.CTkFrame):
    def __init__(self, master, status_bar, dispatcher, **kw):
        super().__init__(master, fg_color="transparent", **kw)
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
        MutedLabel(hdr, text="Live restriction status \u00b7 auto-scans 2.5 s").pack(
            side="left", padx=(12, 0), pady=(4, 0))

        GlowButton(self, text="\u26a1  Unlock Everything \u2014 One Click",
            command=self._unlock_all, height=42,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")).pack(
            fill="x", padx=20, pady=(0, 16))

        for label, (hive, subkey, vname) in REG_UNLOCK_TARGETS.items():
            self._add_row(label, hive, subkey, vname, False)
        self._add_row("Explorer Shell (Winlogon)",
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
            "Shell", True)

    def _add_row(self, label, hive, subkey, vname, is_explorer):
        card  = CardFrame(self)
        card.pack(fill="x", padx=20, pady=4)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)
        info  = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(info, text=label, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), anchor="w").pack(anchor="w")
        hint = "Resets Shell= to explorer.exe" if is_explorer else f"{subkey} \u2192 {vname}"
        ctk.CTkLabel(info, text=hint, text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI Mono", size=10), anchor="w").pack(anchor="w")
        status_lbl = ctk.CTkLabel(inner, text="\u25cf  Checking\u2026", text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12))
        status_lbl.pack(side="right", padx=(12, 0))
        cmd = self._restore_shell if is_explorer else \
            (lambda h=hive, s=subkey, v=vname, lbl=label: self._unlock_one(h, s, v, lbl))
        btn = GlowButton(inner, text="Restore" if is_explorer else "Unlock", width=90, command=cmd)
        btn.pack(side="right", padx=(8, 0))
        self._rows[label] = {"hive": hive, "subkey": subkey, "value": vname,
            "status_lbl": status_lbl, "btn": btn, "is_explorer": is_explorer}

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
        locked = 0
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
                locked += 1
            else:
                lbl.configure(text="\u25cf  Unknown",  text_color=WARNING)
                btn.configure(state="normal",   fg_color=ACCENT)
        if locked:
            self._status.set(f"\u26a0  {locked} restriction(s) active!", DANGER)
        else:
            self._status.set("All restrictions clear.", SUCCESS)

    def _unlock_one(self, hive, subkey, vname, label):
        ok, msg = delete_reg_value(hive, subkey, vname)
        (self._status.set(f"Unlocked: {label}", SUCCESS) if ok
         else (messagebox.showerror("Error", msg), self._status.set(f"Failed: {msg}", DANGER)))
        self._dispatcher.force_refresh()

    def _restore_shell(self):
        ok, msg = set_reg_value(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
            "Shell", "explorer.exe")
        (self._status.set("Shell restored to explorer.exe", SUCCESS) if ok
         else (messagebox.showerror("Error", msg), self._status.set(f"Failed: {msg}", DANGER)))
        self._dispatcher.force_refresh()

    def _unlock_all(self):
        if not messagebox.askyesno("Unlock All",
            "Remove ALL virus-imposed restrictions?\n\n"
            "\u2022 Task Manager\n\u2022 Registry Editor\n"
            "\u2022 Command Prompt\n\u2022 Run dialog\n\u2022 Explorer shell"):
            return
        errors = []
        for label, row in self._rows.items():
            if row["is_explorer"]:
                ok, msg = set_reg_value(row["hive"], row["subkey"], row["value"], "explorer.exe")
            else:
                ok, msg = delete_reg_value(row["hive"], row["subkey"], row["value"])
            if not ok:
                errors.append(f"{label}: {msg}")
        if errors:
            messagebox.showerror("Partial", "\n".join(errors))
            self._status.set("Partial unlock.", WARNING)
        else:
            self._status.set("All restrictions removed!", SUCCESS)
        self._dispatcher.force_refresh()


# PAGE: TOOLS & RESCUE
class ToolsRescuePage(ctk.CTkFrame):
    def __init__(self, master, status_bar, dispatcher, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._status     = status_bar
        self._dispatcher = dispatcher
        self._last_tools = []
        self._build_ui()
        self.auto_refresh()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\U0001f6e0  Tools & Rescue").pack(side="left")
        MutedLabel(hdr, text="Quick-launch \u00b7 drop .exe into tools/ to auto-detect").pack(
            side="left", padx=(12, 0), pady=(4, 0))

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

        rescue = CardFrame(self)
        rescue.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(rescue, text="Explorer Rescue", text_color=TEXT_DESC,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 6))
        inner = ctk.CTkFrame(rescue, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=(0, 12))
        GlowButton(inner, text="\u27f3 Restart Explorer",
            command=self._restart_explorer, width=160).pack(side="left", padx=(0, 12))
        MutedLabel(inner, text="Kill explorer.exe and restart.\nFix frozen desktop/taskbar.").pack(side="left")

        self._custom_card = CardFrame(self)
        self._custom_card.pack(fill="x", padx=20, pady=(0, 20))
        ch = ctk.CTkFrame(self._custom_card, fg_color="transparent")
        ch.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(ch, text="Custom Tools (./tools/)", text_color=TEXT_DESC,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")).pack(side="left")
        LiveDot(ch).pack(side="left", padx=(10, 0))
        GlowButton(ch, text="\U0001f4c2 Open Folder",
            command=lambda: subprocess.Popen(["explorer", get_tools_dir()]),
            width=120).pack(side="right")
        self._custom_inner = ctk.CTkFrame(self._custom_card, fg_color="transparent")
        self._custom_inner.pack(fill="x", padx=12, pady=(0, 12))

    def auto_refresh(self):
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
                     "Drop Process Hacker, AVZ, CureIt etc. \u2014 appear in 2.5 s."
            ).pack(anchor="w", pady=6)
            return
        for path in tools:
            row = ctk.CTkFrame(self._custom_inner, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=os.path.basename(path), text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                anchor="w", width=240).pack(side="left")
            MutedLabel(row, text=path).pack(side="left", padx=8)
            GlowButton(row, text="\u25b6 Launch", width=100,
                command=lambda p=path: subprocess.Popen([p])).pack(side="right")
        self._status.set(f"{len(tools)} tool(s) in ./tools/", SUCCESS)

    def _launch(self, exe, args):
        try:
            subprocess.Popen([exe] + args, shell=True)
            self._status.set(f"Launched {exe}", SUCCESS)
        except Exception as exc:
            messagebox.showerror("Launch Error", str(exc))

    def _restart_explorer(self):
        if not messagebox.askyesno("Restart Explorer",
            "Kill all explorer.exe and restart.\nDesktop briefly disappears. OK?"):
            return
        try:
            subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"],
                capture_output=True, check=False)
            import time as _t; _t.sleep(0.8)
            subprocess.Popen(["explorer.exe"])
            self._status.set("Explorer restarted.", SUCCESS)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))


# PAGE: COMMUNITY PLUGINS
class PluginsPage(ctk.CTkFrame):
    def __init__(self, master, status_bar, dispatcher, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._status  = status_bar
        self._dispatcher = dispatcher
        self._pm: Optional[PluginManager] = None
        self._build_ui()
        self._reload()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        SectionLabel(hdr, text="\U0001f9e9  Community Plugins").pack(side="left")
        MutedLabel(hdr, text="Drop .py files into ./plugins/ to extend EZUnlocker").pack(
            side="left", padx=(12, 0), pady=(4, 0))

        tb = ctk.CTkFrame(self, fg_color="transparent")
        tb.pack(fill="x", padx=20, pady=(0, 8))
        GlowButton(tb, text="\u27f3  Reload Plugins", command=self._reload, width=140).pack(
            side="left", padx=(0, 8))
        GlowButton(tb, text="\U0001f4c2  Open Folder", command=self._open_folder, width=130).pack(
            side="left")

        # Load log card
        log_card = CardFrame(self)
        log_card.pack(fill="x", padx=20, pady=(0, 10))
        log_inner = ctk.CTkFrame(log_card, fg_color="transparent")
        log_inner.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(log_inner, text="Load Log", text_color=TEXT_DESC,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        ).pack(anchor="w")
        self._log_lbl = ctk.CTkLabel(log_inner, text="—",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI Mono", size=10),
            anchor="w", justify="left")
        self._log_lbl.pack(anchor="w", fill="x")

        # Scrollable plugin list
        self._list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent", label_text="")
        self._list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def _reload(self):
        if not _PLUGINS_OK:
            self._log_lbl.configure(
                text="Plugin system unavailable.\nEnsure plugin_base.py and plugin_manager.py are next to main.py.",
                text_color=WARNING)
            return

        api = PluginAPI(self._status, self.winfo_toplevel())
        if self._pm is None:
            self._pm = PluginManager(get_plugins_dir())
        self._pm.reload(api)
        log_text = "\n".join(self._pm.load_log) or "No plugins found."
        self._log_lbl.configure(text=log_text, text_color=TEXT_MUTED)
        self._render_list(api)
        self._status.set(
            f"{sum(1 for p in self._pm.plugins if p.ok)} plugin(s) loaded.",
            SUCCESS)

    def _render_list(self, api):
        for w in self._list_frame.winfo_children():
            w.destroy()

        if not self._pm or not self._pm.plugins:
            MutedLabel(self._list_frame,
                text="No plugins loaded.\nCreate a .py file in ./plugins/ that subclasses BasePlugin."
            ).pack(anchor="w", pady=8)
            return

        for lp in self._pm.plugins:
            self._add_plugin_card(lp, api)

    def _add_plugin_card(self, lp, api):
        card  = CardFrame(self._list_frame)
        card.pack(fill="x", pady=4)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        if lp.ok:
            m = lp.instance.metadata
            info = ctk.CTkFrame(inner, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(info, text=f"{m.name}  v{m.version}",
                text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=f"by {m.author}",
                text_color=ACCENT,
                font=ctk.CTkFont(family="Segoe UI", size=11), anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=m.description,
                text_color=TEXT_DESC,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                anchor="w", wraplength=500).pack(anchor="w")
            GlowButton(inner, text="\u25b6  Run", width=90,
                command=lambda p=lp, a=api: self._run_plugin(p, a)).pack(side="right")
        else:
            ctk.CTkLabel(inner,
                text=f"\u26a0  {Path(lp.path).name}",
                text_color=DANGER,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
            ).pack(side="left")
            MutedLabel(inner, text="Failed to load — see log above").pack(side="left", padx=8)

    def _run_plugin(self, lp, api):
        if not lp.ok:
            return
        self._status.set(f"Running: {lp.instance.metadata.name}\u2026", SUCCESS)
        threading.Thread(
            target=self._exec_safe, args=(lp, api), daemon=True
        ).start()

    def _exec_safe(self, lp, api):
        try:
            lp.instance.execute(api)
        except Exception as exc:
            self.after(0, messagebox.showerror, "Plugin Error", str(exc))
            self.after(0, self._status.set, f"Plugin error: {exc}", DANGER)

    def _open_folder(self):
        d = get_plugins_dir()
        os.makedirs(d, exist_ok=True)
        subprocess.Popen(["explorer", d])

    def auto_refresh(self):
        pass   # Plugins page does not need periodic refresh


# SIDEBAR NAV BUTTON
class NavButton(ctk.CTkButton):
    def __init__(self, master, text, icon, command, **kw):
        super().__init__(master,
            text=f"  {icon}  {text}", command=command, anchor="w",
            fg_color="transparent", hover_color="#1A1C24", text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            corner_radius=10, height=40, **kw)

    def set_active(self, active):
        if active:
            self.configure(fg_color="#1E2238", text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"))
        else:
            self.configure(fg_color="transparent", text_color=TEXT_MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="normal"))


# MAIN APPLICATION WINDOW
class EZUnlockerApp(ctk.CTk):
    PAGES = [
        ("Process Manager",   "\u2699",  ProcessManagerPage),
        ("Startup Manager",   "\U0001f680", StartupManagerPage),
        ("Registry Unlocker", "\U0001f513", RegistryUnlockerPage),
        ("Tools & Rescue",    "\U0001f6e0", ToolsRescuePage),
        ("Community Plugins", "\U0001f9e9", PluginsPage),
    ]

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("EZUnlocker \u2014 Windows Recovery Tool")
        self.geometry("1100x720")
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
        self._sidebar = ctk.CTkFrame(
            self, width=SIDEBAR_W, fg_color=BG_SIDEBAR, corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        col = ctk.CTkFrame(self, fg_color="transparent")
        col.pack(side="left", fill="both", expand=True)
        self._content = ctk.CTkFrame(col, fg_color="transparent")
        self._content.pack(fill="both", expand=True)

        sf = ctk.CTkFrame(col, height=28, fg_color="#0D0E13", corner_radius=0)
        sf.pack(fill="x", side="bottom")
        sf.pack_propagate(False)
        self._status_bar = StatusBar(sf)
        self._status_bar.pack(fill="x", padx=4, pady=4)

        self._build_sidebar()

    def _build_sidebar(self):
        # ── Text-only header — NO icon, NO coloured square ────────────────
        logo = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        logo.pack(fill="x", padx=16, pady=(22, 18))

        ctk.CTkLabel(
            logo,
            text="EZUnlocker",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            logo,
            text="Recovery Multitool",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            anchor="w",
        ).pack(anchor="w")
        # ─────────────────────────────────────────────────────────────────

        ctk.CTkFrame(self._sidebar, height=1, fg_color=BORDER).pack(
            fill="x", padx=14, pady=(0, 12))

        nav = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        nav.pack(fill="x", padx=10)
        for name, icon, _ in self.PAGES:
            btn = NavButton(nav, text=name, icon=icon,
                command=lambda n=name: self._show_page(n))
            btn.pack(fill="x", pady=2)
            self._nav_buttons[name] = btn

        ctk.CTkFrame(self._sidebar, fg_color="transparent").pack(fill="both", expand=True)

        self._admin_badge = ctk.CTkLabel(
            self._sidebar, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=8, padx=10, pady=4)
        self._admin_badge.pack(padx=14, pady=(0, 6), fill="x")

        ctk.CTkLabel(self._sidebar, text="v1.2.0  |  Live + Plugins",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10)).pack(pady=(0, 10))

    def _show_page(self, name):
        if self.current_page_name == name:
            return
        if self.current_page_name:
            self.pages[self.current_page_name].pack_forget()
            self._nav_buttons[self.current_page_name].set_active(False)

        if name not in self.pages:
            cls = next(c for n, _, c in self.PAGES if n == name)
            self.pages[name] = cls(
                self._content,
                status_bar=self._status_bar,
                dispatcher=self._dispatcher)

        self.pages[name].pack(fill="both", expand=True)
        self._nav_buttons[name].set_active(True)
        self.current_page_name = name
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
            icon="warning"):
            request_elevation()


if __name__ == "__main__":
    app = EZUnlockerApp()
    app.mainloop()
