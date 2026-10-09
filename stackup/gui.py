"""Tk desktop editor. Analysis runs in one worker; Tk stays on the main thread."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from .drawing import scene, scene_bounds, svg_sketch
from .examples import EXAMPLES, floating_block
from .io import export_csv, export_html, load_project, number, save_project, _atomic_text
from .model import Body, Dimension, Fit, Gap, Point, Project
from .solver import Analysis, analyze

INK = "#0f172a"
MUTED = "#64748b"
BLUE = "#2563eb"
BG = "#f1f5f9"
COLORS = ["#2563eb", "#8b5cf6", "#0891b2", "#ea580c", "#16a34a"]
KINDS = {"Manufacturing size": "size", "Assembly placement / float": "placement", "Face contact": "contact"}
MODES = {"Free float": "free", "Left face in contact": "left", "Right face in contact": "right", "Centered (equal gaps)": "centered"}


def _numeric(text: str, label: str, *, optional: bool = False) -> float | None:
    if optional and not text.strip():
        return None
    try:
        value = float(text.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class FormDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, project: Project):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(background="white")
        self.project = project
        self.result = None
        self.variables: dict[str, tk.StringVar] = {}
        self.form = ttk.Frame(self, padding=22, style="White.TFrame")
        self.form.pack(fill="both", expand=True)
        self.row = 0
        self.build()
        buttons = ttk.Frame(self.form, style="White.TFrame")
        buttons.grid(row=self.row, column=0, columnspan=2, sticky="e", pady=(20, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=5)
        ttk.Button(buttons, text="Apply", style="Primary.TButton", command=self.submit).pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self.submit())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx() + 100}+{parent.winfo_rooty() + 95}")
        self.deiconify()
        self.grab_set()
        self.focus_set()

    def note(self, text: str) -> None:
        ttk.Label(self.form, text=text, wraplength=430, style="Note.TLabel").grid(row=self.row, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self.row += 1

    def entry(self, key: str, label: str, value="") -> ttk.Entry:
        variable = self.variables[key] = tk.StringVar(value=str(value))
        ttk.Label(self.form, text=label, style="White.TLabel").grid(row=self.row, column=0, sticky="w", padx=(0, 18), pady=6)
        entry = ttk.Entry(self.form, textvariable=variable, width=34)
        entry.grid(row=self.row, column=1, sticky="ew", pady=6)
        self.row += 1
        return entry

    def combo(self, key: str, label: str, values: list[str], current: str) -> ttk.Combobox:
        variable = self.variables[key] = tk.StringVar(value=current)
        ttk.Label(self.form, text=label, style="White.TLabel").grid(row=self.row, column=0, sticky="w", padx=(0, 18), pady=6)
        combo = ttk.Combobox(self.form, textvariable=variable, values=values, state="readonly", width=32)
        combo.grid(row=self.row, column=1, sticky="ew", pady=6)
        self.row += 1
        return combo

    def point_combo(self, key: str, label: str, point_id: str | None) -> None:
        choices = [f"{point.id} · {point.name}" for point in self.project.points]
        selected = next((value for value in choices if value.split(" · ", 1)[0] == point_id), choices[0] if choices else "")
        self.combo(key, label, choices, selected)

    def point_id(self, key: str) -> str:
        return self.variables[key].get().split(" · ", 1)[0]

    def text(self, key: str) -> str:
        return self.variables[key].get().strip()

    def float(self, key: str, *, optional: bool = False):
        return _numeric(self.text(key), key.replace("_", " ").capitalize(), optional=optional)

    def build(self):
        raise NotImplementedError

    def value(self):
        raise NotImplementedError

    def submit(self) -> None:
        try:
            result = self.value()
        except (ValueError, KeyError) as exc:
            messagebox.showerror("Check the inputs", str(exc), parent=self)
            return
        self.result = result
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


class DimensionDialog(FormDialog):
    def __init__(self, parent, project, start=None, end=None, existing: Dimension | None = None):
        self.start, self.end, self.existing = start, end, existing
        super().__init__(parent, "Edit dimension" if existing else "Add dimension or movement", project)

    def build(self):
        old = self.existing
        self.note("The signed dimension is x(To) − x(From). Enter signed lower and upper deviations in mm. A placement range limits assembly movement.")
        self.entry("name", "Name", old.name if old else self.project.next_id("D"))
        self.point_combo("start", "From face", old.start if old else self.start or self.project.points[0].id)
        self.point_combo("end", "To face", old.end if old else self.end or self.project.points[1].id)
        kind = next(label for label, value in KINDS.items() if value == (old.kind if old else "size"))
        combo = self.combo("kind", "Relation", list(KINDS), kind)
        default = 0.0
        if self.start and self.end:
            default = self.project.point(self.end).x - self.project.point(self.start).x
        self.entry("nominal", "Nominal (signed mm)", old.nominal if old else round(default, 3))
        self.entry("lower", "Lower deviation", old.lower if old else -0.1)
        self.entry("upper", "Upper deviation", old.upper if old else 0.1)
        combo.bind("<<ComboboxSelected>>", self.kind_changed)

    def kind_changed(self, _=None):
        if KINDS[self.text("kind")] == "contact":
            for key in ("nominal", "lower", "upper"):
                self.variables[key].set("0")

    def value(self):
        kind = KINDS[self.text("kind")]
        values = (0.0, 0.0, 0.0) if kind == "contact" else (self.float("nominal"), self.float("lower"), self.float("upper"))
        item = Dimension(self.existing.id if self.existing else self.project.next_id("D"), self.text("name") or "Dimension",
                         self.point_id("start"), self.point_id("end"), *values, kind)
        trial = self.project.copy()
        trial.dimensions = [d for d in trial.dimensions if d.id != item.id] + [item]
        trial.validate()
        return item


class BodyDialog(FormDialog):
    def __init__(self, parent, project, left, right, existing: Body | None = None):
        self.left, self.right, self.existing = left, right, existing
        self.old_dimension = next((d for d in project.dimensions if d.start == left and d.end == right and d.kind == "size"), None)
        super().__init__(parent, "Edit body" if existing else "New body", project)

    def build(self):
        body, dimension = self.existing, self.old_dimension
        self.note("The rectangle is a 1D body projected along x. Its height is drawing layout only. The width below drives the calculation.")
        self.entry("name", "Body name", body.name if body else "Part " + self.project.next_id("B"))
        self.combo("type", "Sketch style", ["Solid part", "Housing / slot"], "Housing / slot" if body and body.hollow else "Solid part")
        width = self.project.point(self.right).x - self.project.point(self.left).x
        self.entry("width", "Nominal width (mm)", dimension.nominal if dimension else round(width, 3))
        self.entry("lower", "Lower deviation", dimension.lower if dimension else -0.1)
        self.entry("upper", "Upper deviation", dimension.upper if dimension else 0.1)
        self.entry("height", "Drawing height", body.height if body else 6)

    def value(self):
        width, low, high, height = self.float("width"), self.float("lower"), self.float("upper"), self.float("height")
        if width <= 0 or width + low <= 0 or low > high or height <= 0:
            raise ValueError("Use a positive width and drawing height, a positive minimum width, and ordered deviations.")
        name = self.text("name") or "Part"
        body = Body(self.existing.id if self.existing else self.project.next_id("B"), name, self.left, self.right, height,
                    self.existing.color if self.existing else COLORS[len(self.project.bodies) % len(COLORS)], self.text("type") == "Housing / slot")
        dimension = Dimension(self.old_dimension.id if self.old_dimension else self.project.next_id("D"),
                              self.old_dimension.name if self.old_dimension else name + " width", self.left, self.right, width, low, high)
        return body, dimension


class FitDialog(FormDialog):
    def __init__(self, parent, project, existing: Fit | None = None):
        self.existing = existing
        super().__init__(parent, "Edit assembly fit" if existing else "Add assembly fit", project)

    def build(self):
        old = self.existing
        self.note("Select the slot's two limits and the moving part's two faces. Clearance is calculated from their toleranced widths; the same movement acts on both faces.")
        self.entry("name", "Fit name", old.name if old else "Assembly fit " + self.project.next_id("F"))
        housing = next((body for body in self.project.bodies if body.hollow), None)
        moving = next((body for body in self.project.bodies if not body.hollow), None)
        ids = [p.id for p in self.project.points]
        defaults = [housing.left if housing else ids[0], housing.right if housing else ids[1],
                    moving.left if moving else ids[2], moving.right if moving else ids[3]]
        for key, label, default in zip(("slot_left", "slot_right", "body_left", "body_right"),
                                        ("Slot left limit", "Slot right limit", "Moving left face", "Moving right face"), defaults):
            self.point_combo(key, label, getattr(old, key) if old else default)
        mode = next(label for label, value in MODES.items() if value == (old.mode if old else "free"))
        self.combo("mode", "Assembly condition", list(MODES), mode)

    def value(self):
        item = Fit(self.existing.id if self.existing else self.project.next_id("F"), self.text("name") or "Fit",
                   *[self.point_id(key) for key in ("slot_left", "slot_right", "body_left", "body_right")], MODES[self.text("mode")])
        trial = self.project.copy()
        trial.fits = [fit for fit in trial.fits if fit.id != item.id] + [item]
        trial.validate()
        return item


class GapDialog(FormDialog):
    def __init__(self, parent, project, start=None, end=None):
        self.start, self.end = start, end
        super().__init__(parent, "Functional gap and requirements", project)

    def build(self):
        old = self.project.gap
        self.note("Gap = x(To) − x(From). A negative value indicates overlap in this signed measurement. Leave a requirement blank for no limit.")
        self.entry("name", "Gap name", old.name if old else "Functional gap")
        self.point_combo("start", "From face", self.start or (old.start if old else None))
        self.point_combo("end", "To face", self.end or (old.end if old else self.project.points[1].id))
        self.entry("minimum_allowed", "Minimum required (mm)", old.minimum_allowed if old and old.minimum_allowed is not None else "0")
        self.entry("maximum_allowed", "Maximum required (mm)", old.maximum_allowed if old and old.maximum_allowed is not None else "")

    def value(self):
        item = Gap(self.point_id("start"), self.point_id("end"), self.text("name") or "Gap",
                   self.float("minimum_allowed", optional=True), self.float("maximum_allowed", optional=True))
        trial = self.project.copy()
        trial.gap = item
        trial.validate()
        return item


class PointDialog(FormDialog):
    def __init__(self, parent, project, point: Point):
        self.point = point
        super().__init__(parent, "Edit point / face", project)

    def build(self):
        self.note("The x coordinate requests a reference sketch pose; dimensions and assembly constraints determine the final location. The y coordinate is layout only.")
        self.entry("name", "Point name", self.point.name)
        self.entry("x", "Reference sketch x", self.point.x)
        self.entry("y", "Layout y", self.point.y)

    def value(self):
        return Point(self.point.id, self.text("name") or self.point.id, self.float("x"), self.float("y"))


class StackupApp(tk.Tk):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.title("Odiim · 1D Stackup")
        self.geometry(f"{max(1080, min(1440, self.winfo_screenwidth() - 40))}x{max(720, min(900, self.winfo_screenheight() - 70))}")
        self.minsize(1080, 720)
        self.configure(background=BG)
        self.project = project or floating_block()
        self.file_path: Path | None = None
        self.saved_state = json.dumps(self.project.to_dict(), sort_keys=True)
        self.undo_stack: list[Project] = []
        self.redo_stack: list[Project] = []
        self.result: Analysis | None = None
        self.revision = 0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stackup")
        self.future = None
        self.pending_analysis = False
        self.analysis_timer = None
        self.closing = False
        self.scale, self.origin_x, self.origin_y = 12.0, 110.0, 240.0
        self.mode = tk.StringVar(value="select")
        self.view = tk.StringVar(value="Reference pose")
        self.snap = tk.BooleanVar(value=True)
        self.tool_start = None
        self.drag = None
        self.pan_start = None
        self.selected: tuple[str, str] | None = None
        self._styles()
        self._build()
        self._shortcuts()
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.refresh()
        self.after(120, self.fit_view)
        self.after(60, self.poll_analysis)

    @property
    def dirty(self):
        return json.dumps(self.project.to_dict(), sort_keys=True) != self.saved_state

    def _styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        font = "Segoe UI" if self.tk.call("tk", "windowingsystem") == "win32" else "DejaVu Sans"
        self.font = font
        for named in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            tkfont.nametofont(named, root=self).configure(family=font, size=10)
        self.option_add("*Font", (font, 10))
        style.configure("TFrame", background=BG)
        style.configure("White.TFrame", background="white")
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("White.TLabel", background="white", foreground=INK)
        style.configure("Note.TLabel", background="white", foreground=MUTED, font=(font, 9))
        style.configure("TButton", font=(font, 10), width=0, padding=(10, 7), background="#e2e8f0", foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", "#cbd5e1")])
        style.configure("Primary.TButton", background=BLUE, foreground="white")
        style.map("Primary.TButton", background=[("active", "#1d4ed8")])
        style.configure("Tool.TRadiobutton", background=BG, padding=(9, 7), indicatorrelief="flat")
        style.map("Tool.TRadiobutton", background=[("selected", "#dbeafe")], foreground=[("selected", "#1d4ed8")])
        style.configure("Treeview", font=(font, 9), rowheight=29, background="white", fieldbackground="white", foreground=INK, borderwidth=0)
        style.configure("Treeview.Heading", font=(font, 9, "bold"), padding=7, background="#e2e8f0")
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#1e40af")])
        style.configure("TNotebook", background="white", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(9, 8), background="#e2e8f0")
        style.map("TNotebook.Tab", background=[("selected", "white")])

    def _build(self):
        header = ttk.Frame(self, padding=(22, 14))
        header.pack(fill="x")
        ttk.Label(header, text="ODIIM", font=(self.font, 11, "bold"), foreground=BLUE).pack(side="left", padx=(0, 15))
        ttk.Label(header, text="1D Stackup", font=(self.font, 23, "bold")).pack(side="left")
        ttk.Label(header, text="SKETCH  /  TOLERANCES  /  ASSEMBLY FREEDOM", foreground=MUTED, font=(self.font, 9)).pack(side="left", padx=22)
        ttk.Button(header, text="Help", command=self.help_window).pack(side="right")
        filebar = ttk.Frame(self, padding=(18, 0, 18, 12))
        filebar.pack(fill="x")
        for text, command in [("New", self.new_project), ("Open", self.open_project), ("Save", self.save), ("Save as", lambda: self.save(True)), ("Export report", self.export)]:
            ttk.Button(filebar, text=text, command=command).pack(side="left", padx=3)
        ttk.Button(filebar, text="Undo", command=self.undo).pack(side="left", padx=(18, 3))
        ttk.Button(filebar, text="Redo", command=self.redo).pack(side="left", padx=3)
        ttk.Label(filebar, text="Example:", foreground=MUTED).pack(side="left", padx=(18, 6))
        self.example_var = tk.StringVar(value="Floating block")
        examples = ttk.Combobox(filebar, textvariable=self.example_var, values=list(EXAMPLES), state="readonly", width=20)
        examples.pack(side="left")
        examples.bind("<<ComboboxSelected>>", lambda _: self.load_example())
        ttk.Label(filebar, text="Units: mm", foreground=MUTED).pack(side="right")

        workspace = tk.PanedWindow(self, orient="horizontal", background=BG, sashwidth=8, borderwidth=0)
        workspace.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        left = ttk.Frame(workspace, style="White.TFrame", padding=12)
        center = ttk.Frame(workspace, style="White.TFrame")
        right = ttk.Frame(workspace, style="White.TFrame", padding=17)
        workspace.add(left, minsize=300, width=305, stretch="never")
        workspace.add(center, minsize=450, stretch="always")
        workspace.add(right, minsize=250, width=270, stretch="never")

        ttk.Label(left, text="MODEL", style="White.TLabel", font=(self.font, 10, "bold")).pack(anchor="w", pady=(0, 10))
        self.project_label = ttk.Label(left, text=self.project.name, style="White.TLabel", wraplength=280, font=(self.font, 12, "bold"))
        self.project_label.pack(anchor="w", pady=(0, 12))
        notebook = self.notebook = ttk.Notebook(left)
        notebook.pack(fill="both", expand=True)
        self.trees = {}
        columns = {"dimension": (["name", "nominal", "tol"], ["Name", "Nominal", "Low / high"]),
                   "fit": (["name", "mode"], ["Fit", "Condition"]),
                   "point": (["id", "name"], ["ID", "Face / point"]),
                   "body": (["id", "name"], ["ID", "Sketch body"])}
        for kind, title in [("dimension", "Dims"), ("fit", "Fits"), ("point", "Points"), ("body", "Bodies")]:
            frame = ttk.Frame(notebook, style="White.TFrame")
            notebook.add(frame, text=title)
            names, headings = columns[kind]
            tree = ttk.Treeview(frame, columns=names, show="headings", selectmode="browse")
            widths = {"dimension": [86, 78, 92], "fit": [150, 106], "point": [52, 204], "body": [52, 204]}[kind]
            for name, heading, column_width in zip(names, headings, widths):
                tree.heading(name, text=heading)
                tree.column(name, width=column_width, minwidth=45, stretch=True)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            tree.pack(fill="both", expand=True, pady=7)
            tree.bind("<<TreeviewSelect>>", lambda _, k=kind: self.tree_selected(k))
            tree.bind("<Double-1>", lambda _, k=kind: self.edit_selected(k))
            self.trees[kind] = tree
            buttons = ttk.Frame(frame, style="White.TFrame")
            buttons.pack(fill="x", pady=(6, 2))
            if kind in ("dimension", "fit"):
                ttk.Button(buttons, text="Add", command=self.add_dimension if kind == "dimension" else self.add_fit).pack(side="left", padx=2)
            ttk.Button(buttons, text="Edit", command=lambda k=kind: self.edit_selected(k)).pack(side="left", padx=2)
            ttk.Button(buttons, text="Delete", command=lambda k=kind: self.delete_selected(k)).pack(side="left", padx=2)
            if kind == "point":
                ttk.Button(frame, text="Use selected point as datum A", command=self.set_datum).pack(fill="x", pady=5)
        self.selection_label = ttk.Label(left, text="Double-click an item to edit it.", style="Note.TLabel", wraplength=280)
        self.selection_label.pack(anchor="w", pady=(14, 5))
        ttk.Label(left, text="Dimensions are signed: To − From.\nBody shapes alone do not locate parts.", style="Note.TLabel", wraplength=280).pack(anchor="w", pady=5)

        toolbar = ttk.Frame(center, padding=9)
        toolbar.pack(fill="x")
        for name, key in [("Select", "select"), ("Point", "point"), ("Body", "body"), ("Dimension", "dimension"), ("Gap", "gap")]:
            ttk.Radiobutton(toolbar, text=name, value=key, variable=self.mode, style="Tool.TRadiobutton", command=self.change_tool).pack(side="left", padx=1)
        ttk.Button(toolbar, text="Fit / float", command=self.add_fit).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Fit view", command=self.fit_view).pack(side="right")
        self.canvas = tk.Canvas(center, background="#ffffff", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", self.canvas_down)
        self.canvas.bind("<B1-Motion>", self.canvas_move)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_up)
        self.canvas.bind("<Double-Button-1>", self.canvas_double)
        self.canvas.bind("<Button-3>", self.context_menu)
        self.canvas.bind("<ButtonPress-2>", lambda event: self.start_pan(event))
        self.canvas.bind("<B2-Motion>", self.pan)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda event: self.wheel(event, 1))
        self.canvas.bind("<Button-5>", lambda event: self.wheel(event, -1))
        viewbar = ttk.Frame(center, padding=9)
        viewbar.pack(fill="x")
        ttk.Label(viewbar, text="Assembly pose:", foreground=MUTED).pack(side="left", padx=(0, 8))
        preview = ttk.Combobox(viewbar, textvariable=self.view, values=["Reference pose", "Minimum gap", "Maximum gap"], state="readonly", width=18)
        preview.pack(side="left")
        preview.bind("<<ComboboxSelected>>", lambda _: self.draw())
        ttk.Checkbutton(viewbar, text="Snap 0.5 mm", variable=self.snap).pack(side="right")

        ttk.Label(right, text="GAP ANALYSIS", style="White.TLabel", font=(self.font, 10, "bold")).pack(anchor="w", pady=(0, 10))
        self.gap_label = ttk.Label(right, text="", style="White.TLabel", font=(self.font, 12, "bold"), wraplength=210)
        self.gap_label.pack(anchor="w", pady=(0, 15))
        self.card_values = {}
        self.card_labels = {}
        for key, label, color in [("nominal", "REFERENCE AT NOMINAL SIZES", MUTED), ("minimum", "MINIMUM GAP", "#059669"), ("maximum", "MAXIMUM GAP", BLUE)]:
            card = tk.Frame(right, background="#f8fafc", padx=12, pady=12)
            card.pack(fill="x", pady=4)
            tk.Label(card, text=label, background="#f8fafc", foreground=MUTED, font=(self.font, 8, "bold"), anchor="w").pack(fill="x")
            variable = self.card_values[key] = tk.StringVar(value="—")
            value_label = self.card_labels[key] = tk.Label(card, textvariable=variable, background="#f8fafc", foreground=color, font=(self.font, 24, "bold"), anchor="w")
            value_label.pack(fill="x", pady=(5, 0))
        self.outcome = ttk.Label(right, text="", style="White.TLabel", wraplength=210, font=(self.font, 10, "bold"))
        self.outcome.pack(anchor="w", pady=(14, 7))
        self.requirement = ttk.Label(right, text="", style="Note.TLabel", wraplength=210)
        self.requirement.pack(anchor="w", pady=(0, 12))
        ttk.Button(right, text="Edit gap / requirements", command=self.edit_gap).pack(fill="x", pady=(0, 13))
        ttk.Label(right, text="ASSEMBLY MOVEMENT", style="White.TLabel", font=(self.font, 10, "bold")).pack(anchor="w", pady=(0, 7))
        self.result_text = tk.Text(right, wrap="word", borderwidth=0, background="white", foreground=MUTED, font=(self.font, 9), height=12, state="disabled")
        self.result_text.pack(fill="both", expand=True)
        self.result_text.tag_configure("strong", foreground=INK, font=(self.font, 9, "bold"))
        self.result_text.tag_configure("risk", foreground="#b45309")
        ttk.Label(right, text="Bounds apply to feasible assemblies.\nX is analytical; Y is sketch layout.", style="Note.TLabel", wraplength=240).pack(anchor="w", pady=(12, 0))
        self.status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status, padding=(22, 7), foreground=MUTED, font=(self.font, 9)).pack(fill="x")

    def _shortcuts(self):
        for key, callback in [("<Control-s>", self.save), ("<Control-o>", self.open_project), ("<Control-n>", self.new_project), ("<Control-z>", self.undo), ("<Control-y>", self.redo), ("<Escape>", self.cancel_tool)]:
            self.bind(key, lambda _, fn=callback: fn())
        self.canvas.bind("<Delete>", lambda _: self.delete_selected())

    def checkpoint(self):
        self.undo_stack.append(self.project.copy())
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()

    def refresh(self, *, recalculate=True):
        self.project_label.configure(text=self.project.name)
        self.title(f"{'* ' if self.dirty else ''}{self.project.name} · Odiim 1D Stackup")
        groups = {"point": self.project.points, "body": self.project.bodies, "dimension": self.project.dimensions, "fit": self.project.fits}
        for kind, tree in self.trees.items():
            selection = tree.selection()
            tree.delete(*tree.get_children())
            for item in groups[kind]:
                if kind == "dimension":
                    values = (item.name, f"{item.nominal:g}", f"{item.lower:+g} / {item.upper:+g}")
                elif kind == "fit":
                    values = (item.name, item.mode)
                else:
                    values = (item.id + (" · A" if kind == "point" and item.id == self.project.datum else ""), item.name)
                tree.insert("", "end", iid=item.id, values=values)
            if selection and tree.exists(selection[0]):
                tree.selection_set(selection[0])
        self.gap_label.configure(text=self.project.gap.name if self.project.gap else "Choose a gap")
        if recalculate:
            self.revision += 1
            self.result = None
            self.outcome.configure(text="Calculating…", foreground=MUTED)
            for variable in self.card_values.values():
                variable.set("—")
            self.result_text.configure(state="normal")
            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", "Updating the current assembly…")
            self.result_text.configure(state="disabled")
            if self.analysis_timer is not None:
                self.after_cancel(self.analysis_timer)
            self.analysis_timer = self.after(160, self.start_analysis)
        self.draw()

    def start_analysis(self):
        self.analysis_timer = None
        if self.future is not None:
            self.pending_analysis = True
            return
        snapshot, revision = self.project.copy(), self.revision
        self.future = self.executor.submit(lambda: (revision, analyze(snapshot)))

    def poll_analysis(self):
        if self.closing:
            return
        if self.future is not None and self.future.done():
            try:
                revision, result = self.future.result()
                if revision == self.revision:
                    self.result = result
                    self.show_result()
                    self.draw()
            except Exception as exc:
                self.result = Analysis("error", "Analysis failed: " + str(exc))
                self.show_result()
            self.future = None
            if self.pending_analysis:
                self.pending_analysis = False
                self.start_analysis()
        self.after(60, self.poll_analysis)

    def show_result(self):
        result = self.result
        if result is None:
            return
        for key, variable in self.card_values.items():
            value = getattr(result, key)
            text = number(value) + (" mm" if value is not None and math.isfinite(value) else "")
            variable.set(text)
            measured = tkfont.Font(root=self, family=self.font, size=24, weight="bold").measure(text)
            available = max(120, self.card_labels[key].winfo_width())
            size = min(24, max(12, int(24 * available / max(measured, 1))))
            self.card_labels[key].configure(font=(self.font, size, "bold"))
        risk = any(fit.fit_risk for fit in result.fits)
        if result.status == "ok":
            if risk:
                outcome, color = "SIZE FIT RISK", "#b45309"
            elif result.meets_gap_limits:
                outcome, color = "GAP WITHIN REQUIREMENTS", "#059669"
            else:
                outcome, color = "GAP OUTSIDE REQUIREMENTS", "#dc2626"
        else:
            outcome, color = result.status.upper(), "#dc2626" if result.status in ("infeasible", "error", "invalid") else MUTED
        self.outcome.configure(text=outcome, foreground=color)
        gap = self.project.gap
        if gap:
            lo = number(gap.minimum_allowed) if gap.minimum_allowed is not None else "No lower limit"
            hi = number(gap.maximum_allowed) if gap.maximum_allowed is not None else "No upper limit"
            self.requirement.configure(text=f"Required: {lo} to {hi} mm\nFaces {gap.start} → {gap.end}")
        else:
            self.requirement.configure(text="")
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("end", result.message + "\n\n")
        for fit in result.fits:
            self.result_text.insert("end", fit.name + " · " + fit.mode + "\n", "strong")
            self.result_text.insert("end", f"Size clearance: {number(fit.clearance_min)} to {number(fit.clearance_max)} mm\n")
            self.result_text.insert("end", f"Shift at nominal sizes:\n{number(fit.shift_min)} to {number(fit.shift_max)} mm\n\n")
        if not result.fits and any(d.kind == "placement" for d in self.project.dimensions):
            self.result_text.insert("end", "Assembly placement ranges are included in the gap limits.\n\n")
        for warning in result.warnings:
            self.result_text.insert("end", warning + "\n\n", "risk")
        if result.conflicts:
            self.result_text.insert("end", "Conflicting conditions:\n" + "\n".join("• " + item for item in result.conflicts), "risk")
        self.result_text.configure(state="disabled")
        self.status.set("Wheel: zoom · Middle-drag: pan · Double-click: edit · Right-click a point: datum / edit / delete")

    def display_positions(self):
        if self.result:
            attr = {"Reference pose": "nominal_positions", "Minimum gap": "minimum_positions", "Maximum gap": "maximum_positions"}[self.view.get()]
            return getattr(self.result, attr) or None
        return None

    def world(self, x, y, *, snap=True):
        wx, wy = (x - self.origin_x) / self.scale, (y - self.origin_y) / self.scale
        if snap and self.snap.get():
            wx, wy = round(wx * 2) / 2, round(wy * 2) / 2
        datum_origin = self.project.point(self.project.datum).x if self.project.points else 0
        return wx + datum_origin, wy

    def screen(self, x, y):
        return x * self.scale + self.origin_x, y * self.scale + self.origin_y

    def draw(self):
        if not hasattr(self, "canvas"):
            return
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        step = 5.0
        while step * self.scale < 32:
            step *= 2
        while step * self.scale > 95:
            step /= 2
        space = step * self.scale
        for index in range(int(-self.origin_x / space) - 1, int((width - self.origin_x) / space) + 2):
            x = self.origin_x + index * space
            canvas.create_line(x, 0, x, height, fill="#f1f5f9")
            if 20 < x < width - 80:
                canvas.create_text(x, 12, text=f"{index * step:g}", fill="#94a3b8", font=(self.font, 8))
        for index in range(int(-self.origin_y / space) - 1, int((height - self.origin_y) / space) + 2):
            y = self.origin_y + index * space
            canvas.create_line(0, y, width, y, fill="#f1f5f9")
        canvas.create_text(width - 12, 12, text="x → mm", anchor="e", fill=MUTED, font=(self.font, 9, "bold"))
        for shape in scene(self.project, self.display_positions()):
            coords = [value * self.scale + (self.origin_x if index % 2 == 0 else self.origin_y) for index, value in enumerate(shape.coordinates)]
            tag = shape.tag
            selected = tag and self.selected and tag == ":".join(self.selected)
            stroke = BLUE if selected else shape.stroke
            if shape.kind == "line":
                canvas.create_line(*coords, fill=stroke or MUTED, width=shape.width + (1 if selected else 0), dash=(5, 4) if shape.dashed else (), arrow="both" if shape.arrow else "none", arrowshape=(6, 7, 3), tags=(tag,))
            elif shape.kind == "rectangle":
                canvas.create_rectangle(*coords, fill=shape.fill, outline=stroke, width=shape.width + (1 if selected else 0), tags=(tag,))
            elif shape.kind == "oval":
                x, y = (coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2
                radius = 5 if selected or tag == "point:" + str(self.tool_start) else 4
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=BLUE if tag == "point:" + str(self.tool_start) else "white", outline=stroke or INK, width=shape.width, tags=(tag,))
            elif shape.kind == "text":
                canvas.create_text(*coords, text=shape.text, fill=shape.fill, font=(self.font, shape.size, "bold" if shape.bold else "normal"), tags=(tag,))
        if not self.project.points:
            canvas.create_text(width / 2, height / 2 - 15, text="Sketch your 1D assembly", fill=INK, font=(self.font, 22, "bold"))
            canvas.create_text(width / 2, height / 2 + 23, text="Choose Body and click its left and right faces.\nThen add dimensions, a fit and the gap to measure.", fill=MUTED, font=(self.font, 11), justify="center")
        if self.mode.get() == "body" and isinstance(self.tool_start, tuple):
            x, y = self.tool_start
            datum = self.project.point(self.project.datum).x if self.project.points else 0
            sx, sy = self.screen(x - datum, y)
            canvas.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, outline=BLUE, width=2)
        if self.result and self.result.status not in ("incomplete", "invalid") and not self.display_positions():
            canvas.create_text(width / 2, 40, text="Pose unavailable — showing the requested sketch.", fill="#b45309", font=(self.font, 11, "bold"))

    def fit_view(self):
        shapes = scene(self.project, self.display_positions())
        left, top, right, bottom = scene_bounds(shapes)
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 100 or height < 100:
            return
        self.scale = min(30, (width - 50) / max(right - left, 1), (height - 70) / max(bottom - top, 1))
        self.origin_x = (width - (right - left) * self.scale) / 2 - left * self.scale
        self.origin_y = (height - (bottom - top) * self.scale) / 2 - top * self.scale + 12
        self.draw()

    def wheel(self, event, direction=None):
        direction = direction or (1 if event.delta > 0 else -1)
        old = self.scale
        self.scale = min(100, max(0.5, old * (1.15 if direction > 0 else 1 / 1.15)))
        ratio = self.scale / old
        self.origin_x = event.x - (event.x - self.origin_x) * ratio
        self.origin_y = event.y - (event.y - self.origin_y) * ratio
        self.draw()

    def start_pan(self, event):
        self.pan_start = event.x, event.y, self.origin_x, self.origin_y

    def pan(self, event):
        if self.pan_start:
            x, y, ox, oy = self.pan_start
            self.origin_x, self.origin_y = ox + event.x - x, oy + event.y - y
            self.draw()

    def change_tool(self):
        self.tool_start = None
        instructions = {"select": "Select a point or body; drag to request a nominal pose or adjust layout.", "point": "Click to add a face point. Connect it with dimensions before measuring a gap.", "body": "Click the left face, then the right face. Enter the width and tolerances.", "dimension": "Click the FROM point, then the TO point. Dimension = x(To) − x(From).", "gap": "Click the FROM face, then the TO face of the functional gap."}
        self.status.set(instructions[self.mode.get()])
        self.canvas.configure(cursor="arrow" if self.mode.get() == "select" else "crosshair")
        self.draw()

    def cancel_tool(self):
        self.mode.set("select")
        self.change_tool()

    def hit(self, event) -> tuple[str, str] | None:
        positions = self.display_positions() or {p.id: p.x - self.project.point(self.project.datum).x for p in self.project.points}
        for point in reversed(self.project.points):
            sx, sy = self.screen(positions[point.id], point.y)
            if math.hypot(event.x - sx, event.y - sy) <= 11:
                return "point", point.id
        items = self.canvas.find_overlapping(event.x - 3, event.y - 3, event.x + 3, event.y + 3)
        for item in reversed(items):
            for tag in self.canvas.gettags(item):
                if ":" in tag:
                    kind, item_id = tag.split(":", 1)
                    if kind in self.trees:
                        return kind, item_id
                if tag == "gap":
                    return "gap", "gap"
        return None

    def canvas_down(self, event):
        self.canvas.focus_set()
        mode = self.mode.get()
        hit = self.hit(event)
        if mode == "point":
            self.checkpoint()
            point_id = self.project.next_id("P")
            x, y = self.world(event.x, event.y)
            self.project.points.append(Point(point_id, point_id, x, y))
            self.project.datum = self.project.datum or point_id
            self.refresh()
        elif mode == "body":
            if self.tool_start is None:
                self.tool_start = self.world(event.x, event.y)
                self.status.set("Now click the right face of this body.")
                self.draw()
            else:
                start, end = self.tool_start, self.world(event.x, event.y)
                self.tool_start = None
                self.create_body(start, end)
        elif mode in ("dimension", "gap"):
            if not hit or hit[0] != "point":
                self.status.set("Click a face point (small circle), or use the editor's point dropdowns.")
                return
            if self.tool_start is None:
                self.tool_start = hit[1]
                self.status.set("Now select the TO face point.")
                self.draw()
            elif hit[1] != self.tool_start:
                start, end = self.tool_start, hit[1]
                self.tool_start = None
                if mode == "dimension":
                    self.add_dimension(start, end)
                else:
                    self.edit_gap(start, end)
        else:
            self.selected = hit
            self.select_tree_item(hit)
            if hit and hit[0] in ("point", "body") and self.view.get() == "Reference pose":
                if hit[0] == "point":
                    ids = [hit[1]]
                else:
                    body = next(body for body in self.project.bodies if body.id == hit[1])
                    ids = [body.left, body.right]
                coords = self.display_positions()
                origin = self.project.point(self.project.datum).x
                starts = {point_id: (coords[point_id] + origin if coords else self.project.point(point_id).x, self.project.point(point_id).y) for point_id in ids}
                self.drag = {"mouse": (event.x, event.y), "starts": starts, "changed": False}
            self.draw()

    def canvas_move(self, event):
        if not self.drag:
            return
        x, y = self.drag["mouse"]
        dx, dy = (event.x - x) / self.scale, (event.y - y) / self.scale
        if not self.drag["changed"] and math.hypot(event.x - x, event.y - y) > 3:
            self.checkpoint()
            self.drag["changed"] = True
            self.result = None
        if self.drag["changed"]:
            if self.snap.get():
                dx, dy = round(dx * 2) / 2, round(dy * 2) / 2
            for point_id, (sx, sy) in self.drag["starts"].items():
                point = self.project.point(point_id)
                point.x, point.y = sx + dx, sy + dy
            self.draw()

    def canvas_up(self, _):
        if self.drag and self.drag["changed"]:
            self.refresh()
        self.drag = None

    def canvas_double(self, event):
        if self.mode.get() == "select":
            self.selected = self.hit(event)
            self.edit_selected()

    def context_menu(self, event):
        self.selected = self.hit(event)
        self.select_tree_item(self.selected)
        if not self.selected:
            return
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Edit", command=self.edit_selected)
        if self.selected[0] == "point":
            menu.add_command(label="Use as datum A", command=self.set_datum)
        menu.add_command(label="Delete", command=self.delete_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def select_tree_item(self, item):
        if item and item[0] in self.trees and self.trees[item[0]].exists(item[1]):
            tree = self.trees[item[0]]
            self.notebook.select(tree.master)
            tree.selection_set(item[1])
            tree.see(item[1])

    def tree_selected(self, kind):
        ids = self.trees[kind].selection()
        if ids:
            self.selected = kind, ids[0]
            self.selection_label.configure(text=f"Selected {ids[0]}. Double-click or press Edit.")
            self.draw()

    def create_body(self, start, end):
        if abs(start[0] - end[0]) < 0.05:
            self.status.set("A body needs two horizontally separated faces.")
            self.draw()
            return
        trial = self.project.copy()
        left_id = trial.next_id("P")
        left_x, right_x = sorted((start[0], end[0]))
        trial.points.append(Point(left_id, "New left face", left_x, start[1]))
        right_id = trial.next_id("P")
        trial.points.append(Point(right_id, "New right face", right_x, start[1]))
        trial.datum = trial.datum or left_id
        values = BodyDialog(self, trial, left_id, right_id).show()
        if values:
            self.checkpoint()
            body, dimension = values
            trial.point(left_id).name, trial.point(right_id).name = body.name + " left", body.name + " right"
            trial.bodies.append(body)
            trial.dimensions.append(dimension)
            self.project = trial
            self.refresh()
        else:
            self.draw()

    def add_dimension(self, start=None, end=None):
        if len(self.project.points) < 2:
            self.status.set("Add at least two face points first.")
            return
        value = DimensionDialog(self, self.project, start, end).show()
        if value:
            self.checkpoint()
            self.project.dimensions.append(value)
            self.refresh()

    def add_fit(self):
        self.tool_start = None
        if len(self.project.points) < 4:
            self.status.set("A fit requires the slot's two points and the moving body's two points.")
            return
        value = FitDialog(self, self.project).show()
        if value:
            self.checkpoint()
            self.project.fits.append(value)
            self.refresh()

    def edit_gap(self, start=None, end=None):
        if len(self.project.points) < 2:
            self.status.set("Add at least two face points first.")
            return
        value = GapDialog(self, self.project, start, end).show()
        if value:
            self.checkpoint()
            self.project.gap = value
            self.refresh()

    def edit_selected(self, kind=None):
        selected = self.selected
        if kind:
            ids = self.trees[kind].selection()
            selected = (kind, ids[0]) if ids else None
        if not selected:
            return
        kind, item_id = selected
        if kind == "gap":
            self.edit_gap()
            return
        groups = {"point": self.project.points, "dimension": self.project.dimensions, "fit": self.project.fits, "body": self.project.bodies}
        item = next((item for item in groups[kind] if item.id == item_id), None)
        if item is None:
            return
        if kind == "point":
            value = PointDialog(self, self.project, item).show()
        elif kind == "dimension":
            value = DimensionDialog(self, self.project, existing=item).show()
        elif kind == "fit":
            value = FitDialog(self, self.project, item).show()
        else:
            value = BodyDialog(self, self.project, item.left, item.right, item).show()
        if value:
            self.checkpoint()
            if kind == "body":
                body, dimension = value
                self.project.bodies = [body if old.id == body.id else old for old in self.project.bodies]
                self.project.dimensions = [d for d in self.project.dimensions if d.id != dimension.id] + [dimension]
            else:
                groups[kind][:] = [value if old.id == item_id else old for old in groups[kind]]
            self.refresh()

    def delete_selected(self, kind=None):
        selected = self.selected
        if kind:
            ids = self.trees[kind].selection()
            selected = (kind, ids[0]) if ids else None
        if not selected:
            return
        kind, item_id = selected
        if kind == "point" and not messagebox.askyesno("Delete face point", "Delete this point and every dimension, body, fit or gap that references it? Undo can restore them.", parent=self):
            return
        self.checkpoint()
        if kind == "point":
            self.project.remove_point(item_id)
        elif kind == "gap":
            self.project.gap = None
        else:
            attr = {"dimension": "dimensions", "fit": "fits", "body": "bodies"}[kind]
            setattr(self.project, attr, [item for item in getattr(self.project, attr) if item.id != item_id])
        self.selected = None
        self.refresh()
        if kind == "body":
            self.status.set("Removed the visual body. Its face points and analytical dimensions remain in the model.")

    def set_datum(self):
        if self.selected and self.selected[0] == "point":
            self.checkpoint()
            self.project.datum = self.selected[1]
            self.refresh()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.project.copy())
            self.project = self.undo_stack.pop()
            self.tool_start, self.selected = None, None
            self.refresh()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.project.copy())
            self.project = self.redo_stack.pop()
            self.tool_start, self.selected = None, None
            self.refresh()

    def may_discard(self):
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel("Unsaved stackup", "Save this project before continuing?", parent=self)
        if choice is None:
            return False
        return self.save() if choice else True

    def replace_project(self, project, path=None):
        self.project, self.file_path = project, Path(path) if path else None
        self.saved_state = json.dumps(project.to_dict(), sort_keys=True)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected, self.tool_start = None, None
        self.view.set("Reference pose")
        self.refresh()
        self.after(30, self.fit_view)

    def new_project(self):
        if self.may_discard():
            self.replace_project(Project())
            self.mode.set("body")
            self.change_tool()

    def load_example(self):
        if self.may_discard():
            self.replace_project(EXAMPLES[self.example_var.get()]())

    def open_project(self):
        if not self.may_discard():
            return
        path = filedialog.askopenfilename(parent=self, title="Open stackup project", filetypes=[("Odiim project", "*.stackup.json"), ("JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            self.replace_project(load_project(path), path)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Could not open project", str(exc), parent=self)

    def save(self, save_as=False):
        path = self.file_path
        if save_as or path is None:
            selected = filedialog.asksaveasfilename(parent=self, title="Save stackup", defaultextension=".stackup.json", initialfile=self.project.name.replace("/", "-") + ".stackup.json", filetypes=[("Odiim project", "*.stackup.json")])
            if not selected:
                return False
            path = Path(selected)
        try:
            save_project(self.project, path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not save project", str(exc), parent=self)
            return False
        self.file_path = path
        self.saved_state = json.dumps(self.project.to_dict(), sort_keys=True)
        self.refresh(recalculate=False)
        self.status.set("Saved " + str(path))
        return True

    def export(self):
        if self.result is None:
            self.status.set("Wait for the current analysis to finish before exporting.")
            return
        path = filedialog.asksaveasfilename(parent=self, title="Export analysis", defaultextension=".html", initialfile="stackup-report.html", filetypes=[("Printable HTML report", "*.html"), ("Spreadsheet CSV", "*.csv"), ("Current sketch SVG", "*.svg")])
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".csv":
                export_csv(self.project, self.result, path)
            elif suffix == ".svg":
                _atomic_text(path, svg_sketch(self.project, self.display_positions(), label=self.view.get()))
            elif suffix in (".html", ".htm"):
                export_html(self.project, self.result, path)
            else:
                raise ValueError("Choose .html, .csv or .svg for the report.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not export", str(exc), parent=self)
            return
        self.status.set("Exported " + path)

    def help_window(self):
        window = tk.Toplevel(self)
        window.title("Using Odiim 1D Stackup")
        window.geometry("730x700")
        window.transient(self)
        text = tk.Text(window, wrap="word", padx=24, pady=24, font=(self.font, 11), background="white", foreground=INK)
        scroll = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("end", """BUILD A STACKUP

