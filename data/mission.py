from dataclasses import dataclass,field
from collections.abc import Callable
from types import MappingProxyType
from uuid import uuid4
from typing import Generic, TypeVar
from enum import Enum

@dataclass(frozen=True)
class SubtaskWait:
    parameter: float

@dataclass(frozen=True)
class SubtaskGimball:
    parameter: tuple[float,float,float]

@dataclass(frozen=True)
class SubtaskGazeboGimball:
    parameter: tuple[float,float,float]
    continue_without_waiting: bool = False
    stop_on_failure:bool = False
    max_retries:int = 1
    retry_delay:float = 0.

type Subtask = SubtaskWait | SubtaskGimball | SubtaskGazeboGimball

@dataclass(frozen=True)
class PointLocal:
    position: tuple[float,float,float]
    heading: float = 0.0
    subtasks: tuple[Subtask, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class PointGlobal:
    lat: float
    lon: float
    height_id: str | int
    height: float
    heading: float = 0.0
    subtasks: tuple[Subtask, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class Coverage:
    points: tuple[float,float] 
    time_interval: tuple[float,float]
    uuid: str = field(default_factory=lambda: str(uuid4()))

P = TypeVar("P", PointLocal, PointGlobal)

class FrameID:
    DEFAULT=""
    LOCAL="local"
    GLOBAL="global"

@dataclass(frozen=True)
class Waypoints(Generic[P]):
    points: tuple[P, ...]
    time_interval: tuple[float,float]
    uuid: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.points and not all(type(p) is type(self.points[0]) for p in self.points):
            raise TypeError("All points must be have the same coordinate frame")

    @property 
    def frame(self) -> FrameID:
        point = self.points[0]
        if isinstance(point, PointLocal):
            return FrameID.LOCAL
        if isinstance(point, PointGlobal):
            return FrameID.GLOBAL
        return FrameID.DEFAULT

type MissionTask = Waypoints | Coverage

@dataclass
class Mission:

    _tasks: dict[str, MissionTask] = field(default_factory=dict)

    @property
    def tasks(self):
        return MappingProxyType(self._tasks)

    @property
    def waypoints(self):
        return MappingProxyType(
            {
                uuid: task
                for uuid, task in self._tasks.items()
                if isinstance(task, Waypoints)
            }
        )

    type Callback = Callable[["Mission"],None]

    _callbacks: list[Callback] = field(
        default_factory=list,
        init=False,
        repr=False,
        compare=False,
    )

    def push_task(self, task: MissionTask) -> None:
        if self._tasks.get(task.uuid, None) == task:
            return
        self._tasks[task.uuid] = task
        self._notify()

    def pop_task(self, uuid: str) -> None:
        if self._tasks.pop(uuid, None):
            self._notify()

    def subscribe(self,callback:Callback) -> None:
        self._callbacks.append(callback)
    
    def _notify(self) -> None:
        for callback in self._callbacks:
            callback(self)
