import dearpygui.dearpygui as dpg
import time

from holoswarm_client.data.execution import ExecutionStore, MissionState, QueueExecution, QueueState, SyncState
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.services.queues import CREATING, QueueService

STATE_COLORS = {
    MissionState.SUCCEEDED: (110, 200, 110),
    MissionState.FAILED: (230, 90, 90),
    MissionState.CANCELLED: (170, 170, 170),
    MissionState.EXECUTING: (110, 170, 240),
    MissionState.DISPATCHED: (110, 170, 240),
    MissionState.STAGED: (230, 190, 90),
}
QUEUE_COLORS = {
    QueueState.READY: (230, 190, 90),
    QueueState.RUNNING: (110, 170, 240),
    QueueState.FINISHED: (110, 200, 110),
    QueueState.REJECTED: (230, 90, 90),
    QueueState.INTERRUPTED: (230, 90, 90),
    QueueState.CANCELLED: (170, 170, 170),
}
SYNC_COLORS = {SyncState.LIVE: (110, 200, 110), SyncState.CONNECTING: (230, 190, 90), SyncState.STALE: (230, 90, 90)}
GREY = (150, 150, 150)
TEXT = (220, 220, 220)

DEFAULT_SCHEDULERS = ["batch", "fleet"]
ASSIGNMENT_POLICIES = ["round_robin", "nearest", "fifo"]


