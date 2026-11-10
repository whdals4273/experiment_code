# 4T1C Dot-Product 아두이노 펌웨어

버전: 2.4.9 (`unitcell_4T1C_qk_dotproduct.ino`, 예약 read의 첫 DFF 준비 여유 확보; UPDATE_AND_READ·retention/disturb의 두 BL 바이어스·MUX 유지; 정상 BL 결선·대칭 ON 읽기; 39번=S2 게이트, 46번=BLTOP MUX; compound timing schema 2 유지)

2.4.0의 `pollAdcVerifyControl()`이 선언되지 않은 `LINE_READ_NONE`/`LINE_READ_OVERFLOW`를 참조해 Arduino 컴파일이 실패했다. 2.4.1은 기존 `LineReadStatus` 선언에 맞춰 `LINE_STATUS_NONE`/`LINE_STATUS_OVERFLOW`를 사용한다. 이전 소스 실행 모형은 해당 함수를 대역으로 바꿔 검사해 이 오류를 놓쳤으며, 실제 제어 함수의 무입력·overflow·파싱 실패·ABORT·PING·그 외 명령 분기를 실행하는 회귀 검증을 추가했다.

이 디렉토리는 예전의 DAC sweep 유틸리티 대신, 호스트-핸드셰이크 기반 4T1C 펌웨어를 사용합니다. 아두이노는 타이밍이 중요한 디지털 제어와 ADC 캡처를 담당하고, 호스트 PC는 아날로그 PSU 프로그래밍을 담당합니다 — 업데이트 펄스든 read 시 boost 트랜지스터를 여는 것이든, PSU 세팅이 필요한 모든 단계마다 아두이노가 보내는 `REQ PSU_SET ...`에 호스트가 `OPC PSU_SET ...`으로 응답해야 다음 단계로 넘어갑니다.

## 현재 스케치

- 스케치: `unitcell_4T1C_qk_dotproduct.ino` (사용자가 기존 `array_4T1C_qk_dotproduct.ino`의 이름을 변경)
- 시리얼 프로토콜: 줄 단위 ASCII, 대소문자 구분
- 권장 호스트 러너: [시나리오 README](../../../scenarios/arduino_4T1C_dotprod/README.md)의 `run.py`. PSU 채널 연결·극성·초기값·종료 정책은 해당 문서를 함께 확인합니다.
- 이 문서는 위 단일 셀 스케치 기준입니다. 과거 `_new.ino` 실험본은 수정 대상이 아닙니다.
- 지원 명령어:
  - `CMD UPDATE`
  - `CMD READ_ACTUAL`
  - `CMD READ_REFERENCE`
  - `CMD UPDATE_AND_READ`
  - `CMD RETENTION_TEST`
  - `CMD READ_DISTURB_TEST`
  - `CMD ADC_VERIFY`
  - `CMD DEBUG_FIRE`
  - `CMD ABORT`
  - `PING`

## 단독 READ 개별 샘플 보고 (2.4.3)

`READ_ACTUAL`과 `READ_REFERENCE`는 기존 요약에 취득 순서의 `values` 필드를 추가합니다.

```text
DATA READ_ACTUAL adc=1015 avg=1015.000 samples=4 min=1007 max=1023 values=1007,1011,1019,1023
```

`samples=4`는 WL 펄스 **한 번** 뒤 격리된 적분기 출력을 ADC로 네 번 취득한다는 뜻이며, 네 번의 WL 펄스를 뜻하지 않습니다. `warmup`은 값 목록에서 제외합니다. 단독 READ는 1–4096개 코드를 8KiB 고정 버퍼에 저장하고, ADC 취득·적분기 리셋·PSU 0V 정리를 마친 뒤 보고합니다. 버퍼 한도를 넘으면 핀·전원 동작 전에 거부합니다. `avg=0`이어도 개별값과 평균은 보존하며 기존 `adc`는 마지막 샘플입니다. UPDATE_AND_READ·retention·disturb·ADC_VERIFY의 보고·취득 순서는 바꾸지 않습니다.

호스트는 샘플별 값·평균선과 실행 당시 q/precision/step_us·예상 VREAD ON 시간/전체 프레임을 같은 GUI/저장 PNG에 표시합니다. 이 기능에는 **2.4.3 재업로드**가 필요하며, 이전 평균·최솟값·최댓값만으로 과거 개별 코드를 복원할 수 없습니다.

## 정상 결선·ON 읽기 기준 (2.4.5)

사용자의 새 채널 표에 따라 **BLTOP ON/OFF와 BLBTM ON/OFF를 모두 정상 결선**으로 사용합니다. 네 BL PSU 설정값은 음수가 아니며 소프트웨어에서 부호를 반전하지 않습니다. UPDATE의 이상적인 저장 전압은 `VCAP = bltop_v − blbtm_v`입니다. `1V/1V → 0V`, `2V/1V → +1V`, `1V/2V → −1V`이며 예전 역결선 BTM처럼 두 설정값을 더하지 않습니다. 현재 프리셋의 수치·PSU 주소·채널 번호는 자동 변경하지 않았습니다.

읽기는 정상 극성인 **BLTOP ON → N3/S1**, **BLBTM ON → N4/S2**를 사용하며 해당 MUX를 HIGH로 선택합니다. GUI의 `write_bl_topbtm_v=1.65`는 해당 물리 입력에 +1.65V를 설정하며, q≥0 ACTUAL/q<0 REFERENCE는 S2, q<0 ACTUAL/q≥0 REFERENCE는 S1을 켭니다. 두 OFF 공급은 항상 0V입니다. 단독 READ_ACTUAL/READ_REFERENCE는 ADC 전에 양쪽 MUX를 LOW로 내립니다. **retention/disturb(2.4.7부터)와 UPDATE_AND_READ(2.4.8부터)는 양쪽 ON 전원과 MUX를 유지하고 접근 스위치만 하나씩 선택**하며, 전체 종료·중단·오류 시 MUX를 LOW로 내립니다. PSU 설정 중에는 S1/S2 OFF·MUX LOW입니다. VREAD·N1/N2 OFF·VSS의 역결선은 새 표대로 유지합니다. 이 동작에는 **2.4.8 재업로드가 필요**합니다.

