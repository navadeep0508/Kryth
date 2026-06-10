"""OpenCLI installer — fully automated setup of Node.js, @jackwener/opencli,
and the Chrome Browser Bridge extension.

Setup sequence
--------------
1. Ensure Node.js >= 20 is installed (auto-installs via system package manager)
2. Install @jackwener/opencli globally via npm
3. Open Chrome to the exact Browser Bridge Web Store page so the user can
   click "Add to Chrome" in one step
4. Run `opencli doctor` to confirm everything is wired up
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import webbrowser


MIN_NODE_MAJOR = 20
OPENCLI_PACKAGE = "@jackwener/opencli"

EXTENSION_STORE_URL = (
    "https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk"
)
# Fallback: load-unpacked ZIP from GitHub Releases
EXTENSION_RELEASES_URL = "https://github.com/jackwener/opencli/releases"


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, "", "timed out"
    except Exception as e:
        return 1, "", str(e)


# ---------------------------------------------------------------------------
# Node.js
# ---------------------------------------------------------------------------

def check_node() -> tuple[bool, str]:
    """Return (ok, version_string). ok is True only if node >= 20."""
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return False, "not found"
    rc, out, err = _run([node, "--version"])
    if rc != 0:
        return False, err or "unknown error"
    version = out.lstrip("v")
    try:
        major = int(version.split(".")[0])
        if major < MIN_NODE_MAJOR:
            return False, f"{out} (need >= v{MIN_NODE_MAJOR})"
    except ValueError:
        pass
    return True, out


def install_node() -> str:
    """Install Node.js via the best available system package manager."""
    system = platform.system().lower()
    if system == "darwin":
        if shutil.which("brew"):
            rc, out, err = _run(["brew", "install", "node"], timeout=300)
            return out if rc == 0 else f"[ERROR] brew install node: {err}"
        return (
            "[ERROR] Homebrew not found.\n"
            "Install Node.js >= v20 manually from https://nodejs.org"
        )
    if system == "linux":
        for mgr_cmd in (
            ["apt-get", "install", "-y", "nodejs"],
            ["dnf", "install", "-y", "nodejs"],
            ["pacman", "-S", "--noconfirm", "nodejs"],
        ):
            if shutil.which(mgr_cmd[0]):
                rc, out, err = _run(["sudo"] + mgr_cmd, timeout=300)
                return out if rc == 0 else f"[ERROR] {mgr_cmd[0]}: {err}"
        return (
            "[ERROR] No supported package manager found.\n"
            "Install Node.js >= v20 manually from https://nodejs.org"
        )
    if system == "windows":
        if shutil.which("winget"):
            rc, out, err = _run(
                ["winget", "install", "--id", "OpenJS.NodeJS.LTS", "-e"],
                timeout=300,
            )
            return out if rc == 0 else f"[ERROR] winget: {err}"
        return "[ERROR] Install Node.js >= v20 from https://nodejs.org"
    return "[ERROR] Unsupported platform — install Node.js >= v20 from https://nodejs.org"


# ---------------------------------------------------------------------------
# opencli CLI
# ---------------------------------------------------------------------------

def check_opencli() -> tuple[bool, str]:
    """Return (ok, version_or_error)."""
    cli = shutil.which("opencli") or shutil.which("opencli.cmd")
    if not cli:
        return False, "not found"
    rc, out, err = _run([cli, "--version"])
    if rc != 0:
        return False, err or "unknown error"
    return True, out


def install_opencli() -> str:
    """Install @jackwener/opencli globally via npm."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        return "[ERROR] npm not found — install Node.js first"
    rc, out, err = _run([npm, "install", "-g", OPENCLI_PACKAGE], timeout=120)
    if rc == 0:
        return f"Installed {OPENCLI_PACKAGE}\n{out}".strip()
    return f"[ERROR] npm install failed:\n{err}"


# ---------------------------------------------------------------------------
# Chrome extension
# ---------------------------------------------------------------------------

def _find_chrome() -> str | None:
    """Return the path to Chrome/Chromium, or None if not found."""
    system = platform.system().lower()
    if system == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Chromium\Application\chrome.exe",
        ]
    else:
        candidates = []

    for path in candidates:
        if shutil.which(path) or __import__("os").path.isfile(path):
            return path

    # Fallback: search PATH
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found

    return None


