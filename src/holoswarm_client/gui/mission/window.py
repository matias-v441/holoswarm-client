import dearpygui.dearpygui as dpg
import time
from collections.abc import Callable

from holoswarm_client.data.execution import ExecutionStore, MissionState, QueueExecution, QueueState, SyncState
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.data.queue_view import QueueView
from holoswarm_client.data.session import Session
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
WARN = (230, 190, 90)
RED = (230, 90, 90)


class MissionWindow:
    """The selected queue in the editor (the new one, or a stored one: edited, or read-only while submitted), the
    queues kept by the bridge, and the missions of the selected queue.

    Selection (in the session) is queue > mission > task: clicking a queue opens it in the editor, the map and the
    Task window; clicking a mission selects it (and its first task), which the map shows in orange.
    """

    def __init__(self, service: QueueService, executions: ExecutionStore, draft: QueueDraft, session: Session,
                 view: QueueView | None = None) -> None:
        self.service = service
        self.executions = executions
        self.draft = draft
        self.session = session
        self.view = view
        self.window_tag = "mission_window"
        self._started = False  # the first snapshot selected a queue
        self._shown_robots: tuple | None = None  # what the robot combos show
        self._arriving: str | None = None  # created and opened, but not in the store yet
        self._shown_selection: tuple[str | None, str | None] | None = None
        self._last_sync: SyncState | None = None
        self._schedulers: list[str] = []
        executions.subscribe(lambda _: self._draw())
        draft.subscribe(lambda _: self._draw_draft())
        session.subscribe(lambda _: self._selection_changed())
        service.on_message(self._show_message)
        service.on_created(self._created)
        if view is not None:
            view.mission.subscribe(lambda _: self._draw_robots())  # robots are discovered at any time

    @property
    def selected_queue(self) -> str | None:
        return self.session.selected_queue

    def _t(self, name: str) -> str:
        return f"{self.window_tag}_{name}"

    def add(self) -> None:
        # Disabled buttons look like enabled ones in the default theme.
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvButton, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (110, 110, 110))
                for color in (dpg.mvThemeCol_Button, dpg.mvThemeCol_ButtonHovered, dpg.mvThemeCol_ButtonActive):
                    dpg.add_theme_color(color, (45, 45, 48))
            with dpg.theme_component(dpg.mvSelectable):
                # Match the map's golden hue, darkened for readable table text.
                dpg.add_theme_color(dpg.mvThemeCol_Header, (100, 80, 35, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (112, 90, 39, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (124, 100, 43, 255))

        with dpg.window(label="Mission", tag=self.window_tag, no_close=True):  # part of the layout
            dpg.add_text("", tag=self._t("sync"))
            dpg.add_text("", tag=self._t("message"), wrap=0)

            with dpg.collapsing_header(label="New queue", tag=self._t("editor"), default_open=True):
                dpg.add_text("", tag=self._t("editor_hint"), color=GREY, wrap=0)
                dpg.add_text("", tag=self._t("editor_note"), color=WARN, wrap=0, show=False)
                with dpg.table(tag=self._t("draft"), header_row=True, resizable=True, row_background=True,
                               borders_innerH=True, borders_outerH=True, policy=dpg.mvTable_SizingStretchProp):
                    dpg.add_table_column(label="#", width_fixed=True, init_width_or_weight=20)
                    for label in ("Name", "Type", "Content"):
                        dpg.add_table_column(label=label)
                    dpg.add_table_column(label="", width_fixed=True, init_width_or_weight=80)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(label="Queue name", tag=self._t("queue_name"), width=200, callback=self._name_changed)
                    dpg.add_combo(DEFAULT_SCHEDULERS, label="Scheduler", tag=self._t("scheduler"), default_value="batch",
                                  width=120, callback=self._settings_changed)
                with dpg.group(tag=self._t("batch_options")):
                    dpg.add_text("Runs the missions one after another, each on all of its robots.", color=GREY, wrap=0)
                    dpg.add_checkbox(label="Cancel the remaining missions after a failure", tag=self._t("abort_on_failure"),
                                     callback=self._settings_changed)
                with dpg.group(tag=self._t("fleet_options"), show=False):
                    dpg.add_text("Splits missions into tasks for any capable idle robot. Waypoint routes stay with their robot.",
                                 color=GREY, wrap=0)
                    dpg.add_combo(ASSIGNMENT_POLICIES, label="Assignment", tag=self._t("assignment_policy"),
                                  default_value=ASSIGNMENT_POLICIES[0], width=160, callback=self._settings_changed)
                with dpg.group(horizontal=True, tag=self._t("robots")):
                    pass
                with dpg.group(horizontal=True):
                    self._button("create", "Create queue", self._create,
                                 "Store the queue on the server with all of its missions.\n"
                                 "Nothing is sent to the robots until it is submitted.")
                    self._button("new_mission", "New mission", self._new_mission,
                                 "Add an empty mission and select it; Ctrl+click on the map draws its paths or area.")
                    dpg.add_button(label="Clear", tag=self._t("clear"), callback=lambda: self.draft.clear())
                    self._button("upload", "Upload changes", lambda: self.service.upload(),
                                 "Replace the queue on the server with this copy. It keeps its id and becomes CREATED;\n"
                                 "the progress of its last run is dropped.")
                    self._button("revert", "Revert", self._confirm_revert, "Drop the changes: load the queue from the server again.")

            dpg.add_separator()
            dpg.add_text("Queues")
            with dpg.group(horizontal=True):
                self._button("new_queue", "New queue", self._confirm_close,
                             "Put together a new queue in the editor. The new queue started before comes back.")
                self._button("submit", "Submit", self._submit,
                             "Hand the queue to the fleet manager. It plans the first step and uploads it to the robots;\n"
                             "nothing moves until Start. Only one queue can be submitted at a time.\n"
                             "A finished, cancelled or interrupted queue runs again from scratch.\n"
                             "A queue being edited is submitted once its changes are uploaded or reverted.")
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
        self._shown_selection = (self.session.selected_queue, self.session.selected_mission)
        missions = self.draft.missions
        read_only = self.draft.read_only
        for index, mission in enumerate(missions):
            with dpg.table_row(parent=self._t("draft")):
                dpg.add_text(str(index + 1))
                if read_only:
                    dpg.add_text(mission.name)
                else:
                    dpg.add_input_text(default_value=mission.name, width=-1, user_data=mission.mission_id,
                                       callback=self._rename_mission)
                dpg.add_text(mission.planner_type)
                with dpg.group():
                    dpg.add_selectable(label=mission.summary or "(no path or area yet)",
                                       default_value=mission.mission_id == self.session.selected_mission,
                                       user_data=mission.mission_id, callback=lambda s, a, mission_id: self._select_mission(mission_id))
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Select the mission: the map shows it in orange and the Task window its first path or area.")
                    error = self.draft.error(mission.mission_id)
                    if error and not read_only:
                        dpg.add_text(error, color=RED, wrap=0)
                if read_only:
                    dpg.add_text("")
                    continue
                with dpg.group(horizontal=True):
                    dpg.add_button(arrow=True, direction=dpg.mvDir_Up, enabled=index > 0, user_data=mission.mission_id,
                                   callback=lambda s, a, mission_id: self.draft.move(mission_id, -1))
                    dpg.add_button(arrow=True, direction=dpg.mvDir_Down, enabled=index < len(missions) - 1,
                                   user_data=mission.mission_id, callback=lambda s, a, mission_id: self.draft.move(mission_id, 1))
                    dpg.add_button(label="x", user_data=mission.mission_id,
                                   callback=lambda s, a, mission_id: self.draft.remove(mission_id))
        self._show_settings()
        self._draw_robots()
        self._update_draft_controls()
        self._update_controls()

    def _draw(self) -> None:
        if not dpg.does_item_exist(self._t("queues")):
            return
        self._update_schedulers()

        if not self._started and self.executions.session_id is not None:
            # The first snapshot: show the queue that holds the robots, if any.
            self._started = True
            in_flight = self.executions.in_flight()
            if in_flight is not None and not self.draft.editing:
                self._load(in_flight.queue_id)
        self._sync_edited()
        queues = list(self.executions.queues.values())

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
                dpg.add_selectable(label=mission.display_name, span_columns=True, user_data=mission.mission_id,
                                   default_value=mission.mission_id == self.session.selected_mission,
                                   callback=lambda s, a, mission_id: self._select_mission(mission_id))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(f"{mission.mission_id}\nClick to select it (shown in orange on the map).")
                dpg.add_text(mission.planner_type)
                dpg.add_text(mission.state.value, color=STATE_COLORS.get(mission.state, TEXT))
                dpg.add_text(", ".join(mission.robots))
                dpg.add_progress_bar(default_value=mission.progress, overlay=f"{mission.progress * 100:.0f} %", width=-1)
                dpg.add_text(mission.message, wrap=0)

    def _update_controls(self) -> None:
        queue = self._selected()
        idle = queue is not None and queue.queue_id not in self.service.pending
        state = queue.state if queue else QueueState.UNKNOWN
        unsaved = queue is not None and queue.queue_id == self.draft.queue_id and self.draft.dirty
        dpg.configure_item(self._t("submit"), enabled=idle and state.can_submit and not unsaved)
        dpg.configure_item(self._t("start"), enabled=idle and state.can_start)
        dpg.configure_item(self._t("pause"), enabled=idle and state.can_pause and not queue.paused)
        dpg.configure_item(self._t("resume"), enabled=idle and state.can_pause and queue.paused)
        dpg.configure_item(self._t("cancel"), enabled=idle and state.can_cancel)
        dpg.configure_item(self._t("delete"), enabled=idle and state.can_delete)

    def _update_draft_controls(self) -> None:
        if not dpg.does_item_exist(self._t("create")):
            return
        draft = self.draft
        editing, read_only, dirty = draft.editing, draft.read_only, draft.dirty
        problems = draft.problems
        for name in ("create", "clear"):
            dpg.configure_item(self._t(name), show=not editing)
        for name in ("upload", "revert"):
            dpg.configure_item(self._t(name), show=editing and (not read_only or dirty))
        dpg.configure_item(self._t("new_queue"), enabled=editing)
        dpg.configure_item(self._t("new_mission"), show=not read_only)
        for name in ("queue_name", "scheduler", "abort_on_failure", "assignment_policy"):
            dpg.configure_item(self._t(name), enabled=not read_only)

        problem = ""
        if problems:
            mission, text = problems[0]
            problem = f"Mission '{mission.name or mission.mission_id}': {text}"
        elif draft.robot_conflict:
            problem = f"Robots: {draft.robot_conflict}"

        if not editing:
            creating = CREATING in self.service.pending
            dpg.configure_item(self._t("editor"), label="New queue")
            dpg.set_value(self._t("editor_hint"),
                          "Missions run in this order. 'New mission', then Ctrl+click on the map draws its paths or area.\n"
                          "Click a mission to select it; click a queue below to open it here.")
            dpg.set_value(self._t("editor_note"), problem)
            dpg.configure_item(self._t("editor_note"), show=bool(problem))
            dpg.configure_item(self._t("create"), enabled=len(draft) > 0 and not creating and not problem,
                               label="Creating..." if creating else "Create queue")
            dpg.configure_item(self._t("clear"), enabled=len(draft) > 0 and not creating)
            return

        queue = self.executions.queue(draft.queue_id)
        label = queue.display_name if queue else draft.settings.name or draft.queue_id
        arriving = queue is None and draft.queue_id == self._arriving  # just created: its event is on the way
        pending = draft.queue_id in self.service.pending
        editable = (queue is not None and queue.state.can_submit) or arriving
        if read_only and not dirty:
            dpg.configure_item(self._t("editor"), label=f"Queue {label} (read-only)")
            dpg.set_value(self._t("editor_hint"), "Click a mission to select it on the map.")
        else:
            dpg.configure_item(self._t("editor"), label=f"Edit queue {label}" + (" *" if dirty else ""))
            dpg.set_value(self._t("editor_hint"),
                          "A copy of the queue: change it like a new queue, then upload it. It stays the same queue.")
        if queue is None and not arriving:
            note = "The queue was deleted on the server; the changes cannot be uploaded."
        elif not editable:
            note = (f"The queue is {queue.state.value}: " +
                    ("cancel it to upload the changes." if dirty else "shown read-only; cancel it to change it."))
        elif problem:
            note = problem
        elif draft.outdated:
            note = "The queue changed on the server since it was loaded; uploading replaces those changes."
        else:
            note = ""
        dpg.set_value(self._t("editor_note"), note)
        dpg.configure_item(self._t("editor_note"), show=bool(note))
        dpg.configure_item(self._t("upload"), enabled=dirty and editable and not pending and len(draft) > 0 and not problem,
                           label="Uploading..." if pending and dirty else "Upload changes")
        dpg.configure_item(self._t("revert"), enabled=dirty and queue is not None and not pending)

    def _draw_robots(self) -> None:
        """A combo per robot of the queue: choose another (discovered) robot to use instead, in every mission."""
        if not dpg.does_item_exist(self._t("robots")):
            return
        draft = self.draft
        robots = draft.robots
        available = tuple(sorted(set(self.view.mission.robot_names if self.view else ()) | set(robots)))
        shown = (robots, tuple(draft.robot_choice(r) for r in robots), available, draft.read_only)
        if shown == self._shown_robots:
            return
        self._shown_robots = shown
        dpg.delete_item(self._t("robots"), children_only=True)
        dpg.configure_item(self._t("robots"), show=bool(robots))
        if not robots:
            return
        dpg.add_text("Robots", parent=self._t("robots"))
        with dpg.tooltip(dpg.last_item()):
            dpg.add_text("Use another robot instead of one of the queue: it is replaced in every mission.\n"
                         "Robots must stay distinct; swap two by choosing each other's.")
        for robot in robots:
            dpg.add_combo(list(available), default_value=draft.robot_choice(robot), width=90, parent=self._t("robots"),
                          enabled=not draft.read_only, user_data=robot,
                          callback=lambda s, value, robot: self._robot_chosen(robot, value))
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text(f"Instead of {robot}")

    def _robot_chosen(self, robot: str, substitute: str) -> None:
        if self.draft.read_only:
            return
        conflict = self.draft.choose_robot(robot, substitute)
        if conflict:
            self._show_message(f"Not changed: {conflict}. Choose distinct robots.", False)
        else:
            self._show_message("", True)
        self._update_draft_controls()
        self._update_controls()

    def _show_settings(self) -> None:
        """The widgets show the settings of the draft (new or edited queue)."""
        settings = self.draft.settings
        dpg.set_value(self._t("queue_name"), settings.name)
        dpg.set_value(self._t("scheduler"), settings.scheduler)
        dpg.set_value(self._t("abort_on_failure"), bool(settings.params.get("abort_on_failure", False)))
        policy = settings.params.get("assignment_policy")
        dpg.set_value(self._t("assignment_policy"), policy if policy in ASSIGNMENT_POLICIES else ASSIGNMENT_POLICIES[0])
        self._update_scheduler_options()

    def _settings_changed(self, *_args) -> None:
        """A scheduler widget changed: the draft's params follow; params without a widget stay with their scheduler."""
        scheduler = dpg.get_value(self._t("scheduler"))
        settings = self.draft.settings
        params = dict(settings.params) if scheduler == settings.scheduler else {}
        if scheduler == "batch":
            if dpg.get_value(self._t("abort_on_failure")):
                params["abort_on_failure"] = True
            else:
                params.pop("abort_on_failure", None)
        if scheduler == "fleet":
            params["assignment_policy"] = dpg.get_value(self._t("assignment_policy"))
        self.draft.set_settings(scheduler=scheduler, params=params)
        self._update_scheduler_options()
        self._update_draft_controls()
        self._update_controls()

    def _name_changed(self, sender, value: str) -> None:
        self.draft.set_settings(name=value)
        self._update_draft_controls()
        self._update_controls()

    def _rename_mission(self, sender, value: str, mission_id: str) -> None:
        self.draft.rename(mission_id, value)
        self._update_draft_controls()
        self._update_controls()

    def _sync_edited(self) -> None:
        """The opened queue follows the server unless it has changes; a deleted one goes back to the new queue."""
        if not self.draft.editing:
            return
        queue = self.executions.queue(self.draft.queue_id)
        if queue is not None:
            self._arriving = None
            self.draft.sync(queue)
        elif not self.draft.dirty and self.draft.queue_id != self._arriving:
            self._close()

    def _selection_changed(self) -> None:
        """The highlighted queue and mission follow the session (a mission may be selected on the map)."""
        if self._shown_selection is None or (self.session.selected_queue, self.session.selected_mission) == self._shown_selection:
            return
        self._draw()
        self._draw_draft()

    def _update_schedulers(self) -> None:
        schedulers = self.service.schedulers or DEFAULT_SCHEDULERS
        if schedulers == self._schedulers:
            return
        self._schedulers = list(schedulers)
        current = dpg.get_value(self._t("scheduler"))
        dpg.configure_item(self._t("scheduler"), items=self._schedulers)
        if current not in self._schedulers and not self.draft.editing:
            dpg.set_value(self._t("scheduler"), "batch" if "batch" in self._schedulers else self._schedulers[0])
            self._settings_changed()
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
        if self.service.create() is not None:
            self.draft.set_settings(name="")
            dpg.set_value(self._t("queue_name"), "")
        self._update_draft_controls()

    def _select_queue(self, sender, app_data, queue_id: str) -> None:
        """Open the queue in the editor, the map and the Task window (read-only while it is submitted)."""
        queue = self.executions.queue(queue_id)
        if queue is None or queue_id == self.draft.queue_id:
            self._draw()  # the clicked row toggled itself
            return
        if self.draft.dirty:
            edited = self.executions.queue(self.draft.queue_id)
            self._ask("discard", "Discard changes",
                      f"Discard the changes to queue {edited.display_name if edited else self.draft.queue_id}\n"
                      f"and open queue {queue.display_name}?",
                      lambda: self._load(queue_id))
            self._draw()
        else:
            self._load(queue_id)

    def _load(self, queue_id: str) -> None:
        queue = self.executions.queue(queue_id)
        if queue is not None:
            self.draft.load(queue)
            self.session.select_queue(queue_id)

    def _created(self, queue: QueueExecution) -> None:
        """A queue created from the new queue becomes the selected one, unless an edit with changes is open meanwhile."""
        if self.draft.dirty:
            return
        self._arriving = queue.queue_id  # not in the store until its event arrives; not deleted
        self.draft.load(queue)
        self.session.select_queue(queue.queue_id)

    def _close(self) -> None:
        self.draft.close()
        self.session.select_queue(None)

    def _confirm_revert(self) -> None:
        self._ask("revert", "Revert", "Drop the changes and load the queue from the server again?",
                  lambda: self._load(self.draft.queue_id))

    def _confirm_close(self) -> None:
        if self.draft.dirty:
            self._ask("new_queue", "New queue", "Drop the changes to this queue and go back to the new queue?", self._close)
        else:
            self._close()

    def _new_mission(self) -> None:
        if self.draft.read_only:
            return
        mission = self.draft.add_empty()
        self.session.select_mission(mission.mission_id)

    def _select_mission(self, mission_id: str) -> None:
        """Select a mission of the opened queue: its first task too, and the map shows it."""
        if self.draft.mission(mission_id) is None:
            self._draw_draft()
            return
        self.session.select_mission(mission_id)
        if self.view is not None:
            self.view.focus_mission(mission_id)
        self._draw_draft()  # the clicked selectable toggled itself

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
        """Ask before acting on the selected queue."""
        queue = self._selected()
        if queue is None:
            return
        self._ask(name, title, text.format(queue=queue.display_name), lambda: action(queue.queue_id))

    def _ask(self, name: str, title: str, text: str, action: Callable[[], None]) -> None:
        tag = self._t(f"confirm_{name}")
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        with dpg.window(label=title, tag=tag, modal=True, autosize=True, no_collapse=True, on_close=lambda: dpg.delete_item(tag)):
            dpg.add_text(text)
            with dpg.group(horizontal=True):
                def confirm() -> None:
                    dpg.delete_item(tag)
                    action()
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
