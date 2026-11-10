/*
4T1C Dot-Product Firmware (QK Control Sketch)

Version: 2.4.9 (2026-11-10; reserve scheduled first-DFF preparation headroom)

This sketch is the Arduino-side controller for the 4T1C dot-product setup.
The host PC is responsible for external analog voltage programming, while the
Arduino owns the timing-critical digital work:

- generating the S1/S2 update pulse,
- selecting ACTUAL vs REFERENCE read path,
- collecting ADC samples,
- sequencing retention / disturb loops,
- reporting machine-readable state and data lines over Serial.

The intended host runner is:
  scenarios/arduino_4T1C_dotprod/run.py

-------------------------------------------------------------------------------
What each top-level command is for
-------------------------------------------------------------------------------
UPDATE
  First latch the read-select DFF OFF and disconnect/reset the integrator.
  Confirm BLTOP, BLBTM, BLTOP_OFF, and BLBTM_OFF are all programmed to zero.
  Request BOTH update voltages (BLTOP then BLBTM), wait for matching OPC,
  select their ON inputs (pins 46/34 HIGH), and pulse S1+S2 together.
  Close S1/S2, return the MUX selects LOW, then confirm all four rails are
  zero again before reporting completion. Values sent to the PSU are its
  settings: all four bitline inputs now have normal PSU polarity. Ideally
  VCAP = bltop_v - blbtm_v; equal settings write zero, not their sum.

READ_ACTUAL
  Read the ACTUAL signal path only. After confirming all four rails at zero,
  request write_bl_topbtm_v on BLTOP ON for S1, or on BLBTM ON for S2.
  Select the corresponding ON input HIGH; both OFF inputs remain zero.
  Park N3/N4 at zero during PSU waits: both pins 46/34 LOW.
  End WL integration, disconnect the integrator, close S1/S2, and park again
  before ADC capture and PSU zeroing. No update pulse is applied.

READ_REFERENCE
  Read the REFERENCE signal path only. Same write-bias handshake and gate
  sequencing as READ_ACTUAL, on whichever line REFERENCE mode needs for the
  current Q sign. No update pulse is applied.

UPDATE_AND_READ
  Perform one update transaction, wait post_us, and prepare BOTH BL ON rails
  and MUXes just as for retention/disturb. Measure the ACTUAL path once,
  holding N3/N4 bias through ADC and reset, then park and zero all four rails.
  This command does not measure REFERENCE or use points/cycles/interval_ms.

RETENTION_TEST
  Perform one update and prepare BOTH BL ON rails at write_bl_topbtm_v.
  Select both BL MUXes HIGH once and wait for their settling before the
  additional initial_wait_ms. Take points ACTUAL reads; interval_ms is
  first-DFF-start to first-DFF-start, anchored to the first read; zero means
  fastest possible. No PSU requests or BL MUX changes occur between points.
  Close S1/S2 between reads; each read opens only its selected access switch.
  N3/N4 remain biased during ADC, reset and interval waits. Park the MUXes
  and zero all rails after the run. t_us is the age from the last UPDATE pulse
  falling edge to the first DFF slot; it includes initial PSU preparation.
  q=0 reports a logical slot start because no VREAD pulse is selected.

READ_DISTURB_TEST
  Prepare and hold both BLTOP and BLBTM read biases/MUXes after one update,
  then take cycles ACTUAL+REFERENCE pairs by switching only S1/S2 access.
  Never open S1 and S2 together during reads. N3/N4 stay biased between reads;
  park the MUXes and zero the rails only at completion, abort or error. interval_ms is ACTUAL start to next ACTUAL
  start; REFERENCE follows its ACTUAL without an extra interval wait.
  Report t_us/ref_us for the two read starts, dt_us for observed pair spacing,
  and late_us for deadline lateness. The pair retains actual_avg-reference_avg.
  Impossible configured periods are rejected before UPDATE. Runtime overruns
  over 50us stop without catch-up reads, clean up, and report an error.

ADC_VERIFY
  Verify one external WLUx-resistor-BLUx path (channel 0..4). The host prepares
  zero BL rails and VREAD before CMD. Keep S1/S2 OFF and HL/FB LOW. Capture a
  matched all-WL-OFF baseline, then the selected-WL pulse, resetting for 3ms
  before and after each capture. Emit baseline, signal ADC and software pulse
  duration for host-side R/C prediction. There are no PSU requests in the loop.

ABORT
  Stop a long-running wait/loop and return the firmware to safe idle.

PING
  Lightweight liveness check. Returns current firmware version and state.

-------------------------------------------------------------------------------
Line-oriented ASCII protocol (case-sensitive)
-------------------------------------------------------------------------------
CMD UPDATE bltop_v=<voltage> blbtm_v=<voltage> pw_us=<pulse_width_us>
           [settle_after_opc_us=50] [repeat=1] [opc_timeout_ms=5000]
           [update_gate_settle_us=50]
CMD READ_ACTUAL q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
               [samples=1] [warmup=0] [settle_us=250] [inter_us=50] [avg=1] [read_delay=0]
               [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
               [write_bl_gate_settle_us=50]
CMD READ_REFERENCE q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
                   [samples=1] [warmup=0] [settle_us=250] [inter_us=50] [avg=1] [read_delay=0]
                   [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
                   [write_bl_gate_settle_us=50]
CMD ADC_VERIFY channel=<0..4> pulse_us=<1..1000000>
               [repeats=1] [samples=4] [inter_us=25] [settle_us=250]
CMD UPDATE_AND_READ bltop_v=<voltage> blbtm_v=<voltage> pw_us=<pulse_width_us>
                    [settle_after_opc_us=50] [repeat=1] [opc_timeout_ms=5000]
                    [update_gate_settle_us=50]
                    [post_us=0] q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
                    [samples=1] [warmup=0] [settle_us=250] [inter_us=50] [avg=1] [read_delay=0]
                    [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
                    [write_bl_gate_settle_us=50]
CMD RETENTION_TEST bltop_v=<voltage> blbtm_v=<voltage> pw_us=<pulse_width_us>
                   points=<count> [settle_after_opc_us=50] [repeat=1]
                   [opc_timeout_ms=5000] [update_gate_settle_us=50]
                   [initial_wait_ms=0] [interval_ms=1000]
                   q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
                   [samples=1] [warmup=0] [settle_us=250] [inter_us=50] [avg=1] [read_delay=0]
                   [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
                   [write_bl_gate_settle_us=50]
CMD READ_DISTURB_TEST bltop_v=<voltage> blbtm_v=<voltage> pw_us=<pulse_width_us>
                      cycles=<count> [settle_after_opc_us=50] [repeat=1]
                      [opc_timeout_ms=5000] [update_gate_settle_us=50]
                      [initial_wait_ms=0] [interval_ms=1000]
                      q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
                      [samples=1] [warmup=0] [settle_us=250] [inter_us=50] [avg=1] [read_delay=0]
                      [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
                      [write_bl_gate_settle_us=50]
CMD ABORT
CMD DEBUG_FIRE target=S1 pw_us=<us>
CMD DEBUG_FIRE target=S2 pw_us=<us>
CMD DEBUG_FIRE target=READ_ACTUAL q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
              [settle_us=250] [read_delay=0] [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
              [write_bl_gate_settle_us=50]
CMD DEBUG_FIRE target=READ_REFERENCE q=<value> precision=<bits> step_us=<us> write_bl_topbtm_v=<voltage>
              [settle_us=250] [read_delay=0] [write_bl_settle_after_opc_us=50] [write_bl_opc_timeout_ms=5000]
              [write_bl_gate_settle_us=50]
PING

Notes for new users:
- Every host message is one ASCII line terminated by '\n'.
- Any step that programs a PSU line is a two-stage handshake:
  1) Arduino emits REQ PSU_SET ch=<BLTOP|BLBTM|BLTOP_OFF|BLBTM_OFF> v=<voltage>
  2) host applies the requested PSU value externally
  3) host replies with matching OPC PSU_SET ...
  4) only then does the Arduino continue to the next rail request or GPIO step.
  Every standalone update/read has four zero-setting handshakes before it and four after
  it, in addition to the update's two ON settings or a read's single bias.
  UPDATE_AND_READ and compound runs retain the update sequence, prepare both
  read biases once, and perform four final zero handshakes: 16 requests each
  in a successful run, independent of points/cycles.
  DEBUG_FIRE S1/S2 also confirms zero rails before and after its digital pulse.
  Each REQ/OPC round trip has its own timeout budget (opc_timeout_ms for update,
  write_bl_opc_timeout_ms for read). A failure stops the sequence; GPIO idle
  alone does not prove the external rails are zero. The host must shut them down.
- Every ERR line from a PSU handshake includes step=<tag> (e.g.
  UPDATE_BLTOP, READ_WRITE_BIAS) identifying exactly which handshake failed,
  since one command can involve more than one.
- The firmware is intentionally strict:
  - command names are case-sensitive
  - key/value tokens must use key=value form
  - malformed lines return ERR ...

-------------------------------------------------------------------------------
Pseudo-code: Device control process
-------------------------------------------------------------------------------
1) Boot, configure pins/ADC, print BOOT READY + STATE IDLE + SPEC lines.
2) Poll Serial until one complete line is available.
3) Parse the line into prefix/name/key=value tokens.
4) With S1/S2 and read selection OFF, confirm four zero rails, then program
   BLTOP/BLBTM for update or BLTOP/BLBTM for read bias:
   - print REQ PSU_SET ...
   - wait in STATE WAIT_PSU_OPC for a matching OPC line
   - reject mismatched channel/voltage replies (tagged with step=<site>)
   - once confirmed, apply that channel's HIGH/LOW MUX selection and wait its
     settle delay before opening S1/S2 or the boost transistor
5) Perform the requested hardware action:
   - update pulse only
   - ACTUAL read only
   - REFERENCE read only
   - update + ACTUAL read
   - retention loop
   - disturb loop
6) Close access switches, capture any ADC data before PSU cleanup, and confirm
   all four bitline rails at zero. Emit DATA lines for successful measurements.
7) Finish with OK ... on success or ERR ... on failure, then return to IDLE.

-------------------------------------------------------------------------------
Example serial session
-------------------------------------------------------------------------------
Arduino -> BOOT READY version=2.3.0 protocol=ASCII_KV
Host    -> CMD UPDATE bltop_v=1.000000 blbtm_v=1.000000 pw_us=100 settle_after_opc_us=100
(four REQ/OPC pairs: BLTOP, BLBTM, BLTOP_OFF, BLBTM_OFF each set to 0 V)
Arduino -> REQ PSU_SET ch=BLTOP v=1.000000
Arduino -> STATE WAIT_PSU_OPC
Host    -> OPC PSU_SET ch=BLTOP v=1.000000
Arduino -> REQ PSU_SET ch=BLBTM v=1.000000
Arduino -> STATE WAIT_PSU_OPC
Host    -> OPC PSU_SET ch=BLBTM v=1.000000
Arduino -> STATE UPDATING
(BLTOP_GATE+BLBTM_GATE open, S1+S2 pulse pw_us, S1+S2 close, gates close)
(four REQ/OPC pairs restore all bitline rails to 0 V)
Arduino -> STATE IDLE
Arduino -> OK UPDATE_APPLIED bltop_v=1.000000 blbtm_v=1.000000 pulses=1 pw_us=100

Host    -> CMD READ_ACTUAL q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65 samples=4 settle_us=300 inter_us=100 avg=1
Arduino -> STATE READING_ACTUAL
(four REQ/OPC pairs confirm all bitline rails at 0 V)
(BLBTM_GATE stays LOW to select the zero BLBTM OFF input during the bias wait)
Arduino -> REQ PSU_SET ch=BLBTM v=1.650000
Arduino -> STATE WAIT_PSU_OPC
Host    -> OPC PSU_SET ch=BLBTM v=1.650000
Arduino -> STATE READING_ACTUAL
(BLBTM_GATE goes HIGH, S2 opens, WL0 integration runs, read/access paths close)
(BLBTM_GATE returns LOW to park N4 at zero before ADC capture)
(ADC samples are captured; four REQ/OPC pairs restore all bitline rails to 0 V)
(BLBTM_GATE returns LOW only after all four zero acknowledgements)
Arduino -> STATE IDLE
Arduino -> DATA READ_ACTUAL adc=612 avg=611.750 samples=4 min=610 max=614 values=610,611,612,614
Arduino -> OK READ_ACTUAL_DONE

-------------------------------------------------------------------------------
Hardware mapping notes
-------------------------------------------------------------------------------
- Current schematic names: N1 = S1 gate, N2 = S2 gate, N3 = BLTOP, N4 = BLBTM.
  N2/N3 roles are physically swapped on 2026-11-05 (firmware 2.4.2).
  Reconnect both output probes AND the two MUX input-rail pairs by role.
  Analog node / existing PCB output label / Arduino digital MUX select:
    N1 / N1_0 / pin 51 (BITMASK_S1)
    N2 / N2_0 / pin 39 (BITMASK_S2)
    N3 / N3_0 / pin 46 (BITMASK_BLTOP_GATE)
    N4 / N4_0 / pin 34 (BITMASK_BLBTM_GATE)
  GPIO selects are not the analog nodes. Pin 39 now selects +3V/-2V for S2;
  pin 46 selects the host-programmed BLTOP ON/OFF rails. Logical PSU labels
  still describe function; update their mappings only if physical PSU channels move.
- Arduino controls timing-critical digital lines and ADC capture.
- Host PC owns the analog PSU update and must answer REQ/OPC messages.
- FB (pin 22 / PB26) is held LOW for forward WL-to-BL reads, matching the
  6T1C reference sketch. Q sign selects S3/S4 through HL, not FB.
- The intended read-select connection is WL0 (pin 25) to DFF data, DFF1
  (pin 31) to its clock, and DFF output to the VREAD/VINP MUX select.
  Idle explicitly clocks WL0=LOW into that DFF to select VINP, with CON HIGH
  (disconnected), DS LOW (the existing read-end level), and CR LOW (reset).
  This defines the firmware's idle state; verify the DFF/MUX wiring on the
  actual PCB. DS topology is not inferred from its name.
- BLTOP_GATE (pin 46) and BLBTM_GATE (pin 34) are 2:1 analog MUX selects on
  the PCB, not simple on/off switches. HIGH selects the ON input (BLTOP or
  BLBTM); LOW selects the OFF input (BLTOP_OFF or BLBTM_OFF), not disconnection.
  Both OFF inputs are held at zero in this sequence. S1/S2 isolate
  V_TOP/V_BOT while supply settings change. Both MUX selects stay LOW
  during idle and PSU waits. Standalone reads park LOW before ADC capture;
  compound reads retain both HIGH through ADC, reset and interval waits.
- Wiring used by this sequence: BLTOP ON/OFF and BLBTM ON/OFF all have normal
  PSU polarity. A +1 V BLBTM setting means +1 V at the PCB. UPDATE stores
  the difference BLTOP - BLBTM, allowing either VCAP sign with nonnegative
  PSU settings. S2 read bias uses +1.65 V on BLBTM ON with pin 34 HIGH;
  S1 read bias uses +1.65 V on BLTOP ON with pin 46 HIGH. VREAD, N1/N2 OFF
  and PCB VSS retain the external polarity shown in the user's wiring table.
  Verify actual voltages and common GND physically;
  an OPC acknowledgement confirms the host transaction, not a node measurement.
- CMD DEBUG_FIRE target=S1/S2 keeps both MUX selects LOW and all four rails
  at zero; it tests digital access-switch timing only. Use CMD UPDATE to
  deliver the programmed update voltages to V_TOP/V_BOT.
*/

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/** Firmware version string reported in BOOT READY and PING replies. */
const char *VERSION = "2.4.9";

/** Standalone read samples, retained until ADC/reset/PSU cleanup finishes.
 *  4096 uint16_t codes use 8 KiB; compound reads keep summaries only. */
