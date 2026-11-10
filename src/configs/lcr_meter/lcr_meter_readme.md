# LCR Meter Drivers (`src/configs/lcr_meter`)

This package provides a C-V focused control stack for the **Agilent/Keysight 4284A LCR Meter**.
Implementation is based on the `4284A_programmers_guide.pdf` and the `4284A_CV_Reference.docx`
contained in this directory.

## Class Layout

```text
MeasurementDevice
└── BaseLCRMeter
    └── LCRMeter4284A
```

- `BaseLCRMeter`:
  - Common connection lifecycle helpers.
  - Sweep-range generation (`start`, `stop`, `step`).
  - Shared result containers (`CVRecord`, `CVSweepResult`).
- `LCRMeter4284A`:
  - Full 4284A SCPI command implementation for C-V configuration, sweep execution,
    open/short compensation, and post-processing analysis.

---

## Implemented 4284A Command Families

Implemented from the local `4284A_programmers_guide.pdf` and `4284A_CV_Reference.docx`.

### Function / range

| Command              | Method                                         |
| -------------------- | ---------------------------------------------- |
| `FUNC:IMP:TYPE`      | `set_measurement_function()`                   |
| `FUNC:IMP:RANG`      | `set_impedance_range()`                        |
| `FUNC:IMP:RANG:AUTO` | `set_measurement_function(auto_range=...)`     |

### Source / signal setup

| Command        | Method                              |
| -------------- | ----------------------------------- |
| `FREQ`         | `set_frequency()`                   |
| `VOLT:LEV`     | `set_ac_level()`                    |
| `BIAS:VOLT:LEV`| `set_dc_bias(bias_level_v=...)`     |
| `BIAS:CURR`    | `set_dc_bias(bias_current_a=...)`   |
| `BIAS:STAT`    | `set_dc_bias(enabled=...)`          |

### Integration / averaging

| Command    | Method                        |
| ---------- | ----------------------------- |
| `APER`     | `set_aperture(mode, averages)`|
| `AVER:COUN`| `set_averaging_count(count)`  |

### Open / Short compensation

| Command          | Method                           |
| ---------------- | -------------------------------- |
| `CORR:OPEN`      | `run_open_correction(wait_s)`    |
| `CORR:OPEN:STAT` | `set_correction_open_state()`    |
| `CORR:SHOR`      | `run_short_correction(wait_s)`   |
| `CORR:SHOR:STAT` | `set_correction_short_state()`   |

### Trigger / init

| Command     | Method                              |
| ----------- | ----------------------------------- |
| `TRIG:SOUR` | `set_trigger_source()`              |
| `TRIG:DEL`  | `set_trigger_delay()`               |
| `TRIG:IMM`  | `_trigger_measurement()` (fallback) |
| `*TRG`      | `_trigger_measurement()` (BUS mode) |

### Instrument control

| Command         | Method   |
| --------------- | -------- |
| `*RST` + `*CLS` | `reset()`|

### Data fetch

| Command          | Method                   |
| ---------------- | ------------------------ |
| `FETC?`          | `fetch_measurement()`    |
| `FETC:SMON:VAC?` | `fetch_source_monitor()` |
| `FETC:SMON:IAC?` | `fetch_source_monitor()` |

### Optional list sweep setup

| Command          | Method              |
| ---------------- | ------------------- |
| `LIST:MODE`      | `setup_list_sweep()`|
| `LIST:FREQ`      | `setup_list_sweep()`|
| `LIST:BIAS:VOLT` | `setup_list_sweep()`|

---

## C-V Execution Modes

| Method                             | Description                                               |
| ---------------------------------- | --------------------------------------------------------- |
| `run_single_sweep(...)`            | One forward sweep                                         |
| `run_double_sweep(...)`            | Forward + reverse sweep (hysteresis)                      |
| `run_frequency_split_sweep([...])` | Repeat sweep at each frequency (dispersion analysis)      |

All sweep methods accept optional `presoak_v` and `hold_time_s` parameters for
minority-carrier equilibration before the sweep begins (see §Pre-Soak below).

All runs return `CVSweepResult`, which can be exported via:

- `result.to_rows()`
- `result.to_dataframe()`

---

## Post-Processing (Static Methods — No Instrument I/O)

All analysis methods are static and operate purely on numpy-compatible scalars or arrays.

### Series Resistance (Rs) Correction

Essential at high frequencies (≥ 100 kHz) where substrate and contact resistance
distorts the apparent capacitance.

```python
import math

omega = 2 * math.pi * 1e6   # 1 MHz

# 1. Measure Cp-Gp in strong accumulation (Vg = Vfb - 2 V)
Rs = LCRMeter4284A.extract_rs_from_accumulation(Cm_acc, Gm_acc, omega)

# 2. Apply correction to every data point in the sweep
C_corr, G_corr = LCRMeter4284A.apply_rs_correction(Cm, Gm, Rs, omega)
```

### Conductance Method — Gp/ω Spectrum (Nicollian-Goetzberger)

Most accurate Dit extraction method. Sweep frequency at each gate bias, plot Gp/ω vs log(ω).

```python
Gp_w = LCRMeter4284A.compute_gp_over_omega(C_corr, G_corr, Cox, omega)
Dit  = LCRMeter4284A.extract_dit_conductance(Gp_w_peak, q=1.602e-19, A=gate_area_cm2)
```

