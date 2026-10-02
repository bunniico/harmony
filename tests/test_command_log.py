import logging
from types import SimpleNamespace

import pytest
from discord import app_commands

from harmony.features import commands


def fake(name, namespace, guild_id=555):
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name=name),
        data={"name": name},
        namespace=SimpleNamespace(**namespace),
        user=SimpleNamespace(id=42, __str__=lambda self: "bun"),
        guild_id=guild_id,
        channel_id=777,
    )


def test_describe_guild_command():
    line = commands.describe_command(fake("gel", {"tags": "cat", "count": 2}))
    assert line.startswith("/gel user=")
    assert "(42)" in line and "guild=555" in line and "channel=777" in line
    assert "tags='cat'" in line and "count='2'" in line


def test_describe_dm_and_no_args():
    line = commands.describe_command(fake("harmony status", {}, guild_id=None))
    assert " dm " in line and line.endswith("args: -")


def test_long_values_truncated():
    line = commands.describe_command(fake("gel", {"tags": "x" * 500}))
    assert "x" * 101 not in line


def test_directive_text_not_logged():
    line = commands.describe_command(fake("harmony directive set", {"text": "secret plan"}))
    assert "secret plan" not in line and "(redacted)" in line


async def test_register_logs_and_allows(caplog):
    tree = app_commands.CommandTree.__new__(app_commands.CommandTree)
    tree.add_command = lambda c: None
    tree.error = lambda f: f
    commands.register(tree)
    with caplog.at_level(logging.INFO, logger="harmony.features.commands"):
        assert await tree.interaction_check(fake("gel", {"tags": "cat"})) is True
    assert "Command /gel" in caplog.text
