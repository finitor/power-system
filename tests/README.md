# Tests

Put software tests and hardware simulation tests here.

Run the unit tests with Python's built-in `unittest` runner:

```sh
.venv/bin/python -m unittest discover -s tests
```

Recommended early tests:

- Telemetry unit conversion.
- Sensor parsing.
- Control policy decisions.
- Safety interlocks.
- Startup behavior with missing sensors.
