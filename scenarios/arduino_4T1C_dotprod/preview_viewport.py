"""Fit the pulse diagram to its viewport without horizontal scrolling.

The renderer uses a 96-DPI design space. Map coordinates, pixel fonts and
measured text bounds together so a narrow pane or Windows DPI scaling does
not enlarge the chart beyond its allotted width.
"""


class PreviewViewport:
    def __init__(self, canvas, width, display_scale=1.0):
        self.canvas = canvas
        self.width = max(1, width)
        self.logical_width = max(620, self.width / display_scale)
        self.scale = self.width / self.logical_width

    def delete(self, tag):
        self.canvas.delete(tag)

    def configure(self, **options):
        if "scrollregion" in options:
            _left, top, _right, bottom = options["scrollregion"]
            options["scrollregion"] = (0, top*self.scale, self.width, bottom*self.scale)
        self.canvas.configure(**options)

    def bbox(self, item):
        return tuple(value/self.scale for value in self.canvas.bbox(item))

    def coords(self, item, *coordinates):
        self.canvas.coords(item, *(value*self.scale for value in coordinates))

    def create_text(self, x, y, **options):
        font = options.get("font", ("TkDefaultFont", 11, "normal"))
        pixels = abs(font[1]) * (4/3 if font[1] > 0 else 1)
        options["font"] = (font[0], -max(1, round(pixels*self.scale)), *font[2:])
        anchor = options.get("anchor", "center")
        available = (self.logical_width-x-12 if anchor.endswith("w") else
                     x-12 if anchor.endswith("e") else
                     2*min(x-12, self.logical_width-x-12))
        options["width"] = max(1, min(options.get("width") or available, available)*self.scale)
        return self.canvas.create_text(x*self.scale, y*self.scale, **options)

    def create_line(self, *coordinates, **options):
        options["width"] = options.get("width", 1)*self.scale
        if "dash" in options:
            options["dash"] = tuple(max(1, round(value*self.scale)) for value in options["dash"])
        return self.canvas.create_line(*(value*self.scale for value in coordinates), **options)

    def create_rectangle(self, *coordinates, **options):
        return self.canvas.create_rectangle(*(value*self.scale for value in coordinates), **options)
