import asyncio
import time
from asyncio import AbstractEventLoop
from collections.abc import Sequence
from math import isfinite
from queue import Empty, SimpleQueue

import dearpygui.dearpygui as dpg

from holoswarm_client.data.mission import Mission
from holoswarm_client.data.monitoring import Monitoring, RobotState
from holoswarm_client.gui.robots.telemetry_tree import TelemetryTree
from holoswarm_client.iroc.client import IROCClient

GREEN = (110, 200, 110)
YELLOW = (230, 190, 90)
RED = (230, 90, 90)
GREY = (150, 150, 150)
TEXT = (220, 220, 220)
NONE = "–"

AGE_PERIOD = 0.25   # [s] refresh of the "Updated" column
TREE_PERIOD = 0.5   # [s] refresh of open telemetry trees
COMMANDS = ("takeoff", "hover", "land", "home")


def age_text(age: float | None) -> tuple[str, tuple[int, int, int]]:
    """How long ago the position estimate changed, and how worrying that is."""
    if age is None:
        return NONE, GREY
    age = max(0.0, age)
    text = f"{age:.1f} s" if age < 10 else f"{age:.0f} s"
    return text, GREEN if age < 1 else YELLOW if age < 5 else RED


def battery_text(state: RobotState | None) -> tuple[str, tuple[int, int, int]]:
    info = state.general_robot_info if state is not None else None
    if info is None:
        return NONE, GREY
    battery = info.battery_state
    if not isfinite(battery.percentage) or battery.percentage == 0.0:
        return f"{battery.voltage:.1f} V", TEXT  # not reported (the simulator sends 0)
    percent = battery.percentage * 100 if battery.percentage <= 1 else battery.percentage
    return f"{percent:.0f} % {battery.voltage:.1f} V", RED if percent < 20 else TEXT