constexpr int MAX_READ_SAMPLE_VALUES = 4096;
uint16_t g_read_sample_values[MAX_READ_SAMPLE_VALUES];

/** Maximum accepted ASCII line length including the parser buffer terminator. */
constexpr int MAX_LINE_LENGTH = 192;
/** Maximum number of key=value fields accepted on one line. */
constexpr int MAX_FIELD_COUNT = 16;
/** Maximum storage length for one parsed key token. */
constexpr int MAX_KEY_LENGTH = 24;
/** Maximum storage length for one parsed value token. */
constexpr int MAX_VALUE_LENGTH = 40;
/** Maximum storage length for the logical PSU channel label (for example BLTOP). */
constexpr int MAX_CHANNEL_LENGTH = 24;

/** Arduino pin numbers used only for pinMode setup. */
const int CONTROL_PINLIST[] = {51, 46, 39, 34, 25, 26, 27, 28, 29, 2, 22, 53, 52, 42, 31};
/** ADC input connected to the integrated dot-product output node. */
constexpr int PIN_ADC = A0;

/** Direct-port bit masks used for timing-critical control of the 4T1C array.
 *
 * BLTOP_GATE (46) and BLBTM_GATE (34) are 2:1 analog MUX selects on the PCB,
 * NOT simple on/off switches: HIGH selects the ON PSU input, LOW the OFF
 * input. Both ON inputs supply read bias; both OFF inputs remain zero.
 * S1/S2 are the inner series switches (gates N1/N2) that connect the selected
 * voltage to V_TOP/V_BOT. They stay OFF while PSU settings change, and
 * both MUX inputs are confirmed at zero before/after each normal command.
 * Compound reads retain prepared bias between points and park on zero inputs.
 * These two bitmasks used to be misleadingly named BITMASK_READ_ACTUAL_SELECT /
 * BITMASK_READ_REFERENCE_SELECT -- they gate BLTOP/BLBTM, not the read path. */
constexpr int BITMASK_S1 = 1 << 12;          // PC12 -> Arduino 51
constexpr int BITMASK_S2 = 1 << 7;           // PC7  -> Arduino 39 (N2 / S2 gate MUX)
constexpr int BITMASK_BLTOP_GATE = 1 << 17;  // PC17 -> Arduino 46 (N3 / BLTOP MUX)
constexpr int BITMASK_BLBTM_GATE = 1 << 2;   // PC2  -> Arduino 34 (MUX select for BLBTM on/off)
constexpr int BITMASK_ALL_CONTROLS =
    BITMASK_S1 | BITMASK_S2 | BITMASK_BLTOP_GATE | BITMASK_BLBTM_GATE;

constexpr uint32_t BITMASK_WL0  = 1UL << 0;   // PD0  -> pin 25
// Five PCB read lanes: Arduino 25..29 map to PD0, PD1, PD2, PD3, PD6.
constexpr uint32_t BITMASK_ALL_WL = (1UL << 0) | (1UL << 1) | (1UL << 2) | (1UL << 3) | (1UL << 6);
const uint32_t ADC_VERIFY_WL_MASKS[5] = {1UL << 0, 1UL << 1, 1UL << 2, 1UL << 3, 1UL << 6};
const int ADC_VERIFY_CDR[5] = {7, 6, 5, 4, 3}; // A0..A4, native 12-bit ADC.
const int ADC_VERIFY_PINS[5] = {A0, A1, A2, A3, A4};

constexpr uint32_t BITMASK_HL   = 1UL << 25;  // PB25 -> pin 2 
constexpr uint32_t BITMASK_FB   = 1UL << 26;  // PB26 -> pin 22 (LOW = forward WL -> BL)
constexpr uint32_t BITMASK_CON  = 1UL << 14;  // PB14 -> pin 53
constexpr uint32_t BITMASK_CR   = 1UL << 21;  // PB21 -> pin 52
constexpr uint32_t BITMASK_DS   = 1UL << 19;  // PA19 -> pin 42
constexpr uint32_t BITMASK_DFF1 = 1UL << 7;   // PA7  -> pin 31

/** ADC conversion constants for reporting raw code and equivalent voltage. */
constexpr int ADC_CODE_MAX = 1023;
constexpr float ADC_REF_VOLTAGE = 3.3f;
/** Allowed host/firmware voltage mismatch when validating OPC PSU_SET. */
constexpr float OPC_VOLTAGE_TOLERANCE = 0.005f;

/** Default values used when the host omits optional update keys. */
constexpr int DEFAULT_UPDATE_SETTLE_AFTER_OPC_US = 50;
constexpr int DEFAULT_UPDATE_REPEAT_COUNT = 1;
constexpr int DEFAULT_OPC_TIMEOUT_MS = 5000;
constexpr int DEFAULT_UPDATE_PRE_US  = 0;
constexpr int DEFAULT_UPDATE_POST_US = 0;
constexpr int DEFAULT_UPDATE_ZERO_US = 0;
/** Settling delay after the read-bias OPC (BLTOP/BLBTM) and before the boost
 * transistor opens for a read. Reads reuse DEFAULT_OPC_TIMEOUT_MS above for
 * their own opc_timeout_ms default. */
constexpr int DEFAULT_WRITE_BL_SETTLE_AFTER_OPC_US = 50;
/** Settling delay after selecting the update ON inputs or the read bias
 * ON input HIGH (for either S1 or S2), before the access transistor opens. */
constexpr int DEFAULT_UPDATE_GATE_SETTLE_US = 50;
constexpr int DEFAULT_WRITE_BL_GATE_SETTLE_US = 50;

/** Default values used when the host omits optional read keys. */
constexpr int DEFAULT_SAMPLE_COUNT = 1;
constexpr int DEFAULT_WARMUP_COUNT = 0;
constexpr int DEFAULT_READ_SETTLE_US = 250;
constexpr int DEFAULT_INTER_SAMPLE_US = 50;
constexpr bool DEFAULT_AVERAGE_ENABLE = true;
constexpr int DEFAULT_READ_DELAY = 0;

/** Default values for compound experiment wrappers. */
constexpr int DEFAULT_POST_UPDATE_DELAY_US = 0;
constexpr int DEFAULT_RETENTION_POINTS = 10;
constexpr int DEFAULT_DISTURB_CYCLES = 10;
constexpr int DEFAULT_INITIAL_WAIT_MS = 0;
constexpr int DEFAULT_INTERVAL_MS = 1000;

/** Firmware states exposed to the host through STATE ... serial lines. */
enum FirmwareState {
  STATE_BOOTING = 0,
  STATE_IDLE,
  STATE_WAIT_PSU_OPC,
  STATE_UPDATING,
  STATE_READING_ACTUAL,
  STATE_READING_REFERENCE,
  STATE_RUNNING_UPDATE_AND_READ,
  STATE_RUNNING_RETENTION,
  STATE_RUNNING_DISTURB,
  STATE_DEBUG_FIRE,
  STATE_ADC_VERIFY,
  STATE_ERROR_STATE
};

/** Read-path selector used by performRead(). */
enum ReadMode {
  READ_MODE_ACTUAL = 0,
  READ_MODE_REFERENCE
};

/** Shared return code used by long-running helpers. */
enum OperationStatus {
  OP_STATUS_OK = 0,
  OP_STATUS_ABORTED,
  OP_STATUS_ERROR
};

/** Result of incremental line collection from Serial. */
enum LineReadStatus {
  LINE_STATUS_NONE = 0,
  LINE_STATUS_READY,
  LINE_STATUS_OVERFLOW
};

/** One parsed key=value token from a host command line. */
struct KeyValueField {
  char key[MAX_KEY_LENGTH];
  char value[MAX_VALUE_LENGTH];
};

/** Parsed representation of one incoming ASCII line. */
struct ParsedLine {
  char prefix[12];
  char name[24];
  KeyValueField fields[MAX_FIELD_COUNT];
  int field_count;
};

/** Parameter bundle shared by every command that includes an update step.
 * BLTOP and BLBTM are always programmed together (see performUpdate()) so
 * VCAP always charges from a fully known state, never a leftover voltage on
 * one line from a previous command. */
struct UpdateParams {
  float bltop_voltage;
  float blbtm_voltage;
  int pulse_width_us;
  int settle_after_opc_us;
  int repeat_count;
  int opc_timeout_ms;
  int update_gate_settle_us;
};

/** Parameter bundle shared by every command that includes ADC readout.
 * write_bl_topbtm_v is programmed (via PSU handshake) onto whichever of
 * BLTOP/BLBTM feeds the boost transistor (S1 or S2) that is about to
 * open, immediately before it opens -- see decideReadPath()/readSingleWL().
 * It is the analog bias the read transistors (S3/S4) sense through, not a
 * value being read back from the cell. */
struct ReadParams {
  int sample_count;
  int warmup_count;
  int settle_us;
  int inter_sample_us;
  bool average_enable;
  float q;
  int precision;
  int step_us;
  int read_delay;
  float write_bl_topbtm_v;
  int write_bl_settle_after_opc_us;
  int opc_timeout_ms;
  int write_bl_gate_settle_us;
};

/** One readback summary returned by captureReadSamples(). */
struct ReadResult {
  int raw_sum;
  int raw_min;
  int raw_max;
  float average_adc;
  float reported_adc;
  int sample_count;
  int first_sample;
  int last_sample;
  int sample_values_count;
};

/** Full argument container for CMD UPDATE_AND_READ. */
struct UpdateAndReadParams {
  UpdateParams update;
  ReadParams read;
  int post_update_delay_us;
};

/** Full argument container for CMD RETENTION_TEST. */
struct RetentionParams {
  UpdateParams update;
  ReadParams read;
  int point_count;
  int initial_wait_ms;
  int interval_ms;
};

/** Full argument container for CMD DEBUG_FIRE. */
struct DebugFireParams {
  char target[16];
  int pw_us;
  float q;
  int precision;
  int step_us;
  int settle_us;
  int read_delay;
  float write_bl_topbtm_v;
  int write_bl_settle_after_opc_us;
  int opc_timeout_ms;
  int write_bl_gate_settle_us;
};

/** Boost-transistor/HL routing decision computed by decideReadPath(), applied
 * by applyReadPath(). Splitting "decide" from "apply" lets readSingleWL() run
 * the write-bias PSU handshake in between, without duplicating the Q-sign +
 * read-mode routing logic anywhere else. */
struct ReadPathDecision {
  bool hl_high;
  const char *boost_channel;  // "BLTOP" (S1) or "BLBTM" (S2)
  uint32_t boost_bitmask;     // BITMASK_S1 or BITMASK_S2
  uint32_t gate_bitmask;      // Select for the corresponding BLTOP/BLBTM MUX.
  bool gate_high;            // Both S1/S2 use their ON input HIGH.
};

/** Full argument container for CMD READ_DISTURB_TEST. */
struct DisturbParams {
  UpdateParams update;
  ReadParams read;
  int cycle_count;
  int initial_wait_ms;
  int interval_ms;
};

/** Timestamps at the first DFF slot. For q=0 this is a logical read start,
 * because the DFF never selects VREAD. Both times use microseconds. */
struct CompoundReadTiming {
  uint64_t start_us;
  uint64_t late_us;
};

/** Allow a small software-dispatch margin, never a catch-up read after a
 * missed period. Every actual lateness is reported, including this margin. */
constexpr uint64_t COMPOUND_LATE_TOLERANCE_US = 50;
// Start scheduled path setup early enough for GPIO/dispatch work, in addition
// to the requested minimum access settling. The first DFF still waits for target.
constexpr uint64_t COMPOUND_PREP_MARGIN_US = 250;

bool g_compound_timing = false;
uint32_t g_compound_clock_low = 0;
uint64_t g_compound_clock_us = 0;
uint64_t g_compound_update_end_us = 0;
uint64_t g_compound_read_target_us = 0;
CompoundReadTiming g_compound_read_timing = {0, 0};
bool g_compound_read_overrun = false;
char g_compound_error[128] = "";

/** Legacy scratch array retained for quick manual debugging/inspection. */
int result_array[10];
/** Current firmware state visible to the host through STATE messages. */
FirmwareState g_state = STATE_BOOTING;
/** Incremental UART receive buffer for line-based parsing. */
char g_rx_line[MAX_LINE_LENGTH];
/** Current number of buffered characters in g_rx_line. */
int g_rx_len = 0;
/** Set when the incoming line exceeds MAX_LINE_LENGTH. */
bool g_rx_overflow = false;

/** ADC verification uses a resistor between the selected WLU/BLU pair.
 *  Host prepares VREAD and zero BL supplies; access transistors remain OFF.
 *  R/C/reference-voltage calculations are host-side; these are timing controls.
 */
struct AdcVerifyParams {
  int channel;
  int pulse_us;
  int repeats;
  int samples;
  int inter_us;
  int settle_us;
};

struct AdcVerifyWindow {
  float adc;
  int minimum;
  int maximum;
  uint32_t duration_us;
};

bool g_adc_verify_pong_pending = false;
bool loadAdcVerifyParams(const ParsedLine &parsed, AdcVerifyParams &params);
OperationStatus pollAdcVerifyControl();
OperationStatus waitAdcVerifyUs(uint32_t duration_us);
uint32_t latchAdcVerifyWL(uint32_t mask);
int readAdcVerifyCode(int channel);
OperationStatus captureAdcVerifyWindow(const AdcVerifyParams &params,
                                      bool signal, AdcVerifyWindow &result);
OperationStatus performAdcVerify(const AdcVerifyParams &params);

// Low-level hardware helpers
void setupPins();
void parkBitlinePaths();
void resetReadControls();
void enterSafeIdleState();
ReadPathDecision decideReadPath(ReadMode mode, float q);
void applyReadPath(const ReadPathDecision &decision);
int computeReadOnCount(float q_abs, int steps);
void readScalingSlots(int steps, int on_count, int step_us, int read_delay);
void readScaling(float q_abs, int precision, int step_us, int read_delay);
OperationStatus readSingleWL(ReadMode mode, const ReadParams &params);
void applyUpdatePulse(int pulse_width_us);
int readSingleAdcCode();
bool captureReadSamples(const ReadParams &params, ReadResult &result, bool keep_samples = false);

// State/reporting helpers
const char *stateName(FirmwareState state);
void setState(FirmwareState next_state);
void emitBootReady();
void emitOk(const char *message);
void emitError(const char *message);
void emitBusyError();
void emitPong();
void emitHelp();
void emitReadData(const char *label, const ReadResult &result);

// Parsing and validation helpers
void copyText(char *dst, const char *src, int dst_size);
void trimWhitespace(char *text);
bool stringsEqual(const char *lhs, const char *rhs);
bool parseLine(char *line, ParsedLine &parsed);
bool parseRawLine(const char *line, ParsedLine &parsed);
const char *findField(const ParsedLine &parsed, const char *key);
bool parseIntText(const char *text, int &value);
bool parseFloatText(const char *text, float &value);
bool parseBoolText(const char *text, bool &value);
bool loadRequiredText(const ParsedLine &parsed, const char *key, char *dst, int dst_size);
bool loadRequiredInt(const ParsedLine &parsed, const char *key, int &value);
bool loadRequiredFloat(const ParsedLine &parsed, const char *key, float &value);
bool loadOptionalInt(const ParsedLine &parsed, const char *key, int &value, int default_value);
bool loadOptionalBool(const ParsedLine &parsed, const char *key, bool &value, bool default_value);

bool loadUpdateParams(const ParsedLine &parsed, UpdateParams &params);
bool loadReadParams(const ParsedLine &parsed, ReadParams &params);
bool loadUpdateAndReadParams(const ParsedLine &parsed, UpdateAndReadParams &params);
bool loadRetentionParams(const ParsedLine &parsed, RetentionParams &params);
bool loadDisturbParams(const ParsedLine &parsed, DisturbParams &params);

