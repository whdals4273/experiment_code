# Parameter Analyzer 4155 Documentation

## Overview

`parameter_analyzer_4155.py` provides a Python driver for the Agilent/Keysight 4155-class Semiconductor Parameter Analyzer using the SCPI (Standard Commands for Programmable Instruments) interface over VISA.

This driver class `ParameterAnalyzer4155` extends the base `MeasurementDevice` class and provides high-level methods for configuring and operating the instrument in various measurement modes.

**Notes:**
- Supports SMU, VMU, and VSU channel keys with capability-aware command handling.
- Legacy helper APIs are still available but deprecated (see below).

---

## Class: ParameterAnalyzer4155

### Inheritance
```python
ParameterAnalyzer4155(MeasurementDevice)
```

### Constructor

```python
def __init__(
    self,
    resource_manager: visa.ResourceManager,
    resource_name: str,
    *,
    timeout: int = 20_000,
    debug_mode: bool = False,
) -> None
```

**Parameters:**
- `resource_manager`: PyVISA ResourceManager instance
- `resource_name`: VISA resource string (e.g., "GPIB0::17::INSTR")
- `timeout`: Communication timeout in milliseconds (default: 20,000)
- `debug_mode`: Enable debug logging (default: False)

---

## Initialization

### initialize()
Brings the instrument into a known-safe state and enables Service Request Queue (SRQ).

**Actions:**
- Resets the instrument (`*RST`)
- Sets all SMUs (1-4) to CONST function
- Clears event registers
- Enables standard event status register
- Enables SRQ for measurement completion detection

---

## Configuration APIs

### set_sweep()
```python
def set_sweep(
    self,
    channel_cfg: Mapping[str, Union[SweepChannelConfig, Dict[str, Any]]],
    *,
    integration: str = "MED",
) -> None
```
Configures sweep measurement channels on the sweep page.

**Parameters:**
- `channel_cfg`: Mapping of channel keys (`"SMU1"`, `"VMU1"`, `"VSU1"`, etc.) to `SweepChannelConfig` or dicts.
- `integration`: Integration time ("SHORT", "MED", "LONG").

**SweepChannelConfig fields:**
- `mode` (required): Channel mode (e.g., `"V"`, `"I"`, `"COMM"`).
- `func` (optional): Channel function (e.g., `"CONS"`, `"VAR1"`, `"VAR2"`).
- `names` (optional): `{ "vname": "...", "iname": "..." }`
- `const` (optional): `{ "value": float, "compliance": float }`
- `sweep` (optional): `{ "var": 1|2, "start": float, "step": float, "stop": float?, "points": int?, "compliance": float }`

**Notes:**
- VAR1 uses `start/stop/step`; VAR2 uses `start/points/step` or `start/stop/step`.
- VAR reassignments clear the previous channel by setting it to `CONS`.
- VMU channels ignore `FUNC`, `INAM`, and `CONST`/`SWEEP` settings (warnings logged).
- VSU channels support `MODE`/`FUNC`/`CONST`, but do not support `INAM`.

**Example:**
```python
analyzer.set_sweep(
    {
        "SMU1": {
            "mode": "V",
            "func": "VAR1",
            "names": {"vname": "VG", "iname": "IG"},
            "sweep": {
                "var": 1,
                "start": -1.0,
                "stop": 1.0,
                "step": 0.01,
                "compliance": 1e-6,
            },
        },
        "SMU2": {
            "mode": "V",
            "func": "CONS",
            "names": {"vname": "VD", "iname": "ID"},
            "const": {"value": 0.1, "compliance": 0.1},
        },
    },
    integration="SHORT",
)
```

---

### set_sampling()
```python
def set_sampling(
    self,
    timing: Union[SamplingTiming, Dict[str, Any]],
    channel_cfg: Mapping[str, Union[SamplingChannelConfig, Dict[str, Any]]],
) -> None
```
Configures sampling timing and channel setup.

