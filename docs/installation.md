# Installing KRYTH on Windows

KRYTH requires Python 3.10–3.14. The Python `pip` installer creates the `kryth.exe` launcher in the selected interpreter's Scripts directory, but pip does not add that directory to Windows PATH.

## Recommended: Windows installer

Download `KRYTH-Setup-<version>.exe` from the [latest GitHub release](https://github.com/navadeep0508/Kryth/releases/latest). The installer:

1. Selects the newest installed Python from 3.10 through 3.14 that has pip.
2. Installs the bundled KRYTH wheel for the current user.
3. Adds that interpreter's user Scripts directory to `HKCU\Environment\Path`.
4. Verifies the installed module and generated `kryth.exe` launcher.

It needs no administrator rights. Open a new terminal after it finishes.

## Install with pip

```powershell
python -m pip install --user kryth
python -m kryth setup --path
```

If KRYTH was installed into the active interpreter's regular environment instead of the user site, the command configures that interpreter's `sysconfig` Scripts directory. `python -m kryth` is the recovery route when `kryth.exe` cannot be found on PATH. It works without loading the KRYTH provider configuration.

After setup, close the terminal, open a new one, and run:

```powershell
kryth --version
kryth
```

The setup command preserves the existing per-user PATH value and registry type, avoids adding duplicate entries, and broadcasts the Windows environment-change notification. It changes only the current user's environment. A failed registry update prints the directory to add manually.

## Python and virtual environments

KRYTH's PATH manager uses `sysconfig` and supports the Windows schemes for Python 3.10–3.14, including `nt_user`. With a virtual environment, use that environment's Python to install and configure KRYTH; the manager then targets that environment's Scripts directory. PATH changes are persistent for the Windows user, so install KRYTH into a user-visible interpreter if you expect `kryth` to work from every terminal.

On non-Windows platforms, `setup --path` reports that PATH configuration is Windows-only.
