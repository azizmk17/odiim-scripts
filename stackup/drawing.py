"""Shared vector sketch used by the desktop canvas and printable reports."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math

from .model import Project


@dataclass
class Shape:
    kind: str
    coordinates: tuple[float, ...]
    fill: str = ""
    stroke: str = ""
    width: float = 1.0
    text: str = ""
    size: int = 11
    bold: bool = False
    dashed: bool = False
    arrow: bool = False
    tag: str = ""


def scene(project: Project, positions: dict[str, float] | None = None) -> list[Shape]:
    if not project.points:
        return []
    origin = project.point(project.datum).x
    xs = {point.id: point.x - origin for point in project.points}
    if positions:
        xs.update({key: value for key, value in positions.items() if math.isfinite(value)})
    ys = {point.id: point.y for point in project.points}
    shapes: list[Shape] = []
    line = lambda coords, color, tag="", width=1, dashed=False, arrow=False: shapes.append(
        Shape("line", tuple(coords), stroke=color, width=width, dashed=dashed, arrow=arrow, tag=tag))
    text = lambda x, y, value, color="#334155", tag="", size=11, bold=False: shapes.append(
        Shape("text", (x, y), fill=color, text=value, size=size, bold=bold, tag=tag))
    min_y = min(ys.values())
    max_y = max(ys.values())
    height = max((body.height / 2 for body in project.bodies), default=3)
    top = min_y - 13 - 5 * len(project.dimensions)
    bottom = max_y + height + 21 + 7 * len(project.fits)
    datum_x = xs[project.datum]
    line((datum_x, top, datum_x, bottom), "#cbd5e1", dashed=True)
    text(datum_x, bottom + 2, "A · DATUM", "#64748b", size=10, bold=True)

    # Large hollow housings must be drawn behind their contained parts.
    for body in sorted(project.bodies, key=lambda body: not body.hollow):
        x1, x2 = xs[body.left], xs[body.right]
        center_y = (ys[body.left] + ys[body.right]) / 2
        shapes.append(Shape("rectangle", (x1, center_y - body.height / 2, x2, center_y + body.height / 2),
                            fill="#f8fafc" if body.hollow else body.color,
                            stroke=body.color, width=2, tag="body:" + body.id))
        text((x1 + x2) / 2, center_y - body.height / 2 - 2 if body.hollow else center_y,
             body.name, body.color if body.hollow else "#ffffff", "body:" + body.id, bold=True)

    for index, dimension in enumerate(project.dimensions):
        x1, x2 = xs[dimension.start], xs[dimension.end]
        dimension_y = min_y - height - 6 - index * 5
        color = "#7c3aed" if dimension.kind == "placement" else "#64748b" if dimension.kind == "contact" else "#475569"
        tag = "dimension:" + dimension.id
        for point_id in (dimension.start, dimension.end):
            line((xs[point_id], ys[point_id], xs[point_id], dimension_y - 1), "#cbd5e1", tag)
        line((x1, dimension_y, x2, dimension_y), color, tag, arrow=True)
        label = f"{dimension.id}  {dimension.nominal:g}  ({dimension.lower:+g} / {dimension.upper:+g})"
        if dimension.kind == "placement":
            label += "  FLOAT"
        elif dimension.kind == "contact":
            label = f"{dimension.id}  CONTACT"
        text((x1 + x2) / 2, dimension_y - 1.4, label, color, tag, size=10)

    for index, fit in enumerate(project.fits):
        y = max_y + height + 5 + index * 7
        tag = "fit:" + fit.id
        sl, sr, bl, br = (xs[p] for p in (fit.slot_left, fit.slot_right, fit.body_left, fit.body_right))
        line((sl, y, bl, y), "#0d9488", tag, dashed=True, arrow=True)
        line((br, y, sr, y), "#0d9488", tag, dashed=True, arrow=True)
        modes = {"free": "FREE FLOAT", "left": "LEFT CONTACT", "right": "RIGHT CONTACT", "centered": "CENTERED"}
        text((sl + sr) / 2, y + 2.1, fit.id + " · " + modes[fit.mode], "#0f766e", tag, size=10, bold=True)

    if project.gap:
        gap = project.gap
        x1, x2 = xs[gap.start], xs[gap.end]
        y = max_y + height + 13 + 7 * len(project.fits)
        for point_id in (gap.start, gap.end):
            line((xs[point_id], ys[point_id], xs[point_id], y + 1), "#99f6e4", "gap")
        line((x1, y, x2, y), "#059669", "gap", width=2.5, arrow=True)
        if abs(x2 - x1) < 1e-8:
            line((x1, y - 1.5, x1, y + 1.5), "#059669", "gap", width=2)
        text((x1 + x2) / 2, y + 2, f"GAP = {x2 - x1:.3f} mm", "#047857", "gap", bold=True)

    overlaps: dict[tuple[float, float], int] = {}
    for point in project.points:
        x, y = xs[point.id], point.y
        tag = "point:" + point.id
        shapes.append(Shape("oval", (x - 0.36, y - 0.36, x + 0.36, y + 0.36), fill="#ffffff", stroke="#0f172a", width=1.7, tag=tag))
        key = (round(x, 3), round(y, 3))
        offset = overlaps.get(key, 0)
        overlaps[key] = offset + 1
        text(x, y + 3.1 + offset * 1.7, point.id, "#334155", tag, size=10, bold=point.id == project.datum)
    return shapes


def scene_bounds(shapes: list[Shape]) -> tuple[float, float, float, float]:
    if not shapes:
        return -10, -10, 50, 40
    coordinates = [shape.coordinates for shape in shapes]
    xs = [value for coords in coordinates for value in coords[::2]]
    ys = [value for coords in coordinates for value in coords[1::2]]
    return min(xs) - 6, min(ys) - 4, max(xs) + 6, max(ys) + 4


def svg_sketch(project: Project, positions: dict[str, float] | None = None, *, label: str = "Assembly sketch") -> str:
    shapes = scene(project, positions)
    left, top, right, bottom = scene_bounds(shapes)
    scale = min(17.0, 1050 / max(right - left, 1))
    width, height = max(600, (right - left) * scale + 60), max(260, (bottom - top) * scale + 40)
    transform = lambda value, axis: (value - (left if axis == 0 else top)) * scale + (30 if axis == 0 else 20)
    items = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{escape(label, quote=True)}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>']
    for shape in shapes:
        coords = [transform(value, index % 2) for index, value in enumerate(shape.coordinates)]
        style = f'fill="{shape.fill or "none"}" stroke="{shape.stroke or "none"}" stroke-width="{shape.width}"'
        if shape.dashed:
            style += ' stroke-dasharray="5 4"'
        if shape.kind == "line":
            arrow = ' marker-start="url(#arrow)" marker-end="url(#arrow)"' if shape.arrow else ""
            items.append(f'<line x1="{coords[0]:.3f}" y1="{coords[1]:.3f}" x2="{coords[2]:.3f}" y2="{coords[3]:.3f}" {style}{arrow}/>')
        elif shape.kind == "rectangle":
            items.append(f'<rect x="{min(coords[0], coords[2]):.3f}" y="{min(coords[1], coords[3]):.3f}" width="{abs(coords[2]-coords[0]):.3f}" height="{abs(coords[3]-coords[1]):.3f}" {style}/>')
        elif shape.kind == "oval":
            items.append(f'<ellipse cx="{(coords[0]+coords[2])/2:.3f}" cy="{(coords[1]+coords[3])/2:.3f}" rx="{abs(coords[2]-coords[0])/2:.3f}" ry="{abs(coords[3]-coords[1])/2:.3f}" {style}/>')
        elif shape.kind == "text":
            items.append(f'<text x="{coords[0]:.3f}" y="{coords[1]:.3f}" fill="{shape.fill}" text-anchor="middle" dominant-baseline="middle" font-family="Arial, sans-serif" font-size="{shape.size}" font-weight="{700 if shape.bold else 400}">{escape(shape.text)}</text>')
    items.append("</svg>")
    return "".join(items)
