"""Post-edit verification tool.

Wraps ``agent.validators.validate_paths`` as a tool the model can call
after a batch of file edits to confirm syntax / parse correctness
before moving on. Cheap, deterministic, no network calls.
"""

from __future__ import annotations

from agent.validators import validate_paths


def verify_files(paths):
    """Run language-aware validators on ``paths`` and report failures.

    Returns a multi-line ``validation: ...`` report when issues exist;
    empty string when everything checked passes; an advisory line when
    no checker applies to any of the supplied extensions.
    """
    result = validate_paths(paths)
    if result:
        return result
    return "verification: all checked files parse cleanly"


__all__ = ["verify_files"]
