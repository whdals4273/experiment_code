"""Canvas drawing for the 4T1C parameter preview (no hardware or Tk imports)."""

from .pulse_preview import Preview

INK = "#17324d"
MUTED = "#60758a"
BLUE = "#2775b6"
TEAL = "#168477"
ORANGE = "#be7130"
PALE = "#edf3f8"
PIN_ROLE_NOTE = "Arduino MUX 제어핀 · N1/S1: 51 · N2/S2: 39 · N3/BLTOP: 46 · N4/BLBTM: 34"


def duration(us):
    if us >= 1_000_000:
        return f"{us / 1_000_000:,.4g} s"
    if us >= 1000:
        return f"{us / 1000:,.4g} ms"
    return f"{us:,.4g} µs"


def _draw_update_preview(canvas, preview, width, top):
    """Expand performUpdate(): ON supplies, MUX settling, then access pulses.

    Each trace has its own vertical scale. Prior stored charge is unknown;
    only the ideal target after the first access pulse is drawn as a value.
    Show at most three pulses, regardless of the requested repeat count.
    """
    def text(x, y, value, size=10, color=INK, bold=False, anchor="nw", wrap=None):
        return canvas.create_text(x, y, text=value, anchor=anchor, fill=color,
                                  font=("TkDefaultFont", size, "bold" if bold else "normal"),
                                  width=wrap or 0)

    def paragraph(y, value, **kwargs):
        item = text(24, y, value, wrap=width-48, **kwargs)
        return canvas.bbox(item)[3]+12

    y = paragraph(top, "UPDATE 상세 · BL 전압과 VCAP", size=12, bold=True)
    input_mode = "VCAP 입력" if preview.use_vcap_input else "BL 직접 입력"
    y = paragraph(y, f"{input_mode} · 정상 결선 · VCAP(이상적) = BLTOP − BLBTM = "
                  f"{preview.update_top_v:g} − {preview.update_bottom_v:g} = {preview.update_cap_v:g} V")

    card_w = (width-64)/3
    card_top, card_bottom = y, y+62
    cards = [("BLTOP ON → N3", preview.update_top_v, ORANGE),
             ("BLBTM ON → N4", preview.update_bottom_v, TEAL),
             ("VCAP · 이상적 저장값", preview.update_cap_v, "#7762a1")]
    rectangles = []
    for i, (label, value, color) in enumerate(cards):
        left = 24+i*(card_w+8)
        item = canvas.create_rectangle(left, card_top, left+card_w, card_bottom,
                                       fill=PALE, outline="")
        rectangles.append((item, left))
        title = text(left+10, card_top+7, label, size=9, color=MUTED, wrap=card_w-20)
        value_item = text(left+10, canvas.bbox(title)[3]+5, f"{value:g} V", size=15,
                          color=color, bold=True, wrap=card_w-20)
        card_bottom = max(card_bottom, canvas.bbox(value_item)[3]+10)
    for item, left in rectangles:
        canvas.coords(item, left, card_top, left+card_w, card_bottom)

    y = paragraph(card_bottom+18, "전원 준비: 네 BL 전원 0 V 확인 → BLTOP/BLBTM ON 설정 → OPC 응답 (가변)",
                  color=MUTED)
    y = paragraph(y, "시간축 비례 아님 · 각 행의 높이는 독립적이며 전압 크기는 숫자로 표시합니다.",
                  size=9, color=MUTED)
    left, right = 155, width-24
    x = [left]
    for weight in (.16, .16, .38, .12, .18):
        x.append(x[-1]+(right-left)*weight)
    phases = [("OPC 후 대기", duration(preview.update_settle_us)),
              ("MUX 안정화", duration(preview.update_gate_settle_us)),
              ("UPDATE 펄스", f"{duration(preview.update_pulse_us)} × {preview.update_repeat}"),
              ("BL 복귀", "MUX → OFF"),
              ("PSU 정리", "설정 → 0 V\n응답 대기")]
    graph_top, header_bottom = y, y
    backgrounds = []
    for i, (label, value) in enumerate(phases):
        backgrounds.append(canvas.create_rectangle(x[i], graph_top, x[i+1], graph_top+400,
                           fill="#f3f7fa" if i % 2 == 0 else "#ffffff", outline=""))
        title = text((x[i]+x[i+1])/2, graph_top+8, label, size=8, anchor="n", wrap=x[i+1]-x[i]-6)
        item = text((x[i]+x[i+1])/2, canvas.bbox(title)[3]+8, value, size=9,
                    anchor="n", wrap=x[i+1]-x[i]-6)
        header_bottom = max(header_bottom, canvas.bbox(item)[3])
    row_top = header_bottom+44
    graph_bottom = row_top+4*65+32
    for i, item in enumerate(backgrounds):
        canvas.coords(item, x[i], graph_top, x[i+1], graph_bottom)

    def row(y, label, color):
        text(24, y-17, label, color=color, wrap=126)
        canvas.create_line(left, y, right, y, fill="#d4dfe8")

    # Both bitline MUX outputs stay at their zero OFF inputs while waiting for
    # OPC. ON selection spans MUX settling and every access pulse, with no
    # return to zero between repeat iterations.
    for i, (node, rail, value, color) in enumerate([
        ("N3", "BLTOP", preview.update_top_v, ORANGE),
        ("N4", "BLBTM", preview.update_bottom_v, TEAL),
    ]):
        base = row_top+i*65
        row(base, f"{node} / {rail}\n0 → {value:g} V → 0", color)
        level = base-20 if value else base
        canvas.create_line(left, base, x[1], base, x[1], level,
                           x[3], level, x[3], base, right, base, fill=color, width=2.5)

    base = row_top+2*65
    row(base, "N1/N2 · S1/S2\n동시 ON / OFF", BLUE)
    # A small visible OFF gap denotes code overhead, never a configured delay.
    # For large repeat counts reserve one bin for the omitted middle pulses.
    bins = min(preview.update_repeat, 4)
    bin_w = (x[3]-x[2])/bins
    indices = range(bins) if bins < 4 else (0, 1, 3)
    points = [left, base]
    for i in indices:
        start = x[2]+i*bin_w
        end = x[3] if i == bins-1 else start+bin_w-min(8, bin_w*.15)
        points.extend((start, base, start, base-20, end, base-20, end, base))
        label = "마지막" if bins == 4 and i == 3 else f"#{i+1}"
        text((start+end)/2, base-26, label, size=8, color=BLUE, anchor="s", wrap=bin_w-2)
    points.extend((right, base))
    canvas.create_line(*points, fill=BLUE, width=2.5)
    if bins == 4:
        text(x[2]+2.5*bin_w, base-13, "…", size=13, color=BLUE, anchor="center")

    base = row_top+3*65
    color = "#7762a1"
    row(base, "VCAP\n이상적 저장값", color)
    canvas.create_line(left, base, x[2]-4, base, fill=MUTED, dash=(3, 3), width=1.5)
    text((left+x[2])/2, base-12, "기존 값 미측정", size=8, color=MUTED,
         anchor="s", wrap=x[2]-left-8)
    level = base-20 if preview.update_cap_v > 0 else (base+20 if preview.update_cap_v < 0 else base)
    canvas.create_line(x[2], level, right, level, fill=color, width=2.5)
    text(x[2]+5, level-5, f"{preview.update_cap_v:g} V · 저장 유지", size=9, color=color, anchor="sw",
         wrap=right-x[2]-10)

    base = row_top+4*65
    row(base, "VREAD\nOFF", MUTED)
    canvas.create_line(left, base, right, base, fill=MUTED, width=2.5)
    y = paragraph(graph_bottom+18, "S1/S2 동시 ON → pw_us 유지 → OFF를 repeat회 수행합니다. "
                  "BL 전압은 반복 중 유지하며 펄스 사이에 별도의 대기 시간은 없습니다.")
    y = paragraph(y, "그림의 짧은 OFF 간격은 코드 실행 간격입니다. "
                  "settle_after_opc_us와 update_gate_settle_us는 반복 전에 각각 한 번 적용합니다.", color=MUTED)
    if bins == 4:
        y = paragraph(y, "반복 파형은 처음 2회와 마지막 1회만 표시하며 중간 펄스는 …로 생략합니다.", color=MUTED)
    y = paragraph(y, "종료: S1/S2 OFF → 두 BL MUX의 0 V 입력 선택 → N3/N4=0 V → 네 BL PSU 설정 0 V.")
    y = paragraph(y, "VCAP은 첫 쓰기부터 BLTOP − BLBTM의 이상적 목표값으로 표시합니다. "
                  "실제 충전 과정·저장 전압·누설을 계산한 파형은 아닙니다.", size=9, color=MUTED)
    y = paragraph(y, PIN_ROLE_NOTE, size=9, color=MUTED)
    canvas.configure(scrollregion=(0, 0, width, y+12))