**Timing fields:**
- `interval_s`: Sampling interval in seconds (clamped to 60 μs – 65.535 s).
- `points`: Number of sampling points (clamped to 1–10,001).
- `integration`: Integration time ("SHORT", "MED", "LONG").
- `mode`: Sampling mode (e.g., `"LIN"`).

**SamplingChannelConfig fields:**
- `mode` (required)
- `func` (optional)
- `names` (optional)
- `const` (optional)

**Example:**
```python
analyzer.set_sampling(
    {"interval_s": 1.0, "points": 3600, "integration": "MED", "mode": "LIN"},
    {
        "SMU1": {
            "mode": "V",
            "func": "CONS",
            "names": {"vname": "VG", "iname": "IG"},
            "const": {"value": 0.5, "compliance": 1e-9},
        }
    },
)
```

---

### set_stress()
```python
def set_stress(
    self,
    channel_cfg: Mapping[str, Union[StressChannelConfig, Dict[str, Any]]],
) -> None
```
Configures stress channels.

**StressChannelConfig fields:**
- `mode` (required)
- `func` (required)
- `const` (required): `{ "value": float, "compliance": float }`

**Note:** VMU channels ignore stress settings (warnings logged).

---

## Legacy Helpers (Deprecated)

The following helpers are still available but emit `DeprecationWarning` and will be removed in a future release:
- `set_sweep_common`
- `set_sweep_const_voltage`
- `set_sweep_var_voltage`
- `set_sampling_timing`
- `set_sampling_mode`
- `set_sampling_vmu`
- `set_stress_voltage`

Use `set_sweep`, `set_sampling`, and `set_stress` instead.

---

## Scenario Management

### save_scenario()
```python
def save_scenario(self, slot: int, mode: str) -> None
```
Saves current instrument configuration to memory slot.

**Parameters:**
- `slot`: Memory slot number (1-4)
- `mode`: Configuration type ("MES" for measurement, "STR" for stress)

---

### load_scenario()
```python
def load_scenario(self, slot: int, mode: str) -> None
```
Loads instrument configuration from memory slot.

**Parameters:**
- `slot`: Memory slot number (1-4)
- `mode`: Configuration type ("MES" for measurement, "STR" for stress)

**Use Case:** Quickly switch between pre-configured measurement setups without reconfiguring parameters.

---

## Measurement Execution

### sweep_single()
```python
def sweep_single(self, slot: Optional[int] = None) -> bool
```
Executes a single sweep measurement. If `slot` is provided, loads that scenario first.

**Parameters:**
- `slot`: Optional memory slot number (1-4)

**Returns:** `True` if measurement completed successfully, `False` otherwise.

---

### sweep_append()
```python
def sweep_append(self) -> bool
```
Executes an additional sweep and appends data to existing measurement.

**Returns:** `True` if measurement completed successfully, `False` otherwise.

**Use Case:** Multi-sweep measurements, averaging multiple sweeps.

---

### sampling_single()
```python
def sampling_single(
    self,
    *,
    on_started: Optional[Callable[[], None]] = None,
) -> bool
```
Initiates sampling measurement with configured interval and points.

**Parameters:**
- `on_started`: Optional callback invoked immediately after `:PAGE:SCON:SING` is sent

**Returns:** `True` if measurement completed successfully, `False` otherwise.

**Features:**
- Automatically queries sampling interval and points for progress bar
- Displays real-time progress during measurement
- Updates every 0.5 seconds

---

### sampling_single_multitask()
```python
def sampling_single_multitask(
    self,
    execution_queue: Optional[Union[Callable[[], Any], Iterable[Callable[[], Any]]]] = None,
    *,
    on_started: Optional[Callable[[], None]] = None,
    progress_poll_s: float = 0.5,
) -> bool
```
Initiates sampling measurement, then runs a sequential background task queue while the analyzer sampling continues.

