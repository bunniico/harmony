"""Decides when Harmony speaks: always on direct triggers, sometimes unprompted, never in quiet channels."""

from __future__ import annotations

import random
import re
import time
from typing import Callable, Iterable

from harmony.security.sanitize import skeleton

NAME = "harmony"


def contains_word(text: str, word: str) -> bool:
    """Whole-word, case-insensitive match after normalization."""
    return re.search(rf"(?<![\w]){re.escape(skeleton(word))}(?![\w])", skeleton(text)) is not None


def channel_name_tokens(name: str) -> set[str]:
    """Split on anything that isn't a letter or digit (-, _, spaces, emoji...)."""
    return {t for t in re.split(r"[^\w]+|_", skeleton(name)) if t}


def is_quiet_channel(
    channel_ids: Iterable[int], channel_names: Iterable[str], quiet_channel_ids: Iterable[int], quiet_words: Iterable[str]
) -> bool:
    """`channel_ids`/`channel_names` include a thread's parent, so threads in #vent are quiet too."""
    if set(channel_ids) & set(quiet_channel_ids):
        return True
    words = {w.lower() for w in quiet_words}
    return any(channel_name_tokens(n) & words for n in channel_names)


class UnpromptedTrigger:
    def __init__(
        self,
        *,
        name_mention: bool,
        random_chance: float,
        cooldown_seconds: float,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name_mention = name_mention
        self.random_chance = random_chance
        self.cooldown = cooldown_seconds
        self.rng, self.clock = rng, clock
        self._last: dict[int, float] = {}

    def reason(self, channel_id: int, text: str, flagged: bool, keywords: Iterable[str]) -> str | None:
        """Returns why Harmony would chime in, or None. Checked in order: name, keyword, random."""
        if flagged:
            return None
        last = self._last.get(channel_id)
        if last is not None and self.clock() - last < self.cooldown:
            return None
        if self.name_mention and contains_word(text, NAME):
            return "name"
        if any(contains_word(text, k) for k in keywords):
            return "keyword"
        if self.rng() < self.random_chance:
            return "random"
        return None

    def mark(self, channel_id: int) -> None:
        self._last[channel_id] = self.clock()


def should_respond(
    *, quiet: bool, direct: bool, unprompted_reason: Callable[[], str | None]
) -> str | None:
    """Quiet channels always win, even over direct mentions."""
    if quiet:
        return None
    if direct:
        return "direct"
    return unprompted_reason()
