from dataclasses import dataclass,field
from collections.abc import Callable
from types import MappingProxyType
from uuid import uuid4
from enum import Enum

@dataclass(frozen=True)
class Selection:
    """Selected tasks (map primitives): what Ctrl+click on the map picks."""
    uuid:str = field(default_factory=lambda: str(uuid4()))
    items: tuple[str] = field(default_factory=tuple)

@dataclass(frozen=True)
class QueueSelection:
    """The queue shown in the editor, the map and the Task window; None is the new queue."""
    queue_id: str | None = None
    uuid: str = "queue_selection"

@dataclass(frozen=True)
class MissionSelection:
    """The mission of the selected queue being worked on; its tasks are drawn highlighted."""
    mission_id: str | None = None
    uuid: str = "mission_selection"

@dataclass(frozen=True)
class Settings:
    locations: dict[str, list[float]]
    current_location: str
    use_local_pose: bool = False
    uuid:str = field(default_factory=lambda: str(uuid4()))

type Callback = Callable[["Session"],None]

type Primitive = Selection | QueueSelection | MissionSelection | Settings

class Session:
    """Selections and settings of the operator. Three selection levels: queue > mission > task."""

    def __init__(self):
        self._states: dict[str, Primitive] = {}
        self._callbacks: list[Callback] = []

    @property
    def selection(self) -> Selection | None:
        return next((v for v in self._states.values() if isinstance(v, Selection)), None)

    def item_selected(self, uuid:str) -> bool:
        sel = self.selection
        return sel and uuid in sel.items

    @property
    def selected_items(self) -> str | None:
        sel = self.selection
        if not sel or not sel.items:
            return None
        return sel.items

    @property
    def selected_task(self) -> str | None:
        items = self.selected_items
        return items[0] if items else None

    @property
    def selected_queue(self) -> str | None:
        selection = self._states.get(QueueSelection.uuid)
        return selection.queue_id if selection else None

    @property
    def selected_mission(self) -> str | None:
        selection = self._states.get(MissionSelection.uuid)
        return selection.mission_id if selection else None

    def select_queue(self, queue_id: str | None) -> None:
        self.push(QueueSelection(queue_id))

    def select_mission(self, mission_id: str | None) -> None:
        self.push(MissionSelection(mission_id))

    def select_task(self, uuid: str | None) -> None:
        selection = self.selection
        if uuid is None:
            if selection:
                self.pop(selection.uuid)
            return
        self.push(Selection(uuid=selection.uuid, items=(uuid,)) if selection else Selection(items=(uuid,)))

    def push(self, task: Primitive) -> None:
        if self._states.get(task.uuid, None) == task:
            return
        self._states[task.uuid] = task
        self._notify()

    def pop(self, uuid) -> None:
        if self._states.pop(uuid, None):
            self._notify()

    def subscribe(self,callback:Callback) -> None:
        self._callbacks.append(callback)

    def unsubscribe(self, callback: Callback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._callbacks):  # a callback may unsubscribe
            callback(self)