// Command dispatch / long-running operation helpers
LineReadStatus pollSerialLine(char *out, int out_size);
bool isAbortCommand(const ParsedLine &parsed);
bool isPingCommand(const ParsedLine &parsed);
bool isHelpCommand(const ParsedLine &parsed);
void printCommandSpec();
OperationStatus abortToIdle();
OperationStatus requestAndWaitPsu(const char *channel, float voltage, int timeout_ms, const char *step_tag);
OperationStatus zeroBitlineSupplies(int timeout_ms, const char *step_tag);
OperationStatus waitMillisecondsWithAbort(int wait_ms);

OperationStatus performUpdate(const UpdateParams &params, FirmwareState resume_state, bool emit_completion);
OperationStatus performRead(ReadMode mode, const ReadParams &params, ReadResult &result,
                            FirmwareState resume_state, bool emit_data);
OperationStatus runUpdateAndRead(const UpdateAndReadParams &params);
OperationStatus runRetentionTest(const RetentionParams &params);
OperationStatus runReadDisturbTest(const DisturbParams &params);

uint64_t compoundClockUs();
void beginCompoundTiming();
bool validateCompoundTiming(const UpdateParams &update, const ReadParams &read,
                            int count, int initial_wait_ms, int interval_ms, bool pair);
OperationStatus pollCompoundControl();
OperationStatus waitCompoundUntil(uint64_t target_us);
OperationStatus prepareCompoundReadBias(const ReadParams &params);
OperationStatus performPreparedRead(ReadMode mode, const ReadParams &params,
                                    ReadResult &result, uint64_t target_us,
                                    CompoundReadTiming &timing);
OperationStatus finishCompound(OperationStatus status, const ReadParams &params);

bool loadDebugFireParams(const ParsedLine &parsed, DebugFireParams &params);
OperationStatus performDebugFire(const DebugFireParams &params);

void handleTopLevelLine(const ParsedLine &parsed);


/**
 * @brief Initialize GPIO direction, ADC, PRNG seed, and startup serial banner.
 *
 * Startup behavior intentionally mirrors the train-demo sketches:
 * - configure hardware once,
 * - announce protocol/version,
 * - enter STATE IDLE before accepting host commands.
 */
void setup() {
  setupPins();

  ADC->ADC_MR   |= 0x80;
  ADC->ADC_CR    = 2;
  ADC->ADC_CHER  = 0xF8;

  Serial.begin(115200);
  randomSeed(0);

  for (int i = 0; i < 10; i++) result_array[i] = -1;

  printCommandSpec();
}

/**
 * @brief Poll for one complete host line, parse it, and dispatch exactly one command.
 *
 * The firmware is line-oriented rather than stream-oriented. Each iteration
 * consumes at most one finished line and either:
 * - ignores it (empty line),
 * - rejects it with ERR ...,
 * - or routes it into handleTopLevelLine(...).
 */
void loop() {
  char line[MAX_LINE_LENGTH];
  LineReadStatus line_status = pollSerialLine(line, sizeof(line));
  if (line_status == LINE_STATUS_NONE) return;

  if (line_status == LINE_STATUS_OVERFLOW) {
    emitError("BAD_CMD reason=line_too_long");
    return;
  }

  if (line[0] == '\0') return;

  ParsedLine parsed;
  if (!parseRawLine(line, parsed)) {
    emitError("BAD_CMD");
    return;
  }

  handleTopLevelLine(parsed);
}

/*
 * ================================
 * |     Hardware Control         |
 * ================================
 */

/**
 * @brief Configure GPIO direction and latch the inactive read state before startup.
 *
 * @return No return value. Hardware pin direction is updated in-place.
 */
void setupPins() {
  for (int i = 0; i < (int)(sizeof(CONTROL_PINLIST) / sizeof(CONTROL_PINLIST[0])); i++) {
    pinMode(CONTROL_PINLIST[i], OUTPUT);
  }
  enterSafeIdleState();
  for (int i = 0; i < 5; i++) pinMode(ADC_VERIFY_PINS[i], INPUT);
}

/**
 * @brief Close S1/S2 before selecting the inactive bitline inputs.
 *
 * Close S1/S2 first, then select both zero OFF inputs (pins 46/34 LOW).
 * The ON supplies may remain biased during compound reads or a lost OPC.
 * Neither node is connected to those supplies while parked.
 */
void parkBitlinePaths() {
  PIOC->PIO_CODR = BITMASK_S1 | BITMASK_S2;
  PIOC->PIO_CODR = BITMASK_BLTOP_GATE | BITMASK_BLBTM_GATE;
}

/**
 * @brief Close write paths, latch read selection OFF, and reset the integrator.
 *
 * Clearing WL0 alone does not clear a stored DFF output. Disconnect the
 * integrator first, then clock LOW data into the read-select DFF. The explicit
 * setup/high delays are outside the Q-encoded read pulse. Keep CR HIGH until
 * ADC capture completes; callers must not enter idle while retaining a sample.
 *
 * @return No return value. CON is HIGH; both bitline MUX selects finish LOW.
 */
void enterSafeIdleState() {
  parkBitlinePaths();
  resetReadControls();
}

/** Disconnect/reset the read unit without changing access or BL MUX selects.
 * Call only with S1/S2 OFF. Compound reads retain both prepared BL biases. */
void resetReadControls() {
  PIOB->PIO_SODR = BITMASK_CON; // Disconnect before changing the read selection.
  PIOD->PIO_CODR = BITMASK_ALL_WL; // Latch every read lane to VINP, including ADC_VERIFY lanes.
  PIOA->PIO_CODR = BITMASK_DFF1;
  delayMicroseconds(1);       // Data setup and a known LOW clock before its edge.
  PIOA->PIO_SODR = BITMASK_DFF1;
  delayMicroseconds(1);       // Explicit clock HIGH width for the OFF latch.
  PIOA->PIO_CODR = BITMASK_DFF1 | BITMASK_DS; // Match the existing read-end order.
  PIOB->PIO_CODR = BITMASK_CR | BITMASK_HL | BITMASK_FB;
}

/**
 * @brief Decide the boost transistor and HL routing for a read operation,
 * without touching any pins yet.
 *
 * HL selects which read transistor path is active, determined solely by Q sign:
 *   HL low  → S3/WLU path (positive Q)
 *   HL high → S4/WLD path (negative Q)
 *
 * The boost transistor depends on both mode and Q sign:
 *   positive Q: ACTUAL → S2 (boosts bottom); REFERENCE → S1 (boosts top)
 *   negative Q: ACTUAL → S1 (boosts top);    REFERENCE → S2 (boosts bottom)
 *
 * S1 reads use BLTOP ON; S2 reads use BLBTM ON. Both are selected HIGH.
 * Both normal-polarity OFF supplies remain zero.
 * readSingleWL() programs that positive bias before opening the transistor
 * via applyReadPath(). The choice of MUX input does not change the Q/HL rule.
 *
 * @param mode Selects ACTUAL or REFERENCE read path.
 * @param q    Query value in [-1, 1]; sign determines transistor routing.
 * @return The HL level, boost channel label, and boost bitmask to apply.
 */
ReadPathDecision decideReadPath(ReadMode mode, float q) {
  bool pos_q = (q >= 0.0f);
  bool use_s2 = pos_q ? (mode == READ_MODE_ACTUAL) : (mode != READ_MODE_ACTUAL);

  ReadPathDecision decision;
  decision.hl_high = !pos_q;
  if (use_s2) {
    decision.boost_channel = "BLBTM";
    decision.boost_bitmask = BITMASK_S2;
    decision.gate_bitmask = BITMASK_BLBTM_GATE;
    decision.gate_high = true;
  } else {
    decision.boost_channel = "BLTOP";
    decision.boost_bitmask = BITMASK_S1;
    decision.gate_bitmask = BITMASK_BLTOP_GATE;
    decision.gate_high = true;
  }
  return decision;
}

/**
 * @brief Physically assert the HL level and boost transistor chosen by
 * decideReadPath(). Called only after the matching write-bias PSU line has
 * already been confirmed via OPC (see readSingleWL()). enterSafeIdleState()
 * clears S1/S2 and HL after the read completes.
 *
 * @param decision Routing decision from decideReadPath().
 * @return No return value. The relevant boost and HL pins are updated in-place.
 */
void applyReadPath(const ReadPathDecision &decision) {
  PIOB->PIO_CODR = BITMASK_FB; // Both Q signs use forward WL -> BL routing.
  if (decision.hl_high) {
    PIOB->PIO_SODR = BITMASK_HL;
  } else {
    PIOB->PIO_CODR = BITMASK_HL;
  }
  PIOC->PIO_SODR = decision.boost_bitmask;
}

/** Quantize Q before entering the time-critical part of a read. */
int computeReadOnCount(float q_abs, int steps) {
  if (q_abs > 1.0f) q_abs = 1.0f;
  return (int)floor((double)q_abs * steps + 1e-9);
}

/**
 * @brief Drive the WL integration sequence using PWM-encoded Q magnitude.
 *
 * The Q magnitude is encoded as a PWM duty cycle over (1 << precision) time slots:
 *   on_count = floor(q_abs * steps)
 *   WL0 is HIGH for the first on_count slots, LOW for the remainder.
 * Each slot lasts step_us microseconds. After the PWM loop, read_delay additional
 * DFF1-only 10 µs settling ticks are applied.
 *
 * CR is left HIGH on return so the integrator capacitor holds the accumulated
 * charge for subsequent ADC reads. The caller clears CR after all samples are taken.
 *
 * @param q_abs      Magnitude of Q (0..1); clamped to 1.0 if larger.
 * @param precision  PWM resolution in bits; total slots = 1 << precision.
 * @param step_us    Duration of each PWM slot in microseconds.
 * @param read_delay Number of 10 µs DFF1-only settling ticks after the PWM loop.
 * @return No return value. Pin state is updated in-place.
 */
void readScaling(float q_abs, int precision, int step_us, int read_delay) {
  int steps = 1 << precision;
  int on_count = computeReadOnCount(q_abs, steps);
  readScalingSlots(steps, on_count, step_us, read_delay);
}

/** Drive an already computed PWM frame. Compound callers prepare the slot
 * counts before waiting for their deadline; no floating-point work belongs
 * between the final wait and the first DFF edge. */
void readScalingSlots(int steps, int on_count, int step_us, int read_delay) {
  PIOB->PIO_CODR = BITMASK_CON; // CON(Connect cell & integrator)
  PIOA->PIO_SODR = BITMASK_DS; // DS
  PIOB->PIO_SODR = BITMASK_CR; // CR (integrator cap open)

  for (int k = 0; k < steps; k++) {
    if (k < on_count) PIOD->PIO_SODR = BITMASK_WL0; // WL SET
    else PIOD->PIO_CODR = BITMASK_WL0; // WL CLEAR
    if (g_compound_timing && k == 0) {
      // The WL data is ready but is not latched until the DFF clock below.
      // Wait only after the frame arithmetic and GPIO setup have completed.
      while (g_compound_read_target_us > 0 && compoundClockUs() < g_compound_read_target_us) {
        delayMicroseconds(1);
      }
      // Stamp immediately before the first clock edge, not function entry
      // or CR setup. q=0 intentionally records the first LOW selection slot.
      g_compound_read_timing.start_us = compoundClockUs();
      g_compound_read_timing.late_us = 0;
      if (g_compound_read_target_us > 0 &&
          g_compound_read_timing.start_us > g_compound_read_target_us) {
        g_compound_read_timing.late_us =
            g_compound_read_timing.start_us - g_compound_read_target_us;
      }
      if (g_compound_read_timing.late_us > COMPOUND_LATE_TOLERANCE_US) {
        g_compound_read_overrun = true;
        PIOD->PIO_CODR = BITMASK_WL0;
        break; // No VREAD edge when the scheduled start has been missed.
      }
    }
    PIOA->PIO_SODR = BITMASK_DFF1; // DFF1 CLK HIGH
    PIOA->PIO_CODR = BITMASK_DFF1; // DFF1 CLK LOW
    PIOD->PIO_CODR = BITMASK_WL0; // WL CLEAR
    delayMicroseconds(step_us);
  }

  for (int i = 0; i < read_delay && !(g_compound_timing && g_compound_read_overrun); i++) {
    PIOA->PIO_SODR = BITMASK_DFF1;
    PIOA->PIO_CODR = BITMASK_DFF1;
    delayMicroseconds(10);
  }

  PIOA->PIO_SODR = BITMASK_DFF1;
  PIOA->PIO_CODR = BITMASK_DFF1;
  PIOA->PIO_CODR = BITMASK_DS;
  PIOB->PIO_SODR = BITMASK_CON;
}

/**
 * @brief Execute one complete WL read cycle for the given read mode.
 *
 * Sequence: enter idle, confirm four zero rails, decide boost/HL/MUX routing,
 * program write_bl_topbtm_v on BLTOP (S1) or BLBTM (S2), and wait for OPC.
 * After write_bl_settle_after_opc_us, select the corresponding ON input HIGH and wait
 * write_bl_gate_settle_us. Assert boost+HL, wait settle_us, then readScaling
 * produces the WL pulse and ends with DFF selection OFF and CON disconnected.
 * De-assert S1/S2 and park N3/N4 at zero (TOP LOW, BTM LOW). The read supply
 * can still be biased: the MUX parks it away while CR remains HIGH for
 * ADC capture. The caller captures ADC first, resets the integrator, and only
 * then confirms all four rails at zero while keeping both MUX selects LOW.
 * No PSU wait is inserted while holding
 * the pending ADC sample. HL is cleared by enterSafeIdleState() after capture.
 *
 * Boost de-assertion mirror of decideReadPath:
 *   positive Q: ACTUAL clears S2, REFERENCE clears S1
 *   negative Q: ACTUAL clears S1, REFERENCE clears S2
 *
 * @param mode   Selects ACTUAL or REFERENCE read path.
 * @param params Read settings; q, precision, step_us, settle_us, read_delay,
 * write_bl_topbtm_v, write_bl_settle_after_opc_us, write_bl_gate_settle_us,
 * opc_timeout_ms consumed here.
 * @return OP_STATUS_OK once the read cycle completes normally.
 * @return OP_STATUS_ABORTED when CMD ABORT arrives during the write-bias wait.
 * @return OP_STATUS_ERROR on write-bias PSU timeout/mismatch failure.
 */
OperationStatus readSingleWL(ReadMode mode, const ReadParams &params) {
  enterSafeIdleState();
  OperationStatus wait_status = zeroBitlineSupplies(params.opc_timeout_ms, "READ_BASELINE");
  if (wait_status != OP_STATUS_OK) return wait_status;

  // Both OFF inputs remain zero. Park N3/N4 before raising either ON rail.
  parkBitlinePaths();
  ReadPathDecision decision = decideReadPath(mode, params.q);

  wait_status = requestAndWaitPsu(
      decision.boost_channel, params.write_bl_topbtm_v, params.opc_timeout_ms, "READ_WRITE_BIAS");
  if (wait_status != OP_STATUS_OK) return wait_status;

  // requestAndWaitPsu() moved the reported state to WAIT_PSU_OPC; restore the
  // READING_* state immediately so STATE lines stay accurate for the rest of
  // this read.
  setState(mode == READ_MODE_ACTUAL ? STATE_READING_ACTUAL : STATE_READING_REFERENCE);

  if (params.write_bl_settle_after_opc_us > 0) {
    delayMicroseconds(params.write_bl_settle_after_opc_us);
  }

  // Both read paths select their positive ON bias HIGH after PSU preparation.
  if (decision.gate_high) PIOC->PIO_SODR = decision.gate_bitmask;
  else PIOC->PIO_CODR = decision.gate_bitmask;
  if (params.write_bl_gate_settle_us > 0) delayMicroseconds(params.write_bl_gate_settle_us);

  applyReadPath(decision);
  if (params.settle_us > 0) delayMicroseconds(params.settle_us);
  readScaling(fabsf(params.q), params.precision, params.step_us, params.read_delay);
  parkBitlinePaths(); // Close S1/S2, then return N3/N4 to the zero inputs.

  // Keep CR HIGH and do not issue slow PSU requests here: the caller must
  // capture the held ADC value before zeroing the four bitline supplies.

  return OP_STATUS_OK;
}

