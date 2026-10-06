"""The selected queue: the new one being put together, or a stored one (edited, or shown read-only).

Missions are encoded planner payloads ({"type", "details"}); QueueView re-encodes a mission whenever
its paths or areas change on the map. A new mission has no payload ({}) until its first path or area
is drawn. The whole queue is sent at once, to create a new queue or to replace the stored one being
edited (same id).

A stored queue is edited on a copy loaded from the bridge; the new queue put together before is
kept meanwhile and comes back when the edit is closed. Kept in memory only.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from holoswarm_client.data.execution import MissionExecution, QueueExecution
from holoswarm_client.iroc.mission_codec import describe


@dataclass(frozen=True)
class QueueSettings:
    name: str = ""
    scheduler: str = "batch"
    params: Mapping[str, Any] = field(default_factory=dict)
    world_id: str = ""

    @staticmethod
    def of(queue: QueueExecution) -> "QueueSettings":
        return QueueSettings(name=queue.name, scheduler=queue.scheduler, params=dict(queue.params), world_id=queue.world_id)


@dataclass(frozen=True)
class DraftMission:
    mission_id: str
    name: str
    payload: Mapping[str, Any]  # {"type", "details"}, validated by the codec; {} until it has a path or area
    summary: str = ""
    priority: int = 0
    return_home_policy: int = 0

    @property
    def planner_type(self) -> str:
        return str(self.payload.get("type", ""))

    @property
    def empty(self) -> bool:
        return not self.payload

    def to_wire(self) -> dict[str, Any]:
        """Mission entry of POST/PUT /queues."""
        wire = {"id": self.mission_id, "name": self.name, "type": self.payload.get("type", ""),
                "details": self.payload.get("details", {})}
        if self.priority:
            wire["priority"] = self.priority
        if self.return_home_policy:
            wire["return_home_policy"] = self.return_home_policy
        return wire

    @staticmethod
    def of(mission: MissionExecution) -> "DraftMission":
        """A stored mission, to edit."""
        payload = {"type": mission.planner_type, "details": mission.details}
        return DraftMission(mission.mission_id, mission.name, payload, describe(payload), mission.priority,
                            mission.return_home_policy)


type Contents = tuple[QueueSettings, tuple[dict[str, Any], ...]]


def contents_of(queue: QueueExecution) -> Contents:
    """What an edit can change, as the bridge has it."""
    return QueueSettings.of(queue), tuple(DraftMission.of(m).to_wire() for m in queue.missions.values())


@dataclass
class _Slot:
    missions: list[DraftMission] = field(default_factory=list)
    settings: QueueSettings = field(default_factory=QueueSettings)
    added: int = 0  # missions added so far, for their default names
    errors: dict[str, str] = field(default_factory=dict)  # mission id -> why its paths or areas do not encode

    def contents(self) -> Contents:
        return self.settings, tuple(m.to_wire() for m in self.missions)


type Callback = Callable[["QueueDraft"], None]


class QueueDraft:
    """Ordered missions and settings of the new queue or of the stored queue being edited. GUI thread only."""

    def __init__(self) -> None:
        self._new = _Slot()
        self._edit: _Slot | None = None
        self.queue_id: str | None = None   # the stored queue being edited; None: the new queue
        self._base: Contents | None = None  # the bridge's copy the edit is based on
        self._base_updated = 0.0            # its updated_at: older copies (e.g. before an upload's event) are ignored
        self.outdated = False               # the bridge's copy changed while the edit had changes of its own
        self.read_only = False              # the stored queue is submitted: shown, but cannot be changed
        self._callbacks: list[Callback] = []
        self._changed = True

    @property
    def _slot(self) -> _Slot:
        return self._edit if self._edit is not None else self._new

    @property
    def editing(self) -> bool:
        return self.queue_id is not None

    @property
    def missions(self) -> tuple[DraftMission, ...]:
        return tuple(self._slot.missions)

    @property
    def settings(self) -> QueueSettings:
        return self._slot.settings

    def mission(self, mission_id: str | None) -> DraftMission | None:
        return next((m for m in self._slot.missions if m.mission_id == mission_id), None)

    def __len__(self) -> int:
        return len(self._slot.missions)

    @property
    def problems(self) -> list[tuple[DraftMission, str]]:
        """Missions that cannot be sent as they are."""
        errors = self._slot.errors
        return [(m, errors.get(m.mission_id) or "no path or area yet") for m in self._slot.missions
                if m.empty or m.mission_id in errors]

    def error(self, mission_id: str) -> str | None:
        return self._slot.errors.get(mission_id)

    @property
    def dirty(self) -> bool:
        """The edited queue differs from the bridge's copy it is based on."""
        return self._edit is not None and self._edit.contents() != self._base

    # | ----------------------- missions and settings ----------------------- |

    def add(self, payload: Mapping[str, Any], summary: str = "", name: str | None = None) -> DraftMission:
        slot = self._slot
        slot.added += 1
        mission = DraftMission(
            mission_id=f"m-{uuid.uuid4().hex[:12]}",
            name=name or f"Mission {slot.added}",
            payload=payload,
            summary=summary,
        )
        slot.missions.append(mission)
        self._changed = True
        return mission

    def add_empty(self, name: str | None = None) -> DraftMission:
        """A new mission without paths or areas yet."""
        return self.add({}, "", name)

    def set_error(self, mission_id: str, message: str | None) -> None:
        """Why the mission's paths or areas cannot be encoded (None: they can)."""
        errors = self._slot.errors
        if errors.get(mission_id) == message:
            return
        if message is None:
            errors.pop(mission_id, None)
        else:
            errors[mission_id] = message
        self._changed = True

    def update(self, mission_id: str, payload: Mapping[str, Any], summary: str = "") -> None:
        """New content of a mission (e.g. its geometry edited in the Task window)."""
        slot = self._slot
        slot.missions = [replace(m, payload=payload, summary=summary) if m.mission_id == mission_id else m for m in slot.missions]
        self._changed = True

    def remove(self, mission_id: str) -> None:
        self.clear({mission_id})

    def rename(self, mission_id: str, name: str) -> None:
        # Not notified: the name is typed in the editor that shows it, and redrawing would end the typing.
        slot = self._slot
        slot.missions = [replace(m, name=name) if m.mission_id == mission_id else m for m in slot.missions]

    def set_settings(self, **changes: Any) -> None:
        """Name, scheduler, params. Not notified, like rename()."""
        slot = self._slot
        slot.settings = replace(slot.settings, **changes)

    def move(self, mission_id: str, delta: int) -> None:
        """Move a mission earlier (delta < 0) or later in the execution order."""
        missions = self._slot.missions
        index = next((i for i, m in enumerate(missions) if m.mission_id == mission_id), None)
        if index is None:
            return
        target = max(0, min(len(missions) - 1, index + delta))
        if target != index:
            missions.insert(target, missions.pop(index))
            self._changed = True

    def clear(self, mission_ids: set[str] | None = None) -> None:
        """Remove the given missions (all when None)."""
        slot = self._slot
        slot.missions = [] if mission_ids is None else [m for m in slot.missions if m.mission_id not in mission_ids]
        slot.errors = {k: v for k, v in slot.errors.items() if self.mission(k) is not None}
        self._changed = True

    def forget_created(self, mission_ids: set[str]) -> None:
        """The new queue was created with these missions: drop them from it, also while a stored queue is edited."""
        self._new.missions = [m for m in self._new.missions if m.mission_id not in mission_ids]
        self._new.errors = {k: v for k, v in self._new.errors.items() if k not in mission_ids}
        self._changed = True

    # | ----------------------- editing a stored queue ----------------------- |

    def load(self, queue: QueueExecution) -> None:
        """Edit a copy of the stored queue (replacing an edit of another one); read-only while it is submitted.
        The new queue is kept meanwhile."""
        missions = [DraftMission.of(m) for m in queue.missions.values()]
        self._edit = _Slot(missions, QueueSettings.of(queue), len(missions))
        self.queue_id = queue.queue_id
        self._base = contents_of(queue)
        self._base_updated = queue.updated_at
        self.outdated = False
        self.read_only = not queue.state.can_submit
        self._changed = True

    def close(self) -> None:
        """Back to the new queue; changes of the edit are dropped."""
        self._edit = None
        self.queue_id = None
        self._base = None
        self.outdated = False
        self.read_only = False
        self._changed = True

    def sync(self, queue: QueueExecution) -> None:
        """The bridge's copy of the edited queue may have changed: follow it unless the edit has changes of its own."""
        if self._edit is None or queue.queue_id != self.queue_id or queue.updated_at < self._base_updated:
            return
        if self.read_only != (not queue.state.can_submit):
            self.read_only = not queue.state.can_submit
            self._changed = True
        theirs = contents_of(queue)
        if theirs == self._base:
            return
        if self._edit.contents() == theirs:  # the upload of this edit
            self._base = theirs
            self._base_updated = queue.updated_at
            self.outdated = False
            self._changed = True
        elif not self.dirty:
            self.load(queue)
        else:
            self.outdated = True
            self._changed = True

    # | ----------------------- notification ----------------------- |

    def subscribe(self, callback: Callback) -> None:
        self._callbacks.append(callback)

    def notify(self) -> None:
        if not self._changed:
            return
        self._changed = False
        for callback in self._callbacks:
            callback(self)
