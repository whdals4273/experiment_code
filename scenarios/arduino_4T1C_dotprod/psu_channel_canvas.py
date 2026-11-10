"""Static transcription of the user's PSU channel/wiring reference table.

This is the supplied reference, not a live view of the editable PSU mapping.
Normal/reverse wiring is physical; this view never changes output polarity.
"""


def draw_psu_channels(canvas, width, height):
    """Fit the supplied three-PSU table, preserving its labels and colors."""
    canvas.delete("all")
    width, height = max(1, width), max(1, height)
    scale = min(max(1, width-24)/1702, max(1, height-24)/1440)
    left, top = (width-1702*scale)/2, (height-1440*scale)/2

    def point(x, y):
        return left+x*scale, top+y*scale

    def text(x, y, value, *, size=56, color="black", anchor="center", wrap=0):
        canvas.create_text(*point(x, y), text=value, anchor=anchor, fill=color,
                           font=("TkDefaultFont", -max(1, round(size*scale)), "bold"),
                           width=wrap*scale)

    text(12, 32, "✓ PSU channel 설정", size=54, anchor="nw")
    text(1470, 57, "✓ 정상 결선", size=38, anchor="w")
    text(1470, 101, "✓ 역결선", size=38, color="red", anchor="w")

    xs = (58, 278, 743, 1208, 1673)
    ys = (149, 250, 584, 917, 1250)
    for x in xs:
        canvas.create_line(*point(x, ys[0]), *point(x, ys[-1]),
                           fill="#aaaaaa", width=max(.5, scale))
    for y in ys:
        canvas.create_line(*point(xs[0], y), *point(xs[-1], y),
                           fill="#aaaaaa", width=max(.5, scale))
    for column in range(3):
        text((xs[column+1]+xs[column+2])/2, (ys[0]+ys[1])/2,
             f"channel {column+1}")
    cells = (
        (("N3 off\nBLTOP off", "black"), ("N4 off\nBLBTM off", "black"), ("VREAD", "red")),
        (("N3 on\nBLTOP on", "black"), ("N4 on\nBLBTM on", "black"), ("N1/N2 off\n(-2V)", "red")),
        (("VDD\n(5V)", "black"), ("VSS\n(-5V)", "red"), ("N1/N2 on\n(3V)", "black")),
    )
    for row, cells_in_row in enumerate(cells):
        y = (ys[row+1]+ys[row+2])/2
        text((xs[0]+xs[1])/2, y, f"psu{row+1}")
        for column, (value, color) in enumerate(cells_in_row):
            text((xs[column+1]+xs[column+2])/2, y, value, color=color)

    text(48, 1280, "✓ 네 BL 입력 모두 정상 결선 · UPDATE: VCAP = BLTOP − BLBTM",
         size=42, anchor="nw", wrap=1600)
    text(48, 1370, "✓ 괄호 안 전압은 상시 공급 전압입니다.",
         size=42, anchor="nw", wrap=1600)