**Parameters:**
- `execution_queue`: One callable or an iterable of callables to run sequentially on a dedicated background thread
- `on_started`: Optional callback invoked immediately after `:PAGE:SCON:SING` is sent
- `progress_poll_s`: Progress refresh / queue poll interval in seconds

**Returns:** `True` if measurement completed successfully, `False` otherwise.

**Features:**
- Preserves the existing analyzer-owned sampling flow
- Lets non-analyzer side work overlap with analyzer sampling time
- Waits for the queued work to finish before calling the analyzer completion wait
- Raises if any queued task fails, after analyzer completion is still handled

**Important:**
- Queued tasks must not access the same analyzer VISA session while sampling is active
- Queued tasks should avoid changing shared hardware state that would invalidate the active sampling measurement

---

### stress_single()
```python
def stress_single(
    self,
    *,
    duration_s: float = 0.0,
    slot: Optional[int] = None,
    show_progress: bool = True,
) -> bool
```
Applies stress for the specified duration. If `slot` is provided, loads that scenario first.

**Parameters:**
- `duration_s`: Stress duration in seconds
- `slot`: Optional memory slot number (1-4)
- `show_progress`: Toggle tqdm progress bar

**Returns:** `True` if stress completed successfully, `False` otherwise.

**Features:**
- Displays progress bar (tqdm) during stress application
- Updates every 0.5 seconds

---

### start_sampling_measurement()
```python
def start_sampling_measurement(
    self,
    *,
    on_started: Optional[Callable[[], None]] = None,
) -> bool
```
Alias for `sampling_single()`.

**Returns:** `True` if measurement completed successfully, `False` otherwise.

---

## Data Retrieval

### retrieve_data()
```python
def retrieve_data(self, var_name_list: List[str]) -> List[List[float]]
```
Retrieves measured data traces for specified variables.

**Parameters:**
- `var_name_list`: List of variable names to retrieve (e.g., ["VG", "IG", "VD", "ID"])

**Returns:** List of lists, where each inner list contains float values for one variable.

**Example:**
```python
data = analyzer.retrieve_data(["VG", "IG", "VD", "ID"])
vg_data = data[0]  # Gate voltage
ig_data = data[1]  # Gate current
vd_data = data[2]  # Drain voltage
id_data = data[3]  # Drain current
```

**Error Handling:**
- Warns if no response received for a variable
- Warns if response is empty
- Warns if data contains non-numeric values

---

## Context Manager Support

The class supports Python's context manager protocol:

```python
with ParameterAnalyzer4155(rm, "GPIB0::17::INSTR") as analyzer:
    analyzer.initialize()
    # ... perform measurements ...
# Automatically disconnects on exit
```

---

## Internal State Tracking

The class maintains internal state in `_active_vars`:
- Tracks which channel currently owns `VAR1` / `VAR2`
- Used to safely clear previous channel assignments when a VAR is reassigned

---

## Verification Methods

`MeasurementDevice.write_and_verify()` is available for SCPI round-trip checks. The analyzer driver mainly uses direct writes, with targeted verification (e.g., stress duration programming).

---

## Common Measurement Workflows

### 1. Transfer Characteristics (ID-VG)
```python
analyzer.initialize()

# Configure sweep channels
analyzer.set_sweep(
    {
        "SMU1": {
            "mode": "V",
            "func": "VAR1",
            "names": {"vname": "VG", "iname": "IG"},
            "sweep": {
                "var": 1,
                "start": -1.0,
                "stop": 1.0,
                "step": 0.01,
                "compliance": 1e-6,
            },
        },
        "SMU2": {
            "mode": "COMM",
            "func": "CONS",
            "names": {"vname": "VS", "iname": "IS"},
        },
        "SMU3": {
            "mode": "V",
            "func": "CONS",
            "names": {"vname": "VD", "iname": "ID"},
            "const": {"value": 0.1, "compliance": 0.1},
        },
    },
    integration="SHORT",
)

# Save and execute
analyzer.save_scenario(slot=1, mode="MES")
analyzer.sweep_single(slot=1)

# Retrieve data
data = analyzer.retrieve_data(["VG", "IG", "VD", "ID"])
```

