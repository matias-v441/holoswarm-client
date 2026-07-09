import asyncio
import json
from dataclasses import replace
from typing import Any

from data.monitoring import *
from iroc.client import IROCClient
from asyncio import AbstractEventLoop
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

class FeedbackListener:
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
        while True:
            try:
                async for message in self.client.mission_feedback():
                    print(message)
                    self._handle_message(json.loads(message))
            except (InvalidURI, InvalidHandshake, OSError, ConnectionClosed) as exc:
                print(f"WebSocket unavailable: {exc}. Reconnecting soon...")
                await asyncio.sleep(5)
            

    def _handle_message(self, message: dict[str, Any]) -> None:
        payload = message.get("data") or message.get("payload") or message.get("message") or message
        robots = payload.get("robots", ())
        self.monitoring.push(
            MissionState(
                progress=payload["progress"],
                mission_state=payload["mission_state"],
                message=payload["message"],
                success=bool(payload.get("success", False)),
                robots=tuple(
                    MissionRobotResult(robot["robot"], robot["success"], robot["message"])
                    for robot in robots
                    if "success" in robot
                ),
            )
        )
        for robot in robots:
            if "mission_progress" not in robot:
                continue
            feedback = RobotFeedback(
                robot_name=robot["robot"],
                message=robot["message"],
                mission_progress=robot["mission_progress"],
                current_goal=robot["current_goal"],
                distance_to_goal=robot["distance_to_goal"],
                goal_estimated_arrival_time=robot["goal_estimated_arrival_time"],
                goal_progress=robot["goal_progress"],
                distance_to_finish=robot["distance_to_finish"],
                finish_estimated_arrival_time=robot["finish_estimated_arrival_time"],
            )
            current = self.monitoring.telemetry(feedback.robot_name)
            if current is None:
                current = RobotState(feedback.robot_name)
            self.monitoring.push(replace(current, robot_feedback=feedback))
