# Pulse Generator Control Documentation

## Overview

The `pulse_generator` module provides Python drivers for arbitrary/function pulse generators used in measurement setups. It features an abstract base class (`BasePulseGenerator`) and two concrete implementations for popular laboratory instruments: the **Keysight/Agilent 81110A** and **Tektronix AFG31000**.

Both instruments support dual-channel pulse generation with independent timing, voltage levels, triggering, and burst modes.

---

## Architecture

### Class Hierarchy

```
MeasurementDevice (base)
    └── BasePulseGenerator (abstract)
            ├── PulseGenerator81110A(81115A) (Keysight/Agilent)
            └── PulseGeneratorAFG31000 (Tektronix)
```

### Design Philosophy

- **Dictionary-based Configuration**: Settings are passed as Python dictionaries with flexible key aliases
- **Unified Interface**: Both pulse and pattern modes abstracted through a common API
- **Low-level + High-level**: Per-channel SCPI commands and batch operations
- **Smart Parsing**: Automatic key normalization, alias resolution, and parameter swapping
- **Verification**: Optional readback verification after programming
- **Logging**: Full SCPI command traceability through inherited logging

---

## BasePulseGenerator (Abstract Base Class)

### Purpose

`BasePulseGenerator` defines the contract for all pulse generator implementations. It provides:
- Abstract methods for channel configuration, triggering, and burst control
- Utility methods for verification and configuration management
- Consistent channel-centric programming model

### Constructor

```python
BasePulseGenerator(
    resource_manager: pyvisa.ResourceManager,
    resource_name: str,
    *,
    timeout: int = 5_000,
    debug_mode: bool = False,
)
```

**Parameters:**
- `resource_manager`: PyVISA ResourceManager instance
- `resource_name`: VISA resource string (e.g., `"GPIB0::10::INSTR"`)
- `timeout`: SCPI command timeout in milliseconds (default: 5000 ms)
- `debug_mode`: Enable debug-level logging

### Abstract Methods

#### Channel Configuration

```python
@abstractmethod
def set_channel(
    self,
    channel: int,
    settings: Dict[str, Any],
    *,
    verify: bool = True,
) -> None:
    """
    Configure pulse parameters on a channel using a settings dictionary.
    
    Parameters
    ----------
    channel : int
        Channel index (typically 1 or 2).
    settings : dict
        Parameter mapping, e.g., {"period": 100e-9, "width": 10e-9, ...}
    verify : bool
        If True, readback and verify settings after programming.
    """

@abstractmethod
def read_setpoints(self, channel: int) -> Dict[str, float]:
    """
    Read programmed parameters from a channel.
    
    Returns
    -------
    dict[str, float]
        Mapping of parameter names → values in SI units.
    """

@abstractmethod
def enable_channel(self, channel: int, enable: bool = True) -> None:
    """Enable or disable pulse output on a channel."""
```

#### Advanced Features

```python
@abstractmethod
def set_math_mode(self, enable: bool) -> None:
    """Enable/disable channel combination (e.g., CH1 + CH2)."""

@abstractmethod
def set_trigger(self, trigger_source: str) -> None:
    """Configure trigger source (e.g., "BUS", "EXT", "INT")."""

@abstractmethod
def set_burst(self, channel: int, burst: Optional[int]) -> None:
    """
    Configure burst mode.
    
    Parameters
    ----------
    burst : int or None
        - int  → number of pulses in burst
        - None → disable burst
    """

@abstractmethod
def trigger_pulse(self) -> None:
    """Issue a software trigger command."""

@abstractmethod
def save_settings(self, name: str) -> None:
    """Save instrument configuration to memory."""

@abstractmethod
def recall_settings(self, name: str) -> None:
    """Recall previously saved configuration."""

@abstractmethod
def _verify_channel(
    self,
    channel: int,
    expected: Dict[str, Optional[float]],
) -> None:
    """Verify channel parameters against expected values."""
```

---

## PulseGenerator81110A (Keysight/Agilent)

### Overview

SCPI driver for the **Keysight/Agilent 81110A** and **81115A** dual-channel pulse pattern generators.

**Key Features:**
- 2 independent channels (CH1, CH2)
- Advanced pulse mode with period, width, delay, rise/fall times
- Math mode for channel combination (CH1 + CH2)
- Burst and trigger control
- Automatic device detection (81150 variant support)
- Settings memory management

### Usage Example