/**
 * @brief Apply one simultaneous S1/S2 update pulse.
 *
 * @param pulse_width_us Pulse width in microseconds for the update event.
 * @return No return value. The pulse is applied directly on the hardware pins.
 */
void applyUpdatePulse(int pulse_width_us) {
  PIOC->PIO_SODR = BITMASK_S1 | BITMASK_S2;
  delayMicroseconds(pulse_width_us);
  PIOC->PIO_CODR = BITMASK_S1 | BITMASK_S2;
  if (g_compound_timing) g_compound_update_end_us = compoundClockUs();
}

/**
 * @brief Read one ADC sample from the integrated output node.
 *
 * The ADC peripheral is configured in FREERUN mode in setup() so the conversion
 * data register is always up to date. Reading CDR[7] (A0 = channel 7) directly
 * avoids the analogRead() path that resets the ADC configuration.
 * The 12-bit raw value (0-4095) is divided by 4 to stay in the 0-1023 range
 * consistent with ADC_CODE_MAX.
 *
 * @return One ADC code in the 0..1023 range.
 */
int readSingleAdcCode() {
  int adc_value = (int)(ADC->ADC_CDR[7]) / 4;
  if (adc_value < 0) adc_value = 0;
  if (adc_value > ADC_CODE_MAX) adc_value = ADC_CODE_MAX;
  return adc_value;
}

/**
 * @brief Capture warm-up and kept ADC samples for one read command.
 *
 * Warm-up samples are discarded so the caller can ignore short transients.
 * The returned ReadResult keeps both the average and the last sample because
 * the host can choose either reporting mode through avg=...
 *
 * @param params Requested sampling configuration:
 *   - sample_count: number of kept samples
 *   - warmup_count: number of discarded pre-samples
 *   - settle_us/inter_sample_us: timing between reads
 *   - average_enable: select average vs last-sample reporting
 * @param[out] result Filled with raw_sum/min/max, average_adc, reported_adc,
 * first/last sample, final sample_count, and retained sample_values_count.
 * @param keep_samples Retain kept codes in g_read_sample_values for standalone
 * reports. Warmups are excluded; no serial I/O is performed during capture.
 * @return true when at least one kept sample was captured successfully.
 * @return false when the sample count is invalid or exceeds retained storage.
 */
bool captureReadSamples(const ReadParams &params, ReadResult &result, bool keep_samples) {
  result.raw_sum = 0;
  result.raw_min = ADC_CODE_MAX;
  result.raw_max = 0;
  result.average_adc = 0.0f;
  result.reported_adc = 0.0f;
  result.sample_count = params.sample_count;
  result.first_sample = 0;
  result.last_sample = 0;
  result.sample_values_count = 0;

  if (params.sample_count <= 0 || (keep_samples && params.sample_count > MAX_READ_SAMPLE_VALUES)) return false;

  for (int i = 0; i < params.warmup_count; ++i) {
    (void)readSingleAdcCode();
    if (params.inter_sample_us > 0) delayMicroseconds(params.inter_sample_us);
  }

  for (int i = 0; i < params.sample_count; ++i) {
    int sample = readSingleAdcCode();
    if (keep_samples) g_read_sample_values[i] = sample;
    if (i == 0) result.first_sample = sample;
    result.last_sample = sample;
    result.raw_sum += sample;
    if (sample < result.raw_min) result.raw_min = sample;
    if (sample > result.raw_max) result.raw_max = sample;
    if ((i + 1) < params.sample_count && params.inter_sample_us > 0) {
      delayMicroseconds(params.inter_sample_us);
    }
  }

  result.average_adc = static_cast<float>(result.raw_sum) / static_cast<float>(params.sample_count);
  result.reported_adc = params.average_enable ? result.average_adc : static_cast<float>(result.last_sample);
  result.sample_values_count = keep_samples ? params.sample_count : 0;
  return true;
}

/*
 * ================================
 * |     State & Reporting        |
 * ================================
 */

/**
 * @brief Convert an internal state enum into the externally reported text name.
 *
 * @param state Internal firmware state.
 * @return Constant C-string used in STATE ... serial lines.
 */
const char *stateName(FirmwareState state) {
  switch (state) {
    case STATE_BOOTING: return "BOOTING";
    case STATE_IDLE: return "IDLE";
    case STATE_WAIT_PSU_OPC: return "WAIT_PSU_OPC";
    case STATE_UPDATING: return "UPDATING";
    case STATE_READING_ACTUAL: return "READING_ACTUAL";
    case STATE_READING_REFERENCE: return "READING_REFERENCE";
    case STATE_RUNNING_UPDATE_AND_READ: return "RUNNING_UPDATE_AND_READ";
    case STATE_RUNNING_RETENTION: return "RUNNING_RETENTION";
    case STATE_RUNNING_DISTURB: return "RUNNING_DISTURB";
    case STATE_ADC_VERIFY: return "ADC_VERIFY";
    case STATE_DEBUG_FIRE: return "DEBUG_FIRE";
    case STATE_ERROR_STATE: return "ERROR_STATE";
    default: return "UNKNOWN";
  }
}

/**
 * @brief Update the internal state machine and emit a STATE line if it changed.
 *
 * @param next_state Requested next firmware state.
 * @return No return value. Serial output is produced only on real state changes.
 */
void setState(FirmwareState next_state) {
  if (g_state == next_state) return;
  g_state = next_state;

  char buffer[64];
  snprintf(buffer, sizeof(buffer), "STATE %s", stateName(next_state));
  Serial.println(buffer);
}

/**
 * @brief Emit the BOOT READY banner used by the host to detect startup.
 *
 * @return No return value. Writes one BOOT READY line to Serial.
 */
void emitBootReady() {
  char buffer[96];
  snprintf(buffer, sizeof(buffer), "BOOT READY version=%s protocol=ASCII_KV", VERSION);
  Serial.println(buffer);
}

/**
 * @brief Emit one success line with OK prefix.
 *
 * @param message Payload appended after the OK prefix.
 * @return No return value. Writes one line to Serial.
 */
void emitOk(const char *message) {
  char buffer[192];
  snprintf(buffer, sizeof(buffer), "OK %s", message);
  Serial.println(buffer);
}

/**
 * @brief Emit one error line with ERR prefix.
 *
 * @param message Payload appended after the ERR prefix.
 * @return No return value. Writes one line to Serial.
 */
void emitError(const char *message) {
  char buffer[192];
  snprintf(buffer, sizeof(buffer), "ERR %s", message);
  Serial.println(buffer);
}

/**
 * @brief Emit a BUSY error that includes the current firmware state name.
 *
 * @return No return value. Writes one ERR BUSY ... line to Serial.
 */
void emitBusyError() {
  char buffer[96];
  snprintf(buffer, sizeof(buffer), "BUSY state=%s", stateName(g_state));
  emitError(buffer);
}

/**
 * @brief Emit one PONG reply for host liveness checks.
 *
 * @return No return value. Writes INFO PONG while a compound command is active,
 * otherwise the standalone OK PONG reply.
 */
void emitPong() {
  char buffer[96];
  if (g_compound_timing) {
    // The host treats OK as command completion; liveness during an active
    // compound run must not stop its receive/PSU-service loop.
    snprintf(buffer, sizeof(buffer), "INFO PONG version=%s state=%s", VERSION, stateName(g_state));
    Serial.println(buffer);
  } else {
    snprintf(buffer, sizeof(buffer), "PONG version=%s state=%s", VERSION, stateName(g_state));
    emitOk(buffer);
  }
}

/**
 * @brief Emit one compact help line listing supported commands.
 *
 * @return No return value. Writes one OK HELP ... line to Serial.
 */
void emitHelp() {
  emitOk("HELP commands=UPDATE,READ_ACTUAL,READ_REFERENCE,UPDATE_AND_READ,RETENTION_TEST,READ_DISTURB_TEST,ADC_VERIFY,DEBUG_FIRE,ABORT,PING syntax=ASCII_KV");
}

/**
 * @brief Print the startup spec lines expected by the host runner.
 *
 * This also drives the control pins to a safe idle state before the first
 * external command is accepted.
 *
 * @return No return value. Writes BOOT READY, STATE IDLE, and SPEC lines.
 */
void printCommandSpec() {
  enterSafeIdleState();
  emitBootReady();
  setState(STATE_IDLE);
  Serial.println("SPEC,command,key_value_args");
  Serial.println("SPEC_CMDS,UPDATE,READ_ACTUAL,READ_REFERENCE,UPDATE_AND_READ,RETENTION_TEST,READ_DISTURB_TEST,ADC_VERIFY,DEBUG_FIRE,ABORT,PING");
}

/**
 * @brief Emit one normalized DATA line for a completed readback.
 *
 * @param label Text label inserted after DATA, usually READ_ACTUAL or READ_REFERENCE.
 * @param result Read summary containing the values to serialize.
 * @return No return value. Writes one DATA ... line to Serial.
 */
void emitReadData(const char *label, const ReadResult &result) {
  char buffer[192];
  snprintf(buffer, sizeof(buffer),
           "DATA %s adc=%d avg=%.3f samples=%d min=%d max=%d",
           label,
           (int)(result.reported_adc + 0.5f),
           result.average_adc,
           result.sample_count,
           result.raw_min,
           result.raw_max);
  Serial.print(buffer);
  if (result.sample_values_count > 0) {
    Serial.print(" values=");
    for (int i = 0; i < result.sample_values_count; ++i) {
      if (i > 0) Serial.print(',');
      Serial.print(g_read_sample_values[i]);
    }
  }
  Serial.println();
}

/*
 * ================================
 * |   Parsing & Validation       |
 * ================================
 */

/**
 * @brief Copy a C-string into a fixed-size destination safely.
 *
 * @param[out] dst Destination character buffer.
 * @param src Source C-string. NULL is treated as empty string.
 * @param dst_size Size of dst in bytes.
 * @return No return value. dst is always null-terminated when dst_size > 0.
 */
void copyText(char *dst, const char *src, int dst_size) {
  if (dst_size == 0) return;
  if (src == NULL) src = "";
  strncpy(dst, src, dst_size - 1);
  dst[dst_size - 1] = '\0';
}

/**
 * @brief Remove leading and trailing ASCII whitespace from a mutable string.
 *
 * @param[in,out] text Buffer modified in-place.
 * @return No return value. The same buffer now holds the trimmed text.
 */
void trimWhitespace(char *text) {
  if (text == NULL || text[0] == '\0') return;

  int start = 0;
  while (text[start] != '\0' && isspace((int)text[start])) {
    ++start;
  }

  if (start > 0) {
    memmove(text, text + start, strlen(text + start) + 1);
  }

  int len = strlen(text);
  while (len > 0 && isspace((int)text[len - 1])) {
    text[--len] = '\0';
  }
}

/**
 * @brief Perform exact case-sensitive string equality.
 *
 * @param lhs Left-hand C-string.
 * @param rhs Right-hand C-string.
 * @return true when both strings are non-null and exactly identical.
 * @return false otherwise.
 */
bool stringsEqual(const char *lhs, const char *rhs) {
  if (lhs == NULL || rhs == NULL) return false;
  return strcmp(lhs, rhs) == 0;
}

/**
 * @brief Parse one whitespace-separated protocol line into prefix/name/fields.
 *
 * Accepted examples:
 * - PING
 * - CMD UPDATE ch=BLTOP v=1.65 pw_us=100
 * - OPC PSU_SET ch=BLTOP v=1.65
 *
 * @param[in,out] line Mutable scratch buffer; strtok_r modifies it during parsing.
 * @param[out] parsed Structured parse result filled with prefix/name/fields.
 * @return true when the line matches one of the supported protocol forms.
 * @return false when the line is malformed or uses an unsupported prefix.
 */
bool parseLine(char *line, ParsedLine &parsed) {
  memset(&parsed, 0, sizeof(parsed));

  char *save_ptr = NULL;
  char *token = strtok_r(line, " \t", &save_ptr);
  if (token == NULL) return false;

  copyText(parsed.prefix, token, sizeof(parsed.prefix));

  if (stringsEqual(token, "PING") || stringsEqual(token, "HELP")) {
    copyText(parsed.name, token, sizeof(parsed.name));
    return true;
  }

  if (!stringsEqual(token, "CMD") && !stringsEqual(token, "OPC")) {
    return false;
  }

  token = strtok_r(NULL, " \t", &save_ptr);
  if (token == NULL) return false;
  copyText(parsed.name, token, sizeof(parsed.name));

  while ((token = strtok_r(NULL, " \t", &save_ptr)) != NULL) {
    char *eq = strchr(token, '=');
    if (eq == NULL) return false;
    *eq = '\0';
    ++eq;

    if (parsed.field_count >= MAX_FIELD_COUNT) return false;
    copyText(parsed.fields[parsed.field_count].key, token, sizeof(parsed.fields[parsed.field_count].key));
    copyText(parsed.fields[parsed.field_count].value, eq, sizeof(parsed.fields[parsed.field_count].value));
    ++parsed.field_count;
  }

  return true;
}

/**
 * @brief Copy/trim a raw input line and then parse it into structured fields.
 *
 * @param line Raw newline-free serial line as received from the host.
 * @param[out] parsed Structured parse result.
 * @return true when parsing succeeds after trimming.
 * @return false when the line is empty after trimming or fails parseLine(...).
 */
bool parseRawLine(const char *line, ParsedLine &parsed) {
  char scratch[MAX_LINE_LENGTH];
  copyText(scratch, line, sizeof(scratch));
  trimWhitespace(scratch);
  if (scratch[0] == '\0') return false;
  return parseLine(scratch, parsed);
}

/**
 * @brief Look up one key in a ParsedLine field list.
 *
 * @param parsed Parsed command/opc line.
 * @param key Exact key name to search for.
 * @return Pointer to the matching value string when found.
 * @return NULL when the key is not present.
 */
const char *findField(const ParsedLine &parsed, const char *key) {
  for (int i = 0; i < parsed.field_count; ++i) {
    if (stringsEqual(parsed.fields[i].key, key)) {
      return parsed.fields[i].value;
    }
  }
  return NULL;
}

/**
 * @brief Parse one decimal integer token.
 *
 * @param text Input text token.
 * @param[out] value Parsed integer result when successful.
 * @return true on strict full-token parse success.
 * @return false when text is empty, malformed, or only partially consumed.
 */
bool parseIntText(const char *text, int &value) {
  if (text == NULL || text[0] == '\0') return false;
  char *end_ptr = NULL;
  long parsed = strtol(text, &end_ptr, 10);
  if (end_ptr == NULL || *end_ptr != '\0') return false;
  value = static_cast<int>(parsed);
  return true;
}

/**
 * @brief Parse one finite floating-point token.
 *
 * @param text Input text token.
 * @param[out] value Parsed floating-point result when successful.
 * @return true on strict full-token parse success.
 * @return false when text is empty, malformed, non-finite, or partially consumed.
 */
bool parseFloatText(const char *text, float &value) {
  if (text == NULL || text[0] == '\0') return false;
  char *end_ptr = NULL;
  float parsed = strtof(text, &end_ptr);
  if (end_ptr == NULL || *end_ptr != '\0' || !isfinite(parsed)) return false;
  value = parsed;
  return true;
}

/**
 * @brief Parse one boolean token accepted by the firmware protocol.
 *
 * @param text Input text token.
 * @param[out] value Parsed boolean result.
 * @return true for accepted values 0/1/true/false (case forms supported here).
 * @return false for anything else.
 */
