"""
EZUnlocker Heuristic Scanner
Stateful risk analysis for processes and startup registry entries.
"""
import os
import re
import time
from dataclasses import dataclass, field


# ── Suspicious directory fragments (checked lowercase) ────────────────────────
SUSPICIOUS_DIRS = [
    "\\appdata\\",
    "\\temp\\",
    "\\tmp\\",
    "\\roaming\\",
    "\\programdata\\",
    "\\users\\public\\",
    "\\downloads\\",
    "\\desktop\\",
    "\\recycle",
]

# System processes that must reside in System32 / SysWOW64
SYSTEM_PROCESS_NAMES = {
    "svchost.exe", "csrss.exe", "lsass.exe", "winlogon.exe",
    "services.exe", "smss.exe", "wininit.exe", "explorer.exe",
    "taskhostw.exe", "taskhost.exe",
}

_SYSROOT  = os.environ.get("SystemRoot", r"C:\Windows")
SYSTEM32  = os.path.join(_SYSROOT, "System32").lower()
SYSWOW64  = os.path.join(_SYSROOT, "SysWOW64").lower()

# Startup command patterns
_RE_SCRIPT_EXT   = re.compile(r"\.(bat|cmd|vbs|ps1|js|wsf|hta)\b", re.IGNORECASE)
_RE_HIDDEN_FLAGS = re.compile(
    r"(powershell|cmd)[^\n]*?(-w\s+hidden|-windowstyle\s+hidden|-enc\s+|-encoded\b|-nop\b|-exec\s+bypass)",
    re.IGNORECASE,
)
_RE_LOLBINS      = re.compile(
    r"\b(wscript|cscript|mshta|regsvr32|rundll32|certutil|bitsadmin|msiexec)\b",
    re.IGNORECASE,
)
_RE_B64_BLOCK    = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")
_RE_EXE_PATH     = re.compile(r'"?([A-Za-z]:\\[^"\n]+\.exe)"?', re.IGNORECASE)

_RISK_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


@dataclass
class SuspicionResult:
    is_suspicious : bool      = False
    risk_level    : str       = "NONE"   # NONE / LOW / MEDIUM / HIGH
    reasons       : list[str] = field(default_factory=list)

    def flag(self, reason: str, level: str = "MEDIUM") -> None:
        self.is_suspicious = True
        self.reasons.append(reason)
        if _RISK_ORDER.get(level, 0) > _RISK_ORDER.get(self.risk_level, 0):
            self.risk_level = level

    @property
    def badge(self) -> str:
        return {"HIGH": "[HIGH]", "MEDIUM": "[MED]", "LOW": "[LOW]"}.get(
            self.risk_level, ""
        )

    @property
    def tag(self) -> str:
        """Treeview tag name for this risk level."""
        return {"HIGH": "risk_high", "MEDIUM": "risk_med", "LOW": "risk_low"}.get(
            self.risk_level, ""
        )


class HeuristicScanner:
    """
    Stateful engine for process and startup-entry risk analysis.

    Workflow
    ────────
    1. Call capture_baseline() once at app start (or when monitoring begins).
       This snapshots all current PIDs; new PIDs detected later are flagged LOW.
    2. Call scan_process(pid, name, exe_path) per row in the process list.
    3. Call scan_startup_entry(name, command) per row in the startup list.
    4. Use SuspicionResult.is_suspicious / .risk_level / .tag for UI decisions.
    """

    def __init__(self) -> None:
        self._known_pids  : set[int] = set()
        self._baseline_ok : bool     = False

    def capture_baseline(self) -> None:
        """Snapshot current PID set. New PIDs after this call are flagged LOW."""
        try:
            import psutil
            self._known_pids  = {p.pid for p in psutil.process_iter(["pid"])}
            self._baseline_ok = True
        except Exception:
            pass

    # ── Process heuristics ────────────────────────────────────────────────
    def scan_process(self, pid: int, name: str, exe_path: str) -> SuspicionResult:
        r      = SuspicionResult()
        name_l = (name or "").lower()
        path_l = (exe_path or "").lower()

        # 1. Runs from a suspicious directory
        for frag in SUSPICIOUS_DIRS:
            if frag in path_l:
                r.flag(f"Runs from suspicious dir: {frag.strip(chr(92))}", "HIGH")
                break

        # 2. Masquerading as a critical system process outside System32
        if name_l in SYSTEM_PROCESS_NAMES:
            if path_l and SYSTEM32 not in path_l and SYSWOW64 not in path_l:
                r.flag("Masquerades as system process (outside System32)", "HIGH")

        # 3. Appeared after baseline capture (new process)
        if self._baseline_ok and pid not in self._known_pids:
            r.flag("New process — appeared after monitoring started", "LOW")
            self._known_pids.add(pid)   # add so it is only flagged once

        # 4. Unresolvable executable path (injection / hollowing indicator)
        known_virtual = {"system", "registry", "memory compression", "secure system"}
        if (not exe_path or exe_path == "\u2014") and name_l not in known_virtual:
            r.flag("Executable path unresolvable (possible hollowing/injection)", "MEDIUM")

        # 5. Executable directly in drive root (e.g. C:\evil.exe)
        if path_l and re.match(r"^[a-z]:\\[^\\]+\.exe$", path_l):
            r.flag("Executable directly in drive root", "MEDIUM")

        return r

    # ── Startup entry heuristics ──────────────────────────────────────────
    def scan_startup_entry(self, name: str, command: str) -> SuspicionResult:
        r = SuspicionResult()

        # 1. Script file extension in the command
        if _RE_SCRIPT_EXT.search(command):
            r.flag("Runs a script file (.bat/.cmd/.vbs/.ps1 etc.)", "MEDIUM")

        # 2. Hidden / encoded execution flags
        if _RE_HIDDEN_FLAGS.search(command):
            r.flag("Uses hidden/encoded execution flags (-enc, -w hidden, etc.)", "HIGH")

        # 3. LOLBin launcher
        if _RE_LOLBINS.search(command):
            r.flag("Uses LOLBin launcher (wscript/mshta/rundll32 etc.)", "HIGH")

        # 4. Suspicious directory in path
        cmd_l = command.lower()
        for frag in SUSPICIOUS_DIRS:
            if frag in cmd_l:
                r.flag(f"Path points to suspicious dir: {frag.strip(chr(92))}", "HIGH")
                break

        # 5. Long base64-like argument (encoded payload indicator)
        if len(command) > 200 and _RE_B64_BLOCK.search(command):
            r.flag("Long base64-like argument (encoded payload?)", "HIGH")

        # 6. Recently modified/created executable (< 3 days)
        for m in _RE_EXE_PATH.finditer(command):
            exe_path = m.group(1)
            if os.path.isfile(exe_path):
                try:
                    age_days = (time.time() - os.path.getmtime(exe_path)) / 86400
                    if age_days < 3:
                        r.flag("Executable modified/created within last 3 days", "MEDIUM")
                except OSError:
                    pass
                break

        return r
