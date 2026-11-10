# Power Supply Control Documentation

## Overview

The `power_supply` module provides Python drivers for multi-channel DC power supplies used in measurement setups. It features a unified abstract base class (`BasePowerSupply`) and two concrete implementations for popular laboratory instruments: the **Keithley 2230G** and **Keysight EDU36311A**.

Both instruments provide triple-output (3-channel) capability with independent voltage and current programming on each channel.

---

## Architecture

### Class Hierarchy

```
MeasurementDevice (base)
    └── BasePowerSupply (abstract)
            ├── PowerSupply2230G (Keithley)
            └── PowerSupplyEDU36311A (Keysight)
```

### Design Philosophy

- **Abstraction**: `BasePowerSupply` defines a uniform SCPI interface, allowing seamless switching between different power supply models
- **Low-level + High-level**: Separate methods for per-channel SCPI commands and batch multi-channel operations
- **Verification**: Optional readback verification after programming to ensure settings took effect
- **Logging**: Integrated logging from `MeasurementDevice` for full command traceability

---

## BasePowerSupply (Abstract Base Class)

### Purpose

`BasePowerSupply` defines the contract for all power supply implementations. It provides:
- Abstract methods (to be implemented by subclasses)
- Concrete utility methods (e.g., `_verify_channel`)
- Channel selection abstraction (device-dependent)

### Constructor

```python
BasePowerSupply(
    resource_manager: visa.ResourceManager,
    resource_name: str,
    *,
    timeout: int = 1_000,
    delay_time: Optional[float] = None,
    debug_mode: bool = False,
)
```

**Parameters:**
- `resource_manager`: PyVISA ResourceManager instance
- `resource_name`: VISA resource string (e.g., `"GPIB0::5::INSTR"`)
- `timeout`: SCPI command timeout in milliseconds (default: 1000 ms)
- `delay_time`: Settle time between operations in seconds (default: 0.5 s)
- `debug_mode`: Enable debug-level logging

**Tolerances (verification):**
- `tol_v`: Voltage tolerance = 2 mV (0.002 V)
- `tol_i`: Current tolerance = 2 mA (0.002 A)

### Abstract Methods (for subclasses to implement)

#### Low-level SCPI Interface

```python
@abstractmethod
def select_channel(self, chan: int) -> None:
    """Select the active output channel (1, 2, or 3)."""

@abstractmethod
def set_voltage(self, volts: float) -> None:
    """Program voltage setpoint for the active channel."""

@abstractmethod
def set_current(self, amps: float) -> None:
    """Program current compliance limit for the active channel."""

@abstractmethod
def read_setpoints(self) -> Tuple[float, float]:
    """Return (voltage, current) setpoints of active channel."""

@abstractmethod
def read_measpoints(self) -> Tuple[float, float]:
    """Return measured (voltage, current) of active channel."""

@abstractmethod
def output_enable(self, enable: bool = True) -> None:
    """Enable or disable the active channel output."""
```

#### High-level Multi-channel Helpers

```python
@abstractmethod
def enable_outputs(self, channels: Optional[Iterable[int]] = None) -> None:
    """Enable specified channels (default: all)."""

@abstractmethod
def disable_outputs(self, channels: Optional[Iterable[int]] = None) -> None:
    """Disable specified channels (default: all)."""

@abstractmethod
def zero_voltages(self, channels: Optional[Iterable[int]] = None) -> None:
    """Set all outputs to 0 V on specified channels."""

@abstractmethod
def set_voltages(
    self,
    voltages: Dict[int, float],
    *,
    verify: bool = True,
) -> None:
    """Program multiple channel voltages with optional verification."""

@abstractmethod
def set_compliance_currents(
    self,
    currents: Dict[int, float],
    *,
    verify: bool = True,
) -> None:
    """Program multiple channel current limits with optional verification."""
```

### Utility Methods

#### `_verify_channel(ch, v_expected, i_expected)`

Verifies that programmed values match measured values within tolerance.

```python
def _verify_channel(
    self,
    ch: int,
    v_expected: Optional[float],
    i_expected: Optional[float],
) -> None:
    """
    Verify measured channel values against expected setpoints.
    
    Raises RuntimeError if deviation exceeds tolerance.
    """
```

**Usage:**
```python
psu.select_channel(1)
psu.set_voltage(2.5)
psu._verify_channel(1, v_expected=2.5, i_expected=None)  # Verify voltage
```

---

## PowerSupply2230G (Keithley)

### Overview

