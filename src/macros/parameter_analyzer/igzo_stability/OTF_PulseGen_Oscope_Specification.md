# OTF (On-The-Fly) Measurement Specification: Pulse Generator + Oscilloscope

## Overview

This document describes the intended behavior of the **OTF (On-The-Fly) stress-and-measure** module in the measurement automation codebase. The OTF module characterizes bias-stress-induced instability in InGaZnO TFTs **without interrupting the stress application**, by embedding short read pulses at defined sample points within an otherwise continuous stress phase.

> **Scope of this specification**: This document covers **only the OTF measurement behavior** — i.e., how the pulse generator and oscilloscope are coordinated to apply stress and perform in-situ reads. It does **not** govern the overall stressing scenario logic, including multi-step sequential stress protocols, recovery testing intervals, or stress condition scheduling. Those are handled by the higher-level stressing scenario controller and must not be modified based on this specification.

---

## Instrument Roles

| Instrument | Role |
|---|---|
| **Pulse Generator** (e.g., Keysight 81160A or equivalent) | Dual-channel: applies continuous stress via idle/baseline voltage, delivers read pulses at defined sample points |
| **Oscilloscope** (e.g., Keysight DSOX or equivalent) | Captures the transient drain current (or drain voltage across a load resistor) during each read pulse |

---

## Core Concept: Idle Voltage = Stress Voltage

The pulse generator operates in a mode where the **idle (off) state** of each channel corresponds to the **stress bias condition**, and the **active pulse** represents the **read condition**. This is the inverse of the typical mental model where the idle state is zero or ground.

### Voltage Assignment Convention

| Signal | Channel | Idle (Stress) Voltage | Pulse (Read) Voltage |
|---|---|---|---|
| Gate | CH1 | `V_gate_stress` (e.g., +3 V for PBS, or −2 V for NBS) | `V_gate_read` (e.g., 0.5 V) |
| Drain | CH2 | `V_drain_stress` (e.g., 0 V for PBS, or +1.5 V for DBS) | `V_drain_read` (e.g., 0.1 V) |

> **Example (Positive Bias Stress)**:
> - CH1 idle = 3 V (gate stress), CH1 pulse = 0.5 V (gate read) → pulse is a **negative-going** pulse
> - CH2 idle = 0 V (drain during stress), CH2 pulse = 0.1 V (drain read) → pulse is a **positive-going** pulse

### Automatic Polarity Handling

The pulse generator automatically sets pulse polarity based on the relative magnitudes of the high and low voltage levels:
- If `V_high > V_low`: positive pulse (pulse goes up from idle)
- If `V_low > V_high`: negative pulse (pulse goes down from idle) ← **this is the case for CH1 in PBS scenarios**

**The code must set the high/low voltage levels correctly and allow the instrument to determine polarity automatically.** Do not hard-code polarity; instead, set:
- `V_low` = `min(V_stress, V_read)`
- `V_high` = `max(V_stress, V_read)`
- `V_idle (baseline)` = `V_stress`
- `V_pulse` = `V_read`

The instrument firmware will automatically configure the correct polarity from these settings.

---

## OTF Measurement Sequence

### Phase 0: Optional Pre-Stress Transfer Curve (Parameter Analyzer)

Before initiating the pulse generator stress, an initial transfer curve sweep may be performed using a parameter analyzer (e.g., Keysight B1500A or Keithley 4200-SCS). This provides a baseline characterization (I_D–V_G sweep) of the device prior to any stress exposure.

- The delay time between the completion of the parameter analyzer sweep and the activation of the pulse generator **must be user-configurable** (e.g., `pre_stress_delay_s`).
- During this delay, all terminals should remain at a defined safe voltage (typically 0 V) to prevent unintended stress accumulation.
- The parameter analyzer sweep and the pulse generator stress/OTF logic are distinct subsystems; the OTF code should only handle the **handoff** (i.e., wait for the specified delay, then activate the pulse generator).

### Phase 1: Stress Onset — Turning On the Pulse Generator

The stress phase begins when **both CH1 and CH2 of the pulse generator are enabled simultaneously**. Upon activation:
- CH1 immediately outputs `V_gate_stress` (the idle/baseline voltage)
- CH2 immediately outputs `V_drain_stress` (the idle/baseline voltage)

This is the `t = 0` reference point for stress time. The stress timer starts at this moment.

> **Implementation note**: Enabling the channels is the trigger for stress start. Ensure that channel output enable is the last action before starting the stress timer, and that channel voltages are pre-configured before enabling.

### Phase 2: Continuous Stress with Periodic Read Pulses

During the stress phase, the device is continuously biased at (`V_gate_stress`, `V_drain_stress`) via the idle states of CH1 and CH2.

At each **user-defined stress sample time** (e.g., logarithmically spaced: 1 s, 3 s, 10 s, 30 s, 100 s, ...):

#### Step 2a: Arm the Oscilloscope

Before triggering the read pulse, the oscilloscope must be placed in **single-capture (single-trigger) mode**:
- Set the oscilloscope trigger source to **external trigger input** (Trig-In / Ext Trig port), **not** to any signal channel (CH1, CH2, etc.).
- Arm the oscilloscope for single acquisition (`oscilloscope.single()` or equivalent).
- Confirm that the oscilloscope is in the "waiting for trigger" state before proceeding.

> **Critical**: The trigger source must be the external trigger input port, connected to the **Trigger Out** port of the pulse generator. Using a signal channel as the trigger source will cause trigger timing errors and is not acceptable.

#### Step 2b: Fire the Read Pulse

