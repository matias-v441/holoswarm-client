import asyncio
import json
from dataclasses import replace
from typing import Any

from data.monitoring import *
from iroc.client import IROCClient
from asyncio import AbstractEventLoop

class TelemetryListener:
    def __init__(self, monitoring: Monitoring, client: IROCClient, api_loop: AbstractEventLoop):
        self.monitoring = monitoring
        self.client = client
        self.api_loop = api_loop
        self._future = None

    def start(self):
        if self._future is None or self._future.done():
            self._future = asyncio.run_coroutine_threadsafe(self._listen(), self.api_loop)
        return self._future

    def stop(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()

    async def _listen(self) -> None:
        async for message in self.client.telemetry():
            self._handle_message(json.loads(message))

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = (
            message.get("type")
            or message.get("message_type")
            or message.get("telemetry_type")
            or message.get("name")
        )
        payload = message.get("data") or message.get("payload") or message.get("message") or message
        if message_type is None:
            message_type = self._infer_message_type(payload)
        field_name, telemetry = self._parse(message_type, payload)
        robot_name = telemetry.name if isinstance(telemetry, GeneralRobotInfo) else telemetry.robot_name
        current = self.monitoring.telemetry(robot_name)
        if current is None:
            current = RobotState(robot_name)
        self.monitoring.push(replace(current, **{field_name: telemetry}))

    def _parse(self, message_type: str, payload: dict[str, Any]) -> tuple[str, object]:
        if message_type == "GeneralRobotInfo":
            battery = payload["battery_state"]
            return "general_robot_info", GeneralRobotInfo(
                name=payload["robot_name"],
                type=payload["robot_type"],
                battery_state=BatteryState(battery["voltage"], battery["percentage"], battery["wh_drained"]),
                ready_to_start=payload["ready_to_start"],
                problems_preventing_start=tuple(payload.get("problems_preventing_start", ())),
                errors=tuple(payload.get("errors", ())),
            )
        if message_type == "StateEstimationInfo":
            return "state_estimation_info", StateEstimationInfo(
                robot_name=payload["robot_name"],
                estimation_frame=payload["estimation_frame"],
                above_ground_level_height=payload["above_ground_level_height"],
                current_estimator=payload["current_estimator"],
                local_pose=self._local_pose(payload["local_pose"]),
                global_pose=self._global_pose(payload["global_pose"]),
                velocity=self._vector_pair(payload["velocity"]),
                acceleration=self._vector_pair(payload["acceleration"]),
                running_estimators=self._tuple(payload.get("running_estimators", ())),
                switchable_estimators=self._tuple(payload.get("switchable_estimators", ())),
            )
        if message_type == "ControlInfo":
            return "control_info", ControlInfo(
                robot_name=payload["robot_name"],
                active_controller=payload["active_controller"],
                available_controllers=tuple(payload.get("available_controllers", ())),
                active_gains=payload["active_gains"],
                available_gains=tuple(payload.get("available_gains", ())),
                active_tracker=payload["active_tracker"],
                available_trackers=tuple(payload.get("available_trackers", ())),
                active_constraints=payload["active_constraints"],
                available_constraints=tuple(payload.get("available_constraints", ())),
                thrust=payload["thrust"],
            )
        if message_type == "CollisionAvoidanceInfo":
            return "collision_avoidance_info", CollisionAvoidanceInfo(
                robot_name=payload["robot_name"],
                collision_avoidance_enabled=payload["collision_avoidance_enabled"],
                avoiding_collision=payload["avoiding_collision"],
                other_robots_visible=tuple(payload.get("other_robots_visible", ())),
            )
        if message_type == "UavInfo":
            return "uav_info", UavInfo(
                robot_name=payload["robot_name"],
                armed=payload["armed"],
                offboard=payload["offboard"],
                flight_state=payload["flight_state"],
                flight_duration=payload["flight_duration"],
                mass_nominal=payload["mass_nominal"],
            )
        if message_type == "SensorInfo":
            return "sensor_info", SensorInfo(
                robot_name=payload["robot_name"],
                sensor_type=payload["sensor_type"],
                details=payload.get("details"),
            )
        if message_type == "SystemHealthInfo":
            return "system_health_info", SystemHealthInfo(
                robot_name=payload["robot_name"],
                cpu_load=payload["cpu_load"],
                free_ram=payload["free_ram"],
                total_ram=payload["total_ram"],
                free_hdd=payload["free_hdd"],
                hw_api_rate=payload["hw_api_rate"],
                control_manager_rate=payload["control_manager_rate"],
                state_estimation_rate=payload["state_estimation_rate"],
                wifi_interface=payload["wifi_interface"],
                wifi_link_quality=payload["wifi_link_quality"],
                wifi_signal_dbm=payload["wifi_signal_dbm"],
                node_cpu_loads=tuple(NodeCpuLoad(load[0], load[1]) for load in payload.get("node_cpu_loads", ())),
                available_sensors=tuple(
                    AvailableSensor(sensor["name"], sensor["status"], sensor["ready"], sensor["rate"], sensor.get("details"))
                    for sensor in payload.get("available_sensors", ())
                ),
            )
        raise ValueError(f"Unknown telemetry message type: {message_type}")

    def _infer_message_type(self, payload: dict[str, Any]) -> str:
        if "battery_state" in payload:
            return "GeneralRobotInfo"
        if "current_estimator" in payload:
            return "StateEstimationInfo"
        if "active_controller" in payload:
            return "ControlInfo"
        if "collision_avoidance_enabled" in payload:
            return "CollisionAvoidanceInfo"
        if "armed" in payload:
            return "UavInfo"
        if "sensor_type" in payload:
            return "SensorInfo"
        if "hw_api_rate" in payload:
            return "SystemHealthInfo"
        raise ValueError("Unknown telemetry payload")

    def _tuple(self, value: Any) -> tuple:
        if len(value) == 1 and isinstance(value[0], list):
            return tuple(value[0])
        return tuple(value)

    def _local_pose(self, value: dict[str, Any]) -> LocalPose:
        return LocalPose(value["x"], value["y"], value["z"], value["heading"])

    def _global_pose(self, value: dict[str, Any]) -> GlobalPose:
        return GlobalPose(value["latitude"], value["longitude"], value["altitude"], value["heading"])

    def _vector3(self, value: dict[str, Any]) -> Vector3:
        return Vector3(value["x"], value["y"], value["z"])

    def _vector_pair(self, value: dict[str, Any]) -> VectorPair:
        return VectorPair(self._vector3(value["linear"]), self._vector3(value["angular"]))