### 2. BTI Stress with Periodic Sensing
```python
# Configure stress
analyzer.set_stress(
    {
        "SMU1": {
            "mode": "V",
            "func": "SYNC",
            "const": {"value": 2.0, "compliance": 1e-3},
        }
    }
)
analyzer.save_scenario(slot=1, mode="STR")

# Configure sensing sweep
analyzer.set_sweep(
    {
        "SMU1": {
            "mode": "V",
            "func": "VAR1",
            "names": {"vname": "VG", "iname": "IG"},
            "sweep": {
                "var": 1,
                "start": 0,
                "stop": 1.0,
                "step": 0.05,
                "compliance": 1e-6,
            },
        }
    }
)
analyzer.save_scenario(slot=2, mode="MES")

# Stress loop
for cycle in range(10):
    analyzer.stress_single(slot=1, duration_s=100.0)  # 100s stress
    analyzer.sweep_single(slot=2)  # Quick sensing
    data = analyzer.retrieve_data(["VG", "IG"])
    # Process data...
```

### 3. Retention/Leakage Measurement
```python
# Configure sampling
analyzer.set_sampling(
    {"interval_s": 1.0, "points": 3600, "integration": "MED", "mode": "LIN"},
    {
        "SMU1": {
            "mode": "V",
            "func": "CONS",
            "names": {"vname": "VG", "iname": "IG"},
            "const": {"value": 0.5, "compliance": 1e-9},
        }
    },
)

# Execute measurement (1 hour)
analyzer.start_sampling_measurement()

# Retrieve time-series data
data = analyzer.retrieve_data(["IG"])
leakage_current = data[0]
```

---

## Dependencies

- **pyvisa**: VISA communication library
- **tqdm**: Progress bar display
- **time**: Time-related functions

---

## Notes and Best Practices

1. **Compliance Settings**: Always set appropriate compliance limits to protect devices under test
2. **Integration Time**: Use "SHORT" for fast measurements, "LONG" for low-noise measurements
3. **VAR1 vs VAR2**: VAR1 uses `start/stop/step`, VAR2 uses `start/points/step` or `start/stop/step`
4. **Channel Capabilities**: VMUs do not support `FUNC`, `INAM`, `CONST`, or `SWEEP`; VSUs do not support `INAM`
5. **Sampling Timing**: Interval and points are clamped to instrument limits
6. **Memory Slots**: Use slots 1-4 to store different measurement configurations
7. **SRQ Mechanism**: The driver uses Service Request Queue for synchronization
8. **Progress Bars**: Long measurements display progress using tqdm

---

## Troubleshooting

### Common Issues

**Problem:** Timeout errors during measurement  
**Solution:** Increase timeout parameter in constructor or check instrument connection

**Problem:** Configuration not confirmed warnings  
**Solution:** Check SCPI command compatibility with your instrument model

**Problem:** No data returned  
**Solution:** Verify variable names match those configured in setup methods

**Problem:** Compliance errors  
**Solution:** Increase compliance limit or reduce applied voltage/current

---

## Version Information

- **Driver Class:** ParameterAnalyzer4155
- **Driver Version:** 2.0.0
- **Interface:** SCPI over VISA
- **Supported Instruments:** Agilent/Keysight 4155-class Parameter Analyzers
- **Python Version:** 3.7+ (uses type hints and f-strings)

---

## See Also

- Base class: `src.configs.measurement_device.MeasurementDevice`
- Related utilities: `src.utils.data_extract_4155.py`
- Example scenarios: `scenarios/` directory
- Obsolete drivers: `src/configs/analyzer/obsolete/`