class MissionWindow:
    """The next queue being put together, the queues kept by the bridge, and the missions of the selected one."""

    def __init__(self, service: QueueService, executions: ExecutionStore, draft: QueueDraft) -> None:
        self.service = service
        self.executions = executions
        self.draft = draft
        self.window_tag = "mission_window"
        self.selected_queue: str | None = None
        self._last_sync: SyncState | None = None
        self._schedulers: list[str] = []
        executions.subscribe(lambda _: self._draw())
        draft.subscribe(lambda _: self._draw_draft())
        service.on_message(self._show_message)

    def _t(self, name: str) -> str:
        return f"{self.window_tag}_{name}"

    def add(self) -> None:
        # Disabled buttons look like enabled ones in the default theme.
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvButton, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (110, 110, 110))
                for color in (dpg.mvThemeCol_Button, dpg.mvThemeCol_ButtonHovered, dpg.mvThemeCol_ButtonActive):
                    dpg.add_theme_color(color, (45, 45, 48))

        with dpg.window(label="Mission", tag=self.window_tag):
            dpg.add_text("", tag=self._t("sync"))
            dpg.add_text("", tag=self._t("message"), wrap=0)

            with dpg.collapsing_header(label="New queue", default_open=True):
                dpg.add_text("Add missions with 'Add to queue' in the Path window. They run in this order.",
                             color=GREY, wrap=0)
                with dpg.table(tag=self._t("draft"), header_row=True, resizable=True, row_background=True,
                               borders_innerH=True, borders_outerH=True, policy=dpg.mvTable_SizingStretchProp):
                    dpg.add_table_column(label="#", width_fixed=True, init_width_or_weight=20)
                    for label in ("Name", "Type", "Content"):
                        dpg.add_table_column(label=label)
                    dpg.add_table_column(label="", width_fixed=True, init_width_or_weight=80)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(label="Queue name", tag=self._t("queue_name"), width=200)
                    dpg.add_combo(DEFAULT_SCHEDULERS, label="Scheduler", tag=self._t("scheduler"), default_value="batch",
                                  width=120, callback=self._update_scheduler_options)
                with dpg.group(tag=self._t("batch_options")):
                    dpg.add_text("Runs the missions one after another, each on all of its robots.", color=GREY, wrap=0)
                    dpg.add_checkbox(label="Cancel the remaining missions after a failure", tag=self._t("abort_on_failure"))
                with dpg.group(tag=self._t("fleet_options"), show=False):
                    dpg.add_text("Splits missions into tasks for any capable idle robot. Waypoint routes stay with their robot.",
                                 color=GREY, wrap=0)
                    dpg.add_combo(ASSIGNMENT_POLICIES, label="Assignment", tag=self._t("assignment_policy"),
                                  default_value=ASSIGNMENT_POLICIES[0], width=160)
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Create queue", tag=self._t("create"), callback=self._create)
                    with dpg.tooltip(self._t("create")):
                        dpg.add_text("Store the queue on the server with all of its missions.\n"
                                     "Nothing is sent to the robots until it is submitted.")
                    dpg.add_button(label="Clear", tag=self._t("clear"), callback=lambda: self.draft.clear())

            dpg.add_separator()
            dpg.add_text("Queues")
            with dpg.group(horizontal=True):
                self._button("submit", "Submit", self._submit,
                             "Hand the queue to the fleet manager. It plans the first step and uploads it to the robots;\n"
                             "nothing moves until Start. Only one queue can be submitted at a time.\n"
                             "A finished, cancelled or interrupted queue runs again from scratch.")
                self._button("start", "Start", lambda: self._control("start"),
                             "Start the uploaded goals; the rest of the queue follows automatically.")
                self._button("pause", "Pause", lambda: self._control("pause"), "Pause the robots of the running queue.")
                self._button("resume", "Resume", lambda: self._control("resume"), "Resume the paused queue.")
                self._button("cancel", "Cancel", self._confirm_cancel,
                             "Abort running missions, unload uploaded ones and drop the rest.")
                self._button("delete", "Delete", self._confirm_delete, "Remove the queue from the server.")
                dpg.add_button(label="Refresh", callback=lambda: self.service.refresh())

            with dpg.table(tag=self._t("queues"), header_row=True, resizable=True, row_background=True,
                           borders_innerH=True, borders_outerH=True, policy=dpg.mvTable_SizingStretchProp):
                for label in ("Queue", "Scheduler", "State", "Missions", "Message"):
                    dpg.add_table_column(label=label)

            dpg.add_text("Missions", tag=self._t("missions_title"))
            with dpg.table(tag=self._t("missions"), header_row=True, resizable=True, row_background=True,
                           borders_innerH=True, borders_outerH=True, policy=dpg.mvTable_SizingStretchProp):
                for label in ("Mission", "Type", "State", "Robots", "Progress", "Message"):
                    dpg.add_table_column(label=label)

        dpg.bind_item_theme(self.window_tag, theme)
        self._draw()
        self._draw_draft()

    def process_events(self) -> None:
        # The age of the view changes every frame.
        self._draw_sync()

    def _button(self, name: str, label: str, callback, tooltip: str) -> None:
        dpg.add_button(label=label, tag=self._t(name), callback=callback)
        with dpg.tooltip(self._t(name)):
            dpg.add_text(tooltip)

    # | ----------------------- drawing ----------------------- |

    def _draw_sync(self) -> None:
        if not dpg.does_item_exist(self._t("sync")):
            return
        store = self.executions
        session = store.session_id[:8] if store.session_id else "-"
        age = f", updated {time.time() - store.updated_at:.0f} s ago" if store.updated_at else ""
        text = f"Server {store.sync.value} (session {session}{age})"
        if store.sync != SyncState.LIVE and store.sync_message:
            text += f": {store.sync_message}"
        if store.sync != self._last_sync:
            if store.sync == SyncState.LIVE and self._last_sync is not None:
                self._show_message("", True)  # connection errors are resolved
            self._last_sync = store.sync
        dpg.set_value(self._t("sync"), text)
        dpg.configure_item(self._t("sync"), color=SYNC_COLORS[store.sync])

    def _draw_draft(self) -> None:
        if not dpg.does_item_exist(self._t("draft")):
            return
        self._clear_rows(self._t("draft"))
        missions = self.draft.missions
        for index, mission in enumerate(missions):
            with dpg.table_row(parent=self._t("draft")):
                dpg.add_text(str(index + 1))
                dpg.add_input_text(default_value=mission.name, width=-1, user_data=mission.mission_id,
                                   callback=lambda s, value, mission_id: self.draft.rename(mission_id, value))
                dpg.add_text(mission.planner_type)
                dpg.add_text(mission.summary, wrap=0)
                with dpg.group(horizontal=True):
                    dpg.add_button(arrow=True, direction=dpg.mvDir_Up, enabled=index > 0, user_data=mission.mission_id,
                                   callback=lambda s, a, mission_id: self.draft.move(mission_id, -1))
                    dpg.add_button(arrow=True, direction=dpg.mvDir_Down, enabled=index < len(missions) - 1,
                                   user_data=mission.mission_id, callback=lambda s, a, mission_id: self.draft.move(mission_id, 1))
                    dpg.add_button(label="x", user_data=mission.mission_id,
                                   callback=lambda s, a, mission_id: self.draft.remove(mission_id))
        self._update_draft_controls()

    def _draw(self) -> None:
        if not dpg.does_item_exist(self._t("queues")):
            return
        self._update_schedulers()

        queues = list(self.executions.queues.values())
        if self.selected_queue not in self.executions.queues:
            in_flight = self.executions.in_flight()
            self.selected_queue = (in_flight or queues[-1]).queue_id if queues else None

        self._clear_rows(self._t("queues"))
        for queue in queues:
            with dpg.table_row(parent=self._t("queues")):
                dpg.add_selectable(label=queue.display_name, span_columns=True, default_value=queue.queue_id == self.selected_queue,
                                   callback=self._select_queue, user_data=queue.queue_id)
                if queue.name:
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(queue.queue_id)
                dpg.add_text(queue.scheduler)
                state = queue.state.value + (" (paused)" if queue.paused else "")
                if queue.queue_id in self.service.pending:
                    state += " ..."
                dpg.add_text(state, color=QUEUE_COLORS.get(queue.state, TEXT))
                dpg.add_text(f"{queue.done}/{len(queue.missions)}")
                dpg.add_text(queue.message, wrap=0)

        self._draw_missions()
        self._update_controls()
        self._update_draft_controls()
        self._draw_sync()

    def _draw_missions(self) -> None:
        self._clear_rows(self._t("missions"))
        queue = self._selected()
        dpg.set_value(self._t("missions_title"), f"Missions of {queue.display_name}" if queue else "Missions")
        if queue is None:
            return
        for mission in queue.missions.values():
            with dpg.table_row(parent=self._t("missions")):
                dpg.add_text(mission.display_name)
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(mission.mission_id)
                dpg.add_text(mission.planner_type)
                dpg.add_text(mission.state.value, color=STATE_COLORS.get(mission.state, TEXT))
                dpg.add_text(", ".join(mission.robots))
                dpg.add_progress_bar(default_value=mission.progress, overlay=f"{mission.progress * 100:.0f} %", width=-1)
                dpg.add_text(mission.message, wrap=0)

    def _update_controls(self) -> None:
        queue = self._selected()
        idle = queue is not None and queue.queue_id not in self.service.pending
        state = queue.state if queue else QueueState.UNKNOWN
        dpg.configure_item(self._t("submit"), enabled=idle and state.can_submit)
        dpg.configure_item(self._t("start"), enabled=idle and state.can_start)
        dpg.configure_item(self._t("pause"), enabled=idle and state.can_pause and not queue.paused)
        dpg.configure_item(self._t("resume"), enabled=idle and state.can_pause and queue.paused)
        dpg.configure_item(self._t("cancel"), enabled=idle and state.can_cancel)
        dpg.configure_item(self._t("delete"), enabled=idle and state.can_delete)

    def _update_draft_controls(self) -> None:
        if not dpg.does_item_exist(self._t("create")):
            return
        creating = CREATING in self.service.pending
        dpg.configure_item(self._t("create"), enabled=len(self.draft) > 0 and not creating,
                           label="Creating..." if creating else "Create queue")
        dpg.configure_item(self._t("clear"), enabled=len(self.draft) > 0 and not creating)

    def _update_schedulers(self) -> None:
        schedulers = self.service.schedulers or DEFAULT_SCHEDULERS
        if schedulers == self._schedulers:
            return
        self._schedulers = list(schedulers)
        current = dpg.get_value(self._t("scheduler"))
        dpg.configure_item(self._t("scheduler"), items=self._schedulers)
        if current not in self._schedulers:
            dpg.set_value(self._t("scheduler"), "batch" if "batch" in self._schedulers else self._schedulers[0])
        self._update_scheduler_options()

    def _update_scheduler_options(self, *_args) -> None:
        scheduler = dpg.get_value(self._t("scheduler"))
        dpg.configure_item(self._t("batch_options"), show=scheduler == "batch")
        dpg.configure_item(self._t("fleet_options"), show=scheduler == "fleet")

    def _clear_rows(self, table: str) -> None:
        for row in dpg.get_item_children(table, 1) or ():
            dpg.delete_item(row)

    def _selected(self) -> QueueExecution | None:
        return self.executions.queue(self.selected_queue) if self.selected_queue else None

    # | ----------------------- actions ----------------------- |

    def _create(self) -> None:
        scheduler = dpg.get_value(self._t("scheduler"))
        params = {}
        if scheduler == "batch" and dpg.get_value(self._t("abort_on_failure")):
            params["abort_on_failure"] = True
        if scheduler == "fleet":
            params["assignment_policy"] = dpg.get_value(self._t("assignment_policy"))
        name = dpg.get_value(self._t("queue_name")).strip()
        if self.service.create(scheduler, name=name, params=params) is not None:
            dpg.set_value(self._t("queue_name"), "")
        self._update_draft_controls()

    def _select_queue(self, sender, app_data, queue_id: str) -> None:
        self.selected_queue = queue_id
        self._draw()

    def _submit(self) -> None:
        queue = self._selected()
        if queue is None:
            return
        if queue.state.resubmit_replaces_run:
            self._confirm("submit", "Submit again",
                          "Submit queue {queue} again?\nIt runs from scratch; the progress of its last run is replaced.",
                          self.service.submit)
        else:
            self.service.submit(queue.queue_id)

    def _control(self, command: str) -> None:
        if self.selected_queue:
            self.service.control(self.selected_queue, command)

    def _confirm(self, name: str, title: str, text: str, action) -> None:
        queue = self._selected()
        if queue is None:
            return
        tag = self._t(f"confirm_{name}")
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        with dpg.window(label=title, tag=tag, modal=True, autosize=True, no_collapse=True, on_close=lambda: dpg.delete_item(tag)):
            dpg.add_text(text.format(queue=queue.display_name))
            with dpg.group(horizontal=True):
                def confirm() -> None:
                    dpg.delete_item(tag)
                    action(queue.queue_id)
                dpg.add_button(label=title, callback=confirm)
                dpg.add_button(label="Keep", callback=lambda: dpg.delete_item(tag))

    def _confirm_cancel(self) -> None:
        self._confirm("cancel", "Cancel queue", "Cancel queue {queue}?\nRunning missions are aborted, uploaded ones unloaded, the rest dropped.",
                      lambda queue_id: self.service.control(queue_id, "cancel"))

    def _confirm_delete(self) -> None:
        self._confirm("delete", "Delete queue", "Delete queue {queue} from the server?", self.service.delete)

    def _show_message(self, text: str, success: bool) -> None:
        if dpg.does_item_exist(self._t("message")):
            dpg.set_value(self._t("message"), text)
            dpg.configure_item(self._t("message"), color=(200, 200, 200) if success else (230, 90, 90))
