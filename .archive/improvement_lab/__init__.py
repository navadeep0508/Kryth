"""KRYTH Autonomous Improvement Laboratory.

Observe → Recommend → Experiment → Benchmark → Evaluate → Compare → Ask.

KRYTH MUST NOT merge automatically. The lab recommends; humans decide.

Usage::

    from improvement_lab.lab_manager import LabManager

    lab = LabManager(project_root=".")
    session = lab.run_from_latest()          # loads latest benchmark, runs top experiment
    print(session.recommendation.headline)   # present to user — never auto-merge
"""

from improvement_lab.lab_manager import LabManager, LabSession, LabConfig

__all__ = ["LabManager", "LabSession", "LabConfig"]
