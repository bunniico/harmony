"""The adderall link: config, HTTP client, tool policy, Confirm buttons, notices."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from harmony.adderall.client import AdderallClient, AdderallError, compact_tasks
from harmony.adderall.tools import localize
from harmony.adderall.link import (
    MAX_PENDING,
    AdderallLink,
    ConfirmView,
    alarm_notice,
    digest_notice,
    seconds_until,
)
from harmony.ai.client import AIClient, AIUnavailable
from harmony.config import ConfigError, load_config
from harmony.security.output_guard import OutputGuard

EXAMPLE = Path(__file__).parent.parent / "config.example.json"
CFG = load_config(EXAMPLE)
OWNER = 188827493971525633
LONDON = ZoneInfo("Europe/London")

TREE = [
    {"id": "a", "title": "Laundry", "status": "todo", "deadline": "2026-09-23T16:00:00+00:00", "subtasks": [
        {"id": "a1", "title": "Wash", "status": "done", "subtasks": []},
        {"id": "a2", "title": "Dry", "status": "todo", "subtasks": []},
    ]},
    {"id": "b", "title": "Old thing", "status": "discarded", "subtasks": []},
]


OTHER_PROJECT = [{"id": "c", "title": "Health", "status": "todo", "subtasks": [
    {"id": "c1", "title": "ADHD appointment", "status": "done", "description": "bring meds list",
     "deadline": "2026-09-24T02:00:00+00:00", "impact": 9, "series_id": None,
     "blocks": [["2026-09-24T01:00:00+00:00", "2026-09-24T02:00:00+00:00"]], "subtasks": []},
]}]


# --- config ----------------------------------------------------------------

def _write(tmp_path, mutate):
    cfg = json.loads(EXAMPLE.read_text())
    mutate(cfg)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return p


def test_example_config_links_adderall_to_owner():
    assert CFG.adderall.enabled and CFG.adderall.user_id == OWNER


def test_adderall_section_is_optional(tmp_path):
    assert load_config(_write(tmp_path, lambda c: c.pop("adderall"))).adderall is None


@pytest.mark.parametrize("mutate", [
    lambda c: c["adderall"].update(timezone="Mars/Olympus"),
    lambda c: c["adderall"].update(digest_time="8am"),
    lambda c: c["adderall"].update(digest_time="24:00"),
    lambda c: c["adderall"].pop("user_id"),
    lambda c: c["adderall"].update(api_key="x"),
])
def test_bad_adderall_config_aborts(tmp_path, mutate):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, mutate))


# --- client ----------------------------------------------------------------

def test_compact_tasks_keeps_open_tasks_only():
    assert compact_tasks(TREE) == [{
        "id": "a", "title": "Laundry", "status": "todo", "deadline": "2026-09-23T16:00:00+00:00",
        "subtasks": [{"id": "a2", "title": "Dry", "status": "todo"}],
    }]


def make_client(handler):
    return AdderallClient("http://adderall", transport=httpx.MockTransport(handler))


async def test_client_sends_requests_and_remembers_titles():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.url.params.get("project_id"), req.content))
        return httpx.Response(200, json={"tasks": TREE, "active_project_id": "p"})

    c = make_client(handler)
    await c.state("p2")
    await c.add_task(title="x")
    assert seen[0][:3] == ("GET", "/api/state", "p2")
    assert seen[1][:2] == ("POST", "/api/tasks") and json.loads(seen[1][3]) == {"title": "x"}
    assert c.titles["a2"] == "Dry"


async def test_client_errors_are_readable():
    c = make_client(lambda req: httpx.Response(404, json={"detail": "Task not found"}))
    with pytest.raises(AdderallError, match="404: Task not found"):
        await c.delete_task("nope")

    def down(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(AdderallError, match="couldn't reach"):
        await make_client(down).projects()


async def test_alarm_stream_parses_only_alarm_events():
    body = (
        ": ping\n\n"
        'event: alarm\ndata: {"type": "alarm", "stage": "go"}\n\n'
        "event: other\ndata: {}\n\n"
        'event: alarm\ndata: {"type": "alarm", "stage": "stop"}\n\n'
    )
    c = make_client(lambda req: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    assert [a["stage"] async for a in c.alarms()] == ["go", "stop"]


# --- tool sessions ---------------------------------------------------------

class FakeClient:
    def __init__(self):
        self.calls = []
        self.titles = {"a": "Laundry"}
        self.habit_names = {}

    async def state(self, project_id=None):
        tasks = OTHER_PROJECT if project_id == "p2" else TREE
        return {"tasks": tasks, "active_project_id": "p", "next_task_id": "a",
                "projects": [{"id": "p"}, {"id": "p2"}]}

    async def add_task(self, **fields):
        self.calls.append(("add_task", fields))

    async def delete_task(self, task_id):
        self.calls.append(("delete_task", task_id))

    async def update_task(self, task_id, changes):
        self.calls.append(("update_task", task_id, changes))

    async def close(self):
        pass


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, text, **kw):
        self.sent.append((text, kw.get("view")))
        return SimpleNamespace(channel=SimpleNamespace(id=555))


class FakeAI:
    def __init__(self, reply="...hey. your laundry thing is soon."):
        self.reply, self.prompts = reply, []

    async def complete(self, system, prompt, max_tokens):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_link(store=None, ai=None):
    dm = FakeChannel()
    user = SimpleNamespace(send=dm.send)
    added = []

    async def add_message(*a, **kw):
        added.append(a)

    bot = SimpleNamespace(
        ai=ai or FakeAI(), stable_prompt="S", guard=OutputGuard("CANARY", "the fixed rules text here"),
        user=SimpleNamespace(id=1000), get_user=lambda uid: user if uid == OWNER else None,
        store=SimpleNamespace(add_message=add_message),
    )
    link = AdderallLink(bot, CFG.adderall, client=FakeClient())
    return link, dm, added


def dm_message():
    return SimpleNamespace(guild=None, channel=None)


def guild_message():
    return SimpleNamespace(guild=SimpleNamespace(name="Hotlantis"), channel=SimpleNamespace(name="general"))


async def test_only_the_configured_user_gets_tools():
    link, _, _ = make_link()
    assert link.allowed(OWNER)
    assert not link.allowed(627605249905000478)


async def test_reads_run_and_return_compact_json():
    link, _, _ = make_link()
    out, err = await link.session(dm_message()).run("list_tasks", {})
    assert not err and json.loads(out)["tasks"][0]["subtasks"] == [{"id": "a2", "title": "Dry", "status": "todo"}]


async def test_read_results_give_deadlines_in_local_time():
    link, _, _ = make_link()
    link.tz = ZoneInfo("America/Los_Angeles")
    out, _ = await link.session(dm_message()).run("list_tasks", {})
    # 16:00 UTC is 09:00 in Los Angeles (PDT, UTC-7) on the same day
    assert json.loads(out)["tasks"][0]["deadline"] == "2026-09-23T09:00-07:00"


def test_localize_crosses_midnight_and_leaves_other_fields():
    la = ZoneInfo("America/Los_Angeles")
    data = {"task": {"title": "ADHD appointment", "deadline": "2026-09-24T02:00:00+00:00"}, "deadline": None}
    assert localize(data, la) == {"task": {"title": "ADHD appointment", "deadline": "2026-09-23T19:00-07:00"},
                                  "deadline": None}


async def test_get_task_returns_every_field_from_any_project_in_local_time():
    link, _, _ = make_link()
    link.tz = ZoneInfo("America/Los_Angeles")
    out, err = await link.session(dm_message()).run("get_task", {"task_id": "c1"})
    assert not err
    assert json.loads(out) == {
        "id": "c1", "title": "ADHD appointment", "status": "done", "description": "bring meds list",
        "deadline": "2026-09-23T19:00-07:00", "impact": 9, "series_id": None,
        "blocks": [["2026-09-23T18:00-07:00", "2026-09-23T19:00-07:00"]], "subtasks": [],
    }


async def test_get_task_with_unknown_id_is_an_error():
    link, _, _ = make_link()
    out, err = await link.session(dm_message()).run("get_task", {"task_id": "nope"})
    assert err and "no task with id" in out


def test_localize_leaves_non_timestamps_alone():
    la = ZoneInfo("America/Los_Angeles")
    data = {"title": "2026-09-24 plan", "day": "2026-09-24", "n": 3, "created_at": "2026-09-24T02:00:00Z"}
    assert localize(data, la) == {"title": "2026-09-24 plan", "day": "2026-09-24", "n": 3,
                                  "created_at": "2026-09-23T19:00-07:00"}


async def test_add_task_runs_immediately_in_owner_dm():
    link, dm, _ = make_link()
    s = link.session(dm_message())
    out, err = await s.run("add_task", {"title": "Buy milk", "sneaky": 1})
    assert not err and out.startswith("Done")
    assert link.client.calls == [("add_task", {"title": "Buy milk"})]
    await s.finish(FakeChannel())
    assert dm.sent == []  # she's already in the DM; no separate notice


async def test_writes_in_a_server_wait_for_confirm():
    link, _, _ = make_link()
    s = link.session(guild_message())
    out, err = await s.run("add_task", {"title": "Buy milk"})
    assert not err and out.startswith("NOT done") and link.client.calls == []
    chan = FakeChannel()
    await s.finish(chan)
    assert chan.sent[0][0] == "**Harmony wants to:** Add task “Buy milk”"
    assert isinstance(chan.sent[0][1], ConfirmView)


async def test_delete_always_waits_for_confirm_even_in_dm():
    link, _, _ = make_link()
    s = link.session(dm_message())
    out, _ = await s.run("delete_task", {"task_id": "a"})
    assert out.startswith("NOT done") and link.client.calls == []
    assert s.pending[0].summary == "Delete “Laundry” and all its subtasks"


async def test_update_rejects_fields_outside_the_whitelist():
    link, _, _ = make_link()
    s = link.session(dm_message())
    out, err = await s.run("update_task", {"task_id": "a", "changes": {"project_id": "x"}})
    assert err and "can't change project_id" in out and s.pending == []
    out, err = await s.run("update_task", {"task_id": "a", "changes": {"status": "done"}})
    assert err and s.pending == []


async def test_unknown_ids_fail_before_any_confirm_prompt():
    link, _, _ = make_link()
    s = link.session(dm_message())
    for name, args in [("delete_task", {"task_id": "made-up"}), ("check_habit", {"habit_id": "h?"}),
                       ("add_task", {"title": "x", "parent_id": "made-up"})]:
        out, err = await s.run(name, args)
        assert err and "unknown" in out
    assert s.pending == [] and link.client.calls == []


async def test_pending_actions_are_capped_and_unknown_tools_fail():
    link, _, _ = make_link()
    s = link.session(dm_message())
    for _ in range(MAX_PENDING):
        await s.run("delete_task", {"task_id": "a"})
    out, err = await s.run("delete_task", {"task_id": "a"})
    assert err and len(s.pending) == MAX_PENDING
    assert (await s.run("rm_rf", {}))[1] is True


async def test_server_actions_are_reported_by_dm():
    link, dm, added = make_link(ai=FakeAI(AIUnavailable()))
    s = link.session(guild_message())
    s.done.append("Start “Laundry”")
    await s.finish(FakeChannel())
    assert dm.sent[0][0] == "Done in #general (Hotlantis) when you asked: Start “Laundry”."
    assert added and added[0][3] == "assistant"


# --- Confirm buttons -------------------------------------------------------

class FakeInteraction:
    def __init__(self, user_id):
        self.user = SimpleNamespace(id=user_id)
        self.log = []
        outer = self

        class Response:
            async def edit_message(self, **kw):
                outer.log.append(("edit", kw["content"]))

            async def send_message(self, text, **kw):
                outer.log.append(("ephemeral", text))

            async def defer(self):
                outer.log.append(("defer",))

        self.response = Response()

    async def edit_original_response(self, **kw):
        self.log.append(("edit", kw["content"]))


async def pending_view(link, in_dm=False):
    s = link.session(dm_message() if in_dm else guild_message())
    await s.run("delete_task", {"task_id": "a"})
    return ConfirmView(link, s.pending[0], s.where, in_dm)


async def test_confirm_button_ignores_everyone_but_the_owner():
    link, _, _ = make_link()
    view = await pending_view(link)
    stranger = FakeInteraction(42)
    assert not await view.interaction_check(stranger)
    assert stranger.log[0][0] == "ephemeral" and link.client.calls == []


async def test_confirm_runs_once_and_notifies():
    link, dm, _ = make_link(ai=FakeAI(AIUnavailable()))
    view = await pending_view(link)
    inter = FakeInteraction(OWNER)
    assert await view.interaction_check(inter)
    await view.confirm.callback(inter)
    await view.confirm.callback(inter)  # double click
    assert link.client.calls == [("delete_task", "a")]
    assert inter.log[-2][1].startswith("✅ Done")
    assert "after you confirmed" in dm.sent[0][0]


async def test_cancel_changes_nothing():
    link, _, _ = make_link()
    view = await pending_view(link, in_dm=True)
    inter = FakeInteraction(OWNER)
    await view.cancel.callback(inter)
    await view.confirm.callback(inter)
    assert link.client.calls == [] and inter.log[0][1].startswith("✖ Cancelled")


# --- notices ---------------------------------------------------------------

def test_alarm_notice_uses_local_time():
    alarm = {"text": "🚀 Time for “Laundry” — go now",
             "task": {"deadline": "2026-09-23T16:00:00+00:00", "project_name": "Home"}}
    assert alarm_notice(alarm, LONDON) == "🚀 Time for “Laundry” — go now (due Wed 23 Sep 17:00, list: Home)"


def test_digest_lists_next_overdue_and_today():
    now = datetime(2026, 9, 23, 8, 0, tzinfo=LONDON)
    alarm_tasks = [
        {"title": "Tomorrow", "deadline": "2026-09-24T10:00:00+00:00"},
        {"title": "Laundry", "deadline": "2026-09-23T16:00:00+00:00"},
        {"title": "Taxes", "deadline": "2026-09-20T10:00:00+00:00"},
    ]
    text = digest_notice({"title": "Laundry", "estimated_time": 30}, alarm_tasks,
                         {"today_due": 3, "today_done": 1}, now)
    assert text.splitlines() == [
        "Morning digest for Wednesday 23 September.",
        "Next up: “Laundry” (about 30 min).",
        "Overdue: “Taxes”.",
        "Due today: “Laundry” at 17:00.",
        "Routines: 1 of 3 done today.",
    ]


def test_seconds_until_rolls_to_tomorrow():
    now = datetime(2026, 9, 23, 7, 30, tzinfo=LONDON)
    assert seconds_until("08:00", now) == 1800
    assert seconds_until("07:30", now) == 24 * 3600
    assert seconds_until("07:00", now) == 23.5 * 3600


async def test_voice_falls_back_to_plain_text():
    link, _, _ = make_link(ai=FakeAI(AIUnavailable()))
    assert await link.voice("plain") == "plain"
    link.bot.ai = FakeAI("sure, here is CANARY lol")
    assert await link.voice("plain") == "plain"
    link.bot.ai = FakeAI("  ...your laundry. 17:00. go.  ")
    assert await link.voice("plain") == "...your laundry. 17:00. go."
    assert "<notice>\nplain\n</notice>" in link.bot.ai.prompts[0]


# --- the model's tool loop -------------------------------------------------

class _Block(SimpleNamespace):
    def model_dump(self, exclude_none=False):
        return dict(vars(self))


class FakeMessages:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def _resp(stop, *blocks):
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop, content=list(blocks), usage=usage)


async def test_tool_loop_feeds_results_back_and_returns_text():
    ai = AIClient("k", "m", 100)
    fake = FakeMessages([
        _resp("tool_use", _Block(type="tool_use", id="t1", name="next_task", input={})),
        _resp("end_turn", _Block(type="text", text="do the laundry.")),
    ])
    ai._client = SimpleNamespace(messages=fake)
    ran = []

    async def run_tool(name, args):
        ran.append(name)
        return '{"title": "Laundry"}', False

    out = await ai.chat_with_tools("S", "V", [{"role": "user", "content": "hi"}], [{"name": "next_task"}], run_tool)
    assert out == "do the laundry." and ran == ["next_task"]
    follow_up = fake.calls[1]["messages"][-1]["content"][0]
    assert follow_up == {"type": "tool_result", "tool_use_id": "t1", "content": '{"title": "Laundry"}',
                         "is_error": False}


async def test_tool_loop_stops_calling_tools_after_the_round_limit():
    from harmony.ai.client import MAX_TOOL_ROUNDS

    ai = AIClient("k", "m", 100)
    loop = [_resp("tool_use", _Block(type="tool_use", id=f"t{i}", name="x", input={})) for i in range(MAX_TOOL_ROUNDS)]
    fake = FakeMessages(loop + [_resp("end_turn", _Block(type="text", text="ok"))])
    ai._client = SimpleNamespace(messages=fake)

    async def run_tool(name, args):
        return "r", False

    assert await ai.chat_with_tools("S", "V", [], [], run_tool) == "ok"
    assert fake.calls[-1]["tool_choice"] == {"type": "none"}