def open_extension_page() -> str:
    """Open Chrome to the Browser Bridge Web Store page.

    Tries Chrome directly so the user lands in the right browser.
    Falls back to the system default browser if Chrome is not found.
    Returns a status message describing what was opened.
    """
    chrome = _find_chrome()
    if chrome:
        try:
            subprocess.Popen(
                [chrome, EXTENSION_STORE_URL],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return (
                f"Opened Chrome → {EXTENSION_STORE_URL}\n"
                "Click  'Add to Chrome'  then  'Add extension'  in the popup."
            )
        except Exception as e:
            pass  # fall through to webbrowser

    # webbrowser fallback (opens whatever is default)
    try:
        webbrowser.open(EXTENSION_STORE_URL)
        return (
            f"Opened browser → {EXTENSION_STORE_URL}\n"
            "Click  'Add to Chrome'  then  'Add extension'  in the popup."
        )
    except Exception as e:
        return (
            f"[WARN] Could not open browser automatically: {e}\n"
            f"Open this URL manually in Chrome:\n  {EXTENSION_STORE_URL}"
        )


# ---------------------------------------------------------------------------
# opencli doctor
# ---------------------------------------------------------------------------

def verify() -> str:
    """Run opencli doctor and return the full diagnostic report."""
    ok_node, ver_node = check_node()
    ok_cli, ver_cli = check_opencli()
    lines = [
        f"node:    {'✓ ' + ver_node if ok_node else '✗ ' + ver_node}",
        f"opencli: {'✓ ' + ver_cli  if ok_cli  else '✗ ' + ver_cli}",
    ]

    if not ok_cli:
        lines.append("\nRun browser_setup to finish installation.")
        return "\n".join(lines)

    cli = shutil.which("opencli") or shutil.which("opencli.cmd")
    rc, out, err = _run([cli, "doctor"], timeout=30)  # type: ignore[list-item]
    doctor = out or err
    lines.append(f"\ndoctor (exit {rc}):\n{doctor}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full automated setup
# ---------------------------------------------------------------------------

def setup() -> str:
    """Run the complete OpenCLI setup sequence and return a status report.

    Steps
    -----
    1. Check / install Node.js >= 20
    2. Check / install @jackwener/opencli via npm
    3. Open Chrome to the Browser Bridge Web Store page
    4. Run opencli doctor
    """
    report: list[str] = ["=== OpenCLI Setup ===\n"]

    # Step 1 — Node.js
    ok_node, ver_node = check_node()
    if ok_node:
        report.append(f"[1/4] Node.js  ✓  {ver_node}")
    else:
        report.append(f"[1/4] Node.js  ✗  {ver_node} — installing...")
        result = install_node()
        ok_node, ver_node = check_node()
        if ok_node:
            report.append(f"      Installed  ✓  {ver_node}")
        else:
            report.append(f"      FAILED: {result}")
            report.append("\nSetup cannot continue without Node.js >= v20.")
            return "\n".join(report)

    # Step 2 — opencli CLI
    ok_cli, ver_cli = check_opencli()
    if ok_cli:
        report.append(f"[2/4] opencli  ✓  {ver_cli}")
    else:
        report.append(f"[2/4] opencli  ✗  not installed — running npm install...")
        result = install_opencli()
        ok_cli, ver_cli = check_opencli()
        if ok_cli:
            report.append(f"      Installed  ✓  {ver_cli}")
        else:
            report.append(f"      FAILED: {result}")
            report.append("\nSetup cannot continue without @jackwener/opencli.")
            return "\n".join(report)

    # Step 3 — Chrome Browser Bridge extension
    report.append("[3/4] Browser Bridge extension — opening Chrome Web Store...")
    ext_msg = open_extension_page()
    report.append(f"      {ext_msg}")
    report.append(
        "\n      >> After clicking 'Add to Chrome' come back and run: verify()"
    )

    # Step 4 — opencli doctor
    report.append("\n[4/4] Running opencli doctor...")
    cli = shutil.which("opencli") or shutil.which("opencli.cmd")
    rc, out, err = _run([cli, "doctor"], timeout=30)  # type: ignore[list-item]
    doctor = out or err
    if rc == 0:
        report.append(f"      ✓  doctor passed\n{doctor}")
    elif rc == 69:
        report.append(
            "      ✗  Browser Bridge not connected (exit 69).\n"
            "         Complete step 3: install the extension in Chrome,\n"
            "         then run  browser_setup_verify  to re-check."
        )
    else:
        report.append(f"      ✗  doctor exit {rc}:\n{doctor}")

    report.append("\n=== Setup complete ===")
    return "\n".join(report)
