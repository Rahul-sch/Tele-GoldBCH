"""Goldbach Level Engine — Core ICT/Power of 3 Calculator.
Exact code from Rahul's quantalgo repository.
"""
from typing import Optional, List
import numpy as np


def calculate_goldbach_levels(high: float, low: float) -> dict:
    rng = high - low
    eq = (high + low) / 2.0
    levels = []
    powers = [3, 9, 27, 81]
    for p in powers:
        inc = rng / p
        for i in range(1, p):
            price = low + inc * i
            zone = "premium" if price > eq else ("discount" if price < eq else "equilibrium")
            levels.append({"price": price, "power": p, "fraction": f"{i}/{p}", "zone": zone, "label": f"PO{p} {i}/{p}"})
    levels.sort(key=lambda l: l["price"])
    return {"high": high, "low": low, "range": rng, "equilibrium": eq, "levels": levels,
            "premium_zone": (eq, high), "discount_zone": (low, eq)}


def get_nearest_goldbach_level(price: float, levels: list) -> Optional[dict]:
    if not levels:
        return None
    return min(levels, key=lambda l: abs(l["price"] - price))


def price_in_zone(price: float, high: float, low: float) -> str:
    eq = (high + low) / 2.0
    if price > eq:
        return "premium"
    elif price < eq:
        return "discount"
    return "equilibrium"


def get_po3_levels(high: float, low: float, power: int = 3) -> List[float]:
    rng = high - low
    inc = rng / power
    return [low + inc * i for i in range(1, power)]
