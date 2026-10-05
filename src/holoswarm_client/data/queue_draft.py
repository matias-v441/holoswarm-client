"""The mission sequence being put together for a new queue.

Missions are encoded planner payloads ({"type", "details"}), frozen when added: editing the map
afterwards does not change them. The whole sequence is sent at once when the queue is created.
Kept in memory only; once created, the queue lives on the bridge.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class DraftMission:
    mission_id: str
    name: str
    payload: Mapping[str, Any]  # {"type", "details"}, validated by the codec
    summary: str = ""

    @property
    def planner_type(self) -> str:
        return str(self.payload.get("type", ""))

    def to_wire(self) -> dict[str, Any]:
        """Mission entry of POST /queues."""
        return {"id": self.mission_id, "name": self.name, "type": self.payload["type"], "details": self.payload["details"]}


type Callback = Callable[["QueueDraft"], None]


class QueueDraft:
    """Ordered missions of the next queue. GUI thread only."""

    def __init__(self) -> None:
        self._missions: list[DraftMission] = []
        self._added = 0
        self._callbacks: list[Callback] = []
        self._dirty = True

    @property
    def missions(self) -> tuple[DraftMission, ...]:
        return tuple(self._missions)

    def __len__(self) -> int:
        return len(self._missions)

    def add(self, payload: Mapping[str, Any], summary: str = "", name: str | None = None) -> DraftMission:
        self._added += 1
        mission = DraftMission(
            mission_id=f"m-{uuid.uuid4().hex[:12]}",
            name=name or f"Mission {self._added}",
            payload=payload,
            summary=summary,
        )
        self._missions.append(mission)
        self._dirty = True
        return mission

    def remove(self, mission_id: str) -> None:
        self._missions = [m for m in self._missions if m.mission_id != mission_id]
        self._dirty = True

    def rename(self, mission_id: str, name: str) -> None:
        # Not notified: the name is typed in the editor that shows it, and redrawing would end the typing.
        self._missions = [replace(m, name=name) if m.mission_id == mission_id else m for m in self._missions]

    def move(self, mission_id: str, delta: int) -> None:
        """Move a mission earlier (delta < 0) or later in the execution order."""
        index = next((i for i, m in enumerate(self._missions) if m.mission_id == mission_id), None)
        if index is None:
            return
        target = max(0, min(len(self._missions) - 1, index + delta))
        if target != index:
            self._missions.insert(target, self._missions.pop(index))
            self._dirty = True

    def clear(self, mission_ids: set[str] | None = None) -> None:
        """Remove the given missions (all when None), e.g. the ones a created queue took."""
        self._missions = [] if mission_ids is None else [m for m in self._missions if m.mission_id not in mission_ids]
        self._dirty = True

    def subscribe(self, callback: Callback) -> None:
        self._callbacks.append(callback)

    def notify(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        for callback in self._callbacks:
            callback(self)