bool parseBoolText(const char *text, bool &value) {
  if (text == NULL) return false;
  if (stringsEqual(text, "1") || stringsEqual(text, "true") || stringsEqual(text, "TRUE")) {
    value = true;
    return true;
  }
  if (stringsEqual(text, "0") || stringsEqual(text, "false") || stringsEqual(text, "FALSE")) {
    value = false;
    return true;
  }
  return false;
}

/**
 * @brief Load one required text field from a parsed command line.
 *
 * @param parsed Parsed command line containing key/value fields.
 * @param key Required key name.
 * @param[out] dst Destination buffer for the copied value.
 * @param dst_size Size of dst in bytes.
 * @return true when the key exists and contains non-empty text.
 * @return false after emitting ERR BAD_ARG when the key is missing/empty.
 */
bool loadRequiredText(const ParsedLine &parsed, const char *key, char *dst, int dst_size) {
  const char *value = findField(parsed, key);
  if (value == NULL || value[0] == '\0') {
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "BAD_ARG key=%s", key);
    emitError(buffer);
    return false;
  }
  copyText(dst, value, dst_size);
  return true;
}

/**
 * @brief Load one required integer field from a parsed command line.
 *
 * @param parsed Parsed command line containing key/value fields.
 * @param key Required key name.
 * @param[out] value Parsed integer result.
 * @return true when the key exists and parses as an integer.
 * @return false after emitting ERR BAD_ARG on failure.
 */
bool loadRequiredInt(const ParsedLine &parsed, const char *key, int &value) {
  const char *text = findField(parsed, key);
  if (!parseIntText(text, value)) {
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "BAD_ARG key=%s", key);
    emitError(buffer);
    return false;
  }
  return true;
}

/**
 * @brief Load one required floating-point field from a parsed command line.
 *
 * @param parsed Parsed command line containing key/value fields.
 * @param key Required key name.
 * @param[out] value Parsed float result.
 * @return true when the key exists and parses as a float.
 * @return false after emitting ERR BAD_ARG on failure.
 */
bool loadRequiredFloat(const ParsedLine &parsed, const char *key, float &value) {
  const char *text = findField(parsed, key);
  if (!parseFloatText(text, value)) {
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "BAD_ARG key=%s", key);
    emitError(buffer);
    return false;
  }
  return true;
}

/**
 * @brief Load one optional integer field with fallback default.
 *
 * @param parsed Parsed command line containing key/value fields.
 * @param key Optional key name.
 * @param[out] value Parsed integer result or default_value when absent.
 * @param default_value Value used when the key is not present.
 * @return true when the field is absent or parses successfully.
 * @return false after emitting ERR BAD_ARG when the present value is malformed.
 */
bool loadOptionalInt(const ParsedLine &parsed, const char *key, int &value, int default_value) {
  const char *text = findField(parsed, key);
  if (text == NULL) {
    value = default_value;
    return true;
  }
  if (!parseIntText(text, value)) {
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "BAD_ARG key=%s", key);
    emitError(buffer);
    return false;
  }
  return true;
}

/**
 * @brief Load one optional boolean field with fallback default.
 *
 * @param parsed Parsed command line containing key/value fields.
 * @param key Optional key name.
 * @param[out] value Parsed bool result or default_value when absent.
 * @param default_value Value used when the key is not present.
 * @return true when the field is absent or parses successfully.
 * @return false after emitting ERR BAD_ARG when the present value is malformed.
 */
bool loadOptionalBool(const ParsedLine &parsed, const char *key, bool &value, bool default_value) {
  const char *text = findField(parsed, key);
  if (text == NULL) {
    value = default_value;
    return true;
  }
  if (!parseBoolText(text, value)) {
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "BAD_ARG key=%s", key);
    emitError(buffer);
    return false;
  }
  return true;
}

/**
 * @brief Parse the parameter subset shared by all update-bearing commands.
 *
 * @param parsed Parsed top-level command line.
 * @param[out] params Filled with logical channel, target voltage, pulse width,
 * repeat count, settle delay, and OPC timeout.
 * @return true when all required fields parse and the values pass basic checks.
 * @return false after emitting ERR BAD_ARG on validation failure.
 */
bool loadUpdateParams(const ParsedLine &parsed, UpdateParams &params) {
  if (!loadRequiredFloat(parsed, "bltop_v", params.bltop_voltage)) return false;
  if (!loadRequiredFloat(parsed, "blbtm_v", params.blbtm_voltage)) return false;
  if (!loadRequiredInt(parsed, "pw_us", params.pulse_width_us)) return false;
  if (!loadOptionalInt(parsed, "settle_after_opc_us", params.settle_after_opc_us, DEFAULT_UPDATE_SETTLE_AFTER_OPC_US)) return false;
  if (!loadOptionalInt(parsed, "repeat", params.repeat_count, DEFAULT_UPDATE_REPEAT_COUNT)) return false;
  if (!loadOptionalInt(parsed, "opc_timeout_ms", params.opc_timeout_ms, DEFAULT_OPC_TIMEOUT_MS)) return false;
  if (!loadOptionalInt(parsed, "update_gate_settle_us", params.update_gate_settle_us, DEFAULT_UPDATE_GATE_SETTLE_US)) return false;

  if (params.repeat_count == 0 || params.pulse_width_us == 0) {
    emitError("BAD_ARG key=pw_us/repeat");
    return false;
  }

  return true;
}

/**
 * @brief Parse the parameter subset shared by all read-bearing commands.
 *
 * Required fields: q (query value in [-1,1]), precision (PWM bits), step_us (slot duration).
 * Optional fields: samples, warmup, settle_us, inter_us, avg, read_delay.
 *
 * @param parsed Parsed top-level command line.
 * @param[out] params Filled with all read settings.
 * @return true when all required fields parse and optional values are valid.
 * @return false after emitting ERR BAD_ARG on validation failure.
 */
bool loadReadParams(const ParsedLine &parsed, ReadParams &params) {
  if (!loadRequiredFloat(parsed, "q", params.q)) return false;
  if (!loadRequiredInt(parsed, "precision", params.precision)) return false;
  if (!loadRequiredInt(parsed, "step_us", params.step_us)) return false;
  if (!loadRequiredFloat(parsed, "write_bl_topbtm_v", params.write_bl_topbtm_v)) return false;
  if (!loadOptionalInt(parsed, "samples", params.sample_count, DEFAULT_SAMPLE_COUNT)) return false;
  if (!loadOptionalInt(parsed, "warmup", params.warmup_count, DEFAULT_WARMUP_COUNT)) return false;
  if (!loadOptionalInt(parsed, "settle_us", params.settle_us, DEFAULT_READ_SETTLE_US)) return false;
  if (!loadOptionalInt(parsed, "inter_us", params.inter_sample_us, DEFAULT_INTER_SAMPLE_US)) return false;
  if (!loadOptionalBool(parsed, "avg", params.average_enable, DEFAULT_AVERAGE_ENABLE)) return false;
  if (!loadOptionalInt(parsed, "read_delay", params.read_delay, DEFAULT_READ_DELAY)) return false;
  if (!loadOptionalInt(parsed, "write_bl_settle_after_opc_us", params.write_bl_settle_after_opc_us, DEFAULT_WRITE_BL_SETTLE_AFTER_OPC_US)) return false;
  // Named write_bl_opc_timeout_ms (not opc_timeout_ms) so a combined command
  // like UPDATE_AND_READ, whose line carries both an update opc_timeout_ms
  // and this one, has two distinct keys instead of one silently shadowing
  // the other in findField()'s first-match lookup.
  if (!loadOptionalInt(parsed, "write_bl_opc_timeout_ms", params.opc_timeout_ms, DEFAULT_OPC_TIMEOUT_MS)) return false;
  if (!loadOptionalInt(parsed, "write_bl_gate_settle_us", params.write_bl_gate_settle_us, DEFAULT_WRITE_BL_GATE_SETTLE_US)) return false;

  if (params.sample_count == 0) {
    emitError("BAD_ARG key=samples");
    return false;
  }

  return true;
}

/**
 * @brief Parse the full argument bundle for CMD UPDATE_AND_READ.
 *
 * @param parsed Parsed top-level command line.
 * @param[out] params Filled with both update settings and read settings plus post_us.
 * @return true when the full command bundle is valid.
 * @return false when any sub-bundle fails validation.
 */
bool loadUpdateAndReadParams(const ParsedLine &parsed, UpdateAndReadParams &params) {
  if (!loadUpdateParams(parsed, params.update)) return false;
  if (!loadReadParams(parsed, params.read)) return false;
  if (!loadOptionalInt(parsed, "post_us", params.post_update_delay_us, DEFAULT_POST_UPDATE_DELAY_US)) return false;
  return true;
}

/**
 * @brief Parse the full argument bundle for CMD RETENTION_TEST.
 *
 * @param parsed Parsed top-level command line.
 * @param[out] params Filled with update settings, read settings, and loop timing.
 * @return true when the full command bundle is valid.
 * @return false when any sub-bundle fails validation.
 */
bool loadRetentionParams(const ParsedLine &parsed, RetentionParams &params) {
  if (!loadUpdateParams(parsed, params.update)) return false;
  if (!loadReadParams(parsed, params.read)) return false;
  if (!loadOptionalInt(parsed, "points", params.point_count, DEFAULT_RETENTION_POINTS)) return false;
  if (!loadOptionalInt(parsed, "initial_wait_ms", params.initial_wait_ms, DEFAULT_INITIAL_WAIT_MS)) return false;
  if (!loadOptionalInt(parsed, "interval_ms", params.interval_ms, DEFAULT_INTERVAL_MS)) return false;

  if (params.point_count == 0) {
    emitError("BAD_ARG key=points");
    return false;
  }

  return true;
}

/**
 * @brief Parse the full argument bundle for CMD READ_DISTURB_TEST.
 *
 * @param parsed Parsed top-level command line.
 * @param[out] params Filled with update settings, read settings, and disturb-loop timing.
 * @return true when the full command bundle is valid.
 * @return false when any sub-bundle fails validation.
 */
bool loadDisturbParams(const ParsedLine &parsed, DisturbParams &params) {
  if (!loadUpdateParams(parsed, params.update)) return false;
  if (!loadReadParams(parsed, params.read)) return false;
  if (!loadOptionalInt(parsed, "cycles", params.cycle_count, DEFAULT_DISTURB_CYCLES)) return false;
  if (!loadOptionalInt(parsed, "initial_wait_ms", params.initial_wait_ms, DEFAULT_INITIAL_WAIT_MS)) return false;
  if (!loadOptionalInt(parsed, "interval_ms", params.interval_ms, DEFAULT_INTERVAL_MS)) return false;

  if (params.cycle_count == 0) {
    emitError("BAD_ARG key=cycles");
    return false;
  }

  return true;
}

/*
 * ================================
 * |        Serial I/O            |
 * ================================
 */

/**
 * @brief Incrementally collect one newline-terminated protocol line from Serial.
 *
 * This function keeps state in g_rx_line/g_rx_len so the main loop can remain
 * responsive even when characters arrive over multiple loop() iterations.
 *
 * @param[out] out Destination buffer for the completed line.
 * @param out_size Size of out in bytes.
 * @return LINE_STATUS_NONE when no complete line is ready yet.
 * @return LINE_STATUS_READY when out contains one trimmed complete line.
 * @return LINE_STATUS_OVERFLOW when the incoming line exceeded MAX_LINE_LENGTH.
 */
LineReadStatus pollSerialLine(char *out, int out_size) {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());

    if (c == '\r') continue;

    if (c == '\n') {
      if (g_rx_overflow) {
        g_rx_len = 0;
        g_rx_overflow = false;
        if (out_size > 0) out[0] = '\0';
        return LINE_STATUS_OVERFLOW;
      }

      g_rx_line[g_rx_len] = '\0';
      copyText(out, g_rx_line, out_size);
      trimWhitespace(out);
      g_rx_len = 0;
      return LINE_STATUS_READY;
    }

    if (g_rx_overflow) continue;

    if (g_rx_len < (MAX_LINE_LENGTH - 1)) {
      g_rx_line[g_rx_len++] = c;
    } else {
      g_rx_overflow = true;
    }
  }

  return LINE_STATUS_NONE;
}

/*
 * ================================
 * |      Command Helpers         |
 * ================================
 */

/**
 * @brief Test whether a parsed line is the top-level abort command.
 *
 * @param parsed Parsed line.
 * @return true only for CMD ABORT.
 * @return false for all other inputs.
 */
bool isAbortCommand(const ParsedLine &parsed) {
  return stringsEqual(parsed.prefix, "CMD") && stringsEqual(parsed.name, "ABORT");
}

/**
 * @brief Test whether a parsed line is the bare PING utility line.
 *
 * @param parsed Parsed line.
 * @return true only for PING.
 * @return false otherwise.
 */
bool isPingCommand(const ParsedLine &parsed) {
  return stringsEqual(parsed.prefix, "PING");
}

/**
 * @brief Test whether a parsed line requests protocol help.
 *
 * @param parsed Parsed line.
 * @return true for HELP or CMD HELP.
 * @return false otherwise.
 */
bool isHelpCommand(const ParsedLine &parsed) {
  return stringsEqual(parsed.prefix, "HELP") || (stringsEqual(parsed.prefix, "CMD") && stringsEqual(parsed.name, "HELP"));
}

/**
 * @brief Abort the current operation and return the firmware to idle.
 *
 * @return OP_STATUS_ABORTED after safe-idle pins, STATE IDLE, and OK ABORTED are emitted.
 */
OperationStatus abortToIdle() {
  enterSafeIdleState();
  setState(STATE_IDLE);
  emitOk("ABORTED");
  return OP_STATUS_ABORTED;
}

/**
 * @brief Request one PSU channel's voltage and wait for matching OPC confirmation.
 *
 * This is the critical host/firmware handshake reused by every step that
 * needs a PSU line at a known setting: update BLTOP/BLBTM, read bias
 * BLTOP/BLBTM, and baseline/cleanup zero settings for all four rails.
 * The caller does not proceed until both
 * channel and voltage match the outstanding REQ PSU_SET line.
 *
 * @param channel Logical rail: BLTOP, BLBTM, BLTOP_OFF, or BLBTM_OFF.
 * @param voltage Target voltage for that channel.
 * @param timeout_ms Max wait for a matching OPC PSU_SET reply.
 * @param step_tag Short label identifying the call site (e.g. "UPDATE_BLTOP",
 * "READ_WRITE_BIAS", "READ_ZERO"). Included in every ERR line this
 * function emits, so a failure can be traced back to exactly which of the
 * (now several) handshakes within one command produced it.
 * @return OP_STATUS_OK when a matching OPC PSU_SET is received in time.
 * @return OP_STATUS_ABORTED when CMD ABORT arrives while waiting.
 * @return OP_STATUS_ERROR on timeout or other fatal handshake failure.
 */
