"""GPIO relay controller for heat/fan and charge-disable outputs."""
from __future__ import annotations

import logging
from threading import Lock

log = logging.getLogger(__name__)

RELAY_NAMES = frozenset({"heat_fan", "charge_disable"})


class RelayController:
    """Controls two GPIO-driven relays via the blinka digitalio stack.

    Falls back to stub (no-op) mode when GPIO hardware or blinka is not
    available, so the endpoint works for development on non-Pi hardware.
    """

    def __init__(self, heat_fan_gpio: int = 17, charge_disable_gpio: int = 27) -> None:
        self._gpio_map = {"heat_fan": heat_fan_gpio, "charge_disable": charge_disable_gpio}
        self._state: dict[str, bool] = {"heat_fan": False, "charge_disable": False}
        self._pins: dict[str, object] = {}
        self._lock = Lock()
        self._stub = False
        self._init_pins()

    def _init_pins(self) -> None:
        try:
            import board  # type: ignore[import]
            import digitalio  # type: ignore[import]
        except ImportError:
            log.warning("blinka not installed; relay controller running in stub mode")
            self._stub = True
            return
        try:
            for name, gpio in self._gpio_map.items():
                board_pin = getattr(board, f"D{gpio}", None)
                if board_pin is None:
                    raise RuntimeError(f"board.D{gpio} not found")
                pin = digitalio.DigitalInOut(board_pin)
                pin.direction = digitalio.Direction.OUTPUT
                pin.value = False
                self._pins[name] = pin
        except Exception as exc:  # noqa: BLE001
            log.warning("GPIO init failed (%s); relay controller running in stub mode", exc)
            self._stub = True

    def set(self, name: str, on: bool) -> None:
        if name not in RELAY_NAMES:
            raise ValueError(f"unknown relay {name!r}; valid names: {sorted(RELAY_NAMES)}")
        with self._lock:
            self._state[name] = on
            if not self._stub and name in self._pins:
                self._pins[name].value = on  # type: ignore[union-attr]

    def state(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._state)

    @property
    def is_stub(self) -> bool:
        return self._stub
