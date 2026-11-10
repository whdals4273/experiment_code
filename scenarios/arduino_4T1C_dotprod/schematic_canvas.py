"""Static Canvas transcription of the user's 2×2 4T1C reference drawing.

N1–N4 here are the transistor labels in that supplied figure. They are kept
verbatim, independently of the experiment's current N1–N4 node naming.
This view does not drive pins or infer a selected measurement path.
"""

DRAWING_WIDTH = 1943
DRAWING_HEIGHT = 1424


def draw_schematic(canvas, width, height):
    """Fit the full reference diagram while keeping the original aspect ratio."""
    canvas.delete("all")
    width, height = max(1, width), max(1, height)
    scale = min(max(1, width-24)/DRAWING_WIDTH, max(1, height-24)/DRAWING_HEIGHT)
    left, top = (width-DRAWING_WIDTH*scale)/2, (height-DRAWING_HEIGHT*scale)/2
    stroke = max(1, 6*scale)

    def coordinates(points):
        return tuple((left if i % 2 == 0 else top) + value*scale
                     for i, value in enumerate(points))

    def wire(*points):
        canvas.create_line(*coordinates(points), fill="black", width=stroke,
                           capstyle="projecting", joinstyle="round")

    def label(x, y, text, *, bold=False, anchor="center"):
        canvas.create_text(*coordinates((x, y)), text=text, anchor=anchor, fill="black",
                           font=("Arial", -max(1, round(32*scale)), "bold" if bold else "normal"))

    def dot(x, y, *, hollow=False):
        canvas.create_oval(*coordinates((x-7, y-7, x+7, y+7)),
                           fill="#c9e8f6" if hollow else "black", outline="black",
                           width=stroke)

    # Rounded blue cell backgrounds, drawn before the common wires.
    for x in (254, 1109):
        for y in (43, 725):
            r, w, h = 30, 788, 626
            points = (x+r, y, x+w-r, y, x+w, y, x+w, y+r,
                      x+w, y+h-r, x+w, y+h, x+w-r, y+h,
                      x+r, y+h, x, y+h, x, y+h-r, x, y+r, x, y)
            canvas.create_polygon(*coordinates(points), smooth=True,
                                  fill="#c9e8f6", outline="")

    # Shared row input rails and independent column gate/output rails.
    for y in (0, 682):
        for offset, name in ((79, "Q>0"), (132, "Q<0"),
                             (571, "VCAP TOP"), (626, "VCAP BTM")):
            wire(209, y+offset, 1918, y+offset)
            label(197, y+offset, name, anchor="e")
    for x in (0, 855):
        for offset in (352, 399, 994):
            wire(x+offset, 26, x+offset, 1361)
        label(x+284, 1394, "N1·N2 on/off", anchor="w")
        label(x+994, 1394, "OUT")

    for column, x in enumerate((0, 855)):
        for row, y in enumerate((0, 682)):
            def path(*points):
                wire(*(value + (x if i % 2 == 0 else y)
                       for i, value in enumerate(points)))

            # Write-access transistors and their independently controlled gates.
            path(352, 187, 567, 187, 567, 229)
            path(532, 229, 602, 229)
            path(289, 571, 289, 275, 532, 275, 532, 243,
                 602, 243, 602, 275, 820, 275)
            path(399, 354, 567, 354, 567, 372)
            path(532, 372, 602, 372)
            path(312, 626, 312, 418, 532, 418, 532, 386,
                 602, 386, 602, 418, 820, 418)

            # Storage capacitor between the two read-transistor gate nodes.
            path(694, 275, 694, 317)
            path(662, 317, 726, 317)
            path(662, 335, 726, 335)
            path(694, 335, 694, 418)

            # Positive/negative query paths share this column's output.
            path(826, 244, 826, 307)
            path(874, 79, 874, 244, 840, 244, 840, 308,
                 874, 308, 874, 333, 994, 333)
            path(826, 386, 826, 451)
            path(948, 132, 948, 363, 874, 363, 874, 386,
                 840, 386, 840, 451, 874, 451, 874, 495, 994, 495)

            for px, py in ((352, 187), (399, 354), (289, 571), (312, 626),
                           (874, 79), (948, 132), (994, 333), (994, 495)):
                dot(x+px, y+py)
            for py in (275, 418):
                dot(x+694, y+py, hollow=column == 0 and row == 0)
            for px, py, text, anchor in ((523, 250, "N1", "e"), (523, 393, "N2", "e"),
                                         (879, 273, "N3", "w"), (879, 418, "N4", "w")):
                label(x+px, y+py, text, bold=True, anchor=anchor)
