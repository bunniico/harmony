"""The adderall actions Harmony can take, and which of them wait for a Confirm button.

Three kinds:
- `read`: runs straight away, changes nothing.
- `write`: runs straight away in bun.rot's DMs. In a server channel it waits
  for Confirm, because other people's messages are in the conversation there
  and could have steered the model.
- `risky`: always waits for Confirm (data is lost, or AI budget is spent).

Every summary shown on a Confirm button or in a notification is written here
from the tool arguments, never by the model, so what you approve is what runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from harmony.adderall.client import AdderallClient, compact_tasks

MAX_SHOWN = 200  # characters of any model-supplied text quoted in a summary


def local_time(iso: str | None, tz: ZoneInfo) -> str:
    """An ISO instant as a short local time, or the raw text if it doesn't parse."""
    if not iso:
        return "no deadline"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz).strftime("%a %d %b %H:%M")


def localize(data: Any, tz: ZoneInfo) -> Any:
    """Rewrite every `deadline` in a tool result from adderall's UTC to local
    time with its offset, so the model never has to convert time zones itself."""
    if isinstance(data, list):
        return [localize(v, tz) for v in data]
    if not isinstance(data, dict):
        return data
    out = {k: localize(v, tz) for k, v in data.items()}
    if isinstance(out.get("deadline"), str):
        try:
            dt = datetime.fromisoformat(out["deadline"].replace("Z", "+00:00"))
            out["deadline"] = (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(tz).isoformat(timespec="minutes")
        except ValueError:
            pass
    return out


def _title(c: AdderallClient, task_id: str) -> str:
    """A task's title, or ValueError for an id no task list has shown, so a
    guessed id never reaches a Confirm button."""
    if task_id not in c.titles:
        raise ValueError(f"unknown task id {task_id!r}; look it up with list_tasks first")
    return c.titles[task_id]


def _q(text: str) -> str:
    text = " ".join(str(text).split())
    return f"“{text[:MAX_SHOWN]}{'…' if len(text) > MAX_SHOWN else ''}”"


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str  # "read" | "write" | "risky"
    description: str
    schema: dict
    run: Callable[[AdderallClient, dict], Awaitable[Any]]
    describe: Callable[[AdderallClient, dict, ZoneInfo], str] | None = None

    def needs_confirm(self, in_owner_dm: bool) -> bool:
        return self.kind == "risky" or (self.kind == "write" and not in_owner_dm)

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


_TASK_ID = {"type": "string", "description": "A task id from list_tasks or next_task. Never guess one."}

UPDATE_FIELDS = {
    "title": {"type": "string"},
    "description": {"type": "string"},
    "deadline": {"type": "string", "description": "ISO 8601 with offset"},
    "clear_deadline": {"type": "boolean"},
    "start_at": {"type": "string", "description": "ISO 8601 with offset"},
    "clear_start_at": {"type": "boolean"},
    "estimated_time": {"type": "integer", "minimum": 1, "description": "minutes"},
    "impact": {"type": "integer", "minimum": 0, "maximum": 10},
    "effort": {"type": "integer", "minimum": 0, "maximum": 10},
    "status": {"type": "string", "enum": ["todo", "in_progress", "discarded"]},
    "flexibility": {"type": "integer", "minimum": 1, "maximum": 5},
    "workday_only": {"type": "boolean"},
}


# --- runners ---------------------------------------------------------------

async def _list_tasks(c: AdderallClient, a: dict) -> dict:
    state = await c.state(a.get("project_id"))
    return {"project_id": a.get("project_id") or state["active_project_id"],
            "next_task_id": state.get("next_task_id"), "tasks": compact_tasks(state["tasks"])}


async def _list_projects(c: AdderallClient, a: dict) -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "open_tasks": p.get("open_tasks", 0)} for p in await c.projects()]


async def _next_task(c: AdderallClient, a: dict) -> dict | None:
    t = await c.next_task()
    return compact_tasks([t])[0] if t else None


async def _list_habits(c: AdderallClient, a: dict) -> dict:
    h = await c.habits()
    return {
        "today": h["today"],
        "habits": [{"id": r["id"], "name": r["name"], "rule": r.get("rule_label"),
                    "due_today": r["stats"]["due_today"], "done_today": r["stats"]["done_today"],
                    "streak": r["stats"]["current"]} for r in h["habits"]],
    }


async def _add_task(c: AdderallClient, a: dict) -> None:
    await c.add_task(**{k: a[k] for k in ("title", "description", "parent_id", "deadline", "estimated_time") if k in a})


async def _update_task(c: AdderallClient, a: dict) -> None:
    await c.update_task(a["task_id"], a["changes"])


