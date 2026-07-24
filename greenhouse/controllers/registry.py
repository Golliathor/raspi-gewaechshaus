from __future__ import annotations

from greenhouse.controllers.base import Controller
from greenhouse.controllers.adaptive_local import AdaptiveLocalController
from greenhouse.controllers.adaptive_weather import AdaptiveWeatherController
from greenhouse.controllers.baseline_fixed import BaselineFixedController
from greenhouse.controllers.baseline_hysteresis import BaselineHysteresisController
from greenhouse.controllers.legacy import LegacyController


_CONTROLLERS = {
    BaselineFixedController.controller_id: BaselineFixedController,
    BaselineHysteresisController.controller_id: BaselineHysteresisController,
    AdaptiveLocalController.controller_id: AdaptiveLocalController,
    AdaptiveWeatherController.controller_id: AdaptiveWeatherController,
    LegacyController.controller_id: LegacyController,
}


def registered_controller_ids() -> tuple[str, ...]:
    return tuple(sorted(_CONTROLLERS))


def create_controller(controller_id: str) -> Controller:
    try:
        controller_class = _CONTROLLERS[controller_id]
    except KeyError as error:
        available = ", ".join(registered_controller_ids())
        raise ValueError(
            f"Unbekannter Controller {controller_id!r}; verfügbar: {available}"
        ) from error
    return controller_class()