SCPI driver for the **Keithley 2230G** triple-output DC power supply. Uses `:INST:SEL` channel selection model.

**Key Features:**
- 3 independent channels (CH1, CH2, CH3)
- Channel selection via SCPI command (cached to reduce traffic)
- Default settle time: 0.5 seconds

### Usage Example

```python
import pyvisa
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G

# Create resource manager and initialize PSU
rm = pyvisa.ResourceManager()
psu = PowerSupply2230G(rm, "GPIB0::5::INSTR")

# Manual connection
psu.connect()

try:
    # Set voltages on multiple channels
    psu.set_voltages({
        1: 1.2,    # CH1 → 1.2 V
        2: 3.3,    # CH2 → 3.3 V
        3: 5.0,    # CH3 → 5.0 V
    }, verify=True)
    
    # Set current limits
    psu.set_compliance_currents({
        1: 0.5,    # CH1 → 500 mA limit
        2: 1.0,    # CH2 → 1 A limit
        3: 0.2,    # CH3 → 200 mA limit
    })
    
    # Enable outputs
    psu.enable_outputs()
    
    # Read measured values
    psu.select_channel(1)
    v_meas, i_meas = psu.read_measpoints()
    print(f"CH1: {v_meas:.3f} V, {i_meas:.3f} A")
    
finally:
    psu.disconnect()
```

### Context Manager (Recommended)

```python
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G
import pyvisa

rm = pyvisa.ResourceManager()

with PowerSupply2230G(rm, "GPIB0::5::INSTR") as psu:
    psu.set_voltages({1: 1.2, 2: 3.3, 3: 5.0})
    psu.enable_outputs()
    
    # Measurements...
    
    # Automatic disconnect on exit
```

### Key Methods

#### `select_channel(chan: int)`

Select output channel. Redundant selections are cached and ignored.

```python
psu.select_channel(1)  # Select CH1
psu.set_voltage(2.5)   # Set CH1 to 2.5 V
psu.select_channel(2)  # Select CH2
psu.set_voltage(3.3)   # Set CH2 to 3.3 V
```

#### `set_voltage(volts: float)`

Program voltage on the **currently selected channel**.

```python
psu.select_channel(1)
psu.set_voltage(1.8)  # CH1 → 1.8 V
```

#### `set_current(amps: float)`

Program current compliance on the **currently selected channel**.

```python
psu.select_channel(2)
psu.set_current(0.5)  # CH2 → 500 mA limit
```

#### `read_setpoints() → (float, float)`

Return *programmed* (voltage, current) setpoints of the currently selected channel.

```python
psu.select_channel(1)
v_set, i_set = psu.read_setpoints()
print(f"Setpoints: {v_set} V, {i_set} A")
```

#### `read_measpoints() → (float, float)`

Return *measured* (voltage, current) values of the currently selected channel.

```python
psu.select_channel(1)
v_meas, i_meas = psu.read_measpoints()
print(f"Measured: {v_meas} V, {i_meas} A")
```

#### `output_enable(enable: bool = True)`

Enable or disable output on the **currently selected channel**.

```python
psu.select_channel(1)
psu.output_enable(True)   # Enable CH1
psu.output_enable(False)  # Disable CH1
```

#### `enable_outputs(channels=None)`

Enable multiple channels at once (default: all).

```python
psu.enable_outputs()           # Enable CH1, CH2, CH3
psu.enable_outputs([1, 3])     # Enable CH1 and CH3 only
```

#### `disable_outputs(channels=None)`

Disable multiple channels at once (default: all).

```python
psu.disable_outputs()          # Disable all channels
```

#### `zero_voltages(channels=None)`

Set outputs to 0 V on multiple channels.

```python
psu.zero_voltages()            # Set all channels to 0 V
psu.zero_voltages([1, 2])      # Set CH1, CH2 to 0 V
```

#### `set_voltages(voltages: Dict[int, float], verify=True)`

Program multiple channel voltages with optional verification.

```python
psu.set_voltages({
    1: 1.2,
    2: 3.3,
    3: 5.0,
}, verify=True)  # Verify after each channel
```

#### `set_compliance_currents(currents: Dict[int, float], verify=True)`

Program multiple channel current limits with optional verification.

```python
psu.set_compliance_currents({
    1: 0.5,    # 500 mA
    2: 1.0,    # 1 A
    3: 0.2,    # 200 mA
}, verify=True)
```

---

## PowerSupplyEDU36311A (Keysight)

### Overview

