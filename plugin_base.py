"""
EZUnlocker Plugin Base
BasePlugin abstract contract + PluginAPI context object.
Place this file in the project root alongside main.py.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import winreg


@dataclass
class PluginMetadata:
    """Static descriptor every plugin must provide."""
    name        : str
    author      : str
    version     : str
    description : str
    target_tab  : str = "Plugins"   # reserved for future routing


class PluginAPI:
    """
    Safe context object passed to plugin.execute().
    Wraps registry, process control, UI helpers and the auto-refresh bus.
    Plugins should never import customtkinter directly — use this API instead.
    """

    def __init__(self, status_bar, app) -> None:
        self._sb  = status_bar
        self._app = app

    # ── UI helpers ────────────────────────────────────────────────────────
    def set_status(self, msg: str, color: str = "#9CA3AF") -> None:
        """Update the global status bar (main-thread safe)."""
        self._sb.set(msg, color)

    def show_info(self, title: str, message: str) -> None:
        from tkinter import messagebox
        messagebox.showinfo(title, message)

    def show_error(self, title: str, message: str) -> None:
        from tkinter import messagebox
        messagebox.showerror(title, message)

    def ask_yes_no(self, title: str, message: str) -> bool:
        from tkinter import messagebox
        return messagebox.askyesno(title, message)

    # ── Registry ──────────────────────────────────────────────────────────
    def read_registry(self, hive, subkey: str, value: str) -> Optional[str]:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, value)
                return val
        except Exception:
            return None

    def write_registry(self, hive, subkey: str, value: str, data: str) -> bool:
        try:
            with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, value, 0, winreg.REG_SZ, data)
            return True
        except Exception:
            return False

    def delete_registry_value(self, hive, subkey: str, value: str) -> bool:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, value)
            return True
        except Exception:
            return False

    # ── Process control ───────────────────────────────────────────────────
    def get_processes(self) -> list[dict]:
        """Returns list of dicts with keys: pid, name, exe, memory_info."""
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return procs

    def kill_process(self, pid: int) -> bool:
        import psutil
        try:
            psutil.Process(pid).kill()
            return True
        except Exception:
            return False

    # ── App integration ───────────────────────────────────────────────────
    def force_refresh(self) -> None:
        """Trigger an immediate auto-refresh on the active page."""
        if hasattr(self._app, "_dispatcher"):
            self._app._dispatcher.force_refresh()

    def get_app(self):
        """Direct reference to the EZUnlockerApp root window (use sparingly)."""
        return self._app


class BasePlugin(ABC):
    """
    Abstract base class every EZUnlocker community plugin must inherit from.

    Minimal example
    ───────────────
        class HelloPlugin(BasePlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="Hello World",
                    author="You",
                    version="1.0.0",
                    description="Shows a greeting dialog.",
                )

            def execute(self, api: PluginAPI) -> None:
                api.show_info("Hello", "Plugin works!")
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Static metadata descriptor — must be a PluginMetadata instance."""
        ...

    @abstractmethod
    def execute(self, api: PluginAPI) -> None:
        """
        Main entry point called when the user clicks Run.
        Long-running work must be dispatched to a daemon thread;
        never block the Tkinter main loop here.
        """
        ...

    def on_load(self, api: PluginAPI) -> None:
        """Optional: called once after successful import and instantiation."""
        pass

    def on_unload(self) -> None:
        """Optional: called before plugin is removed from memory on reload."""
        pass