OperationStatus requestAndWaitPsu(const char *channel, float voltage, int timeout_ms, const char *step_tag) {
  char request_buffer[128];
  snprintf(request_buffer, sizeof(request_buffer), "REQ PSU_SET ch=%s v=%.6f", channel, voltage);
  Serial.println(request_buffer);

  setState(STATE_WAIT_PSU_OPC);

  uint32_t start_ms = (uint32_t)millis();
  char line[MAX_LINE_LENGTH];

  while (timeout_ms > 0 && (uint32_t)((uint32_t)millis() - start_ms) < (uint32_t)timeout_ms) {
    if (g_compound_timing) (void)compoundClockUs();
    LineReadStatus line_status = pollSerialLine(line, sizeof(line));
    if (line_status == LINE_STATUS_NONE) {
      delay(1);
      continue;
    }

    if (line_status == LINE_STATUS_OVERFLOW) {
      emitError("BAD_CMD reason=line_too_long");
      continue;
    }

    if (line[0] == '\0') continue;

    ParsedLine parsed;
    if (!parseRawLine(line, parsed)) {
      emitError("BAD_CMD");
      continue;
    }

    if (isPingCommand(parsed)) {
      emitPong();
      continue;
    }

    if (isAbortCommand(parsed)) {
      return abortToIdle();
    }

    if (stringsEqual(parsed.prefix, "OPC") && stringsEqual(parsed.name, "PSU_SET")) {
      char opc_channel[MAX_CHANNEL_LENGTH];
      float opc_voltage = 0.0f;
      if (!loadRequiredText(parsed, "ch", opc_channel, sizeof(opc_channel))) continue;
      if (!loadRequiredFloat(parsed, "v", opc_voltage)) continue;

      if (!stringsEqual(opc_channel, channel)) {
        char mismatch_buffer[96];
        snprintf(mismatch_buffer, sizeof(mismatch_buffer), "BAD_PSU_OPC reason=channel_mismatch step=%s", step_tag);
        emitError(mismatch_buffer);
        continue;
      }

      if (fabsf(opc_voltage - voltage) > OPC_VOLTAGE_TOLERANCE) {
        char mismatch_buffer[96];
        snprintf(mismatch_buffer, sizeof(mismatch_buffer), "BAD_PSU_OPC reason=voltage_mismatch step=%s", step_tag);
        emitError(mismatch_buffer);
        continue;
      }

      return OP_STATUS_OK;
    }

    emitBusyError();
  }

  enterSafeIdleState();
  setState(STATE_ERROR_STATE);
  char timeout_buffer[64];
  snprintf(timeout_buffer, sizeof(timeout_buffer), "TIMEOUT_PSU_OPC step=%s ch=%s", step_tag, channel);
  emitError(timeout_buffer);
  setState(STATE_IDLE);
  return OP_STATUS_ERROR;
}

/**
 * @brief Confirm all four bitline supply settings are zero, in a bounded sequence.
 *
 * Call only with S1/S2 and the read path inactive. Read callers must capture
 * ADC data first; these handshakes can take much longer than the read pulse.
 * Each channel is attempted once with timeout_ms, and the first failure is
 * propagated without further requests. GPIO idle cannot itself force a PSU
 * to zero; the host must perform shutdown if communication/programming fails.
 */
OperationStatus zeroBitlineSupplies(int timeout_ms, const char *step_tag) {
  const char *channels[] = {"BLTOP", "BLBTM", "BLTOP_OFF", "BLBTM_OFF"};
  for (int i = 0; i < 4; ++i) {
    OperationStatus status = requestAndWaitPsu(channels[i], 0.0f, timeout_ms, step_tag);
    if (status != OP_STATUS_OK) return status;
  }
  // Both OFF inputs are zero; keep the default LOW selects between operations.
  parkBitlinePaths();
  return OP_STATUS_OK;
}

/**
 * @brief Delay in milliseconds while still allowing PING and ABORT handling.
 *
 * Retention and disturb experiments spend most of their time here. Polling for
 * serial commands keeps long waits interruptible without introducing threads.
 *
 * @param wait_ms Requested delay in milliseconds.
 * @return OP_STATUS_OK when the full wait completed normally.
 * @return OP_STATUS_ABORTED when CMD ABORT interrupted the wait.
 */
OperationStatus waitMillisecondsWithAbort(int wait_ms) {
  if (wait_ms == 0) return OP_STATUS_OK;

  int start_ms = (int)millis();
  char line[MAX_LINE_LENGTH];

  while (((int)millis() - start_ms) < wait_ms) {
    LineReadStatus line_status = pollSerialLine(line, sizeof(line));
    if (line_status == LINE_STATUS_NONE) {
      delay(1);
      continue;
    }

    if (line_status == LINE_STATUS_OVERFLOW) {
      emitError("BAD_CMD reason=line_too_long");
      continue;
    }

    if (line[0] == '\0') continue;

    ParsedLine parsed;
    if (!parseRawLine(line, parsed)) {
      emitError("BAD_CMD");
      continue;
    }

    if (isPingCommand(parsed)) {
      emitPong();
      continue;
    }

    if (isAbortCommand(parsed)) {
      return abortToIdle();
    }

    emitBusyError();
  }

  return OP_STATUS_OK;
}

/*
 * ================================
 * |        Operations            |
 * ================================
 */

/**
 * @brief Execute the update phase common to UPDATE, UPDATE_AND_READ, RETENTION,
 * and READ_DISTURB.
 *
 * BLTOP_GATE/BLBTM_GATE (46/34) open once before the whole repeat_count pulse
 * train (not once per pulse -- a single settle wait is enough since the MUX
 * stays selected to the "on" input the entire time) and close once after, so
 * the two series elements (MUX select + S1/S2) both connect BLTOP/BLBTM to
 * VCAP for the full charging window. Four zero-setting handshakes establish
 * the baseline first and restore it after S1/S2 are OFF; completion is not
 * reported until cleanup succeeds. Both ON supplies now have normal polarity;
 * firmware forwards their nonnegative settings and VCAP = BLTOP - BLBTM.
 *
 * @param params Update settings including channel, voltage, pulse width, repeats,
 * settle delay, and OPC timeout.
 * @param resume_state State to restore after the update finishes successfully.
 * @param emit_completion When true, emit the terminal OK UPDATE_APPLIED ... line.
 * @return OP_STATUS_OK when the update completed normally.
 * @return OP_STATUS_ABORTED when the host aborted during the OPC wait.
 * @return OP_STATUS_ERROR on timeout or fatal handshake failure.
 */
OperationStatus performUpdate(const UpdateParams &params, FirmwareState resume_state, bool emit_completion) {
  enterSafeIdleState();

  OperationStatus wait_status = zeroBitlineSupplies(params.opc_timeout_ms, "UPDATE_BASELINE");
  if (wait_status != OP_STATUS_OK) return wait_status;

  wait_status = requestAndWaitPsu("BLTOP", params.bltop_voltage, params.opc_timeout_ms, "UPDATE_BLTOP");
  if (wait_status != OP_STATUS_OK) return wait_status;

  wait_status = requestAndWaitPsu("BLBTM", params.blbtm_voltage, params.opc_timeout_ms, "UPDATE_BLBTM");
  if (wait_status != OP_STATUS_OK) return wait_status;

  setState(STATE_UPDATING);
  if (params.settle_after_opc_us > 0) {
    delayMicroseconds(params.settle_after_opc_us);
  }

  PIOC->PIO_SODR = BITMASK_BLTOP_GATE | BITMASK_BLBTM_GATE;
  if (params.update_gate_settle_us > 0) {
    delayMicroseconds(params.update_gate_settle_us);
  }

  for (int i = 0; i < params.repeat_count; ++i) {
    applyUpdatePulse(params.pulse_width_us);
  }

  PIOC->PIO_CODR = BITMASK_BLTOP_GATE | BITMASK_BLBTM_GATE;

  enterSafeIdleState();
  wait_status = zeroBitlineSupplies(params.opc_timeout_ms, "UPDATE_ZERO");
  if (wait_status != OP_STATUS_OK) return wait_status;
  setState(resume_state);

  if (emit_completion) {
    char buffer[160];
    snprintf(buffer, sizeof(buffer),
             "UPDATE_APPLIED bltop_v=%.6f blbtm_v=%.6f pulses=%d pw_us=%d",
             params.bltop_voltage,
             params.blbtm_voltage,
             params.repeat_count,
             params.pulse_width_us);
    emitOk(buffer);
  }

  return OP_STATUS_OK;
}

/**
 * @brief Execute one ADC read on either the ACTUAL or REFERENCE path.
 *
 * resume_state tells the helper which state should be restored after the read:
 * - IDLE for top-level single reads
 * - RUNNING_* for compound experiments
 *
 * @param mode Selects ACTUAL vs REFERENCE read path.
 * @param params Sampling settings for warmup/settle/sample_count/reporting mode.
 * @param[out] result Filled with the readback summary on success.
 * @param resume_state State restored after the read completes.
 * @param emit_data When true, emit a DATA READ_ACTUAL/... line directly.
 * @return OP_STATUS_OK when sampling completed successfully.
 * @return OP_STATUS_ERROR when captureReadSamples(...) fails.
 */
OperationStatus performRead(ReadMode mode, const ReadParams &params, ReadResult &result,
                            FirmwareState resume_state, bool emit_data) {
  // Refuse an oversized standalone report before changing pins or PSU rails.
  if (emit_data && params.sample_count > MAX_READ_SAMPLE_VALUES) {
    emitError("BAD_ARG key=samples max=4096");
    return OP_STATUS_ERROR;
  }
  setState(mode == READ_MODE_ACTUAL ? STATE_READING_ACTUAL : STATE_READING_REFERENCE);

  OperationStatus read_status = readSingleWL(mode, params);
  if (read_status != OP_STATUS_OK) {
    // Baseline/bias failures leave the digital paths idle. No boost has been
    // enabled, but PSU levels are not guaranteed zero. A terminal response
    // has already been emitted: stop requests and let the host shut down.
    return read_status;
  }

  if (!captureReadSamples(params, result, emit_data)) {
    PIOB->PIO_CODR = BITMASK_CR;
    enterSafeIdleState();
    OperationStatus zero_status = zeroBitlineSupplies(params.opc_timeout_ms, "READ_ZERO");
    if (zero_status != OP_STATUS_OK) return zero_status;
    setState(STATE_ERROR_STATE);
    emitError("ADC_FAIL");
    setState(STATE_IDLE);
    return OP_STATUS_ERROR;
  }

  PIOB->PIO_CODR = BITMASK_CR;
  delay(3);
  enterSafeIdleState();
  OperationStatus zero_status = zeroBitlineSupplies(params.opc_timeout_ms, "READ_ZERO");
  if (zero_status != OP_STATUS_OK) return zero_status;
  setState(resume_state);

  if (emit_data) {
    emitReadData(mode == READ_MODE_ACTUAL ? "READ_ACTUAL" : "READ_REFERENCE", result);
  }

  return OP_STATUS_OK;
}

/**
 * @brief Execute CMD UPDATE_AND_READ.
 *
 * @param params Combined update bundle, read bundle, and post-update delay.
 * @return OP_STATUS_OK when both update and read complete normally.
 * @return OP_STATUS_ABORTED when the host aborts during the update handshake.
 * @return OP_STATUS_ERROR on handshake or read failure.
 */
OperationStatus runUpdateAndRead(const UpdateAndReadParams &params) {
  if (!validateCompoundTiming(params.update, params.read, 1, 0, 0, false)) {
    return OP_STATUS_ERROR;
  }
  beginCompoundTiming(); // Reuse the same prepared read path, without a deadline.
  setState(STATE_RUNNING_UPDATE_AND_READ);

  OperationStatus status = performUpdate(params.update, STATE_RUNNING_UPDATE_AND_READ, false);
  if (status != OP_STATUS_OK) {
    g_compound_timing = false; // PSU helper already sent the terminal reply.
    return status;
  }

  if (params.post_update_delay_us > 0) {
    delayMicroseconds(params.post_update_delay_us);
  }

  status = prepareCompoundReadBias(params.read);
  if (status != OP_STATUS_OK) {
    g_compound_timing = false; // Stop requests after an unsuccessful handshake.
    return status;
  }
  setState(STATE_READING_ACTUAL);
  ReadResult read_result;
  CompoundReadTiming timing;
  status = performPreparedRead(READ_MODE_ACTUAL, params.read, read_result, 0, timing);
  status = finishCompound(status, params.read);
  if (status != OP_STATUS_OK) return status;

  char buffer[192];
  snprintf(buffer, sizeof(buffer),
           "DATA UPDATE_AND_READ idx=0 bltop_v=%.6f blbtm_v=%.6f pw_us=%d adc=%d avg=%.3f samples=%d",
           params.update.bltop_voltage,
           params.update.blbtm_voltage,
           params.update.pulse_width_us,
           (int)(read_result.reported_adc + 0.5f),
           read_result.average_adc,
           read_result.sample_count);
  Serial.println(buffer);

  setState(STATE_IDLE);
  emitOk("UPDATE_AND_READ_DONE points=1");
  return OP_STATUS_OK;
}

/**
 * @brief Execute CMD RETENTION_TEST.
 *
 * @param params Combined update bundle, read bundle, number of points, and loop timing.
 * @return OP_STATUS_OK when all requested retention points were emitted.
 * @return OP_STATUS_ABORTED when the host aborts during wait windows.
 * @return OP_STATUS_ERROR on update/read failure.
 */
/** Extend micros() by unsigned deltas. All long waits poll this clock; each
 * indivisible update/read block is validated shorter than one 32-bit wrap. */
uint64_t compoundClockUs() {
  uint32_t now_low = (uint32_t)micros();
  uint32_t elapsed_us = (uint32_t)(now_low - g_compound_clock_low);
  g_compound_clock_us += (uint64_t)elapsed_us;
  g_compound_clock_low = now_low;
  return g_compound_clock_us;
}

void beginCompoundTiming() {
  g_compound_clock_low = (uint32_t)micros();
  g_compound_clock_us = 0;
  g_compound_update_end_us = 0;
  g_compound_read_target_us = 0;
  g_compound_read_overrun = false;
  g_compound_error[0] = '\0';
  g_compound_timing = true;
}

/** Reject impossible periods before any UPDATE. This is a lower bound from
 * requested delays; real GPIO/ADC/UART overhead is detected at run time.
 * interval_ms=0 means run as fast as possible, with no periodic deadline. */
bool validateCompoundTiming(const UpdateParams &update, const ReadParams &read,
                            int count, int initial_wait_ms, int interval_ms, bool pair) {
  if (count <= 0 || initial_wait_ms < 0 || interval_ms < 0 ||
      update.pulse_width_us <= 0 || update.repeat_count <= 0 ||
      update.settle_after_opc_us < 0 || update.update_gate_settle_us < 0 ||
      update.opc_timeout_ms <= 0 || read.opc_timeout_ms <= 0 ||
      read.precision < 1 || read.precision > 16 || read.step_us <= 0 ||
      !isfinite(read.q) || fabsf(read.q) > 1.0f ||
      !isfinite(read.write_bl_topbtm_v) || read.write_bl_topbtm_v < 0.0f ||
      read.sample_count <= 0 || read.sample_count > 2099202 ||
      read.warmup_count < 0 || read.inter_sample_us < 0 ||
      read.settle_us < 0 || read.read_delay < 0 ||
      read.write_bl_gate_settle_us < 0 || read.write_bl_settle_after_opc_us < 0) {
    emitError("BAD_ARG reason=compound_timing");
    return false;
  }
  uint64_t frame_us = (1ULL << read.precision) * (uint64_t)read.step_us;
  uint64_t sample_waits = (uint64_t)read.warmup_count + (uint64_t)read.sample_count - 1ULL;
  uint64_t read_min_us = frame_us + (uint64_t)read.read_delay * 10ULL +
      (uint64_t)read.settle_us +
      sample_waits * (uint64_t)read.inter_sample_us + 3000ULL;
  uint64_t update_block_us = (uint64_t)update.pulse_width_us * (uint64_t)update.repeat_count +
      (uint64_t)update.settle_after_opc_us + (uint64_t)update.update_gate_settle_us;
  // The software clock is sampled outside these non-interruptible delays.
  if (read_min_us > 0x7fffffffULL || update_block_us > 0x7fffffffULL ||
      (uint64_t)read.write_bl_settle_after_opc_us > 0x7fffffffULL) {
    emitError("BAD_ARG reason=timing_block_too_long");
    return false;
  }
  uint64_t minimum_us = read_min_us;
  if (pair) minimum_us *= 2ULL;
  uint64_t period_us = (uint64_t)interval_ms * 1000ULL;
  if (period_us > 0 && (uint64_t)(count - 1) > 0xffffffffffffffffULL / period_us) {
    emitError("BAD_ARG reason=timing_duration_overflow");
    return false;
  }
  if (count > 1 && period_us > 0 && period_us < minimum_us) {
    char buffer[128];
    snprintf(buffer, sizeof(buffer), "PERIOD_TOO_SHORT period_us=%llu minimum_us=%llu",
             (unsigned long long)period_us, (unsigned long long)minimum_us);
    emitError(buffer);
    return false;
  }
  return true;
}