호스트 UPDATE의 **VCAP 입력 사용**은 `use_vcap_input=true`일 때 `vcap_v`를 `BLTOP=max(VCAP,0)`, `BLBTM=max(-VCAP,0)`으로 바꿉니다. −1V는 TOP=0V/BTM=+1V입니다. 체크 해제 시 기존 두 전압을 직접 입력합니다. 네 UPDATE 포함 명령에 적용하고, Arduino 명령의 기존 두 인자는 그대로 사용합니다. 설정 스냅샷과 metadata에 입력 의도·유효 전압을 남깁니다.

## ADC_VERIFY (2.4.0)

외부 저항을 `WLUx–BLUx`에 한 개 연결하고 한 채널씩 검증합니다. 호스트가 네 BL 전원을 0V로 준비하고 VREAD를 공급합니다. 펌웨어는 S1/S2 OFF·HL/FB LOW 상태에서 모든 WL을 먼저 끄고, 무입력 기준 취득 후 선택 WL만 펄스하여 동일 채널 ADC를 취득합니다. 각 취득 전후 3ms 리셋을 수행하며 ADC 전에는 WL을 OFF로 래치하고 CON으로 적분기를 격리합니다. 이 명령의 반복 중에는 PSU 요청을 보내지 않습니다.

`CMD ADC_VERIFY channel=0 pulse_us=1000 repeats=3 samples=4 inter_us=25 settle_us=250`

채널 0–4의 Arduino WL 핀은 25/26/27/28/29(PD0/1/2/3/6), ADC 입력은 A0/A1/A2/A3/A4(`ADC_CDR[7/6/5/4/3]`)입니다. 기존 단일 셀 QK 명령은 WL0/A0를 사용합니다. 공통 idle은 다섯 WL 모두 OFF로 래치합니다. 설정에 사용한 마스크와 ADC는 6T1C 매핑을 따르며 실제 PCB 연결은 채널별 저항 시험으로 확인합니다.

