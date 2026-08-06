from __future__ import annotations
import math
from typing import Iterable

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def softmax(values: Iterable[float]) -> list[float]:
    vals = list(values)
    maximum = max(vals)
    exps = [math.exp(v - maximum) for v in vals]
    total = sum(exps)
    return [v / total for v in exps]

def normalize(values: Iterable[float], floor: float = 0.0) -> list[float]:
    vals = [max(floor, float(v)) for v in values]
    total = sum(vals)
    if total <= 0:
        return [1.0 / len(vals)] * len(vals)
    return [v / total for v in vals]

def percentage(values: Iterable[float]) -> list[float]:
    return [round(v * 100, 2) for v in values]
