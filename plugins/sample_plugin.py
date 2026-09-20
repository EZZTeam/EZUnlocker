"""
EZUnlocker Sample Plugin — System Info
Demonstrates the full BasePlugin contract.

Installation: copy this file to ./plugins/sample_plugin.py
It will appear automatically in the Community Plugins tab.
"""
import sys
import os
import threading

# Make plugin_base importable when running from the plugins/ subdirectory
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugin_base import BasePlugin, PluginMetadata, PluginAPI


class SystemInfoPlugin(BasePlugin):
    """Displays OS version, uptime and memory statistics."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name        = "System Info",
            author      = "EZUnlocker Team",
            version     = "1.0.0",
            description = "Shows OS version, uptime and RAM usage.",
        )

    def execute(self, api: PluginAPI) -> None:
        # Run in the caller's thread (it's already a daemon thread from PluginsPage._run_plugin)
        import platform
        import time
        import psutil

        mem     = psutil.virtual_memory()
        boot_ts = psutil.boot_time()
        uptime  = round((time.time() - boot_ts) / 3600, 1)

        info = (
            f"OS:       {platform.system()} {platform.release()}\n"
            f"Version:  {platform.version()}\n"
            f"Machine:  {platform.machine()}\n"
            f"Uptime:   {uptime} hours\n"
            f"RAM used: {mem.used // 1_048_576} MB / {mem.total // 1_048_576} MB "
            f"({mem.percent}%)\n"
            f"CPU cores:{psutil.cpu_count(logical=True)} logical"
        )

        api.show_info("System Information", info)
        api.set_status("System Info plugin executed.", "#22C55E")

    def on_load(self, api: PluginAPI) -> None:
        api.set_status("Sample plugin 'System Info' loaded.", "#6B7280")
