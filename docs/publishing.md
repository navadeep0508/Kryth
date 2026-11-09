# Publishing KRYTH to PyPI

KRYTH builds a wheel and source archive from `pyproject.toml`. The GitHub release workflow tests Python 3.11, builds both distributions, validates their metadata, and publishes with PyPI Trusted Publishing. It does not need a long-lived PyPI API token.

## Before the first release

1. Confirm the `kryth` project name is available to your PyPI account immediately before the first upload. Name checks can change. For a new project, PyPI can create it on first upload from a pending Trusted Publisher; an existing project must be owned by your account.
2. In PyPI account settings, add a pending Trusted Publisher for owner `navadeep0508`, repository `Kryth`, workflow `python-publish.yml`, and GitHub environment `pypi`.
3. In GitHub repository settings, create the `pypi` environment. Add a required reviewer if releases should wait for manual approval.
4. Set the same new release version in `pyproject.toml` and `ui/components/logo.py` (`KRYTH_VERSION`).
5. From the release commit, create a GitHub release whose tag matches that version, prefixed with `v` (for example, version `0.0.37` uses tag `v0.0.37`).

PyPI project names and versions are first-come, first-served. A release workflow will fail if the project name is unavailable or that version has already been uploaded.

## Validate locally

From a clean checkout, run:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check --strict dist/*
```

The release workflow performs the same build and metadata checks after the test job. Publishing only runs for a published GitHub release and uses the `pypi` trusted-publishing environment.
