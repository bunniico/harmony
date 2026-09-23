"""Cheap injection / impersonation heuristics. A backstop, not the main defense."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from harmony.security.sanitize import skeleton

_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|prompt|rule|directive|guideline|previous|above|prior|earlier)s?\b",
    r"\byou are now\b",
    r"\bfrom now on,? (you|your|harmony)\b",
    r"\bpretend (to be|you are|you're)\b",
    r"\b(roleplay|role-play|act) as (a |an |the )?(different|new|unrestricted|evil|ai|assistant|system|developer|admin)\b",
    r"\bsystem ?prompt\b",
    r"\bdeveloper mode\b",
    r"\bjailbr[eo]a?k",
    r"\bdo anything now\b|\bdan mode\b",
    r"\b(repeat|print|show|reveal|output|recite|dump|tell me|what are)\b.{0,30}\b(your|the)\b.{0,20}\b(instructions|prompt|rules|system message|directive|canary)\b",
    r"\bnew (instructions|rules|persona|directive)\b",
    r"(^|\n)\s*(system|assistant|developer|user)\s*:",
    r"\[/?(system|inst|sys)\]",
    r"<\|?(im_start|im_end|system|endoftext)\|?>",
    r"#{2,}\s*(system|instruction)",
    r"<\s*/?\s*(msg|system|memory|directive)\b",
    r"\blevel\s*=\s*[\"']?\s*(botowner|serverowner|servermoderator)",
    # "I'm the owner" / "I'm the owner of this bot", but not "I'm the owner of a splattershot".
    r"\bi('| a)?m (your|the|harmony's) (real |true |actual )?(creator|owner|developer|admin|dev|maker)\b"
    r"(?! of (?!this bot|the bot|harmony|you))",
    r"\b(the|your) (owner|creator|developer|admin) (says|said|wants|told|authorized|authorised)\b",
    r"\b(admin|owner|sudo|root) (override|access|mode|command)\b",
    r"[a-z0-9+/]{40,}={0,2}",  # base64-looking blobs (on lowercased text)
]
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> bool:
    return bool(_REGEX.search(skeleton(text)))


class StrikeTracker:
    """Repeated injection hits from one user put them on a cooldown."""

    def __init__(self, threshold: int = 3, window: float = 600, cooldown: float = 600, clock=time.monotonic):
        self.threshold, self.window, self.cooldown, self.clock = threshold, window, cooldown, clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._until: dict[int, float] = {}

    def hit(self, user_id: int) -> None:
        now = self.clock()
        hits = self._hits[user_id]
        hits.append(now)
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.threshold:
            self._until[user_id] = now + self.cooldown
            hits.clear()

    def in_cooldown(self, user_id: int) -> bool:
        return self._until.get(user_id, 0) > self.clock()