결과는 `DATA ADC_VERIFY channel=... repeat=... baseline=... adc=... pulse_us=... pulse_actual_us=... baseline_window_us=... samples=... baseline_min=... baseline_max=... adc_min=... adc_max=...`이며 정상 종료는 `OK ADC_VERIFY_DONE`입니다. 펄스 중 PING은 취득 후 `INFO PONG ADC_VERIFY`로 응답하며 ABORT/오류 시 WL OFF·격리·리셋으로 돌아갑니다. 호스트가 R/C/VREAD/Vref로 기대값을 계산하고 CSV/JSON/PNG에 비교 결과를 저장합니다. 파라미터 범위와 사용법은 [시나리오 README](../../../scenarios/arduino_4T1C_dotprod/README.md#adc_verify-외부-저항으로-읽기-경로-검증-240)를 확인합니다.

## 동작 요약

PCB의 BLTOP/BLBTM은 2:1 아날로그 MUX를 거쳐 S1/S2에 연결됩니다. `BLTOP_GATE`(46번)·`BLBTM_GATE`(34번)는 **HIGH일 때 ON 입력, LOW일 때 OFF 입력을 선택**합니다. LOW는 전기적 차단이 아닙니다. 현재 시퀀스에서 `BLTOP_OFF/BLBTM_OFF`는 모두 0V이며 ON 입력만 읽기 바이어스로 사용합니다.

현재 배선에서는 BLTOP ON/OFF와 BLBTM ON/OFF 전원이 모두 정상 극성입니다. GUI/명령에는 PSU의 **음수가 아닌 설정값**을 입력합니다. 예를 들어 `bltop_v=1 blbtm_v=1`은 PCB에서 +1V/+1V를 의도하고 이상적인 VCAP은 0V입니다. 펌웨어가 `blbtm_v`의 부호를 반전하지 않습니다. S2 읽기에는 `BLBTM`(ON)을 +1.65V로 설정합니다.

UPDATE와 단독 READ의 시작·정상 종료에는 `BLTOP → BLBTM → BLTOP_OFF → BLBTM_OFF` 순서로 0V를 요청하고 각 OPC를 확인합니다. 2.3.0의 retention/disturb 반복부는 읽기 전원을 한 번 준비해 유지하고, 마지막에 네 전원을 0V로 정리합니다. **PSU 설정을 바꾸는 동안 S1/S2는 OFF**입니다. OPC는 호스트의 설정 완료 응답이며 실제 PCB 전압 측정을 대신하지 않습니다.

1. **UPDATE:** 읽기 DFF OFF·CON 분리·적분기 초기화 → 4개 전원 0V 확인 → BLTOP/BLBTM ON 전원 설정 → 두 MUX HIGH → S1/S2 동시 펄스 → S1/S2 OFF → MUX LOW → 4개 전원 0V 확인.
2. **READ_ACTUAL / READ_REFERENCE:** 읽기 경로 OFF 및 4개 전원 0V 확인 → TOP MUX LOW·BTM MUX LOW로 0V 입력 선택 → 선택된 PSU 채널에 `write_bl_topbtm_v` 설정 → 해당 MUX의 읽기 입력 선택 → S1 또는 S2 ON → q에 따른 WL 펄스 → DFF OFF·CON 분리 → S1/S2 OFF → TOP MUX LOW·BTM MUX LOW로 복귀 → **ADC 캡처** → 적분기 초기화 → 4개 전원 0V 확인 → 두 MUX LOW. 적분기 값을 ADC로 읽기 전에 긴 PSU 정리 대기를 넣지 않습니다.
3. **UPDATE_AND_READ:** 위 UPDATE와 종료 0V 확인 → `post_us` 대기 → BLTOP·BLBTM ON을 모두 `write_bl_topbtm_v`로 설정 → 두 MUX HIGH·안정화 1회 → ACTUAL 읽기 1회 → S1/S2 OFF → ADC·3ms 리셋까지 두 BL 바이어스 유지 → 두 MUX LOW → 네 전원 0V 확인. retention/disturb와 같은 준비·읽기·정리 함수를 사용합니다. REFERENCE는 읽지 않으며 `points/cycles/initial_wait_ms/interval_ms`는 사용하지 않습니다.
4. **RETENTION_TEST / READ_DISTURB_TEST:** UPDATE와 종료 0V 확인 후 BLTOP·BLBTM ON을 모두 `write_bl_topbtm_v`로 설정합니다. 두 MUX HIGH·MUX 안정화를 한 번 수행한 뒤 `initial_wait_ms`를 기다립니다. 반복부에는 PSU 요청이나 BL MUX 전환이 없으며 N3/N4는 ADC·리셋·측정 사이 대기까지 읽기 바이어스를 유지합니다. S1/S2는 읽을 때 해당 스위치 하나만 ON, 그 외에는 모두 OFF입니다. `interval_ms`는 ACTUAL 시작에서 다음 ACTUAL 시작까지의 주기입니다. disturb에서는 ACTUAL+REFERENCE 한 쌍의 시작 주기이며 쌍 내부 간격은 별도로 기록합니다. 전체 종료·중단·오류 시 접근 OFF → 두 MUX LOW → 네 전원 0V 확인 순서로 정리합니다.
5. **DEBUG_FIRE:** `target=S1/S2`는 4개 전원을 0V로 확인한 뒤 두 MUX를 LOW로 유지하면서 해당 디지털 핀만 펄스하고, 종료 시 다시 0V를 확인합니다. 실제 쓰기 전압 전달은 `UPDATE`로 확인합니다. `target=READ_ACTUAL/READ_REFERENCE`는 정상 읽기 시퀀스를 실행하고 ADC 1회 캡처 후 4개 전원을 정리합니다.

읽기 경로는 다음과 같으며 FB는 모두 LOW(forward)입니다.

| q 부호 | 읽기 명령 | 접근 스위치 | 바이어스 PSU 채널 | 해당 MUX 선택 | HL / WL 분기 |
|---|---|---|---|---|---|
| 양수 | ACTUAL | S2 | BLBTM | HIGH | LOW / WLU |
| 양수 | REFERENCE | S1 | BLTOP | HIGH | LOW / WLU |
| 음수 | ACTUAL | S1 | BLTOP | HIGH | HIGH / WLD |
| 음수 | REFERENCE | S2 | BLBTM | HIGH | HIGH / WLD |

`q=0`은 양수 쪽 분기를 사용하지만 WL ON 슬롯은 0개입니다. 바이어스·ADC·전원 정리 시퀀스는 실행합니다.

`parkBitlinePaths()`는 S1/S2를 먼저 끈 뒤 양쪽 MUX LOW로 0V인 OFF 입력을 선택합니다. PSU 준비/cleanup/timeout/ABORT와 단독 읽기의 ADC 전에 사용합니다. UPDATE_AND_READ(2.4.8)와 compound 반복부는 접근 OFF 상태에서 `resetReadControls()`로 읽기 회로만 초기화하므로 N3/N4 바이어스가 유지됩니다. 두 외부 노드가 1.65V여도 S1/S2 중 하나만 켜면 내부 VTOP/VBOT을 동시에 고정하지 않습니다. 예를 들어 q>0 ACTUAL은 VBOT=1.65V, VTOP=1.65V+VCAP이고 REFERENCE는 VTOP=1.65V입니다. 이는 이상적 커패시터 기준이며 실제 누설·기생 성분은 별도 확인해야 합니다. 예전 BLBTM OFF 읽기/HIGH parking은 폐기했습니다.

긴 대기 구간과 OPC 대기 중에는 `CMD ABORT`를 확인합니다. 실패/중단 시 GPIO는 idle로 돌아가지만 **Arduino만으로 외부 PSU가 0V가 됐다고 보장할 수는 없습니다.** 실패한 핸드셰이크 이후의 추가 요청은 중단하며, 호스트가 전원 종료를 처리해야 합니다. 디지털 제어는 PIO 레지스터를 직접 사용합니다.

## 프로토콜 형식

모든 호스트 메시지는 `\n`으로 끝나는 ASCII 한 줄이어야 합니다.

- 최상위 명령 형식:
  - `CMD <NAME> key=value key=value ...`
- PSU 완료 응답 형식:
  - `OPC PSU_SET ch=<channel> v=<voltage>`
- 유틸리티 형식:
  - `PING`
  - `CMD ABORT`
  - `CMD HELP`

파서 관련 중요 사항:

- 토큰은 공백/탭으로 구분됩니다.
- 파라미터는 `key=value` 형태이며 CSV가 아닙니다.
- 명령어 이름은 대소문자를 구분합니다.
- 빈 줄은 무시됩니다.
- 버퍼 제한은 명령 줄 192바이트(종료 문자 포함, 실제 내용 최대 191자), 필드 최대 16개, key 저장 공간 24바이트(종료 문자 포함, 최대 23자)입니다. 이번 변경에서는 파서를 확장하지 않았습니다.
- `write_bl_settle_after_opc_us`처럼 23자를 넘는 key는 현재 저장 과정에서 잘려 설정이 적용되지 않을 수 있습니다. 해당 옵션을 정상 적용된 것으로 가정하지 말고 기본값을 사용합니다.
- 펌웨어 버퍼보다 긴 줄은 다음 에러로 거부됩니다:
  - `ERR BAD_CMD reason=line_too_long`
- 숫자 파싱은 엄격합니다:
  - 정수 필드는 `100`, `5000` 같은 십진수 텍스트
  - 실수 필드는 `1.65`, `0.850000` 같은 텍스트
- 불리언 필드 `avg`는 다음을 허용합니다:
  - `avg=1`, `avg=0`
  - `avg=true`, `avg=false`
  - `avg=TRUE`, `avg=FALSE`
- **한 줄 안에서 같은 key를 중복해서 쓰지 마세요.** 펌웨어는 `findField()`로 첫 번째로 매치되는 값만 사용합니다. (이 때문에 Update와 Read가 각각 자기만의 OPC 타임아웃을 가지도록, Update 쪽은 `opc_timeout_ms`, Read 쪽은 `write_bl_opc_timeout_ms`로 **일부러 다른 key 이름**을 씁니다 — 같은 명령 줄에 두 값이 같이 들어갈 수 있기 때문입니다.)

## 펌웨어 상태(State)

이 스케치는 다음 내부 상태들을 시리얼로 노출합니다:

| 상태 | 의미 | 지속 시간 |
|---|---|---|
| `BOOTING` | 전원 켜짐 / 부팅 배너·spec 라인 출력 전 | 매우 짧음 |
| `IDLE` | 새 최상위 명령을 받을 준비가 됨 | 정상 상태 |
| `WAIT_PSU_OPC` | `REQ PSU_SET ...` 이후 호스트 확인을 기다리는 중. Update의 BLTOP/BLBTM 세팅, Read의 write-bias 세팅 등 **PSU가 개입하는 모든 단계에서 재사용**됩니다. | 매칭되는 `OPC` 또는 타임아웃까지 |
| `UPDATING` | 실제 `S1`/`S2` update 펄스 인가 중 | 매우 짧음 |
| `READING_ACTUAL` | `READ_ACTUAL` 실행 중 | 매우 짧음 |
| `READING_REFERENCE` | `READ_REFERENCE` 실행 중 | 매우 짧음 |
| `DEBUG_FIRE` | 디지털 핀 또는 읽기 경로 디버깅 중 | 설정에 따라 다름 |
| `ADC_VERIFY` | 외부 저항의 무입력 기준/선택 WL 적분·ADC 검증 중 | 설정에 따라 다름 |
| `RUNNING_UPDATE_AND_READ` | update 1회 + actual read 1회를 묶은 상위 실험 wrapper | 짧음 |
| `RUNNING_RETENTION` | retention 루프 진행 중 | 길어질 수 있음 |
| `RUNNING_DISTURB` | read-disturb 루프 진행 중 | 길어질 수 있음 |
| `ERROR_STATE` | `IDLE`로 돌아가기 전의 내부 일시 에러 상태 | 매우 짧음 |

## 상태별로 허용되는 명령

가장 중요한 운영상 포인트는, 펌웨어가 모든 상태에서 시리얼을 폴링하지는 않는다는 것입니다. 즉 일부 상태는 좁은 입력만 받고, 일부 짧은 상태는 호스트 입장에서 "중단 불가능"으로 취급해야 합니다.

| 현재 상태 | 정상적으로 받아들이는 호스트 입력 | 결과 |
|---|---|---|
| `IDLE` | `PING` | `OK PONG ...` |
| `IDLE` | `CMD HELP` | 한 줄짜리 도움말 |
| `IDLE` | `CMD ABORT` | `OK ABORTED`; 이미 idle이라 별도 상태 라인은 보통 없음 |
| `IDLE` | `CMD UPDATE ...` | 4개 전원 0V 확인 → BLTOP/BLBTM 설정 → update → 4개 전원 0V 확인 |
| `IDLE` | `CMD READ_ACTUAL ...` | 4개 전원 0V 확인 → 읽기 바이어스 설정 → actual read·ADC → 전원 정리 |
| `IDLE` | `CMD READ_REFERENCE ...` | 4개 전원 0V 확인 → 읽기 바이어스 설정 → reference read·ADC → 전원 정리 |
| `IDLE` | `CMD UPDATE_AND_READ ...` | update 1회 + actual read 1회 실행 |
| `IDLE` | `CMD RETENTION_TEST ...` | retention 실험 실행 |
| `IDLE` | `CMD READ_DISTURB_TEST ...` | disturb 실험 실행 |
| `IDLE` | `CMD ADC_VERIFY ...` | 선택 채널의 무입력 기준/저항 펄스 측정 반복 |
| `ADC_VERIFY` | `PING` / `CMD ABORT` | 취득 후 `INFO PONG ADC_VERIFY` / 즉시 안전 상태로 복귀 후 `OK ABORTED` |
| `WAIT_PSU_OPC` | `OPC PSU_SET ch=... v=...` | 지금 기다리는 요청의 `ch`/`v`와 일치할 때만 수락 |
| `WAIT_PSU_OPC` | `PING` | compound 명령 실행 중에는 `INFO PONG ...`, 그 외 `OK PONG ...` |
| `WAIT_PSU_OPC` | `CMD ABORT` | 실험 중단 후 `IDLE`로 복귀 |
| `WAIT_PSU_OPC` | 그 외 | `ERR BUSY state=WAIT_PSU_OPC` 또는 파싱/인자 에러 |
| `RUNNING_RETENTION` | 대기 구간 중 `PING` | 비종료 응답 `INFO PONG ...` |
| `RUNNING_RETENTION` | 대기 구간 중 `CMD ABORT` | 중단 후 `IDLE`로 복귀 |
| `RUNNING_RETENTION` | 대기 구간 중 그 외 명령 | 전원 정리 후 `ERR BUSY reason=compound_running` |
| `RUNNING_DISTURB` | 대기 구간 중 `PING` | 비종료 응답 `INFO PONG ...` |
| `RUNNING_DISTURB` | 대기 구간 중 `CMD ABORT` | 중단 후 `IDLE`로 복귀 |
| `RUNNING_DISTURB` | 대기 구간 중 그 외 명령 | 전원 정리 후 `ERR BUSY reason=compound_running` |

짧은 상태에 대한 참고:

- `UPDATING`, `READING_ACTUAL`, `READING_REFERENCE`, 그리고 `RUNNING_UPDATE_AND_READ`의 대기가 아닌 구간에서는, 펌웨어가 즉시 처리해야 하는 하드웨어 작업 중이라 새 명령을 능동적으로 폴링하지 않습니다.
- 가장 안전한 호스트 동작은:
  - 명령 하나를 보낸다
  - 해당하는 `OK ...` 또는 `ERR ...`가 올 때까지 기다린다
  - 그다음 명령을 보낸다

## 실험 파라미터 묶음

각 실험 명령은 파라미터 전체를 명령 이름과 같은 줄에 싣습니다.

기본 형식:

```text
CMD <COMMAND_NAME> key=value key=value key=value
```

별도의 바이너리 payload나 후속 파라미터 블록은 없습니다. 모든 값이 한 줄에 들어가야 합니다.

### 공통 update 묶음

update를 수행하는 모든 명령이 쓰는 key들:

| Key | 필수 | 타입 | 의미 |
|---|---|---|---|
| `bltop_v` | **예** | float | BLTOP ON PSU 설정값. 정상 극성이므로 +1 설정이면 PCB +1V를 의도 |
| `blbtm_v` | **예** | float | 정상 결선 BLBTM ON PSU 설정값. +1 설정이면 N4 +1V; VCAP은 bltop_v − blbtm_v |
| `pw_us` | 예 | int | update 펄스 폭(µs) |
| `settle_after_opc_us` | 아니오 | int | BLTOP/BLBTM 둘 다 확인된 뒤 펄스 인가 전 대기 시간; 기본값 `50` |
| `repeat` | 아니오 | int | update 펄스 반복 횟수; 기본값 `1` |
| `opc_timeout_ms` | 아니오 | int | update의 각 설정·0V 정리 요청마다 적용할 `OPC` 타임아웃; 기본값 `5000` |
| `update_gate_settle_us` | 아니오 | int | `BLTOP_GATE`+`BLBTM_GATE`(46/34번 핀)가 열린 뒤, S1/S2가 열리기 전 대기; 기본값 `50` |

### 공통 read 묶음

ADC 판독을 수행하는 모든 명령이 쓰는 key들:

| Key | 필수 | 타입 | 의미 |
|---|---|---|---|
| `q` | **예** | float | 부호는 HL 경로, 절댓값은 WL ON 시간 비율; 정상 사용 범위 −1…+1 |
| `precision` | **예** | int | 전체 WL 슬롯 수를 `2^precision`으로 정함 |
| `step_us` | **예** | int | 한 WL 슬롯의 길이(µs); `q=1, precision=8, step_us=10`이면 명목상 2.56ms |
| `write_bl_topbtm_v` | **예** | float | S1은 BLTOP ON, S2는 BLBTM ON에 설정하는 양의 읽기 바이어스(보통 1.65V) |
| `samples` | 아니오 | int | 수집할 ADC 샘플 수; 기본값 `1`. 단독 READ_ACTUAL/READ_REFERENCE는 1–4096개 |
| `warmup` | 아니오 | int | 버릴 워밍업 샘플 수; 기본값 `0` |
| `settle_us` | 아니오 | int | boost 인가 후 WL 펄스 시작 전 안정화 대기; 기본값 `250` |
| `inter_us` | 아니오 | int | 연속 ADC 샘플 사이 대기; 기본값 `50` |
| `avg` | 아니오 | bool | `1/true`면 평균값을 `adc`로 보고, `0/false`면 마지막 샘플을 보고; 기본값 `1` |
| `read_delay` | 아니오 | int | WL 슬롯 이후 DFF 클럭·10µs 대기를 반복하는 횟수; 기본값 `0` |
| `write_bl_settle_after_opc_us` | 아니오 | int | 읽기 바이어스 OPC 이후 MUX 선택 전 대기; 기본값 `50`. 현재 key 길이 제한 때문에 명령으로 변경 시 적용되지 않을 수 있음 |
| `write_bl_opc_timeout_ms` | 아니오 | int | 읽기의 각 바이어스·0V 정리 요청에 적용할 `OPC` 타임아웃; 기본값 `5000` |
| `write_bl_gate_settle_us` | 아니오 | int | MUX HIGH 선택 후 접근 ON 전 대기; 기본값 `50`. 단독 READ는 매 read, UPDATE_AND_READ·retention/disturb는 두 MUX 준비 후 최초 한 번 적용 |

### 명령별 추가 key

| 명령 | 공통 update/read 묶음 이외의 추가 key |
|---|---|
| `CMD UPDATE` | 없음 |
| `CMD READ_ACTUAL` | read 묶음만 |
| `CMD READ_REFERENCE` | read 묶음만 |
| `CMD UPDATE_AND_READ` | `post_us` = update 완료 후 actual read 시작 전 대기; 기본값 `0` |
| `CMD RETENTION_TEST` | `points` = read 포인트 수; 기본값 `10` |
| `CMD RETENTION_TEST` | `initial_wait_ms` = 읽기 전원 준비·INFO 출력 완료 후 첫 read 전 추가 대기; 기본값 `0` |
| `CMD RETENTION_TEST` | `interval_ms` = ACTUAL 시작→다음 ACTUAL 시작 주기; 기본값 `1000`, `0`은 최대 속도 |
| `CMD READ_DISTURB_TEST` | `cycles` = actual/reference 사이클 수; 기본값 `10` |
| `CMD READ_DISTURB_TEST` | `initial_wait_ms` = 읽기 전원 준비·INFO 출력 완료 후 첫 쌍 전 추가 대기; 기본값 `0` |
| `CMD READ_DISTURB_TEST` | `interval_ms` = ACTUAL+REFERENCE 쌍 시작→다음 쌍 시작 주기; 기본값 `1000`, `0`은 최대 속도 |

`RETENTION_TEST`/`READ_DISTURB_TEST`의 첫 read까지는 UPDATE 종료 정리와 읽기 전원 준비 시간이 여전히 필요합니다. `initial_wait_ms=0`도 UPDATE 직후 0µs 읽기를 뜻하지 않습니다. 이후 양의 `interval_ms`는 첫 ACTUAL 시작을 기준으로 한 절대 일정이며, 이미 소비한 read·ADC·3ms 리셋·출력 시간을 뺀 나머지만 기다립니다. `interval_ms=0`은 일정 없이 가능한 속도로 반복하고 관측 간격을 기록합니다.

시작 기준은 첫 DFF 슬롯입니다. `q=0`에는 WL ON 펄스가 없으므로 첫 논리 슬롯 시각입니다. 허용 지연은 50µs이며 `late_us`로 기록합니다. 고정 지연만으로도 불가능한 양의 주기는 UPDATE 전에 `ERR PERIOD_TOO_SHORT ...`로 거부합니다. 실행 중 더 큰 지연은 `ERR PERIOD_OVERRUN ...`로 중단하며 밀린 포인트를 연속 실행하지 않습니다. 50µs는 소프트웨어 허용값이며 물리적인 핀 정확도의 실측 보증이 아닙니다. 예를 들어 `precision=8, step_us=10, samples=4, warmup=0, inter_us=25, settle_us=250`이면 2.4.7의 두 read 명시적 지연은 11.77ms이므로 10ms 쌍 주기는 거부됩니다. MUX 대기는 반복 전 한 번이며 UART·GPIO 비용은 이 하한에 추가됩니다.

2.2.1 이하의 `interval_ms`는 완료 후 추가 대기였고 `t_ms`는 UPDATE 정리 완료부터 각 사이클 정리 완료까지의 경과 시간이었습니다. 이를 새 읽기 시작 시각과 혼용하지 않습니다.

2.4.6은 compound 읽기의 Q 양자화·슬롯 수 계산을 예정 시각 대기 전에 마치고, 첫 WL 데이터를 준비한 뒤 첫 DFF 직전에서 최종 시각을 맞춥니다. 이전에는 예정 시각에 도착한 뒤 부동소수점 계산을 수행하여 그 비용도 `first_dff_deadline` 지연에 포함됐습니다. 50µs 허용값과 실제 지연 시 읽기 생략·오류 정리는 유지합니다. 이 수정은 스케치 재업로드가 필요하며, 실제 보드의 `late_us`·물리 파형은 별도 확인해야 합니다.

2.4.9는 경로 설정·GPIO·함수 호출 비용까지 첫 DFF 전에 처리하도록 예약 시각보다 `settle_us + 250µs` 앞서 접근 경로를 준비합니다. 2.4.8의 `target-settle_us`까지 추가로 기다리던 구간을 제거해 준비 여유를 실제로 사용합니다. `settle_us`는 S1/S2 ON 후 **최소** 안정화 시간이며, 예약 ACTUAL은 남은 시간만큼 더 안정화한 후 첫 DFF를 시작할 수 있습니다. 첫 ACTUAL·REFERENCE·주기 0에는 이 추가 대기가 없고, VREAD 슬롯 폭·목표 주기·50µs 지연 검사는 그대로입니다. 여유보다 큰 CPU/통신 지연은 여전히 오류로 중단합니다. GUI를 닫고 **2.4.9를 재업로드**해야 적용됩니다.

### 최소 구성 예시

```text
CMD UPDATE bltop_v=1 blbtm_v=1 pw_us=100
CMD READ_ACTUAL q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65
CMD READ_REFERENCE q=-1 precision=8 step_us=10 write_bl_topbtm_v=1.65
CMD UPDATE_AND_READ bltop_v=1 blbtm_v=1 pw_us=100 q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65
CMD RETENTION_TEST bltop_v=1 blbtm_v=1 pw_us=100 points=20 q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65
CMD READ_DISTURB_TEST bltop_v=1 blbtm_v=1 pw_us=100 cycles=20 q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65
CMD DEBUG_FIRE target=S1 pw_us=100
CMD DEBUG_FIRE target=READ_ACTUAL q=-1 precision=8 step_us=10 write_bl_topbtm_v=1.65
```

### 일부 옵션을 추가한 예시

모든 옵션을 한꺼번에 붙이면 191자·16필드 제한을 넘기기 쉽습니다. 기본값과 다른 옵션만 추가하고 전송할 한 줄의 길이를 확인합니다.

```text
CMD UPDATE bltop_v=1 blbtm_v=1 pw_us=100 repeat=2 opc_timeout_ms=7000
CMD READ_ACTUAL q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65 samples=8 inter_us=100
CMD UPDATE_AND_READ bltop_v=1 blbtm_v=1 pw_us=100 post_us=200 q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65 samples=4
```

## 실험 하나를 실제로 주고받는 방법

권장 호스트 시퀀스:

1. 시작 라인을 기다림:
   - `BOOT READY ...`
   - `STATE IDLE`
   - `SPEC ...`
2. 명령 한 줄 전체를 개행 문자와 함께 전송.
3. 그 명령이 PSU 세팅을 필요로 하는 단계라면, 매 세팅마다:
   - `REQ PSU_SET ch=... v=...`
   - `STATE WAIT_PSU_OPC`
   를 기다림.
4. PC 쪽에서 실제 PSU를 프로그래밍.
5. 일치하는 완료 라인으로 응답:
   - `OPC PSU_SET ch=<same_channel> v=<same_voltage>`
6. 그 명령의 최종 `OK ...` 또는 `ERR ...` 라인이 올 때까지 계속 읽음.

**한 명령 안에서 이 REQ/OPC 왕복이 몇 번이나 필요한지는 명령마다 다릅니다:**

| 명령 | PSU 핸드셰이크 횟수 |
|---|---|
| `UPDATE` | 10회 = 시작 0V 4회 + update 전압 2회 + 종료 0V 4회 |
| `READ_ACTUAL` / `READ_REFERENCE` | 9회 = 시작 0V 4회 + 읽기 바이어스 1회 + ADC 후 0V 4회 |
| `UPDATE_AND_READ` | 16회 = UPDATE 10회 + 두 BL 바이어스 2회 + 최종 0V 4회 |
| `RETENTION_TEST` (points=N) | 16회 = UPDATE 10회 + 두 BL 바이어스 2회 + 최종 0V 4회 |
| `READ_DISTURB_TEST` (cycles=N) | 16회 = UPDATE 10회 + 두 read 바이어스 2회 + 최종 0V 4회 |
| `DEBUG_FIRE target=S1/S2` | 8회 = 시작 0V 4회 + 종료 0V 4회 |
| `DEBUG_FIRE target=READ_ACTUAL/READ_REFERENCE` | 9회 |

위 횟수는 정상 완료 기준이며 호스트의 연결 시 초기화·최종 출력 종료는 포함하지 않습니다. `repeat`는 이미 설정된 update 전압에서 펄스만 반복하므로 UPDATE의 왕복 횟수를 늘리지 않습니다. 각 4채널 정리 함수는 채널별 1회만 요청하며 첫 실패를 반환합니다.

핸드셰이크 타임아웃/불일치 에러에는 `step=<태그>`(예: `UPDATE_BASELINE`, `UPDATE_BLTOP`, `UPDATE_ZERO`, `READ_BASELINE`, `READ_WRITE_BIAS`, `READ_ZERO`)가 붙습니다. 이번 변경은 기존 파서·불일치 처리 규칙을 바꾸지 않았습니다. 에러 이후 PSU가 0V라고 가정하지 말고 호스트 종료 처리를 확인합니다.

구체적인 예시 (`UPDATE_AND_READ`):

```text
CMD UPDATE_AND_READ bltop_v=1 blbtm_v=1 pw_us=100 q=0.5 precision=8 step_us=10 write_bl_topbtm_v=1.65 samples=4
```

그 줄에 대한 호스트 처리:

1. `BLTOP`, `BLBTM`, `BLTOP_OFF`, `BLBTM_OFF`를 차례로 0V 요청·OPC 확인합니다.
2. `REQ PSU_SET ch=BLTOP v=1.000000`, 이어서 `ch=BLBTM v=1.000000`에 각각 응답합니다. 새 정상 결선에서 PCB의 의도한 전압은 +1V/+1V이며 이상적인 VCAP은 0V입니다.
3. Arduino가 두 MUX HIGH → S1/S2 동시 펄스 → S1/S2 OFF → 두 MUX LOW를 실행합니다.
4. 4개 전원의 종료 0V 요청에 응답합니다. 이 시점까지 UPDATE의 10회 왕복입니다.
5. `post_us`가 양수면 대기한 뒤 `REQ PSU_SET ch=BLBTM v=1.650000`, 이어서 `ch=BLTOP v=1.650000`에 응답합니다. PSU 설정 중 두 MUX는 LOW이고 S1/S2는 OFF입니다.
6. Arduino가 46번·34번을 모두 HIGH로 선택하고 MUX 안정화를 한 번 기다립니다. N3/N4 모두 +1.65V입니다.
7. S2 ON → 안정화 대기 → WL 펄스 → DFF OFF·CON 분리 → S1/S2 OFF → ADC 4회 캡처 → 적분기 3ms 리셋을 실행합니다. 이 동안 두 MUX는 HIGH로 바이어스를 유지하며 S1은 OFF입니다.
8. 두 MUX LOW로 0V 입력을 선택한 뒤 마지막 4개 전원 0V 요청에 응답합니다. 저장된 ADC 데이터와 `OK UPDATE_AND_READ_DONE points=1`이 출력됩니다. 총 16회 왕복입니다.

## 핀 매핑

- `BITMASK_S1`: 아두이노 51번 핀 — +3V/−2V MUX 선택. 출력 N1은 S1 트랜지스터의 게이트
- `BITMASK_S2`: 아두이노 39번 핀 — +3V/−2V MUX 선택. 출력 N2는 S2 트랜지스터의 게이트
- `BLTOP_GATE`: 아두이노 46번 핀 — BLTOP 2:1 MUX select (예전엔 `READ_ACTUAL_SELECT`로 잘못 명명돼 있었음)
- `BLBTM_GATE`: 아두이노 34번 핀 — BLBTM 2:1 MUX select (예전엔 `READ_REFERENCE_SELECT`로 잘못 명명돼 있었음)
- `FB`: 아두이노 22번(PB26) — LOW 고정, forward 방향
- `HL_CHOP`: 아두이노 2번(PB25) — q≥0 LOW/WLU, q<0 HIGH/WLD
- 디지털 `WL0`: 아두이노 25번(PD0) — DFF에 전달할 읽기 선택 데이터
- `DFF1`: 아두이노 31번(PA7) — 읽기 선택값을 저장하는 클럭
- `CON`: 아두이노 53번 — HIGH이면 적분기 연결 해제
- `CR`: 아두이노 52번 — LOW이면 적분기 초기화, 읽기 후 ADC 완료까지 HIGH 유지
- `DS`: 아두이노 42번 — 기존 읽기 시퀀스의 HIGH/LOW 제어 유지. 실제 PCB 기능은 이름만으로 단정하지 않음
- `ADC`: QK 명령은 `A0`, ADC_VERIFY는 선택 채널의 `A0`–`A4`

현재 이름 규칙은 S1/S2=접근 트랜지스터, S3/S4=읽기 트랜지스터, N1~N4=MUX 뒤의 아날로그 노드입니다. 2026-11-04 사용자 회로도 기준으로 **N1/N2는 S1/S2의 게이트, N3/N4는 TOP/BTM MUX 출력**입니다. 이전 아날로그 노드 이름 N2와 N3만 서로 바꿨습니다. 위 Arduino 번호는 MUX의 디지털 선택 입력이며 아날로그 출력 노드와 같은 선이 아닙니다.

2.4.2는 사용자의 실제 재배선을 반영해 **N2(S2 게이트)는 PCB N2_0 / 39번 제어, N3(BLTOP)는 PCB N3_0 / 46번 제어**로 교환합니다. 39번 MUX 입력도 S2 게이트용 +3V/−2V, 46번 MUX 입력도 호스트 제어 BLTOP ON/OFF로 연결해야 합니다. 프로브만 교환하면 전압 역할이 맞지 않습니다. 전체 대응표는 [인계 문서](../../../docs/handoff/4t1c-qk-single-cell-debugging.md#셀과-pcb-이름-대응)를 참고합니다.

**BLTOP/BLBTM은 PCB 위 2:1 아날로그 MUX를 거쳐 N3/N4와 S1/S2에 연결됩니다:**

```
PSU BLTOP ON  (정상 극성, update/S1 read) ── HIGH ─┐
                                                 ├─[MUX, BLTOP_GATE(46)]── N3 ── S1 ── V_TOP
PSU BLTOP_OFF (0V) ────────────────────── LOW ──┘

PSU BLBTM ON  (정상 극성, update/S2 read) ─────── HIGH ─┐
                                                 ├─[MUX, BLBTM_GATE(34)]── N4 ── S2 ── V_BOT
PSU BLBTM_OFF (0V) ─ LOW ┘
```

- MUX LOW는 0V인 OFF 입력 선택입니다. S2 읽기에서는 HIGH로 BLBTM ON의 +1.65V를 전달합니다.
- S1/S2를 OFF로 유지한 상태에서 PSU를 설정하고 MUX 선택을 마친 뒤 해당 접근 스위치 하나만 ON으로 바꿉니다. 읽기 완료 후 WL·CON 분리와 접근 OFF를 수행합니다. 단독 읽기는 두 MUX LOW 후 ADC를 취득하며, compound는 두 MUX HIGH를 유지한 채 ADC를 취득합니다.
- Arduino의 디지털 WL0/DFF는 외부 VREAD 아날로그 전압 자체를 저장하지 않습니다. 의도한 배선은 WL0 데이터 → DFF → VREAD/VINP MUX 선택입니다. 실제 연결과 전압은 PCB에서 확인해야 합니다.
- 단독 단계의 정상 종료와 compound 명령의 최종 종료에는 모든 BL 전원이 0V입니다. compound read 사이에는 두 MUX HIGH·N3/N4 읽기 바이어스 유지·S1/S2 OFF입니다. PSU 통신 실패나 다른 배선에서는 같은 상태를 가정하지 않습니다.

## 프로토콜 예시

```text
BOOT READY version=2.3.0 protocol=ASCII_KV
CMD UPDATE bltop_v=1 blbtm_v=1 pw_us=100
(BLTOP → BLBTM → BLTOP_OFF → BLBTM_OFF의 0V REQ/OPC 4쌍)
REQ PSU_SET ch=BLTOP v=1.000000
STATE WAIT_PSU_OPC
OPC PSU_SET ch=BLTOP v=1.000000
REQ PSU_SET ch=BLBTM v=1.000000
STATE WAIT_PSU_OPC
OPC PSU_SET ch=BLBTM v=1.000000
STATE UPDATING
(BLTOP_GATE+BLBTM_GATE 열림 → update_gate_settle_us 대기 → S1+S2 pw_us 펄스 → S1+S2 닫힘 → 게이트 닫힘)
(BLTOP → BLBTM → BLTOP_OFF → BLBTM_OFF의 0V REQ/OPC 4쌍)
STATE IDLE
OK UPDATE_APPLIED bltop_v=1.000000 blbtm_v=1.000000 pulses=1 pw_us=100
```

### Compound 결과 형식

아래 숫자는 프로토콜 설명용이며 측정 결과가 아닙니다.

```text
INFO TIMING schema=2 origin=update_end edge=first_dff q0=logical_slot tolerance_us=50
DATA RETENTION idx=0 t_us=6000300 dt_us=0 late_us=0 adc=100 avg=100.000 samples=4
DATA RETENTION idx=1 t_us=6010302 dt_us=10002 late_us=2 adc=99 avg=99.000 samples=4
DATA DISTURB idx=0 t_us=7200300 ref_us=7206235 dt_us=0 late_us=0 actual=100 actual_avg=100.000 reference=98 reference_avg=98.000 delta=2.000
```

`t_us`는 마지막 실제 UPDATE 펄스가 끝난 뒤 ACTUAL 첫 슬롯까지, `ref_us`는 같은 원점에서 REFERENCE 첫 슬롯까지의 µs입니다. `dt_us`는 인접 ACTUAL 시작 간격이며 첫 행은 0입니다. `late_us`는 계획 시각 대비 ACTUAL 지연입니다. wrap을 누적한 64비트 펌웨어 시간이며 호스트 수신 시각을 대신 넣지 않습니다. 시작 표식은 첫 DFF 상승 에지 바로 앞의 `micros()` 기반 소프트웨어 시각이며, 아날로그 에지를 계측한 시각이 아닙니다. UPDATE 원점도 펄스 종료 직후의 소프트웨어 표식입니다. REFERENCE는 ACTUAL 뒤 가능한 시점에 시작하므로 둘의 시각이 다릅니다.

## 참고

- 2.3.0 검증: 후보 코드의 소스 기반 제어·타이밍 28개와 호스트 결과·세션 39개 검증을 먼저 통과한 뒤 적용했고, 적용본도 총 **67개 테스트**를 통과했습니다. 39개에는 기존 세션 28개가 포함됩니다. 제한된 소스 변환과 이상적인 DFF/MUX, 요청당 1.2초 PSU, 115200 baud·64바이트 UART FIFO, GPIO/CPU 비용 모형을 사용했습니다. 명목 retention 10ms·disturb 20ms는 이 모형에서 확인했으며 disturb 10ms는 고정 지연 하한으로 거부됩니다. C++/Arduino 컴파일·업로드·실제 PCB 측정은 수행하지 않았습니다. 기록은 `scenarios/arduino_4T1C_dotprod/results/offline_compound_timing_20261019/`의 `applied_tests.txt`, `simulation_summary.json`, `modeled_*_raw.txt`와 `modeled_*_data.csv`에 보관합니다. 아래 40개 통과 기록은 2.2.1의 역사 기록입니다.

- 2.2.1의 제안 코드를 먼저 모의 실행한 뒤 원본에 적용했으며, 소스 기반 제어 순서 12개와 호스트 세션 28개, 총 40개 테스트가 통과했습니다. 검증 기록은 `scenarios/arduino_4T1C_dotprod/results/offline_read_bias_parking_20261012/`에 보관합니다. 결과 디렉토리는 기존 `**/results/` 규칙으로 Git에서 제외됩니다. Arduino CLI/C++ 컴파일러가 없어 펌웨어 컴파일은 수행하지 않았고, 업로드나 PCB·PSU 측정도 수행하지 않았습니다.
- 명령 파서는 의도적으로 엄격하고 기계 친화적으로 만들어져 있습니다.
- 펌웨어는 더 이상 파일명으로 버전을 관리하지 않고, 3T1C 데모 방식처럼 스케치 안에서 버전을 관리합니다.
- 전용 호스트 사이드 시나리오 패키지는 `scenarios/arduino_4T1C_dotprod/`에 있습니다 — 저장소 다른 곳의 더 넓은 "4T1C 시냅스 회로" 명명과 구분되도록 분리돼 있습니다.
- 설계 의도와 관련해서는 `arduino_4T1C_concept.md`를 참고하세요.
