"""patterns.py - helpers that generate / transform digital step sequences (lists of 0/1)."""
import random
from typing import List


def clock(n: int, high: int, low: int, phase: int = 0, start_high: bool = True) -> List[int]:
    """Square wave: `high` steps at 1, `low` steps at 0, shifted by `phase` steps."""
    high, low = max(1, high), max(1, low)
    per = high + low
    out = []
    for i in range(n):
        k = (i + phase) % per
        v = 1 if k < high else 0
        out.append(v if start_high else 1 - v)
    return out


def pulse(n: int, start: int, width: int, level: int = 1) -> List[int]:
    """Single pulse of `width` steps starting at step `start` on a background of the opposite level."""
    return [level if start <= i < start + width else 1 - level for i in range(n)]


def constant(n: int, level: int) -> List[int]:
    return [1 if level else 0] * n


def rand(n: int, density: float = 0.5, seed: int | None = None) -> List[int]:
    r = random.Random(seed)
    return [1 if r.random() < density else 0 for _ in range(n)]


def to_text(steps: List[int]) -> str:
    return "".join("1" if s else "0" for s in steps)


def edges(steps: List[int]) -> int:
    return sum(1 for a, b in zip(steps, steps[1:]) if a != b)
