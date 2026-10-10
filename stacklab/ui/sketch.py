"""Interactive axial sketch. Scene coordinates are presentation only."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

from stacklab.domain import FaceRef, Project
from stacklab.sketch_dimensions import GeometryPick, measure_sketch_dimension


SCALE = 6.0  # screen scene units per model millimetre; never used by a solver
TRACK = 116.0


class SketchView(QGraphicsView):
    """Selectable, zoomable presentation of 1D part geometry."""

    face_clicked = Signal(object)
    entity_clicked = Signal(str, str)
    blank_clicked = Signal()
    part_dragged = Signal(str, float)
    edit_requested = Signal(str, str)
    context_requested = Signal(str, str, object)
    outline_finished = Signal(object, object, bool)  # instance ID, scene vertices, closed
    outline_cancelled = Signal()
    point_clicked = Signal(object)
    geometry_clicked = Signal(object)
    tool_cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#f7f9fc"))
        self.setMinimumSize(400, 320)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._project: Project | None = None
        self._presentation: dict = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._selected: tuple[str, str] | None = None
        self._selected_faces: set[tuple[str, str]] = set()
        self._chain_dimensions: set[str] = set()
        self._mouse_start: QPoint | None = None
        self._drag_instance: str | None = None
        self._pan_start: QPoint | None = None
        self._part_rows: dict[str, float] = {}
        self._face_positions: dict[tuple[str, str], QPointF] = {}
        self._grid_visible = True
        self._manually_navigated = False
        self._drawing_active = False
        self._drawing_instance: str | None = None
        self._drawing_closed = True
        self._draft_points: list[QPointF] = []
        self._draft_hover: QPointF | None = None
        self._draft_item = None
        self._picking_point = False
        self._dimension_mode = False
        self._geometry_highlights: list[GeometryPick] = []

    @property
    def grid_visible(self) -> bool:
        return self._grid_visible

    @grid_visible.setter
    def grid_visible(self, value: bool) -> None:
        self._grid_visible = value
        self.viewport().update()

    def set_project(self, project: Project, positions: dict[tuple[str, str], float] | None = None,
                    presentation: dict | None = None) -> None:
        self._project = project
        self._positions = positions or {}
        self._presentation = presentation or {}
        self.rebuild()

    def set_highlights(
        self,
        selected: tuple[str, str] | None = None,
        faces: set[tuple[str, str]] | None = None,
        dimensions: set[str] | None = None,
    ) -> None:
        self._selected = selected
        self._selected_faces = faces or set()
        self._chain_dimensions = dimensions or set()
        self.rebuild()

    def set_dimension_mode(self, active: bool) -> None:
        self._dimension_mode = active
        self._geometry_highlights.clear()
        self.rebuild()

    def set_dimension_picks(self, picks: list[GeometryPick]) -> None:
        self._geometry_highlights = list(picks)
        self.rebuild()

    def _x(self, instance, face) -> float:
        key = (instance.id, face.id)
        value = self._positions.get(key)
        if value is None:
            value = instance.translation + face.local_x
        return value * SCALE

    def _translation(self, instance, definition) -> float:
        for face in definition.faces:
            key = (instance.id, face.id)
            if key in self._positions:
                return self._positions[key] - face.local_x
        return instance.translation

    def _vertex_x(self, instance, definition, vertex) -> float:
        if vertex.face_id:
            face = next((face for face in definition.faces if face.id == vertex.face_id), None)
            if face is not None:
                return self._x(instance, face)
        return (self._translation(instance, definition) + vertex.x) * SCALE

    def rebuild(self) -> None:
        scene = self.scene()
        scene.clear()
        self._part_rows.clear()
        self._face_positions.clear()
        self._draft_item = None
        project = self._project
        if project is None or not project.instances:
            text = scene.addText("Create a part to begin sketching your assembly")
            text.setDefaultTextColor(QColor("#65758b"))
            text.setFont(QFont("Segoe UI", 13))
            text.setPos(20, 24)
            scene.setSceneRect(-30, -30, 590, 340)
            self._draw_draft()
            return

        defs = {definition.id: definition for definition in project.definitions}
        profile_mode = any(definition.outlines for definition in project.definitions)
        visible = [instance for instance in project.instances if instance.visible]
        all_x: list[float] = []
        all_y: list[float] = []
        for row, instance in enumerate(visible):
            definition = defs.get(instance.definition_id)
            if definition is None:
                continue
            faces = sorted(definition.faces, key=lambda face: face.local_x)
            if not faces:
                continue
            y = float(self._presentation.get("part_y", {}).get(instance.id, 70.0 + row * TRACK))
            self._part_rows[instance.id] = y
            xs = [self._x(instance, face) for face in faces]
            all_x.extend(xs)
            all_y.append(y)
            left, right = min(xs), max(xs)
            if abs(right - left) < 8:
                right = left + 8
            is_centerline = len(faces) == 1 and faces[0].lane == "centerline"
            selected = self._selected == ("instance", instance.id)
            if is_centerline:
                top, bottom = ((y - 200, y + 330) if self._presentation.get("long_centerline")
                               else (y - 48, y + 48))
                all_y.extend((top, bottom))
                body = scene.addLine(left, top, left, bottom,
                                     QPen(QColor("#a764b0"), 2, Qt.DashLine))
                body.setData(0, "instance")
                body.setData(1, instance.id)
                body.setZValue(1)
            elif definition.outlines:
                for outline in definition.outlines:
                    outline_key = f"{instance.id}:{definition.id}:{outline.id}"
                    outline_selected = self._selected in {("outline", outline_key),
                                                          ("outline", f"{definition.id}:{outline.id}")}
                    shape = QPainterPath()
                    for index, vertex in enumerate(outline.vertices):
                        x = self._vertex_x(instance, definition, vertex)
                        point_y = y + vertex.y * SCALE
                        all_x.append(x)
                        all_y.append(point_y)
                        if index == 0:
                            shape.moveTo(x, point_y)
                        else:
                            shape.lineTo(x, point_y)
                    if outline.closed:
                        shape.closeSubpath()
                    color = QColor(outline.color)
                    fill = QColor(color)
                    fill.setAlpha(100 if selected or outline_selected else 65)
                    body = scene.addPath(shape, QPen(color, 3 if selected or outline_selected else 2),
                                         QBrush(fill) if outline.closed else QBrush(Qt.NoBrush))
                    body.setData(0, "outline")
                    body.setData(1, outline_key)
                    body.setZValue(1)
                    if self._dimension_mode:
                        rendered = [QPointF(self._vertex_x(instance, definition, vertex),
                                            y + vertex.y * SCALE) for vertex in outline.vertices]
                        for index, point in enumerate(rendered):
                            pick = GeometryPick("vertex", instance.id, definition.id, outline.id, index)
                            selected_pick = pick in self._geometry_highlights
                            marker = scene.addEllipse(point.x() - 5, point.y() - 5, 10, 10,
                                QPen(QColor("#e47728" if selected_pick else "#247ba6"), 2),
                                QBrush(QColor("#fff2d9" if selected_pick else "#ffffff")))
                            marker.setData(0, "sketch_vertex")
                            marker.setData(1, f"{outline_key}:{index}")
                            marker.setToolTip(f"Vertex {index + 1}: click for a dimension")
                            marker.setZValue(7)
                        count = len(rendered) if outline.closed else len(rendered) - 1
                        for index in range(count):
                            pick = GeometryPick("segment", instance.id, definition.id, outline.id, index)
                            start, end = rendered[index], rendered[(index + 1) % len(rendered)]
                            if pick in self._geometry_highlights:
                                scene.addLine(QLineF(start, end), QPen(QColor("#e47728"), 4)).setZValue(5)
                            hit = scene.addLine(QLineF(start, end), QPen(QColor(25, 90, 160, 1), 12))
                            hit.setData(0, "sketch_segment")
                            hit.setData(1, f"{outline_key}:{index}")
                            hit.setToolTip(f"Line {index + 1}: click for a dimension")
                            hit.setZValue(6)
            else:
                # Face coordinates are axial model data. The stepped band is a
                # presentation cue for successive profile sections.
                shape = QPainterPath()
                shape.moveTo(left, y + 28)
                shape.lineTo(left, y - 28)
                for index, x in enumerate(xs[1:], start=1):
                    height = 28 + (index % 3) * 6
                    shape.lineTo(x, y - height)
                shape.lineTo(right, y + 28)
                shape.closeSubpath()
                body = scene.addPath(
                    shape,
                    QPen(QColor("#2376a8" if selected else "#57708d"), 2),
                    QBrush(QColor("#c6e7f5" if selected else "#dfe8f3")),
                )
                body.setData(0, "instance")
                body.setData(1, instance.id)
                body.setZValue(1)
            label = scene.addText(instance.name)
            label.setDefaultTextColor(QColor("#234465"))
            label.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
            label.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            outline_tops = [y + point.y * SCALE for outline in definition.outlines
                            for point in outline.vertices]
            label.setPos(left + 6, (min(outline_tops) if outline_tops else y - 28) - 43)
            label.setData(0, "instance")
            label.setData(1, instance.id)

            label_right = [-float("inf"), -float("inf"), -float("inf")]
            for face in faces:
                x = self._x(instance, face)
                marker_y = y + face.local_y * SCALE
                face_key = (instance.id, face.id)
                self._face_positions[face_key] = QPointF(x, marker_y)
                all_y.append(marker_y)
                highlighted = face_key in self._selected_faces or self._selected == ("face", f"{instance.id}:{face.id}")
                pen_color = QColor("#f28c28" if highlighted else "#1d668c")
                tick = 10 if definition.outlines else 37
                line = scene.addLine(x, marker_y - tick, x, marker_y + tick,
                                     QPen(pen_color, 3 if highlighted else 1.5))
                line.setData(0, "face")
                line.setData(1, f"{instance.id}:{face.id}")
                line.setZValue(3)
                line.setToolTip(f"{instance.name} / {face.name}: x={self._x(instance, face) / SCALE:g} {project.unit}")
                marker = scene.addEllipse(x - 5, marker_y - 5, 10, 10,
                                          QPen(pen_color, 2), QBrush(QColor("#ffffff")))
                marker.setData(0, "face")
                marker.setData(1, f"{instance.id}:{face.id}")
                marker.setZValue(5)
                marker.setToolTip(line.toolTip())
                if (not definition.outlines and not is_centerline) or highlighted:
                    name = scene.addText(face.name)
                    name.setDefaultTextColor(QColor("#426079"))
                    name.setFont(QFont("Segoe UI", 10))
                    name.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                    label_width = name.boundingRect().width()
                    level = next((i for i, right_edge in enumerate(label_right) if x > right_edge + 5),
                                 min(range(3), key=lambda i: label_right[i]))
                    name.setPos(x + 3, marker_y + tick + 20 * level)
                    label_right[level] = x + 3 + label_width
                    name.setData(0, "face")
                    name.setData(1, f"{instance.id}:{face.id}")

        for sketch_dimension in project.sketch_dimensions:
            definition = defs.get(sketch_dimension.definition_id)
            if definition is None:
                continue
            try:
                measured = measure_sketch_dimension(definition, sketch_dimension)
            except (ValueError, KeyError, IndexError):
                continue
            for instance in visible:
                if instance.definition_id != definition.id:
                    continue
                row_y = self._part_rows.get(instance.id)
                if row_y is None:
                    continue
                outlines = {outline.id: outline for outline in definition.outlines}
                def point(outline_id: str, index: int) -> QPointF:
                    vertex = outlines[outline_id].vertices[index]
                    return QPointF(self._vertex_x(instance, definition, vertex), row_y + vertex.y * SCALE)
                first_outline = outlines[sketch_dimension.first_outline_id]
                a = point(first_outline.id, sketch_dimension.first_index)
                if sketch_dimension.kind == "line_length":
                    b = point(first_outline.id, (sketch_dimension.first_index + 1) % len(first_outline.vertices))
                elif sketch_dimension.kind == "point_distance":
                    b = point(sketch_dimension.second_outline_id, sketch_dimension.second_index)
                else:
                    second_outline = outlines[sketch_dimension.second_outline_id]
                    i = sketch_dimension.first_index
                    j = sketch_dimension.second_index
                    a2 = point(first_outline.id, (i + 1) % len(first_outline.vertices))
                    b = point(second_outline.id, j)
                    b2 = point(second_outline.id, (j + 1) % len(second_outline.vertices))
                    a = (a + a2) / 2
                    b = (b + b2) / 2
                mid = (a + b) / 2
                label = scene.addText(f"{sketch_dimension.name}: {measured:.3f} {project.unit}")
                label.setDefaultTextColor(QColor("#5e53a6"))
                label.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
                label.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                label.setPos(mid.x() + 8, mid.y() - 27)
                label.setData(0, "sketch_dimension")
                label.setData(1, sketch_dimension.id)
                line = scene.addLine(QLineF(a, b), QPen(QColor("#7268ae"), 1.5, Qt.DashLine))
                line.setData(0, "sketch_dimension")
                line.setData(1, sketch_dimension.id)
                line.setZValue(2)
                all_y.append(mid.y() - 30)

        dim_row = 0
        for dimension in project.dimensions:
            if (profile_mode and not dimension.show_on_sketch and
                dimension.id not in self._chain_dimensions and self._selected != ("dimension", dimension.id)):
                continue
            a = self._face_positions.get((dimension.first.instance_id, dimension.first.face_id))
            b = self._face_positions.get((dimension.second.instance_id, dimension.second.face_id))
            if a is None or b is None:
                continue
            y = min(a.y(), b.y()) - 75 - (dim_row % 3) * 27
            all_y.append(y - 30)
            dim_row += 1
            color = QColor("#ce6b23" if dimension.id in self._chain_dimensions else "#677b8e")
            pen = QPen(color, 2 if dimension.id in self._chain_dimensions else 1)
            scene.addLine(a.x(), a.y() - 40, a.x(), y, pen)
            scene.addLine(b.x(), b.y() - 40, b.x(), y, pen)
            line = scene.addLine(a.x(), y, b.x(), y, pen)
            line.setData(0, "dimension")
            line.setData(1, dimension.id)
            text = scene.addText(self._dimension_text(dimension, project.unit))
            text.setDefaultTextColor(color)
            text.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
            text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            text.setPos((a.x() + b.x()) / 2 - text.boundingRect().width() / 2, y - 29)
            text.setData(0, "dimension")
            text.setData(1, dimension.id)

        for contact in project.contacts:
            a = self._face_positions.get((contact.first.instance_id, contact.first.face_id))
            b = self._face_positions.get((contact.second.instance_id, contact.second.face_id))
            if a is None or b is None:
                continue
            pen = QPen(QColor("#c05b92"), 1.5, Qt.DashLine)
            link = scene.addLine(QLineF(a, b), pen)
            link.setData(0, "contact")
            link.setData(1, contact.id)
            link.setZValue(0)

        for index, requirement in enumerate(project.requirements):
            a = self._face_positions.get((requirement.first.instance_id, requirement.first.face_id))
            b = self._face_positions.get((requirement.second.instance_id, requirement.second.face_id))
            if a is None or b is None:
                continue
            first_def = defs.get(next((i.definition_id for i in visible
                                       if i.id == requirement.first.instance_id), ""))
            second_def = defs.get(next((i.definition_id for i in visible
                                        if i.id == requirement.second.instance_id), ""))
            first_datum = first_def is not None and len(first_def.faces) == 1 and first_def.faces[0].lane == "centerline"
            second_datum = second_def is not None and len(second_def.faces) == 1 and second_def.faces[0].lane == "centerline"
            ay = b.y() if first_datum else a.y()
            by = a.y() if second_datum else b.y()
            if (first_datum or second_datum) and max(ay, by) < 120:
                arrow_y = min(ay, by) - 45
            elif abs(ay - by) > 85 and not (first_datum or second_datum):
                arrow_y = min(ay, by) - 34
            else:
                arrow_y = max(ay, by) + 35
            all_y.append(arrow_y)
            color = QColor("#b5486d")
            pen = QPen(color, 1.6)
            for point, feature_y in ((a, ay), (b, by)):
                scene.addLine(point.x(), feature_y, point.x(), arrow_y + 5, QPen(color, 1, Qt.DashLine))
            line = scene.addLine(a.x(), arrow_y, b.x(), arrow_y, pen)
            line.setData(0, "requirement")
            line.setData(1, requirement.id)
            low, high = sorted((a.x(), b.x()))
            if high - low > 18:
                for x, tip in ((low, 1), (high, -1)):
                    head = QPolygonF([QPointF(x, arrow_y), QPointF(x + tip * 8, arrow_y - 4),
                                      QPointF(x + tip * 8, arrow_y + 4)])
                    scene.addPolygon(head, pen, QBrush(color))
            measured = requirement.direction * (b.x() - a.x()) / SCALE
            text = scene.addText(f"{requirement.name}: {measured:.3f} {project.unit}")
            text.setDefaultTextColor(color)
            text.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
            text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            text.setPos((max(a.x(), b.x()) + 12 if high - low < 32 else low + 5), arrow_y - 25)
            text.setData(0, "requirement")
            text.setData(1, requirement.id)

        xmin = min(all_x) if all_x else 0
        xmax = max(all_x) if all_x else 500
        ymin = min(all_y) if all_y else 0
        ymax = max(all_y) if all_y else 300
        scene.setSceneRect(xmin - 90, ymin - 85, max(500, xmax - xmin + 180),
                           max(320, ymax - ymin + 180))
        self._draw_draft()

    @staticmethod
    def _dimension_text(dimension, unit: str) -> str:
        lo, hi = dimension.tolerance.lower, dimension.tolerance.upper
        if dimension.display_style == "limits":
            return f"{dimension.name}: {dimension.nominal+lo:.3f} / {dimension.nominal+hi:.3f} {unit}"
        if dimension.display_style == "bilateral" and abs(lo + hi) < 1e-9:
            return f"{dimension.name}: {dimension.nominal:.3f} ±{hi:.3f} {unit}"
        return f"{dimension.name}: {dimension.nominal:.3f} {hi:+.3f}/{lo:+.3f} {unit}"

    def fit_assembly(self) -> None:
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
            self._manually_navigated = False

    def begin_outline(self, instance_id: str | None, *, closed: bool = True) -> None:
        self._picking_point = False
        self._drawing_active = True
        self._drawing_instance = instance_id
        self._drawing_closed = closed
        self._draft_points.clear()
        self._draft_hover = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self._draw_draft()

    def add_outline_point(self, scene_point: QPointF) -> None:
        if not self._drawing_active:
            return
        point = QPointF(scene_point)
        if self._grid_visible:
            point = QPointF(round(point.x() / SCALE) * SCALE,
                            round(point.y() / SCALE) * SCALE)
        if self._draft_points and QLineF(self._draft_points[-1], point).length() < 3:
            return
        self._draft_points.append(point)
        self._draft_hover = None
        self._draw_draft()

    def finish_outline(self) -> bool:
        if not self._drawing_active:
            return False
        if len(self._draft_points) < (3 if self._drawing_closed else 2):
            return False
        instance_id = self._drawing_instance
        vertices = [QPointF(point) for point in self._draft_points]
        closed = self._drawing_closed
        self._drawing_active = False
        self._draft_points.clear()
        self._draft_hover = None
        self.unsetCursor()
        self._draw_draft()
        self.outline_finished.emit(instance_id, vertices, closed)
        return True

    def cancel_outline(self) -> None:
        if not self._drawing_active:
            return
        self._drawing_active = False
        self._draft_points.clear()
        self._draft_hover = None
        self.unsetCursor()
        self._draw_draft()
        self.outline_cancelled.emit()

    def begin_pick_point(self) -> None:
        self.cancel_outline()
        self._picking_point = True
        self.setCursor(Qt.CrossCursor)
        self.setFocus()

    def cancel_pick_point(self) -> None:
        self._picking_point = False
        self.unsetCursor()

    def _draw_draft(self) -> None:
        scene = self.scene()
        if self._draft_item is not None and self._draft_item.scene() is scene:
            scene.removeItem(self._draft_item)
        self._draft_item = None
        if not self._drawing_active or not self._draft_points:
            return
        path = QPainterPath(self._draft_points[0])
        for point in self._draft_points[1:]:
            path.lineTo(point)
        if self._draft_hover is not None:
            path.lineTo(self._draft_hover)
        for point in self._draft_points:
            path.addEllipse(point, 3, 3)
        self._draft_item = scene.addPath(path, QPen(QColor("#cf703b"), 2, Qt.DashLine),
                                         QBrush(Qt.NoBrush))
        self._draft_item.setZValue(20)

    def face_at(self, viewport_position: QPoint) -> FaceRef | None:
        for item in self.items(viewport_position):
            if item.data(0) == "face":
                instance_id, face_id = item.data(1).split(":", 1)
                return FaceRef(instance_id, face_id)
        return None

    def _entity_at(self, position: QPoint) -> tuple[str, str] | None:
        for item in self.items(position):
            kind = item.data(0)
            if kind:
                return str(kind), str(item.data(1))
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if self._picking_point and event.button() == Qt.LeftButton:
            point = self.mapToScene(event.pos())
            self.cancel_pick_point()
            self.point_clicked.emit(point)
            event.accept()
            return
        if self._drawing_active:
            if event.button() == Qt.LeftButton:
                self.add_outline_point(self.mapToScene(event.pos()))
            elif event.button() == Qt.RightButton:
                self.finish_outline()
            event.accept()
            return
        if self._dimension_mode and event.button() == Qt.LeftButton:
            for item in self.items(event.position().toPoint()):
                if item.data(0) in {"sketch_vertex", "sketch_segment"}:
                    instance_id, definition_id, outline_id, index = str(item.data(1)).split(":")
                    kind = "vertex" if item.data(0) == "sketch_vertex" else "segment"
                    self.geometry_clicked.emit(GeometryPick(kind, instance_id, definition_id,
                                                            outline_id, int(index)))
                    event.accept()
                    return
        if event.button() == Qt.LeftButton:
            self._mouse_start = event.pos()
            picked = self._entity_at(event.pos())
            self._drag_instance = picked[1] if picked and picked[0] == "instance" else None
            if picked:
                self.entity_clicked.emit(*picked)
                if picked[0] == "face":
                    instance_id, face_id = picked[1].split(":", 1)
                    self.face_clicked.emit(FaceRef(instance_id, face_id))
            else:
                self.blank_clicked.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_start is not None:
            self._manually_navigated = True
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._drawing_active:
            self._draft_hover = self.mapToScene(event.pos())
            self._draw_draft()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._pan_start = None
            self.unsetCursor()
            if self._drawing_active or self._picking_point:
                self.setCursor(Qt.CrossCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._mouse_start and self._drag_instance and self._project:
            distance = event.pos() - self._mouse_start
            if abs(distance.x()) > 5:
                instance = next((part for part in self._project.instances if part.id == self._drag_instance), None)
                if instance is not None:
                    delta_model = (self.mapToScene(event.pos()).x() - self.mapToScene(self._mouse_start).x()) / SCALE
                    position = instance.translation + delta_model
                    if self._grid_visible:
                        position = round(position)
                    self.part_dragged.emit(instance.id, position)
        self._drag_instance = None
        self._mouse_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._drawing_active:
            if event.button() == Qt.LeftButton:
                self.add_outline_point(self.mapToScene(event.pos()))
                self.finish_outline()
            event.accept()
            return
        picked = self._entity_at(event.pos())
        if picked:
            self.edit_requested.emit(*picked)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._drawing_active:
            event.accept()
            return
        picked = self._entity_at(event.pos())
        if picked:
            self.context_requested.emit(picked[0], picked[1], event.globalPos())
        else:
            super().contextMenuEvent(event)

    def wheelEvent(self, event) -> None:
        self._manually_navigated = True
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if self._dimension_mode and event.key() == Qt.Key_Escape:
            self.tool_cancelled.emit()
            event.accept()
            return
        if self._picking_point and event.key() == Qt.Key_Escape:
            self.cancel_pick_point()
            self.outline_cancelled.emit()
            event.accept()
            return
        if self._drawing_active:
            if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
                self.finish_outline()
            elif event.key() == Qt.Key_Escape:
                self.cancel_outline()
            elif event.key() == Qt.Key_Backspace and self._draft_points:
                self._draft_points.pop()
                self._draw_draft()
            else:
                super().keyPressEvent(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def drawBackground(self, painter: QPainter, rect) -> None:
        super().drawBackground(painter, rect)
        if not self._grid_visible:
            return
        spacing = SCALE * 10
        painter.setPen(QPen(QColor("#e5ebf2"), 0))
        left = int(rect.left() // spacing) - 1
        right = int(rect.right() // spacing) + 1
        top = int(rect.top() // spacing) - 1
        bottom = int(rect.bottom() // spacing) + 1
        for index in range(left, right + 1):
            x = index * spacing
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for index in range(top, bottom + 1):
            y = index * spacing
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
