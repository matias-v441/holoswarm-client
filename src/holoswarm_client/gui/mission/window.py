import dearpygui.dearpygui as dpg
import asyncio
from holoswarm_client.data.monitoring import *
from holoswarm_client.iroc.client import IROCClient
from dataclasses import fields, is_dataclass
from enum import Enum
from asyncio import AbstractEventLoop
from queue import SimpleQueue, Empty

class MissionWindow:
    
    def __init__(self, monitoring: Monitoring, client: IROCClient, api_loop: AbstractEventLoop):
        self.monitoring = monitoring
        self.client = client
        self.api_loop = api_loop
        self.window_tag = f"mission_window"
        self.tree_tag = f"{self.window_tag}_state"
        self.none_theme_tag = f"{self.window_tag}_none_theme"
        self._last_state = None
        monitoring.subscribe(self._draw)
        self._ui_events = SimpleQueue()
        self.response_text_tag = f"{self.window_tag}_response"

    def add(self):
        self._add_none_theme()
        with dpg.window(
            label=f"Mission",
            tag=self.window_tag
        ):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Start", callback=self._start)
                dpg.add_button(label="Pause", callback=self._pause)
                dpg.add_button(label="Stop", callback=self._stop)
            dpg.add_text("", tag=self.response_text_tag)
            dpg.add_group(tag=self.tree_tag)
            for field in fields(MissionState):
                self._draw_member(field.name, None, self.tree_tag, field.name)
        self._draw(self.monitoring)

    def _on_command_done(self, future):
        try:
            res = future.result()
            print("result:", res)
            def show_result():
                dpg.set_value(self.response_text_tag, str(res))
            self._ui_events.put(show_result)
        except Exception as e:
            error = str(e)
            print("error: ", error)
            def show_result():
                dpg.set_value(self.response_text_tag, error)
            self._ui_events.put(show_result)

    def process_events(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event.__call__()

    def _start(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self.client.start_mission(), self.api_loop)
        future.add_done_callback(self._on_command_done)

    def _pause(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self.client.pause_mission(), self.api_loop)
        future.add_done_callback(self._on_command_done)

    def _stop(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self.client.stop_mission(), self.api_loop)
        future.add_done_callback(self._on_command_done)

    def _draw(self, monitoring):
        state = getattr(monitoring, "_mission_state", None)
        if state == self._last_state or not dpg.does_item_exist(self.tree_tag):
            return

        self._last_state = state

        if state is None:
            for field in fields(MissionState):
                self._draw_member(field.name, None, self.tree_tag, field.name)
            return

        for field in fields(state):
            self._draw_member(field.name, getattr(state, field.name), self.tree_tag, field.name)

    def _add_none_theme(self) -> None:
        if dpg.does_item_exist(self.none_theme_tag):
            return
        with dpg.theme(tag=self.none_theme_tag):
            with dpg.theme_component(dpg.mvTreeNode):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (130, 130, 130), category=dpg.mvThemeCat_Core)

    def _draw_member(self, name: str, value: object, parent: str, path: str) -> None:
        tag = self._tag(path)
        if not dpg.does_item_exist(tag):
            with dpg.tree_node(label=name, tag=tag, parent=parent):
                pass

        if value is None:
            dpg.configure_item(tag, label=f"{name}: None", leaf=True)
            dpg.delete_item(tag, children_only=True)
            dpg.bind_item_theme(tag, self.none_theme_tag)
            return

        if isinstance(value, Enum):
            value = value.value

        if not self._has_child_values(value):
            dpg.configure_item(tag, label=f"{name}: {value}", leaf=True)
            dpg.delete_item(tag, children_only=True)
            dpg.bind_item_theme(tag, 0)
            return

        dpg.configure_item(tag, label=name, leaf=False)
        dpg.bind_item_theme(tag, 0)
        self._draw_value(value, tag, path)

    def _draw_value(self, value: object, parent: str, path: str) -> None:
        if is_dataclass(value):
            expected_children = []
            for field in fields(value):
                child_path = f"{path}_{field.name}"
                expected_children.append(self._tag(child_path))
                self._draw_member(field.name, getattr(value, field.name), parent, child_path)
            self._delete_unexpected_children(parent, expected_children)
            return

        if isinstance(value, tuple):
            if not value:
                self._draw_text("empty", parent, path)
                return
            expected_children = []
            for index, item in enumerate(value):
                child_path = f"{path}_{index}"
                if isinstance(item, Enum):
                    item = item.value
                if not self._has_child_values(item):
                    text_tag = self._tag(f"{child_path}_value")
                    expected_children.append(text_tag)
                    self._draw_leaf_text(str(item), text_tag, parent)
                    continue
                expected_children.append(self._tag(child_path))
                self._draw_member(self._tuple_item_name(index, item), item, parent, child_path)
            self._delete_unexpected_children(parent, expected_children)
            return

        if isinstance(value, list):
            if not value:
                self._draw_text("empty", parent, path)
                return
            expected_children = []
            for index, item in enumerate(value):
                child_path = f"{path}_{index}"
                expected_children.append(self._tag(child_path))
                self._draw_member(str(index), item, parent, child_path)
            self._delete_unexpected_children(parent, expected_children)
            return

        if isinstance(value, dict):
            if not value:
                self._draw_text("empty", parent, path)
                return
            expected_children = []
            for key, item in value.items():
                child_path = f"{path}_{key}"
                expected_children.append(self._tag(child_path))
                self._draw_member(str(key), item, parent, child_path)
            self._delete_unexpected_children(parent, expected_children)
            return

    def _has_child_values(self, value: object) -> bool:
        return is_dataclass(value) or isinstance(value, (tuple, list, dict))

    def _tuple_item_name(self, index: int, value: object) -> str:
        if not is_dataclass(value):
            return str(index)
        for field in fields(value):
            field_value = getattr(value, field.name)
            if isinstance(field_value, str):
                return field_value
        return str(index)

    def _draw_text(self, value: str, parent: str, path: str) -> None:
        tag = self._tag(f"{path}_value")
        self._draw_leaf_text(value, tag, parent)
        self._delete_unexpected_children(parent, [tag])

    def _draw_leaf_text(self, value: str, tag: str, parent: str) -> None:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, value)
        else:
            dpg.add_text(value, tag=tag, parent=parent)

    def _delete_unexpected_children(self, parent: str, expected_children: list[str]) -> None:
        expected = set(expected_children)
        for child in dpg.get_item_children(parent, 1) or ():
            child_alias = dpg.get_item_alias(child)
            if (child_alias or child) not in expected:
                dpg.delete_item(child)

    def _tag(self, path: str) -> str:
        safe_path = "".join(char if char.isalnum() or char == "_" else "_" for char in path)
        return f"{self.window_tag}_{safe_path}"
