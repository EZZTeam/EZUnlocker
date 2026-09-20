"""
EZUnlocker Plugin Manager
Dynamic discovery and lifecycle management via importlib.
"""
import importlib.util
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from plugin_base import BasePlugin, PluginAPI


@dataclass
class LoadedPlugin:
    instance : Optional[BasePlugin]
    path     : str
    error    : Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.instance is not None

    @property
    def display_name(self) -> str:
        if self.instance:
            m = self.instance.metadata
            return f"{m.name}  v{m.version}"
        return f"[BROKEN] {Path(self.path).name}"


class PluginManager:
    """
    Discovers, imports and manages lifecycle of EZUnlocker plugins.

    Accepted plugin formats
    ───────────────────────
    • ./plugins/myplugin.py           — single-file plugin
    • ./plugins/myplugin/plugin.py    — package-style plugin

    On each reload:
      1. on_unload() is called on every loaded instance.
      2. All references are cleared.
      3. The plugins directory is re-scanned.
      4. Each candidate is imported via importlib.util.
      5. on_load(api) is called on each successfully instantiated plugin.
    """

    def __init__(self, plugins_dir: str) -> None:
        self._dir      = Path(plugins_dir)
        self._plugins  : list[LoadedPlugin] = []
        self._load_log : list[str]           = []

    # ── Public API ────────────────────────────────────────────────────────
    @property
    def plugins(self) -> list[LoadedPlugin]:
        return list(self._plugins)

    @property
    def load_log(self) -> list[str]:
        return list(self._load_log)

    def scan_and_load(self, api: PluginAPI) -> None:
        """Full cold-load from disk."""
        self._plugins.clear()
        self._load_log.clear()

        if not self._dir.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load_log.append(f"[INFO] Created plugins directory: {self._dir}")
            return

        candidates = self._discover()
        if not candidates:
            self._load_log.append("[INFO] No plugins found in ./plugins/")
            return

        for path in candidates:
            self._import_plugin(path, api)

    def reload(self, api: PluginAPI) -> None:
        """Unload all → re-scan → re-load."""
        for lp in self._plugins:
            if lp.instance:
                try:
                    lp.instance.on_unload()
                except Exception:
                    pass
        self.scan_and_load(api)

    # ── Discovery ─────────────────────────────────────────────────────────
    def _discover(self) -> list[Path]:
        candidates: list[Path] = []
        for item in sorted(self._dir.iterdir()):
            if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
                candidates.append(item)
            elif item.is_dir():
                entry = item / "plugin.py"
                if entry.exists():
                    candidates.append(entry)
        return candidates

    # ── Import ────────────────────────────────────────────────────────────
    def _import_plugin(self, path: Path, api: PluginAPI) -> None:
        # Unique module name prevents collision on hot-reload
        mod_name = f"_ezplugin_{path.stem}_{abs(hash(str(path)))}"

        try:
            spec   = importlib.util.spec_from_file_location(mod_name, str(path))
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception:
            err = traceback.format_exc(limit=4)
            self._load_log.append(f"[ERROR] Import failed: {path.name}\n{err}")
            self._plugins.append(LoadedPlugin(None, str(path), error=err))
            return

        # Locate the first BasePlugin subclass defined in this module
        plugin_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name, None)
            if (isinstance(attr, type)
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin):
                plugin_class = attr
                break

        if plugin_class is None:
            self._load_log.append(f"[WARN] No BasePlugin subclass in {path.name} — skipped")
            return

        try:
            instance = plugin_class()
        except Exception:
            err = traceback.format_exc(limit=4)
            self._load_log.append(f"[ERROR] Instantiation failed: {path.name}\n{err}")
            self._plugins.append(LoadedPlugin(None, str(path), error=err))
            return

        lp = LoadedPlugin(instance, str(path))

        try:
            instance.on_load(api)
        except Exception as exc:
            lp.error = f"on_load() raised: {exc}"
            self._load_log.append(f"[WARN] on_load() error in {path.name}: {exc}")

        self._plugins.append(lp)
        m = instance.metadata
        self._load_log.append(
            f"[OK]  {m.name}  v{m.version}  by {m.author}  ({path.name})"
        )
