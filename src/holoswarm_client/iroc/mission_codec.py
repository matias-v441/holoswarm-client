"""Planner payload import/export for mission drafts.

Pure functions: no files are written and no identifiers are generated here. Callers decide the
mission id (export, submission) and keep it stable across retries.
"""

from dataclasses import replace
from typing import Any, Mapping, Sequence
from uuid import uuid4

from holoswarm_client.data.mission import (
    Coverage,
    MissionTask,
    PointGlobal,
    PointLocal,
    Subtask,
    SubtaskGazeboGimball,
    SubtaskGimball,
    SubtaskWait,
    Waypoints,
)

WAYPOINT_PLANNER = "WaypointPlanner"
COVERAGE_PLANNER = "CoveragePlanner"


class CodecError(ValueError):
    """The draft cannot be expressed as one planner payload. The message is meant for the operator."""


# | ----------------------- export ----------------------- |


def subtask_to_json(subtask: Subtask) -> dict[str, Any]:
    if isinstance(subtask, SubtaskWait):
        return {"type": "wait", "parameters": subtask.parameter}
    if isinstance(subtask, SubtaskGimball):
        return {"type": "gimbal", "parameters": list(subtask.parameter)}
    if isinstance(subtask, SubtaskGazeboGimball):
        return {
            "type": "gazebo_gimbal",
            "parameters": list(subtask.parameter),
            "continue_without_waiting": subtask.continue_without_waiting,
            "stop_on_failure": subtask.stop_on_failure,
            "max_retries": subtask.max_retries,
            "retry_delay": subtask.retry_delay,
        }
    raise CodecError(f"Unsupported subtask type: {type(subtask).__name__}")


def point_to_json(point: PointLocal | PointGlobal, origin: tuple[float, float] | None) -> dict[str, Any]:
    if isinstance(point, PointLocal):
        if origin is None:
            raise CodecError("Local points need the robot's home position")
        x, y, z = point.position
        x -= origin[0]
        y -= origin[1]
    elif isinstance(point, PointGlobal):
        x, y, z = point.lat, point.lon, point.height
    else:
        raise CodecError(f"Unsupported point type: {type(point).__name__}")

    point_json: dict[str, Any] = {"x": x, "y": y, "z": z, "heading": point.heading}
    if point.subtasks:
        point_json["subtasks"] = [subtask_to_json(subtask) for subtask in point.subtasks]
        if len(point.subtasks) > 1:
            point_json["parallel_execution"] = True
    return point_json


def encode_coverage(task: Coverage, robot_names: Sequence[str]) -> dict[str, Any]:
    robots = list(task.assigned_robots or robot_names)
    if not robots:
        raise CodecError("The coverage area has no robots assigned")
    if len(task.points) < 3:
        raise CodecError("The coverage area needs at least 3 points")
    return {
        "type": COVERAGE_PLANNER,
        "details": {
            "robots": robots,
            "search_area": [{"x": point[0], "y": point[1]} for point in task.points],
            "height_id": task.height_id,
            "height": task.height,
            "terminal_action": 0,
        },
    }


def encode_waypoints(
    tasks: Sequence[Waypoints],
    robot_names: Sequence[str],
    robot_homes: Mapping[str, tuple[float, float]],
) -> dict[str, Any]:
    """One coordinated waypoint mission: one route per robot."""
    robots = []
    used: set[str] = set()
    for index, task in enumerate(tasks):
        if not task.points:
            raise CodecError(f"Path {task.uuid} has no points")

        robot_name = task.assigned_robot
        if not robot_name and index < len(robot_names):
            robot_name = robot_names[index]
        if not robot_name:
            raise CodecError(f"Path {task.uuid} has no robot assigned")
        if robot_name in used:
            raise CodecError(f"Robot {robot_name} has more than one path; one mission takes one path per robot")
        used.add(robot_name)

        is_global = isinstance(task.points[0], PointGlobal)
        origin = None if is_global else robot_homes.get(robot_name)
        if not is_global and origin is None:
            raise CodecError(f"Home position of {robot_name} is unknown; local points of path {task.uuid} cannot be converted")

        robots.append({
            "name": robot_name,
            "frame_id": 1 if is_global else 0,
            "height_id": task.points[0].height_id if is_global else 0,
            "points": [point_to_json(point, origin) for point in task.points],
            "terminal_action": 0,
        })

    return {"type": WAYPOINT_PLANNER, "details": {"robots": robots}}


def path_robots(tasks: Sequence[Waypoints], robot_names: Sequence[str]) -> list[str | None]:
    """The robot of each path, as encode_waypoints() assigns them."""
    return [task.assigned_robot or (robot_names[index] if index < len(robot_names) else None)
            for index, task in enumerate(tasks)]


def place_task(tasks: Sequence[MissionTask], task: MissionTask, robot_names: Sequence[str]) -> MissionTask | None:
    """The new task as it can join a mission with these tasks, or None when it cannot.

    A mission is one coverage area, or paths of different robots: a path joining paths gets a robot that has none
    yet. Whether the geometry is complete (e.g. an area with 3 points) is not checked: it is still being drawn.
    """
    if not tasks:
        return task
    if isinstance(task, Coverage) or any(isinstance(t, Coverage) for t in tasks):
        return None
    used = set(path_robots(tasks, robot_names))
    if task.assigned_robot:
        return task if task.assigned_robot not in used else None
    free = next((robot for robot in robot_names if robot not in used), None)
    return replace(task, assigned_robot=free) if free else None