```python
import pyvisa
from src.configs.pulse_generator.pulse_generator_81110A import PulseGenerator81110A

rm = pyvisa.ResourceManager()

with PulseGenerator81110A(rm, "GPIB0::10::INSTR") as pg:
    # Initialize device
    pg.initialize()
    
    # Configure channel 1: 100 ns period, 10 ns width
    pg.set_channel(1, {
        "period": 100e-9,
        "width": 10e-9,
        "delay": 5e-9,
        "high": 2.0,      # volts
        "low": 0.0,
        "rise": 1e-9,     # seconds
        "fall": 1e-9,
    }, verify=True)
    
    # Configure channel 2
    pg.set_channel(2, {
        "period": 100e-9,
        "width": 50e-9,
        "high": 3.3,
        "low": 0.0,
    })
    
    # Enable outputs
    pg.enable_channel(1, True)
    pg.enable_channel(2, True)
    
    # Set burst mode on CH1: 10 pulses
    pg.set_burst(1, burst=10)
    
    # Trigger manually
    pg.trigger_pulse()
    
    # Disable burst
    pg.set_burst(1, burst=None)
```

### Key Methods

#### `initialize()`

Initialize the pulse generator to a known state. Must be called before other operations.

```python
pg = PulseGenerator81110A(rm, "GPIB0::10::INSTR")
pg.initialize()
```

**Actions:**
- Connects to device
- Detects model (81110A vs 81150A)
- Resets and clears status
- Configures voltage mode, disables external width mode
- Sets up trigger and arm modes
- Sets output impedance (50Ω internal, 1MΩ external)

#### `set_channel(channel, settings, verify=True)`

Configure pulse parameters on a channel.

```python
pg.set_channel(1, {
    "period": 100e-9,        # Pulse repetition period (s)
    "width": 10e-9,          # Pulse width (s)
    "delay": 5e-9,           # Leading delay (s)
    "high": 2.5,             # High voltage level (V)
    "low": 0.0,              # Low voltage level (V)
    "rise": 1.5e-9,          # Rising edge time (s)
    "fall": 1.5e-9,          # Falling edge time (s)
    "zsource": 50,           # Source impedance (Ω)
    "zload": 1e6,            # Load impedance (Ω)
    "polarity": "NORM",      # "NORM" or "INV"
}, verify=True)
```

**Supported Keys (with aliases):**

| Canonical | Aliases | Unit |
|-----------|---------|------|
| `period` | period, per, tperiod | seconds |
| `width` | width, pw, pulsewidth | seconds |
| `delay` | delay, del | seconds |
| `high` | high, hi, vhigh, vih | volts |
| `low` | low, lo, vlow, vil | volts |
| `rise` | rise, trise, t_rise | seconds |
| `fall` | fall, tfall, t_fall | seconds |
| `zsource` | zsource, zs, source_impedance | ohms |
| `zload` | zload, zl, load_impedance | ohms |
| `polarity` | pol, polarity | "NORM", "INV" |

**Smart Features:**
- Automatic HIGH/LOW swap if high < low
- Automatic polarity inversion on swap
- Flexible key naming with aliases
- Optional verification after programming

#### `read_setpoints(channel) → Dict[str, float]`

Read back all programmed parameters on a channel.

```python
settings = pg.read_setpoints(1)
print(settings)
# {
#     "period": 1e-07,
#     "width": 1e-08,
#     "delay": 5e-09,
#     "high": 2.5,
#     "low": 0.0,
#     "rise": 1.5e-09,
#     "fall": 1.5e-09,
#     "zsource": 50.0,
#     "zload": 1000000.0,
#     "polarity": "NORM",
# }
```

#### `enable_channel(channel, enable=True)`

Enable or disable pulse output on a channel.

```python
pg.enable_channel(1, True)   # Enable CH1 output
pg.enable_channel(2, False)  # Disable CH2 output
```

#### `set_math_mode(enable)`

Enable or disable channel combination mode (CH1 + CH2).

```python
pg.set_math_mode(True)   # Enable math mode (sum CH1 + CH2)
pg.set_math_mode(False)  # Disable math mode
```

**Note**: This produces an output on CH1 that is the algebraic sum of both channel voltages.

#### `set_trigger(trigger_source)`

Configure trigger source.

```python
pg.set_trigger("IMM")   # Immediate (continuous)
pg.set_trigger("BUS")   # Bus trigger
pg.set_trigger("EXT")   # External trigger
```

#### `set_burst(channel, burst)`

Configure burst mode on a channel.

