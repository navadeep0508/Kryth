# Publishing KRYTH to PyPI

KRYTH builds a wheel and source archive from `pyproject.toml`. The GitHub release workflow runs the test suite, validates a built wheel on clean Windows runners across Python 3.10–3.14, builds the Windows installer, then builds and publishes distributions with PyPI Trusted Publishing. PyPI publishing depends on all installation checks passing.

## Before a release

1. Confirm the `kryth` project and target version are available to your PyPI account. PyPI versions are immutable.
2. Configure a PyPI Trusted Publisher for owner `navadeep0508`, repository `Kryth`, workflow `python-publish.yml`, and GitHub environment `pypi`.
3. Create the same version in `pyproject.toml` and `ui/components/logo.py` (`KRYTH_VERSION`).
4. Push the matching `vX.Y.Z` tag. The workflow runs the complete test and installer build before publishing to PyPI, then creates the GitHub release and attaches the Windows installer.

The workflow blocks PyPI publishing unless the Linux tests, clean Windows wheel installation matrix, distribution validation, and Windows installer build succeed.

## Local validation

```powershell
python -m pip install --upgrade build twine
python -m build
python -m twine check --strict dist\kryth-*.whl dist\kryth-*.tar.gz
```

The installer source is `installer/kryth.iss`. It uses an already installed Python 3.10–3.14, installs the bundled wheel for the current user, runs `python -m kryth setup --path`, and verifies the installed CLI. It does not run during wheel or source builds.