/** Poll only between read operations or while waiting. Never emit a terminal
 * reply here: finishCompound() must zero rails before host stops listening. */
OperationStatus pollCompoundControl() {
  char line[MAX_LINE_LENGTH];
  LineReadStatus line_status = pollSerialLine(line, sizeof(line));
  if (line_status == LINE_STATUS_NONE) return OP_STATUS_OK;
  if (line_status == LINE_STATUS_OVERFLOW) {
    copyText(g_compound_error, "BAD_CMD reason=line_too_long", sizeof(g_compound_error));
    return OP_STATUS_ERROR;
  }
  if (line[0] == '\0') return OP_STATUS_OK;
  ParsedLine parsed;
  if (!parseRawLine(line, parsed)) {
    copyText(g_compound_error, "BAD_CMD", sizeof(g_compound_error));
    return OP_STATUS_ERROR;
  }
  if (isAbortCommand(parsed)) return OP_STATUS_ABORTED;
  if (isPingCommand(parsed)) {
    emitPong();
    return OP_STATUS_OK;
  }
  copyText(g_compound_error, "BUSY reason=compound_running", sizeof(g_compound_error));
  return OP_STATUS_ERROR;
}

/** Absolute 64-bit deadline with S1/S2 OFF. Serial service never occurs in
 * the final trim after opening an access transistor. */
OperationStatus waitCompoundUntil(uint64_t target_us) {
  while (true) {
    OperationStatus status = pollCompoundControl();
    if (status != OP_STATUS_OK) return status;
    uint64_t now_us = compoundClockUs();
    if (now_us >= target_us) return OP_STATUS_OK;
    uint64_t remaining_us = target_us - now_us;
    if (remaining_us > 2000ULL) delay(1);
    else if (remaining_us > 100ULL) delayMicroseconds(50);
    else delayMicroseconds(1);
  }
}

/** Prepare both positive ON rails once with S1/S2 OFF, then select both
 * BL MUXes HIGH. N3/N4 stay biased through waits, reads, ADC and reset.
 * finishCompound() closes access and parks the MUXes before zeroing rails. */
OperationStatus prepareCompoundReadBias(const ReadParams &params) {
  enterSafeIdleState();
  ReadPathDecision actual = decideReadPath(READ_MODE_ACTUAL, params.q);
  OperationStatus status = requestAndWaitPsu(actual.boost_channel, params.write_bl_topbtm_v,
                                            params.opc_timeout_ms, "COMPOUND_ACTUAL_BIAS");
  if (status != OP_STATUS_OK) return status;
  ReadPathDecision reference = decideReadPath(READ_MODE_REFERENCE, params.q);
  status = requestAndWaitPsu(reference.boost_channel, params.write_bl_topbtm_v,
                            params.opc_timeout_ms, "COMPOUND_REFERENCE_BIAS");
  if (status != OP_STATUS_OK) return status;
  if (params.write_bl_settle_after_opc_us > 0) delayMicroseconds(params.write_bl_settle_after_opc_us);
  PIOC->PIO_SODR = BITMASK_BLTOP_GATE | BITMASK_BLBTM_GATE;
  if (params.write_bl_gate_settle_us > 0) delayMicroseconds(params.write_bl_gate_settle_us);
  return OP_STATUS_OK;
}

/** Execute one prepared read without PSU messages or repeated STATE output.
 * The period is measured between first DFF edges, not loop/function entries.
 * No overdue point is silently compressed into a catch-up sequence. */
OperationStatus performPreparedRead(ReadMode mode, const ReadParams &params,
                                    ReadResult &result, uint64_t target_us,
                                    CompoundReadTiming &timing) {
  OperationStatus status = pollCompoundControl();
  if (status != OP_STATUS_OK) return status;
  resetReadControls(); // Access is OFF; keep both BL MUXes selected.
  int steps = 1 << params.precision;
  int on_count = computeReadOnCount(fabsf(params.q), steps);
  uint64_t prep_us = (uint64_t)params.settle_us + COMPOUND_PREP_MARGIN_US;
  if (target_us > prep_us) {
    status = waitCompoundUntil(target_us - prep_us);
    if (status != OP_STATUS_OK) return status;
  }
  uint64_t before_prepare_us = compoundClockUs();
  if (target_us > 0 && before_prepare_us > target_us + COMPOUND_LATE_TOLERANCE_US) {
    snprintf(g_compound_error, sizeof(g_compound_error),
             "PERIOD_OVERRUN reason=read_start_deadline late_us=%llu tolerance_us=50",
             (unsigned long long)(before_prepare_us - target_us));
    return OP_STATUS_ERROR;
  }

  ReadPathDecision decision = decideReadPath(mode, params.q);
  // Do not wait until target-settle here: that consumes the dispatch margin
  // before GPIO setup even starts. settle_us is the minimum S1/S2 settling;
  // scheduled reads may settle longer while readScalingSlots waits for target.
  applyReadPath(decision);
  if (params.settle_us > 0) delayMicroseconds(params.settle_us);
  g_compound_read_target_us = target_us;
  g_compound_read_overrun = false;
  readScalingSlots(steps, on_count, params.step_us, params.read_delay);
  PIOC->PIO_CODR = BITMASK_S1 | BITMASK_S2; // Hold N3/N4 bias; isolate VCAP.
  timing.start_us = g_compound_read_timing.start_us;
  timing.late_us = g_compound_read_timing.late_us;
  if (g_compound_read_overrun) {
    enterSafeIdleState();
    snprintf(g_compound_error, sizeof(g_compound_error),
             "PERIOD_OVERRUN reason=first_dff_deadline late_us=%llu tolerance_us=50",
             (unsigned long long)timing.late_us);
    return OP_STATUS_ERROR;
  }
  if (!captureReadSamples(params, result)) {
    enterSafeIdleState();
    copyText(g_compound_error, "ADC_FAIL", sizeof(g_compound_error));
    return OP_STATUS_ERROR;
  }
  PIOB->PIO_CODR = BITMASK_CR;
  delay(3);
  resetReadControls();
  return OP_STATUS_OK;
}

/** Normal completion and locally detected errors/ABORT all zero rails first.
 * A PSU cleanup failure already emitted its own terminal response and must
 * not be followed by another REQ; the host forces its shutdown policy. */
OperationStatus finishCompound(OperationStatus status, const ReadParams &params) {
  enterSafeIdleState();
  OperationStatus zero_status = zeroBitlineSupplies(params.opc_timeout_ms, "COMPOUND_ZERO");
  g_compound_timing = false;
  if (zero_status != OP_STATUS_OK) return zero_status;
  setState(STATE_IDLE);
  if (status == OP_STATUS_ABORTED) emitOk("ABORTED");
  else if (status == OP_STATUS_ERROR) {
    if (g_compound_error[0] == '\0') emitError("COMPOUND_FAILED");
    else emitError(g_compound_error);
  }
  return status;
}

OperationStatus runRetentionTest(const RetentionParams &params) {
  if (!validateCompoundTiming(params.update, params.read, params.point_count,
                              params.initial_wait_ms, params.interval_ms, false)) {
    return OP_STATUS_ERROR;
  }
  beginCompoundTiming();
  setState(STATE_RUNNING_RETENTION);
  OperationStatus status = performUpdate(params.update, STATE_RUNNING_RETENTION, false);
  if (status != OP_STATUS_OK) {
    g_compound_timing = false; // Existing PSU failure/ABORT already sent terminal reply.
    return status;            // Host forced shutdown; do not issue more REQs.
  }
  status = prepareCompoundReadBias(params.read);
  if (status != OP_STATUS_OK) {
    g_compound_timing = false;
    return status;
  }
  setState(STATE_RUNNING_RETENTION);
  Serial.println("INFO TIMING schema=2 origin=update_end edge=first_dff q0=logical_slot tolerance_us=50");
  Serial.flush(); // Drain startup information before the periodic section.

  // Preserve initial_wait_ms as an additional wait AFTER bias preparation.
  // The reported age still includes UPDATE cleanup and all PSU preparation.
  uint64_t initial_target_us = compoundClockUs() + (uint64_t)params.initial_wait_ms * 1000ULL;
  status = waitCompoundUntil(initial_target_us);
  if (status != OP_STATUS_OK) return finishCompound(status, params.read);

  uint64_t first_start_us = 0;
  uint64_t previous_start_us = 0;
  uint64_t period_us = (uint64_t)params.interval_ms * 1000ULL;
  for (int idx = 0; idx < params.point_count; ++idx) {
    uint64_t target_us = 0;
    if (idx > 0 && period_us > 0) target_us = first_start_us + (uint64_t)idx * period_us;
    ReadResult read_result;
    CompoundReadTiming timing;
    status = performPreparedRead(READ_MODE_ACTUAL, params.read, read_result, target_us, timing);
    if (status != OP_STATUS_OK) return finishCompound(status, params.read);
    if (idx == 0) first_start_us = timing.start_us;
    uint64_t dt_us = 0;
    if (idx > 0) dt_us = timing.start_us - previous_start_us;
    previous_start_us = timing.start_us;

    char buffer[192];
    snprintf(buffer, sizeof(buffer),
             "DATA RETENTION idx=%d t_us=%llu dt_us=%llu late_us=%llu adc=%d avg=%.3f samples=%d",
             idx,
             (unsigned long long)(timing.start_us - g_compound_update_end_us),
             (unsigned long long)dt_us,
             (unsigned long long)timing.late_us,
             (int)(read_result.reported_adc + 0.5f),
             read_result.average_adc, read_result.sample_count);
    Serial.println(buffer);
  }

  status = finishCompound(OP_STATUS_OK, params.read);
  if (status != OP_STATUS_OK) return status;
  char done_buffer[64];
  snprintf(done_buffer, sizeof(done_buffer), "RETENTION_DONE points=%d", params.point_count);
  emitOk(done_buffer);
  return OP_STATUS_OK;
}

/**
 * @brief Execute CMD READ_DISTURB_TEST.
 *
 * @param params Combined update bundle, read bundle, number of cycles, and loop timing.
 * @return OP_STATUS_OK when all requested disturb cycles were emitted.
 * @return OP_STATUS_ABORTED when the host aborts during wait windows.
 * @return OP_STATUS_ERROR on update/read failure.
 */
OperationStatus runReadDisturbTest(const DisturbParams &params) {
  if (!validateCompoundTiming(params.update, params.read, params.cycle_count,
                              params.initial_wait_ms, params.interval_ms, true)) {
    return OP_STATUS_ERROR;
  }
  beginCompoundTiming();
  setState(STATE_RUNNING_DISTURB);
  OperationStatus status = performUpdate(params.update, STATE_RUNNING_DISTURB, false);
  if (status != OP_STATUS_OK) {
    g_compound_timing = false;
    return status;
  }
  status = prepareCompoundReadBias(params.read);
  if (status != OP_STATUS_OK) {
    g_compound_timing = false;
    return status;
  }
  setState(STATE_RUNNING_DISTURB);
  Serial.println("INFO TIMING schema=2 origin=update_end edge=first_dff q0=logical_slot tolerance_us=50");
  Serial.flush();
  uint64_t initial_target_us = compoundClockUs() + (uint64_t)params.initial_wait_ms * 1000ULL;
  status = waitCompoundUntil(initial_target_us);
  if (status != OP_STATUS_OK) return finishCompound(status, params.read);

  uint64_t first_start_us = 0;
  uint64_t previous_start_us = 0;
  uint64_t period_us = (uint64_t)params.interval_ms * 1000ULL;
  for (int idx = 0; idx < params.cycle_count; ++idx) {
    uint64_t target_us = 0;
    if (idx > 0 && period_us > 0) target_us = first_start_us + (uint64_t)idx * period_us;
    ReadResult actual_result;
    ReadResult reference_result;
    CompoundReadTiming actual_timing;
    CompoundReadTiming reference_timing;
    status = performPreparedRead(READ_MODE_ACTUAL, params.read, actual_result, target_us, actual_timing);
    if (status != OP_STATUS_OK) return finishCompound(status, params.read);
    status = performPreparedRead(READ_MODE_REFERENCE, params.read, reference_result, 0, reference_timing);
    if (status != OP_STATUS_OK) {
      // Preserve an already captured ACTUAL when its pair is interrupted.
      // INFO keeps an incomplete pair out of normal paired CSV/plot rows.
      char partial_buffer[160];
      snprintf(partial_buffer, sizeof(partial_buffer),
               "INFO DISTURB_PARTIAL idx=%d t_us=%llu actual=%d actual_avg=%.3f reason=reference_incomplete",
               idx, (unsigned long long)(actual_timing.start_us - g_compound_update_end_us),
               (int)(actual_result.reported_adc + 0.5f), actual_result.average_adc);
      Serial.println(partial_buffer);
      return finishCompound(status, params.read);
    }
    if (idx == 0) first_start_us = actual_timing.start_us;
    uint64_t dt_us = 0;
    if (idx > 0) dt_us = actual_timing.start_us - previous_start_us;
    previous_start_us = actual_timing.start_us;

    char buffer[256];
    snprintf(buffer, sizeof(buffer),
             "DATA DISTURB idx=%d t_us=%llu ref_us=%llu dt_us=%llu late_us=%llu actual=%d actual_avg=%.3f reference=%d reference_avg=%.3f delta=%.3f",
             idx,
             (unsigned long long)(actual_timing.start_us - g_compound_update_end_us),
             (unsigned long long)(reference_timing.start_us - g_compound_update_end_us),
             (unsigned long long)dt_us,
             (unsigned long long)actual_timing.late_us,
             (int)(actual_result.reported_adc + 0.5f), actual_result.average_adc,
             (int)(reference_result.reported_adc + 0.5f), reference_result.average_adc,
             actual_result.average_adc - reference_result.average_adc);
    Serial.println(buffer);
  }

  status = finishCompound(OP_STATUS_OK, params.read);
  if (status != OP_STATUS_OK) return status;
  char done_buffer[72];
  snprintf(done_buffer, sizeof(done_buffer), "READ_DISTURB_DONE cycles=%d", params.cycle_count);
  emitOk(done_buffer);
  return OP_STATUS_OK;
}

/*
 * ================================
 * |          Debug               |
 * ================================
 */

/**
 * @brief Parse parameters for CMD DEBUG_FIRE.
 *
 * target=S1|S2 requires pw_us (pulse width in microseconds).
 * target=READ_ACTUAL|READ_REFERENCE requires q, precision, step_us; settle_us
 * and read_delay are optional.
 *
 * @param parsed Parsed command line.
 * @param[out] params Filled debug fire settings.
 * @return true when all required fields for the chosen target are present.
 * @return false after emitting ERR BAD_ARG on failure.
 */
