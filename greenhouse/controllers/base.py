from __future__ import annotations

from typing import Any, Mapping, Protocol

from greenhouse.models import ControlContext, ControlDecision, SensorSnapshot


class Controller(Protocol):
    """Reine Reglerschnittstelle ohne Datei-, Netzwerk- oder GPIO-Zugriffe."""

    controller_id: str

    def decide(
        self,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
    ) -> ControlDecision:
        ...
