"""Helpers for reading Tado heat pump optimizer API responses."""
from __future__ import annotations

from datetime import datetime
from typing import Any

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def get_path(data: dict[str, Any] | None, path: str) -> Any:
    """Return a nested value by dotted path, or None if any step is missing."""
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def to_float(value: Any) -> float | None:
    """Convert API values (often strings like "48.0") to float."""
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def active_block(schedule: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """Return the schedule block active at `now` ({start, end, setpoint})."""
    if not isinstance(schedule, dict):
        return None
    blocks = schedule.get(WEEKDAYS[now.weekday()])
    if not isinstance(blocks, list):
        return None
    current = now.strftime("%H:%M")
    for block in blocks:
        start = block.get("start", "00:00")
        end = block.get("end", "24:00")
        if start <= current < end:
            return block
    return None


def active_setpoint_type(schedule: dict[str, Any] | None, now: datetime) -> str | None:
    """Return TARGET / FALLBACK for the block active at `now`."""
    block = active_block(schedule, now)
    return get_path(block, "setpoint.setpointType") if block else None


def active_setpoint(schedule: dict[str, Any] | None, now: datetime) -> float | None:
    """Return the temperature the schedule asks for at `now`."""
    block = active_block(schedule, now)
    if not block:
        return None
    # A block may carry its own value; otherwise it points at target/fallback
    own = to_float(get_path(block, "setpoint.setpointValue"))
    if own is not None:
        return own
    kind = get_path(block, "setpoint.setpointType")
    if kind == "TARGET":
        return to_float(schedule.get("targetSetpointValue"))
    if kind == "FALLBACK":
        return to_float(schedule.get("fallbackSetpointValue"))
    return None


def next_change(schedule: dict[str, Any] | None, now: datetime) -> str | None:
    """Return HH:MM when the active block ends today (None at end of day)."""
    block = active_block(schedule, now)
    if not block:
        return None
    end = block.get("end")
    return None if end in (None, "24:00") else end