SCPI driver for the **Keysight EDU36311A** triple-output DC power supply. Uses channel-list notation `(@1)`, `(@2)`, `(@3)` in SCPI commands rather than `:INST:SEL`.

**Key Features:**
- 3 independent channels (CH1, CH2, CH3)
- Channel selection emulated in software (no SCPI command sent)
- Commands embed channel number as `(@chan)` suffix
- Default settle time: 1.0 second (longer than 2230G)

### Usage Example

```python
import pyvisa
from src.configs.power_supply.power_supply_EDU36311A import PowerSupplyEDU36311A

rm = pyvisa.ResourceManager()

with PowerSupplyEDU36311A(rm, "GPIB0::12::INSTR") as psu:
    # Set voltages and currents
    psu.set_voltages({
        1: 1.2,
        2: 3.3,
        3: 5.0,
    }, verify=True)
    
    psu.set_compliance_currents({
        1: 0.5,
        2: 1.0,
        3: 0.2,
    })
    
    # Enable all outputs
    psu.enable_outputs()
    
    # Read measurements
    psu.select_channel(1)
    v, i = psu.read_measpoints()
    print(f"CH1: {v:.3f} V, {i:.6f} A")
```

### Key Methods

All methods are identical to `PowerSupply2230G`, but internally use channel-list notation.

```python
psu.select_channel(1)
psu.set_voltage(2.5)      # SCPI: VOLT 2.5, (@1)
psu.set_current(0.5)      # SCPI: CURR 0.5, (@1)
v, i = psu.read_measpoints()  # SCPI: MEAS:VOLT? (@1) / MEAS:CURR? (@1)
```

### Differences from PowerSupply2230G

| Aspect | 2230G | EDU36311A |
|--------|-------|-----------|
| **Channel Selection** | `:INST:SEL CH1` command | Software-only (`@1` in command) |
| **Voltage Read** | `:SOUR:VOLT?` | `VOLT? (@1)` |
| **Current Read** | `:SOUR:CURR?` | `CURR? (@1)` |
| **Settle Time** | 0.5 s | 1.0 s |
| **SCPI Syntax** | Colon-prefixed | Space-separated |

---

## Common Usage Patterns

### Pattern 1: Device Characterization (IV Sweep)

```python
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G
from src.configs.analyzer.parameter_analyzer_4155 import ParameterAnalyzer4155
import pyvisa

rm = pyvisa.ResourceManager()

with PowerSupply2230G(rm, "GPIB0::5::INSTR") as psu, \
     ParameterAnalyzer4155(rm, "GPIB0::17::INSTR") as pa:
    
    # Sweep gate voltage, measure drain current
    voltages = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    
    for vg in voltages:
        psu.select_channel(1)
        psu.set_voltage(vg)
        
        # Wait for settling
        import time
        time.sleep(0.5)
        
        # Measure current
        psu.select_channel(1)
        v_read, i_read = psu.read_measpoints()
        print(f"Vg={vg:.2f} V → {i_read*1e6:.2f} µA")
    
    psu.disable_outputs()
```

### Pattern 2: Multi-supply Setup

```python
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G
import pyvisa

rm = pyvisa.ResourceManager()

# Two separate power supplies
psu1 = PowerSupply2230G(rm, "GPIB0::5::INSTR")
psu2 = PowerSupply2230G(rm, "GPIB0::6::INSTR")

try:
    psu1.connect()
    psu2.connect()
    
    # Configure each independently
    psu1.set_voltages({1: 1.2, 2: 3.3})
    psu2.set_voltages({1: 5.0, 2: 2.5})
    
    psu1.enable_outputs()
    psu2.enable_outputs()
    
    # Monitor supplies
    psu1.select_channel(1)
    v1, i1 = psu1.read_measpoints()
    
    psu2.select_channel(1)
    v2, i2 = psu2.read_measpoints()
    
    print(f"PSU1 CH1: {v1:.3f} V, {i1:.3f} A")
    print(f"PSU2 CH1: {v2:.3f} V, {i2:.3f} A")
    
finally:
    psu1.disconnect()
    psu2.disconnect()
```

### Pattern 3: Voltage Ramp with Soft Start

