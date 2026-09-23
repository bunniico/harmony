"""HTTP client for the adderall to-do app: its REST API and its alarm stream.

adderall answers anyone who can reach its port, so there is nothing to log in
with. Its MCP server is not used because it only accepts requests addressed to
`localhost`, and Harmony runs in her own container.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

log = logging.getLogger(__name__)

OPEN_STATUSES = {"todo", "in_progress", "missed"}
TIMEOUT = httpx.Timeout(30.0)


class AdderallError(Exception):
    """adderall was unreachable or refused the request. The message is safe to show."""


def compact_tasks(tree: list[dict]) -> list[dict]:
    """The task tree cut down to what the model needs, open tasks only."""
    out = []
    for t in tree:
        if t.get("status") not in OPEN_STATUSES:
            continue
        item = {k: t[k] for k in ("id", "title", "status", "deadline", "estimated_time", "quadrant", "project_name")
                if t.get(k) not in (None, "")}
        subs = compact_tasks(t.get("subtasks") or [])
        if subs:
            item["subtasks"] = subs
        out.append(item)
    return out


def _walk(tree: list[dict]):
    for t in tree:
        yield t
        yield from _walk(t.get("subtasks") or [])


class AdderallClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=TIMEOUT, transport=transport)
        self.titles: dict[str, str] = {}  # task id -> title, from every task list seen
        self.habit_names: dict[str, str] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kw) -> dict:
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise AdderallError(f"couldn't reach adderall ({type(e).__name__})") from e
        if resp.is_error:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = None
            raise AdderallError(f"adderall said {resp.status_code}: {detail or resp.reason_phrase}")
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            self._remember(data["tasks"])
        return data

    def _remember(self, tree: list[dict]) -> None:
        for t in _walk(tree):
            self.titles[t["id"]] = t["title"]

    # --- reads -----------------------------------------------------------

    async def state(self, project_id: str | None = None) -> dict:
        params = {"project_id": project_id} if project_id else None
        return await self._request("GET", "/api/state", params=params)

    async def projects(self) -> list[dict]:
        return (await self._request("GET", "/api/projects"))["projects"]

    async def next_task(self) -> dict | None:
        task = (await self._request("GET", "/api/next"))["task"]
        if task:
            self.titles[task["id"]] = task["title"]
        return task

    async def habits(self) -> dict:
        data = await self._request("GET", "/api/habits")
        self.habit_names.update({h["id"]: h["name"] for h in data["habits"]})
        return data

    # --- writes ----------------------------------------------------------

    async def add_task(self, **fields) -> dict:
        return await self._request("POST", "/api/tasks", json=fields)

    async def update_task(self, task_id: str, changes: dict) -> dict:
        return await self._request("PATCH", f"/api/tasks/{task_id}", json=changes)

    async def start_task(self, task_id: str) -> dict:
        return await self._request("POST", f"/api/tasks/{task_id}/start")

    async def complete_task(self, task_id: str, actual_time: int | None = None) -> dict:
        return await self._request("POST", f"/api/tasks/{task_id}/complete", json={"actual_time": actual_time})

    async def delete_task(self, task_id: str) -> dict:
        return await self._request("DELETE", f"/api/tasks/{task_id}")

    async def compile_braindump(self, text: str) -> dict:
        return await self._request("POST", "/api/compile", json={"text": text})

    async def check_habit(self, habit_id: str, done: bool = True, day: str | None = None) -> dict:
        return await self._request("POST", f"/api/habits/{habit_id}/check", json={"done": done, "day": day})

    # --- alarm stream ----------------------------------------------------

    async def alarms(self) -> AsyncIterator[dict]:
        """Yield each alarm from `GET /api/events` until the connection drops."""
        timeout = httpx.Timeout(10.0, read=None)  # the stream sits idle between pings
        async with self._http.stream("GET", "/api/events", timeout=timeout) as resp:
            resp.raise_for_status()
            event, data = "message", []
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "":
                    if event == "alarm" and data:
                        try:
                            yield json.loads("\n".join(data))
                        except ValueError:
                            log.warning("adderall: unreadable alarm event")
                    event, data = "message", []
