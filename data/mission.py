from dataclasses import dataclass,field
from collections.abc import Callable
from types import MappingProxyType
from uuid import uuid4
from typing import Generic, TypeVar, Any
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
    assigned_robots: tuple[str,...] = field(default_factory=tuple)

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

type Callback = Callable[["Mission"],None]

class Mission:

    def __init__(self, *robot_names: str):
        self._tasks: dict[str, MissionTask] = {}
        self._callbacks: list[Callback] = []
        self.robot_names: tuple[str,...] = robot_names

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

    def to_json(self) -> dict[str, Any]:
        def subtask_to_json(subtask: Subtask):
            if isinstance(subtask, SubtaskWait):
                return {
                    "type": "wait",
                    "parameters": subtask.parameter,
                }
            if isinstance(subtask, SubtaskGimball):
                return {
                    "type": "gimbal",
                    "parameters": list(subtask.parameter),
                }
            if isinstance(subtask, SubtaskGazeboGimball):
                return {
                    "type": "gazebo_gimbal",
                    "parameters": list(subtask.parameter),
                    "continue_without_waiting": subtask.continue_without_waiting,
                    "stop_on_failure": subtask.stop_on_failure,
                    "max_retries": subtask.max_retries,
                    "retry_delay": subtask.retry_delay,
                }
            raise TypeError(f"Unsupported subtask type: {type(subtask).__name__}")

        def point_to_json(point: PointLocal | PointGlobal):
            if isinstance(point, PointLocal):
                x, y, z = point.position
            elif isinstance(point, PointGlobal):
                x, y, z = point.lat, point.lon, point.height
            else:
                raise TypeError(f"Unsupported point type: {type(point).__name__}")

            point_json = {
                "x": x,
                "y": y,
                "z": z,
                "heading": point.heading,
            }
            if point.subtasks:
                point_json["subtasks"] = [subtask_to_json(subtask) for subtask in point.subtasks]
                if len(point.subtasks) > 1:
                    point_json["parallel_execution"] = True
            return point_json

        if len(self._tasks) == 1:
            task = next(iter(self._tasks.values()))
            if isinstance(task, Coverage):
                return {
                    "type": "CoveragePlanner",
                    "uuid": task.uuid,
                    "details": {
                        "robots": list(task.assigned_robots or self.robot_names),
                        "search_area": [
                            {"x": point[0], "y": point[1]}
                            for point in task.points
                        ],
                        "height_id": 0,
                        "height": task.time_interval[1],
                        "terminal_action": 0,
                    },
                }

        robots = []
        for index, task in enumerate(self._tasks.values()):
            if not isinstance(task, Waypoints):
                continue

            robot_name = task.assigned_robot
            if robot_name is None and index < len(self.robot_names):
                robot_name = self.robot_names[index]
            if robot_name is None:
                robot_name = ""

            height_id = 0
            if task.points and isinstance(task.points[0], PointGlobal):
                height_id = task.points[0].height_id

            robots.append({
                "name": robot_name,
                "frame_id": 0,
                "height_id": height_id,
                "points": [point_to_json(point) for point in task.points],
                "terminal_action": 0,
            })

        return {
            "type": "WaypointPlanner",
            "uuid": str(uuid4()),
            "details": {
                "robots": robots,
            },
        }
