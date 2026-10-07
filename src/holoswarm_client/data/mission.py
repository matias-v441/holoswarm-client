from dataclasses import dataclass,field
from collections.abc import Callable
from types import MappingProxyType
from uuid import uuid4
from typing import Generic, TypeVar, Any, Sequence
from enum import Enum
from datetime import datetime
import json

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
    height_id: str | int
    height: float
    target_subtask_count: int = 1  # sub-areas the planner splits the area into (each one robot's job)
    uuid: str = field(default_factory=lambda: str(uuid4()))
    assigned_robots: tuple[str,...] = field(default_factory=tuple)
    mission_id: str | None = None  # the queue mission it belongs to; None while it is being drawn

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
    assigned_robot: str | None = None
    mission_id: str | None = None  # the queue mission it belongs to; None while it is being drawn

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


def write_mission_file(jrepr: dict[str, Any], path: str | None = None) -> str:
    """Write a mission ({"type", "uuid", "details"}) to a file in the format POST /mission and the queues accept."""
    if path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = f"mission_{timestamp}.json"
    with open(path, "w", encoding="utf-8") as file:
        json.dump(jrepr, file, indent=2)
    return path

type Callback = Callable[["Mission"],None]

class Mission:
    """The tasks (paths and areas) of the selected queue, each tagged with the mission it belongs to."""

    def __init__(self, *robot_names: str):
        self._tasks: dict[str, MissionTask] = {}
        self._callbacks: list[Callback] = []
        self.robot_names: tuple[str,...] = robot_names
        self.robot_homes: dict[str, tuple[float,float]] = {}
        self.read_only = False  # the queue cannot be changed: edits from the map and the Task window are ignored

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
    
    @property
    def areas(self):
        return MappingProxyType(
            {
                uuid: task
                for uuid, task in self._tasks.items()
                if isinstance(task, Coverage)
            }
        )

    def set_robot_names(self, robot_names: Sequence[str]) -> None:
        """The robots missions can use (discovered from telemetry); views offering robots redraw."""
        robot_names = tuple(robot_names)
        if robot_names != self.robot_names:
            self.robot_names = robot_names
            self._notify()

    def tasks_of(self, mission_id: str | None) -> tuple[MissionTask, ...]:
        return tuple(task for task in self._tasks.values() if task.mission_id == mission_id)

    def push_task(self, task: MissionTask) -> None:
        if self.read_only or self._tasks.get(task.uuid, None) == task:
            return
        self._tasks[task.uuid] = task
        self._notify()

    def pop_task(self, uuid: str) -> None:
        if not self.read_only and self._tasks.pop(uuid, None):
            self._notify()

    def set_tasks(self, tasks: "Sequence[MissionTask]") -> None:
        """Replace all tasks at once (also when read-only)."""
        tasks = {task.uuid: task for task in tasks}
        if tasks == self._tasks:
            return
        self._tasks = tasks
        self._notify()

    def subscribe(self,callback:Callback) -> None:
        self._callbacks.append(callback)
    
    def unsubscribe(self, callback: Callback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._callbacks):  # a callback may unsubscribe
            callback(self)

    def to_json(self, mission_id: str | None = None) -> dict[str, Any]:
        """Planner payload of the whole collection. Raises CodecError for content that does not fit one mission."""
        from holoswarm_client.iroc.mission_codec import encode_draft

        payload = encode_draft(tuple(self._tasks.values()), self.robot_names, self.robot_homes)
        if mission_id is None:
            areas = tuple(self.areas.values())
            mission_id = areas[0].uuid if areas else str(uuid4())
        return {"type": payload["type"], "uuid": mission_id, "details": payload["details"]}

    def export_json(self, path: str | None = None) -> str:
        """Write the collection to a mission file (the format POST /mission and the queues accept)."""
        return write_mission_file(self.to_json(), path)

    def from_json(self, jrepr: dict[str, Any]) -> None:
        from holoswarm_client.iroc.mission_codec import decode_mission

        self._tasks = {task.uuid: task for task in decode_mission(jrepr, self.robot_homes)}
        self._notify()