1. Choose Body, click two horizontal faces, then enter its width and signed tolerance deviations. Choose Housing / slot for a cavity outline. Or place individual face points with Point.
2. Add a dimension by clicking From and To points, or use Add in the Dims tab. A contact relation ties two faces at zero distance. A placement relation defines a bounded assembly position or known mounting float.
3. For clearance-dependent movement, choose Fit / float. Select the slot limits and both moving faces. Use free float, left contact, right contact or centered.
4. Choose Gap and click its From and To faces. Set minimum / maximum requirements if needed.
5. View the reference, minimum-gap and maximum-gap assemblies. Double-click a dimension or fit to edit it. Save the project and export an HTML report, CSV or SVG.

HOW MOVEMENT IS INCLUDED

For a free fit: slot left ≤ body left, and body right ≤ slot right. Both faces use the same dimensional relationships. The available movement therefore changes when the slot and body widths change.

With a 40 ±0.2 mm slot and a 34 ±0.1 mm block, total size clearance is 5.7 to 6.3 mm. A free block may touch either wall: its right-hand gap is 0 to 6.3 mm. A physically centered block has equal gaps: 2.85 to 3.15 mm. Centered is an assembly condition you must explicitly select.

The reference pose uses nominal manufacturing sizes and stays as close as possible to the requested sketch pose. It is one feasible pose, not a statistical average. Dragging a free body requests a different reference pose; it does not change dimensional specifications or the worst-case limits.

