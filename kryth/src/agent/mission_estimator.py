"""Mode normalizer — validates execution mode strings.

Previously contained the full Mission Cost Estimator + DAG Eligibility Engine
(~370 lines). That code was never wired into the runtime and has been archived
to ``.archive/agent/mission_estimator.py``.
"""

from __future__ import annotations

from typing import Optional

_VALID_MODES = ("direct", "dag", "swarm", "auto")


def normalize_mode(m: str) -> Optional[str]:
    m = (m or "").strip().lower()
    return m if m in _VALID_MODES else None