class RobotsWindow:
    """The robots, as discovered from the telemetry stream: one compact row each (flight state, AMSL altitude,
    battery, time since the position estimate last changed, commands), the full telemetry in collapsed trees."""

    def __init__(self, monitoring: Monitoring, client: IROCClient, api_loop: AbstractEventLoop, mission: Mission,
                 expected: Sequence[str] = ()) -> None:
        self.client = client
        self.api_loop = api_loop
        self.mission = mission
        self.expected = tuple(expected)  # listed before their telemetry arrives
        self.window_tag = "robots_window"
        self.table_tag = f"{self.window_tag}_table"
        self.trees_tag = f"{self.window_tag}_trees"
        self.message_tag = f"{self.window_tag}_message"
        self._robots: tuple[str, ...] = ()
        self._monitoring: Monitoring = monitoring
        self._trees: dict[str, TelemetryTree] = {}
        self._next_age = 0.0
        self._next_tree = 0.0
        self._ui_events: SimpleQueue = SimpleQueue()
        monitoring.subscribe(self._monitoring_changed)

    def add(self) -> None:
        with dpg.window(label="Robots", tag=self.window_tag):
            dpg.add_text("", tag=self.message_tag, wrap=0)
            with dpg.table(tag=self.table_tag, header_row=True, resizable=True, row_background=True,
                           borders_innerH=True, borders_outerH=True, policy=dpg.mvTable_SizingStretchProp):
                for label in ("Robot", "State", "AMSL", "Battery", "Updated"):
                    dpg.add_table_column(label=label)
                dpg.add_table_column(label="", width_fixed=True)
            with dpg.collapsing_header(label="Telemetry", default_open=True):
                dpg.add_group(tag=self.trees_tag)
        self._sync_robots()

    def process_events(self) -> None:
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event()
        now = time.time()
        if now >= self._next_age:
            self._next_age = now + AGE_PERIOD
            self._draw_ages(now)
        if now >= self._next_tree:
            self._next_tree = now + TREE_PERIOD
            for name, tree in self._trees.items():
                if dpg.does_item_exist(self._t(name, "tree")) and dpg.get_value(self._t(name, "tree")):
                    tree.draw(self._monitoring.telemetry(name))

    # | ----------------------- drawing ----------------------- |

    def _t(self, robot: str, name: str) -> str:
        safe = "".join(char if char.isalnum() or char == "_" else "_" for char in robot)
        return f"{self.window_tag}_{safe}_{name}"

    def _monitoring_changed(self, monitoring: Monitoring) -> None:
        self._monitoring = monitoring
        self._sync_robots()
        self._draw_values()

    def _sync_robots(self) -> None:
        """A row and a telemetry tree per robot; rebuilt only when a robot is discovered."""
        robots = tuple(sorted(set(self.expected) | set(self._monitoring.robot_names())))
        if robots == self._robots or not dpg.does_item_exist(self.table_tag):
            return
        self._robots = robots
        for row in dpg.get_item_children(self.table_tag, 1) or ():
            dpg.delete_item(row)
        dpg.delete_item(self.trees_tag, children_only=True)
        self._trees = {}
        for name in robots:
            with dpg.table_row(parent=self.table_tag):
                dpg.add_text(name)
                dpg.add_text(NONE, tag=self._t(name, "state"))
                with dpg.tooltip(self._t(name, "state")):
                    dpg.add_text("", tag=self._t(name, "details"))
                dpg.add_text(NONE, tag=self._t(name, "amsl"))
                dpg.add_text(NONE, tag=self._t(name, "battery"))
                dpg.add_text(NONE, tag=self._t(name, "age"))
                with dpg.group(horizontal=True):
                    for command in COMMANDS:
                        dpg.add_button(label=command.capitalize(), small=True, user_data=(name, command),
                                       callback=lambda s, a, user_data: self._command(*user_data))
            with dpg.tree_node(label=f"{name} telemetry", tag=self._t(name, "tree"), parent=self.trees_tag,
                               default_open=False):
                pass
            self._trees[name] = TelemetryTree(self._t(name, "telemetry"), self._t(name, "tree"))
        self.mission.set_robot_names(robots)
        self._draw_values()
        self._draw_ages(time.time())

    def _draw_values(self) -> None:
        for name in self._robots:
            if not dpg.does_item_exist(self._t(name, "state")):
                continue
            state = self._monitoring.telemetry(name)
            uav = state.uav_info if state is not None else None
            general = state.general_robot_info if state is not None else None
            estimate = state.state_estimation_info if state is not None else None

            errors = general.errors if general is not None else ()
            dpg.set_value(self._t(name, "state"), uav.flight_state if uav is not None else
                          ("no telemetry yet" if state is None else NONE))
            dpg.configure_item(self._t(name, "state"), color=RED if errors else TEXT if uav is not None else GREY)
            details = []
            if uav is not None:
                details.append(f"armed: {uav.armed}, offboard: {uav.offboard}, flying {uav.flight_duration:.0f} s")
            if general is not None:
                details.append("ready to start" if general.ready_to_start else "not ready to start")
                details += [f"problem: {p}" for p in general.problems_preventing_start]
                details += [f"error: {e}" for e in errors]
            dpg.set_value(self._t(name, "details"), "\n".join(details) or "no telemetry yet")

            dpg.set_value(self._t(name, "amsl"), f"{estimate.global_pose.altitude:.1f} m" if estimate is not None else NONE)
            text, color = battery_text(state)
            dpg.set_value(self._t(name, "battery"), text)
            dpg.configure_item(self._t(name, "battery"), color=color)

    def _draw_ages(self, now: float) -> None:
        for name in self._robots:
            if not dpg.does_item_exist(self._t(name, "age")):
                continue
            changed = self._monitoring.position_changed_at(name)
            text, color = age_text(None if changed is None else now - changed)
            dpg.set_value(self._t(name, "age"), text)
            dpg.configure_item(self._t(name, "age"), color=color)

    # | ----------------------- commands ----------------------- |

    def _command(self, robot: str, command: str) -> None:
        call = getattr(self.client, command)
        future = asyncio.run_coroutine_threadsafe(call(robot), self.api_loop)

        def done(future) -> None:
            try:
                response = future.result()
                text = f"{robot} {command}: {response.status_code} {response.text[:200]}"
            except Exception as e:  # unreachable bridge, timeout
                text = f"{robot} {command} failed: {e}"
            print(text)
            self._ui_events.put(lambda: dpg.set_value(self.message_tag, text))

        future.add_done_callback(done)