async def _check_habit(c: AdderallClient, a: dict) -> None:
    await c.check_habit(a["habit_id"], a.get("done", True), a.get("day"))


# --- summaries -------------------------------------------------------------

def _d_add(c: AdderallClient, a: dict, tz: ZoneInfo) -> str:
    s = f"Add task {_q(a['title'])}"
    if a.get("parent_id"):
        s += f" under {_q(_title(c, a['parent_id']))}"
    if a.get("deadline"):
        s += f", due {local_time(a['deadline'], tz)}"
    return s


def _d_update(c: AdderallClient, a: dict, tz: ZoneInfo) -> str:
    unknown = set(a["changes"]) - UPDATE_FIELDS.keys()
    if unknown:
        raise ValueError(f"can't change {', '.join(sorted(unknown))}")
    if a["changes"].get("status") not in (None, *UPDATE_FIELDS["status"]["enum"]):
        raise ValueError("status must be todo, in_progress or discarded (use complete_task for done)")
    parts = []
    for k, v in a["changes"].items():
        if k in ("deadline", "start_at"):
            v = local_time(v, tz)
        parts.append(f"{k} → {_q(v)}")
    return f"Edit {_q(_title(c, a['task_id']))}: " + (", ".join(parts) or "nothing")


def _d_check(c: AdderallClient, a: dict, tz: ZoneInfo) -> str:
    if a["habit_id"] not in c.habit_names:
        raise ValueError(f"unknown routine id {a['habit_id']!r}; look it up with list_habits first")
    name = c.habit_names[a["habit_id"]]
    return f"Mark routine {_q(name)} {'done' if a.get('done', True) else 'not done'} for {a.get('day') or 'today'}"


def _d_braindump(c: AdderallClient, a: dict, tz: ZoneInfo) -> str:
    return f"Turn this braindump into tasks (uses adderall's AI budget): {_q(a['text'])}"


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_projects", "read", "List adderall's projects (tabs) with how many open tasks each has.",
         _obj(), _list_projects),
    Tool("list_tasks", "read",
         "List open tasks as a tree, with deadlines. Defaults to the project open in the app.",
         _obj({"project_id": {"type": "string"}}), _list_tasks),
    Tool("next_task", "read", "The one task adderall says to do next, or null.", _obj(), _next_task),
    Tool("list_habits", "read", "Routines, whether each is due and done today, and streaks.", _obj(), _list_habits),
    Tool("add_task", "write",
         "Add a task to the open project, or under parent_id as a subtask. A title like "
         "\"call mum tomorrow at 5pm\" sets its own deadline.",
         _obj({"title": {"type": "string", "minLength": 1, "maxLength": 500},
               "description": {"type": "string"},
               "parent_id": {"type": "string"},
               "deadline": {"type": "string", "description": "ISO 8601 with offset"},
               "estimated_time": {"type": "integer", "minimum": 1, "description": "minutes"}}, ["title"]),
         _add_task, _d_add),
    Tool("start_task", "write", "Mark a task in progress; its timer starts.",
         _obj({"task_id": _TASK_ID}, ["task_id"]),
         lambda c, a: c.start_task(a["task_id"]), lambda c, a, tz: f"Start {_q(_title(c, a['task_id']))}"),
    Tool("check_habit", "write", "Tick (or untick) a routine for a day (YYYY-MM-DD, default today).",
         _obj({"habit_id": {"type": "string"}, "done": {"type": "boolean"}, "day": {"type": "string"}}, ["habit_id"]),
         _check_habit, _d_check),
    Tool("update_task", "risky", "Edit a task's fields.",
         _obj({"task_id": _TASK_ID, "changes": {**_obj(UPDATE_FIELDS), "minProperties": 1}}, ["task_id", "changes"]),
         _update_task, _d_update),
    Tool("complete_task", "risky", "Mark a task done.",
         _obj({"task_id": _TASK_ID}, ["task_id"]),
         lambda c, a: c.complete_task(a["task_id"]), lambda c, a, tz: f"Mark {_q(_title(c, a['task_id']))} done"),
    Tool("delete_task", "risky", "Delete a task and all its subtasks for good.",
         _obj({"task_id": _TASK_ID}, ["task_id"]),
         lambda c, a: c.delete_task(a["task_id"]),
         lambda c, a, tz: f"Delete {_q(_title(c, a['task_id']))} and all its subtasks"),
    Tool("compile_braindump", "risky", "Turn free text into a tree of tasks in the open project.",
         _obj({"text": {"type": "string", "minLength": 1, "maxLength": 20000}}, ["text"]),
         lambda c, a: c.compile_braindump(a["text"]), _d_braindump),
]}

TOOL_SPECS = [t.spec() for t in TOOLS.values()]