```python
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G
import pyvisa
import time

rm = pyvisa.ResourceManager()

with PowerSupply2230G(rm, "GPIB0::5::INSTR") as psu:
    # Soft-start: ramp from 0 to 3.3 V in 100 mV steps
    target_voltage = 3.3
    step_size = 0.1
    step_delay = 0.2  # seconds
    
    psu.select_channel(1)
    psu.set_current(0.5)  # Set current limit first
    
    current_voltage = 0.0
    while current_voltage < target_voltage:
        current_voltage = min(current_voltage + step_size, target_voltage)
        psu.set_voltage(current_voltage)
        time.sleep(step_delay)
    
    psu.output_enable(True)
    print(f"Ramped to {target_voltage} V")
    
    # Let it settle
    time.sleep(1.0)
    
    # Verify
    v, i = psu.read_measpoints()
    print(f"Final: {v:.3f} V, {i:.3f} A")
```

---

## Error Handling

### Verification Failures

If `verify=True` is set and measured values deviate beyond tolerance, a `RuntimeError` is raised:

```python
try:
    psu.set_voltages({1: 1.2}, verify=True)
except RuntimeError as e:
    print(f"Verification failed: {e}")
    # Handle error (e.g., retry, abort experiment)
```

### Connection Errors

```python
import pyvisa
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G

rm = pyvisa.ResourceManager()

try:
    psu = PowerSupply2230G(rm, "GPIB0::999::INSTR")  # Invalid address
    psu.connect()
except Exception as e:
    print(f"Connection failed: {e}")
```

### Missing Channel Selection (EDU36311A)

```python
psu = PowerSupplyEDU36311A(rm, "GPIB0::12::INSTR")
psu.connect()

try:
    psu.set_voltage(2.5)  # Error! No channel selected yet
except RuntimeError as e:
    print(f"Error: {e}")  # "No channel selected. Call select_channel() first."
```

---

## Logging

Both implementations inherit logging from `MeasurementDevice`. Log output examples:

| Level | Message |
|-------|---------|
| **INFO** | `Connected to GPIB0::5::INSTR (Keithley 2230G)` |
| **INFO** | `TX → :INST:SEL CH1` |
| **INFO** | `TX → :SOUR:VOLT 2.5` |
| **INFO** | `RX ← 2.501` |
| **INFO** | `All selected channels enabled` |
| **DEBUG** | `Selected channel CH1` |
| **DEBUG** | `CH1 → Set voltage 1.200 V` |
| **ERROR** | `CH1 voltage verify failed (read 1.195 V, expected 1.200 V)` |

---

## Integration with Scenarios

Power supply control is used in:
- `scenarios/basic_measure/run.py` – Retention measurements
- `scenarios/IGZO_stability/run.py` – BTI stress testing
- `scenarios/IGZO_leakage/run.py` – Leakage current measurements
- `scenarios/demo/demo_power_supply_test.py` – PSU connectivity test

---

## Switching Between Models

To switch from 2230G to EDU36311A, change only the import and class name:

```python
# Before (Keithley)
from src.configs.power_supply.power_supply_2230G import PowerSupply2230G
psu = PowerSupply2230G(rm, "GPIB0::5::INSTR")

# After (Keysight)
from src.configs.power_supply.power_supply_EDU36311A import PowerSupplyEDU36311A
psu = PowerSupplyEDU36311A(rm, "GPIB0::12::INSTR")

# All method calls remain identical!
```

---

## Specifications Reference

### Keithley 2230G
- **Channels**: 3 (Ch1: 0–30V/0–2A, Ch2–3: 0–6V/0–5A)
- **Accuracy**: ±1% of setting
- **Resolution**: 1 mV / 1 mA
- **Interface**: GPIB/LAN
- **SCPI Command Style**: Colon-prefixed (`:INST:SEL`, `:SOUR:VOLT`, etc.)

### Keysight EDU36311A
- **Channels**: 3 (Ch1–3: 0–25V/0–1A)
- **Accuracy**: ±0.8% of range
- **Resolution**: 1 mV / 1 mA
- **Interface**: GPIB/USB
- **SCPI Command Style**: Space-separated, channel-list notation (`VOLT`, `CURR`, `(@1)`)

---

## Related Files

- **Base Class**: [base_power_supply.py](base_power_supply.py)
- **Keithley Driver**: [power_supply_2230G.py](power_supply_2230G.py)
- **Keysight Driver**: [power_supply_EDU36311A.py](power_supply_EDU36311A.py)
- **Base Class**: [src/configs/measurement_device.py](../measurement_device.py)
- **Logging**: [src/utils/logger.py](../../utils/logger.py)

---

## References

- Keithley 2230G User Manual
- Keysight EDU36311A User Manual
- PyVISA Documentation: [pyvisa.readthedocs.io](https://pyvisa.readthedocs.io/)
- SCPI Standard: IEEE 488.2