```python
pg.set_burst(1, burst=10)   # Enable burst: 10 pulses per trigger
pg.set_burst(1, burst=None) # Disable burst
```

#### `trigger_pulse()`

Issue a manual trigger command.

```python
pg.trigger_pulse()  # Send *TRG command
```

#### `save_settings(name) / recall_settings(name)`

Save and recall instrument configurations.

```python
# Save current configuration
pg.save_settings("config_A")

# Modify and test
pg.set_channel(1, {"period": 50e-9})

# Restore previous config
pg.recall_settings("config_A")
```

### Context Manager (Recommended)

```python
import pyvisa
from src.configs.pulse_generator.pulse_generator_81110A import PulseGenerator81110A

rm = pyvisa.ResourceManager()

with PulseGenerator81110A(rm, "GPIB0::10::INSTR") as pg:
    pg.initialize()
    pg.set_channel(1, {"period": 100e-9, "width": 10e-9})
    pg.enable_channel(1)
    
    # Automatic disconnect on exit (outputs disabled)
```

---

## PulseGeneratorAFG31000 (Tektronix)

### Overview

SCPI driver for the **Tektronix AFG31000-series** arbitrary/function generator operated in **pulse mode**.

**Key Features:**
- 2 independent channels (CH1, CH2)
- Pulse mode with period, width, delay, rise/fall times
- Negative and arbitrary low levels (not limited to ground)
- Burst and gated trigger modes
- Does NOT support simple math mode (raises NotImplementedError)
- Settings memory management

### Usage Example

```python
import pyvisa
from src.configs.pulse_generator.pulse_generator_AFG31000 import PulseGeneratorAFG31000

rm = pyvisa.ResourceManager()

with PulseGeneratorAFG31000(rm, "GPIB0::11::INSTR") as pg:
    # Configure channel 1
    pg.set_channel(1, {
        "period": 1e-6,        # 1 µs period
        "width": 100e-9,       # 100 ns width
        "delay": 50e-9,        # 50 ns delay
        "high": 2.0,           # 2 V high
        "low": -0.5,           # -0.5 V low (negative allowed!)
        "rise": 5e-9,
        "fall": 5e-9,
    })
    
    # Enable and burst
    pg.enable_channel(1, True)
    pg.set_burst(1, burst=20)
    
    # Set external trigger
    pg.set_trigger("EXTernal")
    
    # Disable burst
    pg.set_burst(1, burst=None)
    
    # Disable output
    pg.enable_channel(1, False)
```

### Key Methods

#### `set_channel(channel, settings)`

Configure pulse parameters on a channel.

```python
pg.set_channel(1, {
    "period": 1e-6,          # Pulse period (s)
    "width": 100e-9,         # Pulse width (s)
    "delay": 50e-9,          # Delay (s)
    "high": 2.0,             # High level (V) - can be negative
    "low": -0.5,             # Low level (V) - can be negative
    "rise": 5e-9,            # Rise time (s)
    "fall": 5e-9,            # Fall time (s)
})
```

**Supported Keys (with aliases):**

| Canonical | Aliases | Unit | Notes |
|-----------|---------|------|-------|
| `period` | period, per, tperiod | seconds | |
| `width` | width, wid, pw, pulsewidth | seconds | |
| `delay` | delay, del | seconds | |
| `high` | high, hi, vhigh, vih | volts | Can be negative |
| `low` | low, lo, vlow, vil | volts | Can be negative |
| `rise` | rise, trise, t_rise, leading | seconds | |
| `fall` | fall, tfall, t_fall, trailing | seconds | |

**Automatic Features:**
- Waveform shape forced to PULSe
- NR3 number formatting for SCPI compatibility

#### `read_setpoint(channel) → Dict[str, float]`

Read programmed parameters from a channel.

```python
settings = pg.read_setpoint(1)
# {"period": 1e-06, "width": 1e-07, "delay": 5e-08, ...}
```

**Note**: Method name is singular `read_setpoint()` (not plural).

#### `enable_channel(channel, onoff)`

Enable or disable output on a channel.

```python
pg.enable_channel(1, True)   # Enable
pg.enable_channel(1, False)  # Disable
```

#### `set_trigger(trigger)`

Configure trigger source.

```python
pg.set_trigger("TIMer")     # Internal timer (continuous)
pg.set_trigger("EXTernal")  # External trigger input
```

#### `set_burst(channel, burst)`

Configure burst mode.