def encode_draft(
    tasks: Sequence[MissionTask],
    robot_names: Sequence[str],
    robot_homes: Mapping[str, tuple[float, float]],
) -> dict[str, Any]:
    """The whole task collection as one mission: either one coverage area or a set of waypoint routes.

    Content that does not fit one planner payload is rejected rather than partially exported.
    """
    areas = [task for task in tasks if isinstance(task, Coverage)]
    paths = [task for task in tasks if isinstance(task, Waypoints)]

    if not areas and not paths:
        raise CodecError("The mission is empty")
    if areas and paths:
        raise CodecError("Paths and coverage areas cannot be submitted as one mission yet; remove one of them")
    if len(areas) > 1:
        raise CodecError("Only one coverage area can be submitted as one mission yet; remove the others")
    if areas:
        return encode_coverage(areas[0], robot_names)
    return encode_waypoints(paths, robot_names, robot_homes)


def payload_robots(payload: Mapping[str, Any]) -> list[str]:
    """The robots a planner payload names, in order (none for other planners)."""
    details = payload.get("details") or {}
    if payload.get("type") == COVERAGE_PLANNER:
        return [str(name) for name in details.get("robots", ())]
    if payload.get("type") == WAYPOINT_PLANNER:
        return [str(route.get("name")) for route in details.get("robots", ()) if route.get("name")]
    return []


def rename_robots(payload: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    """A copy of the payload with its robots renamed (old -> new); everything else stays as it was."""
    details = dict(payload.get("details") or {})
    if payload.get("type") == COVERAGE_PLANNER:
        details["robots"] = [names.get(name, name) for name in details.get("robots", ())]
    elif payload.get("type") == WAYPOINT_PLANNER:
        details["robots"] = [{**route, "name": names.get(route.get("name"), route.get("name"))}
                             for route in details.get("robots", ())]
    return {**payload, "details": details}


def describe(payload: Mapping[str, Any]) -> str:
    """Short operator-facing summary of a planner payload."""
    details = payload.get("details", {})
    if payload.get("type") == COVERAGE_PLANNER:
        return f"Coverage, {len(details.get('search_area', ()))} vertices, robots: {', '.join(details.get('robots', ()))}"
    if payload.get("type") == WAYPOINT_PLANNER:
        routes = details.get("robots", ())
        return "Waypoints: " + ", ".join(f"{r.get('name')} ({len(r.get('points', ()))} pts)" for r in routes)
    return str(payload.get("type"))


# | ----------------------- import ----------------------- |


def subtask_from_json(value: Mapping[str, Any]) -> Subtask:
    subtask_type = value["type"]
    parameters = value["parameters"]
    if subtask_type == "wait":
        return SubtaskWait(parameters)
    if subtask_type == "gimbal":
        return SubtaskGimball(tuple(parameters))
    if subtask_type == "gazebo_gimbal":
        return SubtaskGazeboGimball(
            tuple(parameters),
            value.get("continue_without_waiting", False),
            value.get("stop_on_failure", False),
            value.get("max_retries", 1),
            value.get("retry_delay", 0.),
        )
    raise CodecError(f"Unsupported subtask type: {subtask_type}")


def decode_mission(jrepr: Mapping[str, Any], robot_homes: Mapping[str, tuple[float, float]]) -> list[MissionTask]:
    """Draft tasks from a planner payload ({"type", "uuid"|"id"?, "details"})."""
    mission_type = jrepr["type"]
    details = jrepr["details"]
    mission_id = jrepr.get("uuid") or jrepr.get("id")

    if mission_type == COVERAGE_PLANNER:
        return [Coverage(
            points=tuple((point["x"], point["y"]) for point in details["search_area"]),
            time_interval=(0., 1.),
            height_id=details["height_id"],
            height=details["height"],
            uuid=mission_id or str(uuid4()),
            assigned_robots=tuple(details.get("robots", ())),
        )]

    if mission_type == WAYPOINT_PLANNER:
        robots = details["robots"]
        tasks: list[MissionTask] = []
        for robot in robots:
            robot_name = robot["name"]
            height_id = robot["height_id"]
            is_global = robot.get("frame_id", 0) == 1
            origin_x, origin_y = robot_homes.get(robot_name, (0., 0.))
            points = []
            for value in robot["points"]:
                subtasks = tuple(subtask_from_json(subtask) for subtask in value.get("subtasks", ()))
                if is_global:
                    points.append(PointGlobal(value["x"], value["y"], height_id, value["z"], value.get("heading", 0.), subtasks))
                else:
                    points.append(PointLocal(
                        (value["x"] + origin_x, value["y"] + origin_y, value["z"]),
                        value.get("heading", 0.), subtasks,
                    ))
            tasks.append(Waypoints(
                points=tuple(points),
                time_interval=(0., 1.),
                uuid=mission_id if (mission_id and len(robots) == 1) else str(uuid4()),
                assigned_robot=robot_name,
            ))
        return tasks

    raise CodecError(f"Unsupported mission type: {mission_type}")