### Hi-Lo Frequency Method (Castagne-Vapaille)

Simpler approach; requires low-frequency (< 100 Hz) and high-frequency (1 MHz) C-V at the same bias.

```python
Dit = LCRMeter4284A.extract_dit_hilo(CLF, CHF, Cox, q=1.602e-19, A=gate_area_cm2)
```

---

## Compensation Procedure

Open/short compensation should be performed at every measurement frequency and
whenever the cable or probe configuration changes. This is the most critical
calibration step for accurate C-V data.

```python
lcr.connect()
lcr.reset()                              # *RST; *CLS

# 1. Raise probes (open circuit)
lcr.run_open_correction(wait_s=10.0)     # CORR:OPEN + 10 s wait
lcr.set_correction_open_state(True)      # CORR:OPEN:STAT 1

# 2. Short probes to chuck
lcr.run_short_correction(wait_s=10.0)    # CORR:SHOR + 10 s wait
lcr.set_correction_short_state(True)     # CORR:SHOR:STAT 1
```

---

## Pre-Soak Protocol

Apply a hold bias before sweeping to allow minority carrier equilibration (avoids
deep depletion artefacts). Recommended values from the CV Reference:

| Material                       | Hold time    |
| ------------------------------ | ------------ |
| Si                             | 1–10 s       |
| SiC / GaN / wide-bandgap       | 10–100+ s    |

```python
# p-type MOS cap: sweep from inversion (-3 V) toward accumulation (+3 V)
result = lcr.run_single_sweep(
    presoak_v=-3.0,      # pre-soak at strong inversion
    hold_time_s=10.0,    # equilibration time
    dwell_s=0.2,         # step delay (200 ms per step)
)
```

---

## Quick Example — Full C-V Workflow

```python
import math
import pyvisa
from src.configs.lcr_meter.lcr_meter_4284A import LCRMeter4284A

rm  = pyvisa.ResourceManager()
lcr = LCRMeter4284A(rm, "GPIB0::17::INSTR")
lcr.connect()
lcr.reset()

# Open/short compensation (probes raised, then shorted)
lcr.run_open_correction(wait_s=10.0)
lcr.set_correction_open_state(True)
lcr.run_short_correction(wait_s=10.0)
lcr.set_correction_short_state(True)

# Configure 1 MHz Cp-Gp measurement, 30 mV AC, LONG aperture, 4 averages
lcr.configure_cv_measurement(
    function_code="CPGP",
    frequency_hz=1e6,
    ac_level_v=0.030,
    bias_level_v=0.0,
    bias_enabled=True,
    aperture_mode="LONG",
    aperture_averages=1,
    averaging_count=4,
    trigger_source="BUS",
)

# Sweep from -3 V (inversion) to +3 V (accumulation), p-type substrate
lcr.set_sweep_range(start_v=-3.0, stop_v=3.0, step_v=0.05)
result = lcr.run_single_sweep(presoak_v=-3.0, hold_time_s=10.0, dwell_s=0.2)

# Turn off bias after measurement
lcr.set_dc_bias(enabled=False)
lcr.disconnect(kill_rm=True)

# ---- Post-processing ----
omega = 2 * math.pi * 1e6

# Extract Rs from the accumulation end of the sweep
acc_pt = result.points[-1]         # last point = strong accumulation for this direction
Rs = LCRMeter4284A.extract_rs_from_accumulation(acc_pt.primary, acc_pt.secondary, omega)
Cox = acc_pt.primary               # accumulation capacitance = Cox

# Correct all points for Rs
corrected = [
    LCRMeter4284A.apply_rs_correction(p.primary, p.secondary, Rs, omega)
    for p in result.points
]
```

---

## YAML-Driven Usage (LCR4284AMacro)

```yaml
# example_cv.yaml
measurement:
  function_code: CPGP
  frequency_hz: 1.0e6
  ac_level_v: 0.030
  bias_level_v: 0.0
  bias_enabled: true
  aperture_mode: LONG
  aperture_averages: 1
  averaging_count: 4
  trigger_source: BUS
  settle_delay_s: 0.2

sweep:
  start_v: -3.0
  stop_v: 3.0
  step_v: 0.05
  dwell_s: 0.2

correction:
  run_open: true
  open_wait_s: 10.0
  enable_open: true
  run_short: true
  short_wait_s: 10.0
  enable_short: true

presoak:
  v: -3.0
  hold_time_s: 10.0

run:
  mode: single
```

```python
import pyvisa
from src.macros.lcr_meter.lcr_4284A_macro import LCR4284AMacro

rm    = pyvisa.ResourceManager()
macro = LCR4284AMacro(rm, "GPIB0::17::INSTR")
macro.load_config("example_cv.yaml")
macro.connect()
macro.configure_from_dict()      # applies correction, then measurement setup
result = macro.run()
macro.save_results_csv(result, output_path="cv_data.csv")
macro.disconnect(close_rm=True)
```

---

## DOS Extraction

`BaseLCRMeter.extract_dos_from_cv(...)` is intentionally left as a placeholder (`NotImplementedError`).

Reason: full DOS extraction depends on process-specific physics (area, oxide thickness,
equivalent-circuit handling, temperature/series resistance treatment). The driver provides
`extract_dit_hilo()` and `extract_dit_conductance()` as building blocks for implementing
a complete extraction pipeline.
