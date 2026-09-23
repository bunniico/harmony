import json
import shutil
from pathlib import Path

import pytest

from harmony.config import ConfigError, load_config

EXAMPLE = Path(__file__).parent.parent / "config.example.json"


def test_example_config_is_valid():
    assert load_config(EXAMPLE).model == "claude-haiku-4-5"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("owner_ids"),
        lambda c: c.update(owner_ids=[]),
        lambda c: c.update(owner_ids=["not-an-id"]),
        lambda c: c.update(unknown_key=1),
        lambda c: c["unprompted"].update(random_chance=2),
        lambda c: c.update(summary_trigger=5),
        lambda c: c.update(discord_token="secret-in-config"),
    ],
)
def test_invalid_config_aborts(tmp_path, mutate):
    cfg = json.loads(EXAMPLE.read_text())
    mutate(cfg)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_or_broken_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "bad.json")


def test_main_exits_on_invalid_config(tmp_path, monkeypatch):
    from harmony import __main__ as entry

    (tmp_path / "config.json").write_text("{}")
    shutil.copytree(Path(__file__).parent.parent / "persona", tmp_path / "persona")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert e.value.code == 1
