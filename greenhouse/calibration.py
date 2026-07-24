from __future__ import annotations


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def raw_to_percent(
    raw_value: float | int | None,
    raw_dry: float | int,
    raw_wet: float | int,
) -> float | None:
    if raw_value is None:
        return None
    try:
        raw = float(raw_value)
        dry = float(raw_dry)
        wet = float(raw_wet)
    except (TypeError, ValueError):
        return None
    if dry == wet:
        return None
    return round(clamp((dry - raw) / (dry - wet) * 100.0, 0.0, 100.0), 1)


def raw_to_light_percent(
    raw_value: float | int | None,
    raw_dark: float | int,
    raw_bright: float | int,
) -> float | None:
    return raw_to_percent(raw_value, raw_dark, raw_bright)


def light_class(percent: float | None) -> str:
    if percent is None:
        return "unbekannt"
    if percent < 10:
        return "dunkel"
    if percent < 30:
        return "wenig_licht"
    if percent < 60:
        return "schatten"
    if percent < 80:
        return "hell"
    if percent < 95:
        return "sehr_hell"
    return "direkte_sonne"
