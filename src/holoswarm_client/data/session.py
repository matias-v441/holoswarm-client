from dataclasses import dataclass,field
from collections.abc import Callable
from types import MappingProxyType
from uuid import uuid4
from enum import Enum

@dataclass(frozen=True)
class Selection:
    uuid:str = field(default_factory=lambda: str(uuid4()))
    items: tuple[str] = field(default_factory=tuple)

@dataclass(frozen=True)
class Settings:
    locations: dict[str, list[float]]
    current_location: str
    use_local_pose: bool = False
    uuid:str = field(default_factory=lambda: str(uuid4()))

type Callback = Callable[["Session"],None]

type Primitive = Selection | Settings

class Session:

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
    
    def _notify(self) -> None:
        for callback in self._callbacks:
            callback(self)
