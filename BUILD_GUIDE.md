# EZUnlocker — Build & Deploy Guide

## Prerequisites
- Python 3.11+ (64-bit) installed and on PATH
- Windows 10/11 (64-bit)

---

## 1. Create Virtual Environment & Install Dependencies

```powershell
# Navigate to the project folder
cd "C:\Users\Gaz Lounge PC-2\.gemini\antigravity\scratch\EZUnlocker"

# Create venv
python -m venv venv

# Activate venv
.\venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip

# Install all dependencies
pip install -r requirements.txt
```

---

## 2. Run in Development Mode

```powershell
# (venv must be active)
python main.py
```

> **Note:** Run your terminal as Administrator for full functionality.

---

## 3. Build Standalone .exe with PyInstaller

### Option A — Full command (recommended, with manifest & UAC)

```powershell
pyinstaller `
  --onefile `
  --noconsole `
  --uac-admin `
  --name "EZUnlocker" `
  --manifest ezunlocker.manifest `
  --add-data "tools;tools" `
  --hidden-import=customtkinter `
  --hidden-import=psutil `
  --hidden-import=winreg `
  --collect-data customtkinter `
  main.py
```

### Option B — With a custom icon (.ico file)

```powershell
pyinstaller `
  --onefile `
  --noconsole `
  --uac-admin `
  --name "EZUnlocker" `
  --icon "icon.ico" `
  --manifest ezunlocker.manifest `
  --add-data "tools;tools" `
  --hidden-import=customtkinter `
  --hidden-import=psutil `
  --hidden-import=winreg `
  --collect-data customtkinter `
  main.py
```

> After the build, the `.exe` is in the `dist/` folder.

---

## 4. Post-Build: Copy tools/ folder

The `--add-data "tools;tools"` flag bundles the tools/ directory inside the exe.
However, for user-added tools (Process Hacker, AVZ, etc.), users should keep the
`tools/` folder **next to** the `.exe`:

```
dist/
  EZUnlocker.exe
  tools/
    ProcessHacker.exe
    AVZ.exe
    CureIt.exe
    ...
```

The app auto-detects any `.exe` placed in `tools/` at runtime.

---

## 5. Windows Defender False Positive — Remediation Guide

PyInstaller-packed executables are commonly flagged by Windows Defender and other
AV engines due to the generic UPX packing heuristics. Here's how to handle it:

### Method 1 — Defender Exclusion (Development/Testing)

```powershell
# Run PowerShell as Administrator
Add-MpPreference -ExclusionPath "C:\Path\To\EZUnlocker\dist"
```

Remove the exclusion after you're done:
```powershell
Remove-MpPreference -ExclusionPath "C:\Path\To\EZUnlocker\dist"
```

### Method 2 — Submit False Positive to Microsoft

1. Go to: https://www.microsoft.com/en-us/wdsi/filesubmission
2. Upload your `EZUnlocker.exe`
3. Select **"Microsoft security software determined this file is not a threat"**
4. Wait 24–48 hours for whitelisting

### Method 3 — Build WITHOUT UPX compression (reduces false positives)

```powershell
pyinstaller --onefile --noconsole --uac-admin --noupx --name "EZUnlocker" `
  --manifest ezunlocker.manifest --collect-data customtkinter main.py
```

`--noupx` disables compression. The `.exe` will be larger (~60–90MB) but
significantly less likely to be flagged.

### Method 4 — Code Sign the Executable

If you have an EV Code Signing certificate (DigiCert, Sectigo, etc.):

```powershell
# Using signtool from Windows SDK
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /f "YourCert.pfx" /p "YourPassword" dist\EZUnlocker.exe
```

A signed binary is **very rarely** flagged by Windows Defender.

### Method 5 — Nuitka instead of PyInstaller (advanced)

Nuitka compiles Python to C, producing a native binary with much lower AV
detection rates:

```powershell
pip install nuitka
python -m nuitka --standalone --onefile --windows-uac-admin `
  --windows-console-mode=disable --assume-yes-for-downloads main.py
```

---

## 6. Distribution Checklist

- [ ] Build with `--noupx` for lower AV detection
- [ ] Code-sign the binary if distributing publicly
- [ ] Include `tools/` folder (empty) next to the `.exe`
- [ ] Test on a clean VM before releasing
- [ ] Submit to https://www.virustotal.com to check AV detection ratio

---

## 7. Dependency Versions (pinned for reproducibility)

| Package        | Version |
|----------------|---------|
| customtkinter  | ≥5.2.2  |
| psutil         | ≥5.9.8  |
| pyinstaller    | ≥6.6.0  |
| pillow         | ≥10.3.0 |