```python
pg.set_burst(1, burst=50)   # Burst mode: 50 pulses
pg.set_burst(1, burst=None) # Disable burst
```

#### `set_math_mode(mode)`

**NOT SUPPORTED** on AFG31000.

```python
try:
    pg.set_math_mode(True)
except NotImplementedError as e:
    print(f"Error: {e}")
    # "AFG31000 does not support a simple CH1+CH2 math output"
```

#### `trigger_pulse()`

Issue a manual trigger.

```python
pg.trigger_pulse()
```

#### `save_settings(name) / recall_settings(name)`

Save and recall configurations.

```python
pg.save_settings("my_config")
pg.recall_settings("my_config")
```

### Context Manager

```python
with PulseGeneratorAFG31000(rm, "GPIB0::11::INSTR") as pg:
    pg.set_channel(1, {"period": 1e-6, "width": 100e-9})
    pg.enable_channel(1)
    
    # Outputs automatically disabled on exit
```

---

## Key Differences: 81110A vs AFG31000

| Feature | 81110A | AFG31000 |
|---------|--------|----------|
| **Channels** | 2 | 2 |
| **Period Range** | Flexible (ns to µs) | Flexible |
| **Low Level** | Always ≥ 0 V | Can be negative |
| **Math Mode** | Supported (CH1+CH2) | NOT supported |
| **Burst Mode** | Supported | Supported |
| **Trigger Sources** | IMM, BUS, EXT | TIMer, EXTernal |
| **Settings Memory** | Yes | Yes |
| **SCPI Style** | Colon-prefixed (`:PULS:PER1`) | Space-separated (`SOURce1:PULSe:PERiod`) |
| **Method Name** | `read_setpoints()` (plural) | `read_setpoint()` (singular) |

---

## Common Usage Patterns

### Pattern 1: Write Pulse Generation for Memory Measurements

```python
from src.configs.pulse_generator.pulse_generator_81110A import PulseGenerator81110A
import pyvisa

rm = pyvisa.ResourceManager()

with PulseGenerator81110A(rm, "GPIB0::10::INSTR") as pg:
    pg.initialize()
    
    # Write pulse on CH1
    pg.set_channel(1, {
        "period": 10e-6,       # 10 µs period
        "width": 100e-9,       # 100 ns write pulse
        "high": 3.0,           # 3 V write
        "low": 0.0,
        "rise": 1e-9,
        "fall": 1e-9,
    })
    
    # Read pulse on CH2
    pg.set_channel(2, {
        "period": 10e-6,
        "width": 50e-9,        # 50 ns read pulse
        "delay": 200e-9,       # Read 200 ns after write
        "high": 0.1,           # 0.1 V read
        "low": 0.0,
    })
    
    # Enable both
    pg.enable_channel(1)
    pg.enable_channel(2)
    
    print("Write-read pulse pair configured")
```

### Pattern 2: Burst Mode for Array Write

```python
with PulseGenerator81110A(rm, "GPIB0::10::INSTR") as pg:
    pg.initialize()
    
    # Configure write pulse
    pg.set_channel(1, {
        "period": 1e-6,
        "width": 100e-9,
        "high": 3.0,
        "low": 0.0,
    })
    
    # Enable burst: 16 pulses (for 16×1 row write)
    pg.set_burst(1, burst=16)
    pg.enable_channel(1)
    
    # Wait for burst to complete
    import time
    time.sleep(50e-6)  # 50 µs settling
    
    # Disable burst
    pg.set_burst(1, burst=None)
```

### Pattern 3: Negative Low Level (AFG31000 only)

```python
with PulseGeneratorAFG31000(rm, "GPIB0::11::INSTR") as pg:
    # Pulse between 2 V and -1 V (bidirectional)
    pg.set_channel(1, {
        "period": 100e-9,
        "width": 10e-9,
        "high": 2.0,
        "low": -1.0,          # Only possible on AFG31000!
        "rise": 1e-9,
        "fall": 1e-9,
    })
    
    pg.enable_channel(1)
```

### Pattern 4: Synchronized Dual-Channel Output

```python
with PulseGenerator81110A(rm, "GPIB0::10::INSTR") as pg:
    pg.initialize()
    
    # Same period and timing on both channels
    base_config = {
        "period": 100e-9,
        "high": 2.5,
        "low": 0.0,
        "rise": 1e-9,
        "fall": 1e-9,
    }
    
    # CH1: narrow pulse
    pg.set_channel(1, {**base_config, "width": 10e-9})
    
    # CH2: wider pulse
    pg.set_channel(2, {**base_config, "width": 40e-9})
    
    # Enable both
    pg.enable_channel(1)
    pg.enable_channel(2)
```

