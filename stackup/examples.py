"""Small assemblies with independently checkable analytical results."""

from .model import Body, Dimension, Fit, Gap, Point, Project


def floating_block(mode: str = "free") -> Project:
    return Project(
        name={"free": "Floating block", "centered": "Centered block", "left": "Left-seated block", "right": "Right-seated block"}[mode],
        points=[Point("P1", "Slot left", 0, 20), Point("P2", "Slot right", 40, 20),
                Point("P3", "Block left", 3, 20), Point("P4", "Block right", 37, 20)],
        bodies=[Body("B1", "Housing / slot", "P1", "P2", 12, "#64748b", True),
                Body("B2", "Floating block", "P3", "P4", 6, "#2563eb")],
        dimensions=[Dimension("D1", "Slot width", "P1", "P2", 40, -0.2, 0.2),
                    Dimension("D2", "Block width", "P3", "P4", 34, -0.1, 0.1)],
        fits=[Fit("F1", "Block in slot", "P1", "P2", "P3", "P4", mode)],
        datum="P1", gap=Gap("P4", "P2", "Right-hand gap", 0.5, 6.5),
    )


def serial_chain() -> Project:
    return Project(
        name="Three-part chain",
        points=[Point("P1", "Datum", 0, 20), Point("P2", "A right", 20, 20),
                Point("P3", "B right", 35, 20), Point("P4", "C right", 45, 20),
                Point("P5", "Housing end", 50, 20)],
        bodies=[Body("B1", "Part A", "P1", "P2", 6, "#2563eb"),
                Body("B2", "Part B", "P2", "P3", 6, "#8b5cf6"),
                Body("B3", "Part C", "P3", "P4", 6, "#0891b2")],
        dimensions=[Dimension("D1", "Housing length", "P1", "P5", 50, -0.2, 0.2),
                    Dimension("D2", "A length", "P1", "P2", 20, -0.1, 0.1),
                    Dimension("D3", "B length", "P2", "P3", 15, -0.05, 0.05),
                    Dimension("D4", "C length", "P3", "P4", 10, -0.1, 0.1)],
        datum="P1", gap=Gap("P4", "P5", "End gap", 4, 6),
    )


def placement_shift() -> Project:
    return Project(
        name="Bracket with mounting float",
        points=[Point("P1", "Base datum", 0, 20), Point("P2", "Base end", 50, 20),
                Point("P3", "Bracket left", 10, 30), Point("P4", "Bracket tip", 45, 30)],
        bodies=[Body("B1", "Base", "P1", "P2", 5, "#64748b"),
                Body("B2", "Bracket", "P3", "P4", 5, "#2563eb")],
        dimensions=[Dimension("D1", "Base length", "P1", "P2", 50, -0.2, 0.2),
                    Dimension("D2", "Bracket length", "P3", "P4", 35, -0.1, 0.1),
                    Dimension("D3", "Mounting position / float", "P1", "P3", 10, -0.5, 0.5, "placement")],
        datum="P1", gap=Gap("P4", "P2", "Tip gap", 4, 6),
    )


def fit_risk() -> Project:
    project = floating_block()
    project.name = "Some permitted parts do not fit"
    project.dimensions[0].nominal = 34
    project.dimensions[0].lower, project.dimensions[0].upper = -0.3, 0.3
    project.points[1].x = 34
    project.points[2].x, project.points[3].x = 0, 34
    project.gap.minimum_allowed, project.gap.maximum_allowed = 0, 0.5
    return project


EXAMPLES = {
    "Floating block": floating_block,
    "Centered block": lambda: floating_block("centered"),
    "Three-part chain": serial_chain,
    "Mounting float": placement_shift,
    "Assembly fit risk": fit_risk,
}
