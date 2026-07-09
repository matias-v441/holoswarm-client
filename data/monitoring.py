from dataclasses import dataclass,field
from collections.abc import Callable
from types import MappingProxyType
from uuid import uuid4
from typing import Generic, TypeVar
from enum import Enum
from threading import Lock

@dataclass(frozen=True)
class BatteryState:
    voltage: float
    percentage: float
    wh_drained: float

@dataclass(frozen=True)
class GeneralRobotInfo:
    name: str
    type: str
    battery_state: BatteryState
    ready_to_start: bool
    problems_preventing_start: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class Vector3:
    x: float
    y: float
    z: float

@dataclass(frozen=True, slots=True)
class LocalPose:
    x: float
    y: float
    z: float
    heading: float

@dataclass(frozen=True, slots=True)
class GlobalPose:
    latitude: float
    longitude: float
    altitude: float
    heading: float

@dataclass(frozen=True, slots=True)
class VectorPair:
    linear: Vector3
    angular: Vector3

@dataclass(frozen=True, slots=True)
class StateEstimationInfo:
    robot_name: str
    estimation_frame: str
    above_ground_level_height: float
    current_estimator: str
    local_pose: LocalPose
    global_pose: GlobalPose
    velocity: VectorPair
    acceleration: VectorPair
    running_estimators: tuple[str, ...] = field(default_factory=tuple)
    switchable_estimators: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class ControlInfo:
    robot_name: str
    active_controller: str
    active_gains: str
    active_tracker: str
    active_constraints: str
    thrust: float
    available_controllers: tuple[str, ...] = field(default_factory=tuple)
    available_gains: tuple[str, ...] = field(default_factory=tuple)
    available_trackers: tuple[str, ...] = field(default_factory=tuple)
    available_constraints: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class CollisionAvoidanceInfo:
    robot_name: str
    collision_avoidance_enabled: bool
    avoiding_collision: bool
    other_robots_visible: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class UavInfo:
    robot_name: str
    armed: bool
    offboard: bool
    flight_state: str
    flight_duration: float
    mass_nominal: float

@dataclass(frozen=True, slots=True)
class SensorInfo:
    robot_name: str
    sensor_type: str
    details: object

@dataclass(frozen=True, slots=True)
class NodeCpuLoad:
    node_name: str
    cpu_load: float

@dataclass(frozen=True, slots=True)
class AvailableSensor:
    name: str
    status: str
    ready: bool
    rate: float
    details: object

@dataclass(frozen=True, slots=True)
class SystemHealthInfo:
    robot_name: str
    cpu_load: float
    free_ram: float
    total_ram: float
    free_hdd: float
    hw_api_rate: float
    control_manager_rate: float
    state_estimation_rate: float
    wifi_interface: str
    wifi_link_quality: float
    wifi_signal_dbm: float
    node_cpu_loads: tuple[NodeCpuLoad, ...] = field(default_factory=tuple)
    available_sensors: tuple[AvailableSensor, ...] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class RobotFeedback:
    robot_name: str # robot
    message: str
    mission_progress: float
    current_goal: int
    distance_to_goal: float
    goal_estimated_arrival_time: float
    goal_progress: float
    distance_to_finish: float
    finish_estimated_arrival_time: float

@dataclass(frozen=True)
class RobotState:
    robot_name: str
    general_robot_info: GeneralRobotInfo | None = None
    system_health_info: SystemHealthInfo | None = None
    sensor_info: SensorInfo | None  = None
    state_estimation_info: StateEstimationInfo | None = None
    control_info: ControlInfo | None = None
    collision_avoidance_info: CollisionAvoidanceInfo | None = None
    uav_info: UavInfo | None = None
    robot_feedback: RobotFeedback | None = None

    def __post_init__(self) -> None:
        if (
            (self.general_robot_info is not None and self.general_robot_info.name != self.robot_name)
            or (self.system_health_info is not None and self.system_health_info.robot_name != self.robot_name)
            or (self.sensor_info is not None and self.sensor_info.robot_name != self.robot_name)
            or (self.state_estimation_info is not None and self.state_estimation_info.robot_name != self.robot_name)
            or (self.control_info is not None and self.control_info.robot_name != self.robot_name)
            or (self.collision_avoidance_info is not None and self.collision_avoidance_info.robot_name != self.robot_name)
            or (self.uav_info is not None and self.uav_info.robot_name != self.robot_name)
            or (self.robot_feedback is not None and self.robot_feedback.robot_name != self.robot_name)
        ):
            raise ValueError("Telemetry robot_name must match member robot names")

@dataclass(frozen=True, slots=True)
class MissionRobotResult:
    robot: str
    success: bool
    message: str

@dataclass(frozen=True, slots=True)
class MissionState:
    progress: float
    mission_state: str
    message: str
    success: bool
    robots: tuple[MissionRobotResult, ...] = field(default_factory=tuple)


type Callback = Callable[["Monitoring"],None]

class Monitoring:
    def __init__(self):
        self._robot_states: dict[str, RobotState] = {}
        self._mission_state: MissionState = None
        self._callbacks: list[Callback] = []
        self._lock = Lock()

    def telemetry(self, robot_name: str):
        return self._robot_states.get(robot_name, None)
    
    def push(self, state: RobotState | MissionState) -> None:
        with self._lock:
            if isinstance(state, RobotState):
                self._robot_states[state.robot_name] = state
            elif isinstance(state, MissionState):
                print(state)
                self._mission_state = state
            else:
                raise ValueError("Unknown instance")

    def subscribe(self,callback:Callback) -> None:
        self._callbacks.append(callback)
    
    def notify(self) -> None:
        with self._lock:
            ui_monitoring = Monitoring()
            ui_monitoring._robot_states = self._robot_states.copy()
            ui_monitoring._mission_state = self._mission_state
        for callback in self._callbacks:
            callback(ui_monitoring)