bool loadDebugFireParams(const ParsedLine &parsed, DebugFireParams &params) {
  if (!loadRequiredText(parsed, "target", params.target, sizeof(params.target))) return false;

  bool is_s1   = stringsEqual(params.target, "S1");
  bool is_s2   = stringsEqual(params.target, "S2");
  bool is_read_actual    = stringsEqual(params.target, "READ_ACTUAL");
  bool is_read_reference = stringsEqual(params.target, "READ_REFERENCE");

  if (!is_s1 && !is_s2 && !is_read_actual && !is_read_reference) {
    emitError("BAD_ARG key=target");
    return false;
  }

  if (is_s1 || is_s2) {
    if (!loadRequiredInt(parsed, "pw_us", params.pw_us)) return false;
    params.q         = 0.0f;
    params.precision = 0;
    params.step_us   = 0;
    params.settle_us = 0;
    params.read_delay = 0;
    // target=S1/S2 is a raw, manual pin-level pulse for oscilloscope checks --
    // No read-bias handshake is needed for these digital-only targets.
    // performDebugFire() still confirms all four rails at zero before/after
    // the pulse. Both MUX selects stay LOW; use CMD UPDATE to deliver a
    // nonzero update voltage to V_TOP/V_BOT instead.
    params.write_bl_topbtm_v = 0.0f;
    params.write_bl_settle_after_opc_us = 0;
    params.opc_timeout_ms = DEFAULT_OPC_TIMEOUT_MS;
    params.write_bl_gate_settle_us = 0;
  } else {
    if (!loadRequiredFloat(parsed, "q", params.q)) return false;
    if (!loadRequiredInt(parsed, "precision", params.precision)) return false;
    if (!loadRequiredInt(parsed, "step_us", params.step_us)) return false;
    if (!loadRequiredFloat(parsed, "write_bl_topbtm_v", params.write_bl_topbtm_v)) return false;
    if (!loadOptionalInt(parsed, "settle_us", params.settle_us, DEFAULT_READ_SETTLE_US)) return false;
    if (!loadOptionalInt(parsed, "read_delay", params.read_delay, DEFAULT_READ_DELAY)) return false;
    if (!loadOptionalInt(parsed, "write_bl_settle_after_opc_us", params.write_bl_settle_after_opc_us, DEFAULT_WRITE_BL_SETTLE_AFTER_OPC_US)) return false;
    if (!loadOptionalInt(parsed, "write_bl_opc_timeout_ms", params.opc_timeout_ms, DEFAULT_OPC_TIMEOUT_MS)) return false;
    if (!loadOptionalInt(parsed, "write_bl_gate_settle_us", params.write_bl_gate_settle_us, DEFAULT_WRITE_BL_GATE_SETTLE_US)) return false;
    params.pw_us = 0;
  }

  return true;
}

/**
 * @brief Execute CMD DEBUG_FIRE for manual oscilloscope verification.
 *
 * target=S1 or S2: asserts the chosen boost transistor HIGH for pw_us, then
 * releases it. Use this to confirm the S1/S2 digital pulse shape on the
 * oscilloscope. Both MUX selects remain LOW and all four PSU rails are
 * confirmed at zero before/after the pulse. Use CMD UPDATE to apply nonzero
 * BLTOP/BLBTM update voltages to V_TOP/V_BOT.
 *
 * target=READ_ACTUAL or READ_REFERENCE: runs a full readSingleWL() cycle
 * (zero baseline → read-bias PSU handshake → applyReadPath → settle →
 * PWM-encoded WL integration → boost de-assert), then reads one ADC sample
 * and clears the integrator before confirming all four rails at zero.
 * Use this to observe
 * S1/S2, HL, WL0, and DFF1 waveforms simultaneously and verify the ADC
 * response to the programmed Q value.
 *
 * @param params Parsed debug fire settings.
 * @return OP_STATUS_OK after the pulse sequence completes.
 */
OperationStatus performDebugFire(const DebugFireParams &params) {
  setState(STATE_DEBUG_FIRE);
  enterSafeIdleState();

  bool is_s1 = stringsEqual(params.target, "S1");
  bool is_s2 = stringsEqual(params.target, "S2");

  if (is_s1 || is_s2) {
    OperationStatus zero_status = zeroBitlineSupplies(params.opc_timeout_ms, "DEBUG_BASELINE");
    if (zero_status != OP_STATUS_OK) return zero_status;
    setState(STATE_DEBUG_FIRE);
    if (is_s1) {
      PIOC->PIO_SODR = BITMASK_S1;
      delayMicroseconds(params.pw_us);
      PIOC->PIO_CODR = BITMASK_S1;
    } else {
      PIOC->PIO_SODR = BITMASK_S2;
      delayMicroseconds(params.pw_us);
      PIOC->PIO_CODR = BITMASK_S2;
    }
    enterSafeIdleState();
    zero_status = zeroBitlineSupplies(params.opc_timeout_ms, "DEBUG_ZERO");
    if (zero_status != OP_STATUS_OK) return zero_status;

    char buffer[72];
    snprintf(buffer, sizeof(buffer),
             "DEBUG_FIRE_DONE target=%s pw_us=%d", params.target, params.pw_us);
    emitOk(buffer);
    setState(STATE_IDLE);
    return OP_STATUS_OK;
  }

  ReadMode mode = stringsEqual(params.target, "READ_ACTUAL") ? READ_MODE_ACTUAL : READ_MODE_REFERENCE;

  ReadParams rp;
  rp.q              = params.q;
  rp.precision      = params.precision;
  rp.step_us        = params.step_us;
  rp.settle_us      = params.settle_us;
  rp.read_delay     = params.read_delay;
  rp.sample_count   = 1;
  rp.warmup_count   = 0;
  rp.inter_sample_us = 0;
  rp.average_enable = false;
  rp.write_bl_topbtm_v = params.write_bl_topbtm_v;
  rp.write_bl_settle_after_opc_us = params.write_bl_settle_after_opc_us;
  rp.opc_timeout_ms = params.opc_timeout_ms;
  rp.write_bl_gate_settle_us = params.write_bl_gate_settle_us;

  OperationStatus read_status = readSingleWL(mode, rp);
  if (read_status != OP_STATUS_OK) {
    return read_status;
  }
  int adc_val = readSingleAdcCode();
  PIOB->PIO_CODR = BITMASK_CR;
  delay(3);
  enterSafeIdleState();
  OperationStatus zero_status = zeroBitlineSupplies(params.opc_timeout_ms, "DEBUG_READ_ZERO");
  if (zero_status != OP_STATUS_OK) return zero_status;

  char buffer[128];
  snprintf(buffer, sizeof(buffer),
           "DEBUG_FIRE_DONE target=%s q=%.4f precision=%d step_us=%d adc=%d",
           params.target, params.q, params.precision, params.step_us, adc_val);
  emitOk(buffer);
  setState(STATE_IDLE);
  return OP_STATUS_OK;
}

/*
 * ================================
 * |     Top-Level Router         |
 * ================================
 */

/** Validate before touching hardware. channel and pulse_us are explicit. */
bool loadAdcVerifyParams(const ParsedLine &parsed, AdcVerifyParams &params) {
  if (!loadRequiredInt(parsed, "channel", params.channel) ||
      !loadRequiredInt(parsed, "pulse_us", params.pulse_us) ||
      !loadOptionalInt(parsed, "repeats", params.repeats, 1) ||
      !loadOptionalInt(parsed, "samples", params.samples, 4) ||
      !loadOptionalInt(parsed, "inter_us", params.inter_us, 25) ||
      !loadOptionalInt(parsed, "settle_us", params.settle_us, 250)) return false;
  if (params.channel < 0 || params.channel > 4 ||
      params.pulse_us < 1 || params.pulse_us > 1000000 ||
      params.repeats < 1 || params.repeats > 10000 ||
      params.samples < 1 || params.samples > 4096 ||
      params.inter_us < 0 || params.inter_us > 1000000 ||
      params.settle_us < 0 || params.settle_us > 1000000) {
    emitError("BAD_ARG ADC_VERIFY_RANGE");
    return false;
  }
  return true;
}

/** Poll without UART transmission while the integrator is active. */
OperationStatus pollAdcVerifyControl() {
  char line[MAX_LINE_LENGTH];
  LineReadStatus line_status = pollSerialLine(line, sizeof(line));
  if (line_status == LINE_STATUS_NONE) return OP_STATUS_OK;
  if (line_status == LINE_STATUS_OVERFLOW) return OP_STATUS_ERROR;
  ParsedLine parsed;
  if (!parseRawLine(line, parsed)) return OP_STATUS_ERROR;
  if (isAbortCommand(parsed)) return OP_STATUS_ABORTED;
  if (isPingCommand(parsed)) {
    g_adc_verify_pong_pending = true;
    return OP_STATUS_OK;
  }
  return OP_STATUS_ERROR;
}

OperationStatus waitAdcVerifyUs(uint32_t duration_us) {
  uint32_t started_us = micros();
  while ((uint32_t)(micros() - started_us) < duration_us) {
    OperationStatus status = pollAdcVerifyControl();
    if (status != OP_STATUS_OK) return status;
  }
  return OP_STATUS_OK;
}

/** Clock the shared DFF bank. Return the software-observed latch-edge time. */
uint32_t latchAdcVerifyWL(uint32_t mask) {
  PIOD->PIO_CODR = BITMASK_ALL_WL;
  PIOD->PIO_SODR = mask;
  PIOA->PIO_CODR = BITMASK_DFF1;
  delayMicroseconds(1);
  PIOA->PIO_SODR = BITMASK_DFF1;
  uint32_t edge_us = micros();
  delayMicroseconds(1);
  PIOA->PIO_CODR = BITMASK_DFF1;
  PIOD->PIO_CODR = BITMASK_ALL_WL;
  return edge_us;
}

int readAdcVerifyCode(int channel) {
  int value = static_cast<int>(ADC->ADC_CDR[ADC_VERIFY_CDR[channel]]) / 4;
  if (value < 0) value = 0;
  if (value > ADC_CODE_MAX) value = ADC_CODE_MAX;
  return value;
}

/**
 * Reset -> matched integration window -> isolate -> capture -> reset.
 * The baseline runs the same control sequence with every WL at VINP.
 * pulse_actual_us measures software latch edges; scope verification is still
 * required for the analog MUX pulse width and capacitor tolerances.
 */
OperationStatus captureAdcVerifyWindow(const AdcVerifyParams &params,
                                      bool signal, AdcVerifyWindow &result) {
  enterSafeIdleState();
  OperationStatus status = waitAdcVerifyUs(3000);
  if (status != OP_STATUS_OK) return status;
  PIOA->PIO_SODR = BITMASK_DS;
  PIOB->PIO_CODR = BITMASK_CON;
  PIOB->PIO_SODR = BITMASK_CR;
  uint32_t started_us = latchAdcVerifyWL(signal ? ADC_VERIFY_WL_MASKS[params.channel] : 0);
  status = waitAdcVerifyUs(params.pulse_us);
  if (status != OP_STATUS_OK) return status;
  uint32_t ended_us = latchAdcVerifyWL(0);
  result.duration_us = (uint32_t)(ended_us - started_us);
  PIOA->PIO_CODR = BITMASK_DS;
  PIOB->PIO_SODR = BITMASK_CON;
  status = waitAdcVerifyUs(params.settle_us);
  if (status != OP_STATUS_OK) return status;
  int total = 0;
  result.minimum = ADC_CODE_MAX;
  result.maximum = 0;
  for (int i = 0; i < params.samples; i++) {
    status = pollAdcVerifyControl();
    if (status != OP_STATUS_OK) return status;
    int value = readAdcVerifyCode(params.channel);
    total += value;
    if (value < result.minimum) result.minimum = value;
    if (value > result.maximum) result.maximum = value;
    if (i + 1 < params.samples) {
      status = waitAdcVerifyUs(params.inter_us);
      if (status != OP_STATUS_OK) return status;
    }
  }
  result.adc = static_cast<float>(total) / params.samples;
  PIOB->PIO_CODR = BITMASK_CR;
  return waitAdcVerifyUs(3000);
}

OperationStatus performAdcVerify(const AdcVerifyParams &params) {
  enterSafeIdleState();
  setState(STATE_ADC_VERIFY);
  g_adc_verify_pong_pending = false;
  for (int i = 0; i < params.repeats; i++) {
    AdcVerifyWindow baseline;
    AdcVerifyWindow signal;
    OperationStatus status = captureAdcVerifyWindow(params, false, baseline);
    if (status == OP_STATUS_OK) status = captureAdcVerifyWindow(params, true, signal);
    if (status != OP_STATUS_OK) {
      enterSafeIdleState();
      setState(STATE_IDLE);
      if (status == OP_STATUS_ABORTED) emitOk("ABORTED");
      else emitError("ADC_VERIFY_CONTROL_ERROR");
      return status;
    }
    char buffer[384];
    snprintf(buffer, sizeof(buffer),
             "DATA ADC_VERIFY channel=%d repeat=%d baseline=%.3f adc=%.3f pulse_us=%d pulse_actual_us=%lu baseline_window_us=%lu samples=%d baseline_min=%d baseline_max=%d adc_min=%d adc_max=%d",
             params.channel, i + 1, baseline.adc, signal.adc, params.pulse_us,
             (unsigned long)signal.duration_us, (unsigned long)baseline.duration_us,
             params.samples, baseline.minimum, baseline.maximum, signal.minimum, signal.maximum);
    Serial.println(buffer);
    if (g_adc_verify_pong_pending) {
      Serial.println("INFO PONG ADC_VERIFY");
      g_adc_verify_pong_pending = false;
    }
  }
  enterSafeIdleState();
  setState(STATE_IDLE);
  emitOk("ADC_VERIFY_DONE");
  return OP_STATUS_OK;
}

/**
 * @brief Top-level command router.
 *
 * This is the main "user command table" for the firmware. New users looking
 * for where a command name becomes behavior should start reading here.
 *
 * @param parsed One fully parsed top-level line from loop().
 * @return No return value. Serial replies are emitted directly by the selected path.
 */
void handleTopLevelLine(const ParsedLine &parsed) {
  if (isPingCommand(parsed)) {
    emitPong();
    return;
  }

  if (isHelpCommand(parsed)) {
    emitHelp();
    return;
  }

  if (!stringsEqual(parsed.prefix, "CMD")) {
    emitError("BAD_CMD");
    return;
  }

  if (isAbortCommand(parsed)) {
    (void)abortToIdle();
    return;
  }

  if (stringsEqual(parsed.name, "ADC_VERIFY")) {
    AdcVerifyParams params;
    if (!loadAdcVerifyParams(parsed, params)) return;
    (void)performAdcVerify(params);
    return;
  }

  if (stringsEqual(parsed.name, "UPDATE")) {
    UpdateParams params;
    if (!loadUpdateParams(parsed, params)) return;
    (void)performUpdate(params, STATE_IDLE, true);
    return;
  }

  if (stringsEqual(parsed.name, "READ_ACTUAL")) {
    ReadParams params;
    if (!loadReadParams(parsed, params)) return;

    ReadResult result;
    OperationStatus status = performRead(READ_MODE_ACTUAL, params, result, STATE_IDLE, true);
    if (status == OP_STATUS_OK) emitOk("READ_ACTUAL_DONE");
    return;
  }

  if (stringsEqual(parsed.name, "READ_REFERENCE")) {
    ReadParams params;
    if (!loadReadParams(parsed, params)) return;

    ReadResult result;
    OperationStatus status = performRead(READ_MODE_REFERENCE, params, result, STATE_IDLE, true);
    if (status == OP_STATUS_OK) emitOk("READ_REFERENCE_DONE");
    return;
  }

  if (stringsEqual(parsed.name, "UPDATE_AND_READ")) {
    UpdateAndReadParams params;
    if (!loadUpdateAndReadParams(parsed, params)) return;
    (void)runUpdateAndRead(params);
    return;
  }

  if (stringsEqual(parsed.name, "RETENTION_TEST")) {
    RetentionParams params;
    if (!loadRetentionParams(parsed, params)) return;
    (void)runRetentionTest(params);
    return;
  }

  if (stringsEqual(parsed.name, "READ_DISTURB_TEST")) {
    DisturbParams params;
    if (!loadDisturbParams(parsed, params)) return;
    (void)runReadDisturbTest(params);
    return;
  }

  if (stringsEqual(parsed.name, "DEBUG_FIRE")) {
    DebugFireParams params;
    if (!loadDebugFireParams(parsed, params)) return;
    (void)performDebugFire(params);
    return;
  }

  emitError("BAD_CMD");
}