---

## Error Handling

### Connection Errors

```python
try:
    pg = PulseGenerator81110A(rm, "GPIB0::999::INSTR")
    pg.initialize()
except Exception as e:
    print(f"Connection failed: {e}")
```

### Invalid Channel

```python
try:
    pg.set_channel(5, {})  # Only 1-2 allowed
except ValueError as e:
    print(f"Invalid channel: {e}")
```

### Unsupported Keys

```python
try:
    pg.set_channel(1, {"frequency": 1e6})  # Unknown key
except ValueError as e:
    print(f"Invalid setting: {e}")
```

### Verification Failures

```python
try:
    pg.set_channel(1, {"period": 100e-9}, verify=True)
except RuntimeError as e:
    print(f"Verification failed: {e}")
```

### Math Mode Not Supported (AFG31000)

```python
try:
    pg.set_math_mode(True)
except NotImplementedError as e:
    print(f"Not supported: {e}")
```

---

## Logging

Both implementations inherit logging from `MeasurementDevice`. Example log output:

| Level | Message |
|-------|---------|
| **INFO** | `Connected to GPIB0::10::INSTR (Agilent 81110A)` |
| **INFO** | `Pulse generator initialized` |
| **INFO** | `TX → :PULS:PER1 1E-07` |
| **INFO** | `RX ← 1E-07` |
| **INFO** | `CH1 output ON` |
| **DEBUG** | `CH1 configured: {'period': 1e-07, 'width': 1e-08, ...}` |
| **WARNING** | `Swapped HIGH/LOW and inverted polarity due to HIGH<LOW` |

---

## Integration with Scenarios

Pulse generators are used in:
- `scenarios/basic_measure/run.py` – Retention measurements with write/read pulses
- `scenarios/IGZO_leakage/run.py` – Leakage current characterization
- `scenarios/IGZO_stability/run.py` – BTI stress testing pulses
- `scenarios/demo/demo_pulsegen_test.py` – PG connectivity verification

---

## Switching Between Models

To swap from 81110A to AFG31000:

```python
# Before (Keysight)
from src.configs.pulse_generator.pulse_generator_81110A import PulseGenerator81110A
pg = PulseGenerator81110A(rm, "GPIB0::10::INSTR")
pg.initialize()

# After (Tektronix)
from src.configs.pulse_generator.pulse_generator_AFG31000 import PulseGeneratorAFG31000
pg = PulseGeneratorAFG31000(rm, "GPIB0::11::INSTR")
# Note: initialize() not required; happens in set_channel()

# Most method calls remain the same!
# Exception: read_setpoint() vs read_setpoints()
```

---

## Specifications Reference

### Keysight/Agilent 81110A
- **Channels**: 2
- **Pulse Period**: 5 ns to 10 s
- **Pulse Width**: 2 ns to 9.999999 s
- **Voltage Range**: -10 V to +10 V
- **Rise/Fall Time**: 0.5 ns to 1 µs
- **Interface**: GPIB, LAN (optional)
- **Memory Slots**: Device-dependent

### Tektronix AFG31000
- **Channels**: 2
- **Waveform Types**: Sine, Square, Pulse, Ramp, Noise, Arbitrary
- **Pulse Period**: Programmable (typically 20 ns to 1000 s)
- **Pulse Width**: 20 ns to 999.999998 s
- **Voltage Range**: -5 V to +5 V (into 50Ω), ±10 V (open circuit)
- **Rise/Fall Time**: Programmable
- **Interface**: GPIB, USB, LAN
- **Memory Slots**: 4 predefined setups + user memory

---

## Related Files

- **Base Class**: [base_pulse_generator.py](base_pulse_generator.py)
- **Keysight Driver**: [pulse_generator_81110A.py](pulse_generator_81110A.py)
- **Tektronix Driver**: [pulse_generator_AFG31000.py](pulse_generator_AFG31000.py)
- **Base Class**: [src/configs/measurement_device.py](../measurement_device.py)
- **Logging**: [src/utils/logger.py](../../utils/logger.py)

---

## References

- Keysight 81110A / 81115A User Manual
- Tektronix AFG31000 User Manual
- PyVISA Documentation: [pyvisa.readthedocs.io](https://pyvisa.readthedocs.io/)
- SCPI Standard: IEEE 488.2
