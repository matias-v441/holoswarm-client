"""Binds the selected queue (QueueDraft), its paths and areas on the map (Mission) and the selection (Session).

- The map holds the tasks of every mission of the selected queue, each tagged with its mission.
- A changed task re-encodes its mission right away (live): a mission that does not encode keeps its
  last payload and gets an error, which blocks sending the queue until it is fixed.
- A new task joins the selected mission; when it cannot (no mission selected, a second area, a path with no
  robot left), it starts a new mission of the queue.
- Selection cascades downwards on a change: queue -> its first mission -> its first task; and upwards:
  a selected task selects its mission.

GUI thread only; no GUI code.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from holoswarm_client.data.mission import Mission, MissionTask
from holoswarm_client.data.queue_draft import DraftMission, QueueDraft
from holoswarm_client.data.session import Session
from holoswarm_client.iroc.mission_codec import CodecError, decode_mission, describe, encode_draft, place_task

type Focus = Callable[[Sequence[MissionTask]], None]

_UNSET = object()


class QueueView:

    def __init__(self, mission: Mission, session: Session, draft: QueueDraft, on_focus: Focus | None = None) -> None:
        self.mission = mission
        self.session = session
        self.draft = draft
        self.on_focus = on_focus  # show these tasks (the map frames them)
        # mission id -> (payload, tasks) as last decoded or encoded: tasks stay while their payload is unchanged
        self._known: dict[str, tuple[Mapping[str, Any], tuple[MissionTask, ...]]] = {}
        self._queue: object = _UNSET     # queue id the tasks are of
        self._last_mission: object = _UNSET  # selections seen by the last cascade
        self._last_task: object = _UNSET
        self._busy = False
        draft.subscribe(lambda _: self._draft_changed())
        mission.subscribe(lambda _: self._tasks_changed())
        session.subscribe(lambda _: self._selection_changed())
        self._draft_changed()

    def focus_mission(self, mission_id: str) -> None:
        if self.on_focus:
            self.on_focus(self.mission.tasks_of(mission_id))

    # | ----------------------- draft -> tasks ----------------------- |

    def _draft_changed(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            queue_changed = self.draft.queue_id != self._queue
            if queue_changed:
                self._queue = self.draft.queue_id
                self._known = {}

            tasks: list[MissionTask] = []
            known = {}
            for draft_mission in self.draft.missions:
                entry = self._known.get(draft_mission.mission_id)
                if entry is not None and entry[0] is draft_mission.payload:
                    current = self.mission.tasks_of(draft_mission.mission_id)
                else:
                    current = self._decode(draft_mission)
                tasks += current
                known[draft_mission.mission_id] = (draft_mission.payload, current)
            self._known = known

            self.mission.read_only = self.draft.read_only
            selected = self.session.selected_task
            self.mission.set_tasks(tasks)
            if selected is not None and selected not in self.mission.tasks:
                self._last_mission = _UNSET  # its mission was rewritten (e.g. robots renamed): select its first task

            missions = [m.mission_id for m in self.draft.missions]
            if queue_changed or self.session.selected_mission not in missions:
                if queue_changed:
                    self._last_mission = _UNSET  # cascade to the first task even when the mission id stays
                self.session.select_mission(missions[0] if missions else None)
            self._cascade()
            if queue_changed and self.on_focus and tasks:
                self.on_focus(tasks)
        finally:
            self._busy = False

    def _decode(self, draft_mission: DraftMission) -> tuple[MissionTask, ...]:
        if draft_mission.empty:
            return ()
        payload = {"type": draft_mission.payload.get("type"), "details": draft_mission.payload.get("details")}
        try:
            decoded = decode_mission(payload, self.mission.robot_homes)
        except (CodecError, KeyError, TypeError, ValueError):
            return ()  # sent as it is, but not shown
        return tuple(replace(task, mission_id=draft_mission.mission_id) for task in decoded)

    # | ----------------------- tasks -> draft ----------------------- |

    def _tasks_changed(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            for task in [t for t in self.mission.tasks.values() if t.mission_id is None]:
                self._place(task)

            for draft_mission in self.draft.missions:
                mission_id = draft_mission.mission_id
                current = self.mission.tasks_of(mission_id)
                entry = self._known.get(mission_id)
                if entry is not None and entry[1] == current:
                    continue
                if entry is None and not current:
                    self._known[mission_id] = (draft_mission.payload, current)
                    continue
                self._encode(draft_mission, current)
            self._cascade()
        finally:
            self._busy = False

    def _place(self, task: MissionTask) -> None:
        """A new task joins the selected mission; when it cannot (e.g. a second area, or no robot left for a path),
        a new mission of the queue is started with it."""
        owner = self.session.selected_mission
        placed = None
        if self.draft.mission(owner) is not None:
            placed = place_task(self.mission.tasks_of(owner), task, self.mission.robot_names)
        if placed is None:
            owner, placed = self.draft.add_empty().mission_id, task
        self.mission.push_task(replace(placed, mission_id=owner))
        self.session.select_mission(owner)

    def _encode(self, draft_mission: DraftMission, tasks: tuple[MissionTask, ...]) -> None:
        mission_id = draft_mission.mission_id
        self._known[mission_id] = (draft_mission.payload, tasks)
        if not tasks:
            self.draft.set_error(mission_id, "The mission has no path or area")
            return
        try:
            payload = encode_draft(tasks, self.mission.robot_names, self.mission.robot_homes)
        except CodecError as e:
            self.draft.set_error(mission_id, str(e))
            return
        self.draft.update(mission_id, payload, describe(payload))
        self.draft.set_error(mission_id, None)
        self._known[mission_id] = (payload, tasks)

    # | ----------------------- selection ----------------------- |

    def _selection_changed(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._cascade()
        finally:
            self._busy = False

    def _cascade(self) -> None:
        """Follow a changed selection: a newly selected task selects its mission; a newly selected mission its first
        task. Only changes cascade: clearing the task selection keeps the mission, so a new task can be drawn in it."""
        tasks = self.mission.tasks
        task_id = self.session.selected_task
        if task_id is not None and task_id not in tasks:
            self.session.select_task(None)  # e.g. drawn on a read-only queue: it was not added
            task_id = None

        if task_id != self._last_task and task_id is not None:
            owner = tasks[task_id].mission_id
            if owner is not None:
                self.session.select_mission(owner)

        mission_id = self.session.selected_mission
        if mission_id != self._last_mission:
            task = tasks.get(task_id or "")
            if task is None or task.mission_id != mission_id:
                own = self.mission.tasks_of(mission_id) if mission_id is not None else ()
                self.session.select_task(own[0].uuid if own else None)

        self._last_mission = self.session.selected_mission
        self._last_task = self.session.selected_task
