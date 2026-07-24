from __future__ import annotations

from datetime import datetime
from typing import Iterable


def linear_trend_per_minute(
    observations: Iterable[tuple[datetime, float | None]],
    *,
    now: datetime,
    window_seconds: float,
    minimum_span_seconds: float,
) -> float:
    """Berechnet eine lineare Steigung und ignoriert doppelte Zeitstempel."""

    values_by_timestamp: dict[datetime, float] = {}
    for timestamp, value in observations:
        if value is None:
            continue
        age_seconds = (now - timestamp).total_seconds()
        if 0 <= age_seconds <= window_seconds:
            values_by_timestamp[timestamp] = float(value)

    points = sorted(values_by_timestamp.items())
    if len(points) < 2:
        return 0.0
    span_seconds = (points[-1][0] - points[0][0]).total_seconds()
    if span_seconds < minimum_span_seconds:
        return 0.0

    start = points[0][0]
    x_values = [
        (timestamp - start).total_seconds() / 60.0
        for timestamp, _ in points
    ]
    y_values = [value for _, value in points]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0:
        return 0.0
    numerator = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values)
    )
    return numerator / denominator