Issue a **single-shot pulse trigger** to the pulse generator:
- The pulse generator outputs a brief read pulse on both CH1 and CH2 simultaneously:
  - CH1: transitions from `V_gate_stress` → `V_gate_read` → `V_gate_stress`
  - CH2: transitions from `V_drain_stress` → `V_drain_read` → `V_drain_stress`
- Simultaneously, the pulse generator emits a **Trigger Out** signal, which propagates to the oscilloscope's external trigger input.
- The oscilloscope captures the waveform on the rising (or falling) edge of the Trigger Out signal.

> **Pulse parameters** (all user-configurable):
> - `pulse_width_s`: Duration of the read pulse (e.g., 1 ms–100 ms; must be short relative to the local stress accumulation time to minimize measurement-induced perturbation)
> - `pulse_delay_s`: Delay between Trigger Out assertion and pulse onset, if configurable

#### Step 2c: Retrieve and Save Oscilloscope Data

After the pulse is fired and the oscilloscope capture completes:
- Retrieve the captured waveform data from the oscilloscope over the instrument interface (e.g., VISA/GPIB/USB).
- Extract the relevant time window corresponding to the read pulse.
- Compute and log the drain current at the read bias point (may require a transimpedance conversion if measuring voltage across a load resistor).
- Save the waveform and extracted value with the corresponding **cumulative stress time** at the moment the pulse was triggered.

> **Timing convention**: The stress time recorded for each read point should be the elapsed time from Phase 1 channel activation to the moment the pulse is triggered — **not** the time after the previous sample point.

#### Step 2d: Resume Stress

After data retrieval, the pulse generator returns to its idle state (`V_gate_stress`, `V_drain_stress`) automatically (this occurs as the pulse ends). No explicit action is required to resume stress, but the code should confirm the idle voltages are restored before logging the next stress time.

### Phase 3: Stress Termination

Stress is terminated by disabling CH1 and CH2 of the pulse generator (or ramping to 0 V, depending on instrument capability and user preference). The specific termination behavior is controlled by the higher-level stressing scenario logic and is **outside the scope of this OTF module**.

---

## Oscilloscope Configuration Requirements

The following oscilloscope settings must be configured **before** arming for each single capture:

| Parameter | Requirement |
|---|---|
| Trigger source | **External trigger input (Trig-In)** — NOT CH1, CH2, or any signal channel |
| Trigger mode | **Single** (one-shot capture; do not use Normal or Auto) |
| Trigger edge | Rising edge of Trigger Out from pulse generator (configurable) |
| Timebase | Centered on expected pulse; must capture full pulse width + settling time |
| Vertical scale | Auto-scaled or pre-configured per expected drain current/voltage range |
| Acquisition mode | Normal or High-Resolution (not Peak Detect) |

---

## Pulse Generator Configuration Requirements

| Parameter | Assignment |
|---|---|
| CH1 function | Pulse, idle = `V_gate_stress`, active = `V_gate_read` |
| CH2 function | Pulse, idle = `V_drain_stress`, active = `V_drain_read` |
| Trigger mode | External trigger input **or** software-triggered single-shot |
| Trigger Out | Must be connected to oscilloscope external trigger input |
| CH1, CH2 output | Enabled simultaneously at stress start (Phase 1) and remain on throughout |

---

## Data Output Format

Each OTF read point should produce a record containing at minimum:

```
{
  "stress_time_s": <float>,          # Cumulative stress time at pulse trigger moment
  "V_gate_stress": <float>,          # Gate stress voltage applied during idle
  "V_drain_stress": <float>,         # Drain stress voltage applied during idle
  "V_gate_read": <float>,            # Gate voltage during read pulse
  "V_drain_read": <float>,           # Drain voltage during read pulse
  "pulse_width_s": <float>,          # Read pulse width
  "I_drain_read": <float>,           # Extracted drain current at read bias [A]
  "waveform_time": <array of float>, # Oscilloscope time axis [s]
  "waveform_signal": <array of float> # Oscilloscope captured signal [V or A]
}
```

All records for a single OTF stress run should be saved to a structured file (e.g., CSV or HDF5) with the stress condition and sample timestamps.

---

## What NOT to Modify

The following aspects of the measurement system are **outside the scope of this OTF specification** and must not be altered when implementing or editing the OTF behavior:

- Multi-step sequential stress scenario logic (e.g., PBS → recovery → NBS chains)
- Stress condition scheduling (which voltage combinations are applied and in what order)
- Recovery testing intervals and recovery bias conditions
- MSM (Measure-Stress-Measure) module behavior, which uses the parameter analyzer for both stress and read operations
- Parameter analyzer sweep logic beyond the pre-stress initial sweep described in Phase 0

---

## Summary Flowchart

```
[Optional: Parameter Analyzer Initial Transfer Curve Sweep]
         |
         | ← user-defined delay (pre_stress_delay_s)
         ↓
[Enable CH1 + CH2 → idle voltages = stress voltages] ← t_stress = 0
         |
         ↓
[Wait until next sample time t_i]
         |
         ↓
[Arm oscilloscope: single-capture, Trig-In source]
         |
         ↓
[Trigger read pulse on CH1 + CH2] → [Trigger Out → Oscope Ext Trig]
         |
         ↓
[Oscilloscope captures waveform]
         |
         ↓
[Retrieve waveform data; extract I_drain; log with stress_time = t_i]
         |
         ↓
[CH1, CH2 return to idle (stress) voltages automatically]
         |
         ↓ (repeat for all sample times)
[Stress termination: handled by higher-level scenario controller]
```