def _draw_adc_preview(canvas, preview, width):
    """Show the resistor/integrator check without implying a cell write/read."""
    def text(x, y, value, size=10, color=INK, bold=False, anchor="nw", wrap=None):
        canvas.create_text(x, y, text=value, anchor=anchor, fill=color,
                           font=("TkDefaultFont", size, "bold" if bold else "normal"),
                           width=wrap or 0)

    text(24, 18, "ADC_VERIFY · 외부 저항 검증", size=18, bold=True)
    text(24, 52, preview.actual_route, wrap=width-48)
    cards = [
        ("외부 저항 / 적분 커패시터", f"{preview.resistance_kohm:g} kΩ / {preview.capacitance_nf:g} nF"),
        ("실제 결선 기준 VREAD", f"{preview.signed_vread_v:g} V"),
        ("VREAD 펄스 폭", duration(preview.pulse_us)),
        ("이상적 출력 변화", f"ΔV = {preview.expected_delta_v:.4g} V"),
        ("이상적 ADC 변화", f"ΔADC = {preview.expected_delta_adc:.4g} count"),
        ("선택 채널 / 반복", f"ADC{preview.adc_channel} / {preview.count}회"),
    ]
    card_w = (width-64)/3
    for i, (label, value) in enumerate(cards):
        left = 24+(i % 3)*(card_w+8)
        top = 83+(i // 3)*70
        canvas.create_rectangle(left, top, left+card_w, top+62, fill=PALE, outline="")
        text(left+10, top+7, label, size=9, color=MUTED)
        text(left+10, top+29, value, size=12, bold=True, wrap=card_w-16)

    text(24, 236, "매 반복: 무입력 기준값과 저항 경로 측정값 비교", size=12, bold=True)
    text(24, 265, "시간축 비례 아님 · HIGH 표시는 선택 상태이며 음전압 파형의 방향을 뜻하지 않습니다.",
         color=MUTED, wrap=width-48)
    left, right = 135, width-24
    x = [left+(right-left)*i/7 for i in range(8)]
    phases = [("리셋", "3 ms"), ("무입력 기준", duration(preview.pulse_us)),
              ("대기 + ADC", f"{preview.samples}개"), ("리셋", "3 + 3 ms"),
              ("저항 경로", duration(preview.pulse_us)), ("대기 + ADC", f"{preview.samples}개"),
              ("리셋", "3 ms")]
    for i, (label, value) in enumerate(phases):
        canvas.create_rectangle(x[i], 312, x[i+1], 540,
                                fill="#f3f7fa" if i % 2 == 0 else "#ffffff", outline="")
        text((x[i]+x[i+1])/2, 322, label, size=8, anchor="n", wrap=x[i+1]-x[i]-4)
        text((x[i]+x[i+1])/2, 355, value, size=9, anchor="n", wrap=x[i+1]-x[i]-4)

    for y, label, active, color in [
        (409, f"WLU{preview.adc_channel}\nVREAD 선택", (4,), TEAL),
        (462, f"ADC{preview.adc_channel}\n기준 / 측정", (2, 5), BLUE),
        (515, "적분기 리셋", (0, 3, 6), MUTED),
    ]:
        text(24, y-17, label, color=color, wrap=105)
        points = [left, y+8]
        for i in range(7):
            level = y-10 if i in active else y+8
            points.extend((x[i], level, x[i+1], level))
        points.extend((right, y+8))
        canvas.create_line(*points, fill=color, width=2.5)

    text(24, 558, f"기준 측정은 VREAD를 선택하지 않고 동일한 {duration(preview.pulse_us)} 동안 대기합니다.", wrap=width-48)
    text(24, 590, f"각 ADC 취득 전 대기 {duration(preview.settle_us)} · 샘플 간격 {duration(preview.inter_us)}", wrap=width-48)
    text(24, 622, f"ΔADC = 측정 ADC − 기준 ADC · 고정 지연 합: 반복당 {duration(preview.block_min_us)}", bold=True, wrap=width-48)
    text(24, 660, f"저항 한 개를 WLU{preview.adc_channel}–BLU{preview.adc_channel}에 연결합니다. 채널을 바꿀 때 저항도 옮겨 연결하세요.", wrap=width-48)
    text(24, 704, "VREAD 역결선 선택은 기대값의 부호를 지정합니다. 실제 PSU 단자 결선도 일치해야 합니다.", color=MUTED, wrap=width-48)
    y = 750
    for message in (*preview.errors, *preview.warnings):
        text(24, y, message, color=MUTED, wrap=width-48)
        y += 42
    canvas.configure(scrollregion=(0, 0, width, y+20))


def _draw_lookup_preview(canvas, preview, width):
    y = 24
    def text(value, size=12, color=INK, bold=False):
        nonlocal y
        item = canvas.create_text(24, y, text=value, anchor="nw", width=width-48,
                                 fill=color, font=("TkDefaultFont", size, "bold" if bold else "normal"))
        bounds = canvas.bbox(item)
        y = (bounds[3] if bounds else y+size*2) + 18
    count = len(preview.lookup_caps) * len(preview.lookup_queries)
    text("LOOK_UP_TABLE_TEST · K/q 조합 측정", 20, bold=True)
    text(f"열: VCAP (V) = {', '.join(f'{k:g}' for k in preview.lookup_caps)}")
    text(f"행: q = {', '.join(f'{q:g}' for q in preview.lookup_queries)}")
    text(f"{len(preview.lookup_caps)} × {len(preview.lookup_queries)} = {count}조합 · "
         f"조합별 {preview.lookup_cycles}쌍 · 총 {count*preview.lookup_cycles*2}회 읽기", bold=True)
    text("각 조합: VCAP 다시 쓰기 → BL 읽기 전원 준비 → initial_wait_ms 대기\n"
         "→ ACTUAL + REFERENCE → delta 저장 → BL 전원 0V → 다음 조합")
    text(f"VCAP=BLTOP−BLBTM · TOP=max(K,0), BTM=max(−K,0)\n"
         f"읽기 바이어스: {preview.read_bias_v:g} V · UPDATE: "
         f"{duration(preview.update_pulse_us)} × {preview.update_repeat}")
    text(f"읽기 프레임: 2^{preview.precision} × {preview.step_us} µs = {duration(preview.frame_us)}\n"
         "VREAD 선택 시간은 각 q의 |q|를 슬롯 수로 양자화해 결정합니다. q=0은 ON 슬롯이 없습니다.")
    text(f"조합별 반복 횟수: lut_cycles={preview.lookup_cycles}. 여러 회면 delta 평균을 표시합니다.\n"
         "일반 q·목표 VCAP·cycles 대신 위 목록과 조합별 반복 횟수를 사용합니다.", color=MUTED)
    text("결과: 행=q · 열=VCAP · 칸=delta(Actual−Reference, ADC count)\n"
         "CSV·표 PNG와 조합별 원본 데이터를 저장합니다. 미완료 칸은 채우지 않습니다.")
    if preview.errors:
        text("\n".join(preview.errors), color="#a44427")
    text("예상 순서입니다. PSU 응답·연결·정리 시간이 추가되며 실측 파형은 아닙니다.", color=MUTED)
    canvas.configure(scrollregion=(0, 0, width, y+20))


def draw_preview(canvas, preview: Preview, width: int, mode="ACTUAL"):
    """Draw a bounded schematic: segment widths are intentionally not to scale."""
    canvas.delete("all")
    width = max(620, width)
    if preview.lookup_table:
        _draw_lookup_preview(canvas, preview, width)
        return
    if preview.adc_verify:
        _draw_adc_preview(canvas, preview, width)
        return

    # The detailed diagram keeps its compact-layout coordinates. Move its body
    # below the summary cards after measuring the added calculation text.
    body_offset = 0

    def text(x, y, value, size=11, color=INK, bold=False, anchor="nw", wrap=None):
        return canvas.create_text(x, y+body_offset, text=value, anchor=anchor, fill=color,
                                  font=("TkDefaultFont", size, "bold" if bold else "normal"),
                                  width=wrap or 0)

    def line(x1, y1, x2, y2, **kwargs):
        return canvas.create_line(x1, y1+body_offset, x2, y2+body_offset, **kwargs)

    def box(x1, y1, x2, y2, fill=PALE, outline=""):
        return canvas.create_rectangle(x1, y1+body_offset, x2, y2+body_offset, fill=fill, outline=outline)

    def arrow(x1, x2, y, label):
        line(x1, y, x2, y, fill=INK, width=1.5, arrow="both")
        text((x1+x2)/2, y-7, label, size=9, anchor="s")

    text(24, 18, preview.command, size=18, bold=True)
    text(24, 52, "현재 입력값의 예상 순서 · 시간축 비례 아님", color=MUTED)
    if not preview.supported:
        text(24, 105, "이 명령은 펄스 미리보기 대상이 아닙니다.\n측정 명령을 선택하면 스킴이 표시됩니다.", wrap=width-48)
        canvas.configure(scrollregion=(0, 0, width, 300))
        return

    # Compact numerical summary above the schematic.
    if preview.has_read:
        cards = [
            ("VREAD 선택 시간", duration(preview.pulse_us), (
                f"q = {preview.q:g} · ON {preview.on_slots}/{preview.total_slots}",
                f"floor(|q| × {preview.total_slots}) = {preview.on_slots}",
                f"{preview.on_slots} × {preview.step_us} µs = {preview.pulse_us:,} µs",
                f"= {duration(preview.pulse_us)}",
            )),
            ("전체 읽기 프레임", duration(preview.frame_us), (
                f"precision = {preview.precision}",
                f"step_us = {preview.step_us} µs",
                f"2^{preview.precision} = {preview.total_slots} 슬롯",
                f"{preview.total_slots} × {preview.step_us} µs = {preview.frame_us:,} µs",
                f"= {duration(preview.frame_us)} · q와 무관하게 고정",
            )),
            (f"총 {preview.count_label}", str(preview.count), ()),
        ]
    else:
        cards = [("UPDATE 펄스 폭", duration(preview.update_pulse_us), ()),
                 ("UPDATE 반복", str(preview.update_repeat), ()),
                 ("VREAD", "OFF", ())]
    card_w = (width-64)/3
    card_bottom = 142
    rectangles = []
    for i, (label, value, notes) in enumerate(cards):
        left = 24 + i*(card_w+8)
        rectangles.append((box(left, 80, left+card_w, 142), left))
        text(left+12, 87, label, size=9, color=MUTED)
        value_item = text(left+12, 109, value, size=16, bold=True)
        if notes:
            y = max(145, canvas.bbox(value_item)[3]+8)
            for note in notes:
                item = text(left+12, y, note, size=9, color=MUTED, wrap=card_w-24)
                y = canvas.bbox(item)[3]+5
            card_bottom = max(card_bottom, y+7)
    for item, left in rectangles:
        canvas.coords(item, left, 80, left+card_w, card_bottom)
    body_offset = card_bottom-142

    text(24, 163, "전체 실험 순서", size=13, bold=True)
    # Initial connection/PSU setup and final shutdown are variable, not a made-up duration.
    stages = []
    if preview.update:
        stages.append(("UPDATE", f"{duration(preview.update_pulse_us)} × {preview.update_repeat}", ORANGE))
    if preview.command == "UPDATE_AND_READ":
        stages.append(("post_us", duration(preview.post_us), MUTED))
    if preview.has_read:
        stages.append(("PSU 준비", "두 BL·MUX" if preview.hold_read_bias else "응답 대기", MUTED))
    if preview.compound:
        stages.append(("initial_wait", duration(preview.initial_wait_us), MUTED))
    count = min(preview.count, 2) if preview.compound else (1 if preview.has_read else 0)
    read_indices = []
    for i in range(count):
        if i and preview.interval_us > preview.block_min_us:
            stages.append(("남은 시간", "주기 맞춤", MUTED))
        read_indices.append(len(stages))
        label = "ACT + REF" if preview.pair else ("REF" if preview.command == "READ_REFERENCE" else "ACTUAL")
        stages.append((label, f"#{i+1}" if preview.compound else "1회", TEAL))
    if preview.compound and preview.count > count:
        stages.append(("…", f"총 {preview.count}회", MUTED))
    stages.append(("PSU 정리", "BL → 0 V", MUTED))
    gap = 7
    stage_w = (width-48-gap*(len(stages)-1))/len(stages)
    xs = []
    for i, (label, sub, color) in enumerate(stages):
        left = 24+i*(stage_w+gap)
        xs.append(left)
        box(left, 202, left+stage_w, 264, fill="#f5f8fb", outline="#d9e3ec")
        box(left, 202, left+stage_w, 206, fill=color)
        text(left+stage_w/2, 219, label, size=9, color=color, bold=True, anchor="center", wrap=stage_w-4)
        text(left+stage_w/2, 246, sub, size=9, anchor="center", wrap=stage_w-4)
        if i+1 < len(stages):
            line(left+stage_w, 232, left+stage_w+gap, 232, arrow="last", fill=MUTED)
    if preview.compound and count > 1:
        label = "interval_ms = " + (duration(preview.interval_us) if preview.interval_us else "0 · 가능한 빠르게")
        arrow(xs[read_indices[0]], xs[read_indices[1]], 290, label)
        text(24, 309, "ACTUAL 시작 → 다음 ACTUAL 시작 (첫 DFF 클럭 기준)", size=10, color=MUTED)
    else:
        text(24, 284, "읽기 시작 간격은 반복 측정이 2회 이상일 때 적용됩니다." if preview.compound
             else "PSU 응답 시간과 소프트웨어 처리 시간은 구간 폭에 반영하지 않습니다.", size=10, color=MUTED, wrap=width-48)

    if not preview.has_read:
        _draw_update_preview(canvas, preview, width, 340+body_offset)
        return

    # A single read expanded. q controls only VREAD ON time inside the fixed frame.
    mode = "REFERENCE" if preview.command == "READ_REFERENCE" else mode
    if not preview.pair and preview.command != "READ_REFERENCE":
        mode = "ACTUAL"
    use_s2 = preview.positive_q == (mode == "ACTUAL")
    node, access, gate_node = ("N4", "S2", "N2") if use_s2 else ("N3", "S1", "N1")
    rail = "BLBTM ON" if use_s2 else "BLTOP ON"
    zero_rail = "BLBTM OFF" if use_s2 else "BLTOP OFF"
    text(24, 355, f"{mode} 상세 · {rail} → {node} · {access} · {preview.wl_label}", size=12, bold=True)
    text(24, 383, f"읽기 바이어스 = +{preview.read_bias_v:g} V · 선택/ON 상태 도식이며 실제 전압은 측정으로 확인합니다.",
         size=10, color=MUTED, wrap=width-48)
    left, right = 155, width-24
    # Use fixed visual allocations so µs settling, ms frames and ADC all stay readable.
    weights = ([0.17, 0.37, 0.12, 0.17, 0.17] if preview.hold_read_bias
               else [0.14, 0.14, 0.32, 0.12, 0.14, 0.14])
    x = [left]
    for weight in weights:
        x.append(x[-1] + (right-left)*weight)
    phases = [("S 안정화", ("최소 " if preview.compound else "") + duration(preview.settle_us)),
              ("읽기 프레임", duration(preview.frame_us)),
              ("read_delay", duration(preview.read_delay_us)),
              ("ADC 취득", f"{preview.samples}개"),
              ("적분기 리셋", "3 ms")]
    if not preview.hold_read_bias:
        phases.insert(0, ("MUX 안정화", duration(preview.gate_settle_us)))
    for i, (name, val) in enumerate(phases):
        box(x[i], 426, x[i+1], 720, fill="#f3f7fa" if i%2 == 0 else "#ffffff")
        text((x[i]+x[i+1])/2, 433, name, size=8, anchor="n", wrap=x[i+1]-x[i]-4)
        text((x[i]+x[i+1])/2, 468, val, size=9, anchor="n")
    frame_index = 1 if preview.hold_read_bias else 2
    adc_index = frame_index + 2
    pulse_end = x[frame_index] + (x[frame_index+1]-x[frame_index]) * preview.pulse_us/preview.frame_us

    def waveform(y, name, start, end, color, held=False):
        text(24, y-16, name, size=10, color=color, wrap=126)
        base, high = y+9, y-10
        line(left, base, right, base, fill="#d4dfe8")
        if held:
            line(left, high, right, high, fill=color, width=2.5)
        elif end > start:
            canvas.create_line(left, base+body_offset, start, base+body_offset,
                               start, high+body_offset, end, high+body_offset,
                               end, base+body_offset, right, base+body_offset, fill=color, width=2.5)
        else:
            line(left, base, right, base, fill=color, width=2.5)

    bias_label = (f"N3/N4 바이어스 유지\n+{preview.read_bias_v:g} V" if preview.hold_read_bias
                  else f"{node} 읽기 바이어스")
    waveform(512, bias_label, x[0], x[adc_index], ORANGE, held=preview.hold_read_bias)
    waveform(553, f"{gate_node} / {access} ON", x[frame_index-1], x[adc_index], BLUE)
    waveform(594, f"{preview.wl_label}\nVREAD 선택", x[frame_index], pulse_end, TEAL)
    waveform(635, "ADC 취득", x[adc_index], x[adc_index+1], "#7762a1")
    waveform(676, "적분기 리셋", x[adc_index+1], x[adc_index+2], MUTED)
    if preview.hold_read_bias:
        until = "다음 읽기까지" if preview.compound else "최종 정리까지"
        end_note = f"읽기 후 {access} OFF → ADC → 리셋 · N3/N4 바이어스는 {until} 유지"
    else:
        end_note = f"읽기 후 {access} OFF → {zero_rail}(0 V) 선택 → {node}=0 V → ADC → 리셋"
    text(24, 738, end_note, size=10, wrap=width-48)
    text(24, 771, f"q={preview.q:g} · ON 슬롯 {preview.on_slots}/{preview.total_slots} · "
         f"ADC 대기 합 {duration(preview.adc_wait_us)} + 실제 변환 시간", color=MUTED, size=10, wrap=width-48)
    if preview.compound:
        text(24, 805, f"고정 지연만 합한 최소 {'한 쌍' if preview.pair else '1회'} 시간: {duration(preview.block_min_us)}", bold=True, wrap=width-48)
    if preview.hold_read_bias:
        when = "반복 전" if preview.compound else "ACTUAL 전"
        text(24, 836, f"{when} BLTOP/BLBTM ON 및 두 MUX를 준비하고 {duration(preview.gate_settle_us)} 안정화합니다.\n"
             "S1/S2 중 하나씩만 ON · 전체 종료/중단 시 두 BL을 0 V로 복귀합니다.",
             color=MUTED, size=10, wrap=width-48)
    text(24, 887, PIN_ROLE_NOTE, size=10, wrap=width-48)
    y = 940
    if preview.update:
        text(24, y, f"정상 결선 · VCAP(이상적) = BLTOP − BLBTM = "
             f"{preview.update_top_v:g} − {preview.update_bottom_v:g} = {preview.update_cap_v:g} V", wrap=width-48)
        y += 58
    for message in (*preview.errors, *preview.warnings):
        text(24, y, message, size=10, color="#a44427" if message in preview.errors else MUTED, wrap=width-48)
        y += 58
    canvas.configure(scrollregion=(0, 0, width, max(y+20, 900)+body_offset))
