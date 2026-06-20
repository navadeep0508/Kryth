"""Organizational communication (Phase G).

A single append-only log of organizational messages — status reports, blocker
reports, escalations, mission reports, and decision logs — with sender, role,
timestamp, and payload. Messages are stored in order and are replay-capable
(filter by kind/mission and re-emit). Pure data; injectable time for determinism.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class MessageKind(Enum):
    STATUS = "status"
    BLOCKER = "blocker"
    ESCALATION = "escalation"
    MISSION_REPORT = "mission_report"
    DECISION = "decision"
    RESOLUTION = "resolution"


@dataclass
class Message:
    seq: int
    kind: MessageKind
    sender: str
    role: str                       # worker | lead | head | executive
    text: str
    mission_id: str = ""
    at: float = 0.0
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"seq": self.seq, "kind": self.kind.value, "sender": self.sender,
                "role": self.role, "text": self.text, "mission_id": self.mission_id,
                "at": self.at, "data": dict(self.data)}


class CommunicationLog:
    """Append-only, ordered, replay-capable org communication log."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: List[Message] = []
        self._seq = 0

    def post(self, kind: MessageKind, sender: str, role: str, text: str, *,
             mission_id: str = "", at: float = 0.0, **data) -> Message:
        with self._lock:
            self._seq += 1
            m = Message(seq=self._seq, kind=kind, sender=sender, role=role,
                        text=text, mission_id=mission_id, at=at, data=data)
            self._messages.append(m)
            return m

    # convenience emitters
    def status(self, sender, role, text, **kw):
        return self.post(MessageKind.STATUS, sender, role, text, **kw)

    def blocker(self, sender, role, text, **kw):
        return self.post(MessageKind.BLOCKER, sender, role, text, **kw)

    def escalation(self, sender, role, text, **kw):
        return self.post(MessageKind.ESCALATION, sender, role, text, **kw)

    def decision(self, sender, role, text, **kw):
        return self.post(MessageKind.DECISION, sender, role, text, **kw)

    def mission_report(self, sender, role, text, **kw):
        return self.post(MessageKind.MISSION_REPORT, sender, role, text, **kw)

    # -- querying / replay --------------------------------------------------

    def all(self) -> List[Message]:
        with self._lock:
            return list(self._messages)

    def by_kind(self, kind: MessageKind) -> List[Message]:
        with self._lock:
            return [m for m in self._messages if m.kind is kind]

    def by_mission(self, mission_id: str) -> List[Message]:
        with self._lock:
            return [m for m in self._messages if m.mission_id == mission_id]

    def decisions(self) -> List[Message]:
        return self.by_kind(MessageKind.DECISION)

    def open_blockers(self) -> List[Message]:
        """Blockers with no later resolution mentioning the same mission."""
        with self._lock:
            resolved_missions = {m.mission_id for m in self._messages
                                 if m.kind is MessageKind.RESOLUTION and m.mission_id}
            return [m for m in self._messages
                    if m.kind is MessageKind.BLOCKER and m.mission_id not in resolved_missions]

    def replay(self, sink: Callable[[dict], None], *,
               kind: Optional[MessageKind] = None, mission_id: str = "") -> int:
        """Re-emit messages (filtered) in order through a sink. Returns count."""
        with self._lock:
            msgs = list(self._messages)
        n = 0
        for m in msgs:
            if kind is not None and m.kind is not kind:
                continue
            if mission_id and m.mission_id != mission_id:
                continue
            sink(m.to_dict())
            n += 1
        return n

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [m.to_dict() for m in self._messages]