The shift shown for each fit is relative to that reference pose at nominal sizes. The full gap range also includes size tolerances and their effect on available movement.

WHEN THE RESULT NEEDS ATTENTION

Unbounded: the gap's parts are not sufficiently located. Add a contact, placement range, connecting dimension or fit.
Infeasible: no sizes and positions satisfy the stated conditions. The app lists conflicting relations.
Size fit risk: some individually permitted sizes interfere, even when other combinations fit. Gap limits describe feasible assemblies; a passing gap does not certify production fit for every combination. Multiple coupled fits require a separate all-combinations assembly study.

CONTROLS AND SCOPE

Wheel to zoom; middle-drag to pan; Fit view to recenter. Right-click a point to edit it or set datum A. Ctrl+S saves; Ctrl+O opens; Ctrl+Z / Ctrl+Y undo and redo. Delete a body to remove its visual shape; its analytical face points and dimensions remain.

All dimensions are signed along x: To − From. Units are mm. The y axis is layout only. This app covers linear size variation, rigid translation and specified contacts. Rotation, form, angular tolerances, elasticity and full GD&T are outside the model. No probability distribution is assumed.
""")
        text.configure(state="disabled")

    def close_app(self):
        if self.may_discard():
            self.closing = True
            if self.analysis_timer is not None:
                self.after_cancel(self.analysis_timer)
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.destroy()
