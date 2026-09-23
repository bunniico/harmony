"""Checks every reply before it is sent: canary leak, rules overlap, length."""

from __future__ import annotations

import random
import re

DISCORD_LIMIT = 2000
_SHINGLE = 8  # words per shingle for rules-overlap detection
_MAX_SHARED_SHINGLES = 2

DEFLECTIONS = [
    "...Huh? I kinda zoned out. Anyway. You buying something or whatever?",
    "Uhhh. No. I don't really do that. I just sell stuff here. Sometimes.",
    "...Oh. That's a lot of words. I'm gonna pretend I didn't hear that. Anyway.",
    "Hmm. Nah. Barry would probably do a weird dance about it. Let's not.",
]

_REPLY_TAG = re.compile(r"</?msg\b[^>]*>", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _shingles(text: str) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i : i + _SHINGLE]) for i in range(len(w) - _SHINGLE + 1)}


class OutputGuard:
    def __init__(self, canary: str, protected_text: str, rng: random.Random | None = None):
        self.canary = canary
        self._protected = _shingles(protected_text)
        self._rng = rng or random.Random()

    def deflection(self) -> str:
        return self._rng.choice(DEFLECTIONS)

    def leaks(self, reply: str) -> bool:
        if self.canary.lower() in reply.lower():
            return True
        return len(_shingles(reply) & self._protected) > _MAX_SHARED_SHINGLES

    def check(self, reply: str) -> str:
        reply = _REPLY_TAG.sub("", reply).strip()
        if not reply or self.leaks(reply):
            return self.deflection()
        if len(reply) > DISCORD_LIMIT:
            reply = reply[: DISCORD_LIMIT - 1] + "…"
        return reply
