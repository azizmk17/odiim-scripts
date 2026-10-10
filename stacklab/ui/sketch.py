"""Interactive axial sketch. Scene coordinates are presentation only."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from stacklab.domain import FaceRef, Project


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#f7f9fc"))
        self.setMinimumSize(400, 320)
        self._project: Project | None = None
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

    @property
    def grid_visible(self) -> bool:
        return self._grid_visible

    @grid_visible.setter
    def grid_visible(self, value: bool) -> None:
        self._grid_visible = value
        self.viewport().update()

    def set_project(self, project: Project, positions: dict[tuple[str, str], float] | None = None) -> None:
        self._project = project
        self._positions = positions or {}
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

    def _x(self, instance, face) -> float:
        key = (instance.id, face.id)
        value = self._positions.get(key)
        if value is None:
            value = instance.translation + face.local_x
        return value * SCALE

    def rebuild(self) -> None:
        scene = self.scene()
        scene.clear()
        self._part_rows.clear()
        self._face_positions.clear()
        project = self._project
        if project is None or not project.instances:
            text = scene.addText("Create a part to begin sketching your assembly")
            text.setDefaultTextColor(QColor("#65758b"))
            text.setFont(QFont("Segoe UI", 13))
            text.setPos(20, 24)
            scene.setSceneRect(-30, -30, 590, 340)
            return

        defs = {definition.id: definition for definition in project.definitions}
        visible = [instance for instance in project.instances if instance.visible]
        all_x: list[float] = []
        for row, instance in enumerate(visible):
            definition = defs.get(instance.definition_id)
            if definition is None:
                continue
            faces = sorted(definition.faces, key=lambda face: face.local_x)
            if not faces:
                continue
            y = 70.0 + row * TRACK
            self._part_rows[instance.id] = y
            xs = [self._x(instance, face) for face in faces]
            all_x.extend(xs)
            left, right = min(xs), max(xs)
            if abs(right - left) < 8:
                right = left + 8
            is_centerline = len(faces) == 1 and faces[0].lane == "centerline"
            selected = self._selected == ("instance", instance.id)
            if is_centerline:
                body = scene.addLine(left, y - 48, left, y + 48,
                                     QPen(QColor("#a764b0"), 2, Qt.DashLine))
                body.setData(0, "instance")
                body.setData(1, instance.id)
                body.setZValue(1)
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
            label.setPos(left, y - 62)
            label.setData(0, "instance")
            label.setData(1, instance.id)

            label_right = [-float("inf"), -float("inf"), -float("inf")]
            for face in faces:
                x = self._x(instance, face)
                face_key = (instance.id, face.id)
                self._face_positions[face_key] = QPointF(x, y)
                highlighted = face_key in self._selected_faces or self._selected == ("face", f"{instance.id}:{face.id}")
                pen_color = QColor("#f28c28" if highlighted else "#1d668c")
                line = scene.addLine(x, y - 37, x, y + 38, QPen(pen_color, 3 if highlighted else 1.5))
                line.setData(0, "face")
                line.setData(1, f"{instance.id}:{face.id}")
                line.setZValue(3)
                marker = scene.addEllipse(x - 5, y - 5, 10, 10, QPen(pen_color, 2), QBrush(QColor("#ffffff")))
                marker.setData(0, "face")
                marker.setData(1, f"{instance.id}:{face.id}")
                marker.setZValue(5)
                name = scene.addText(face.name)
                name.setDefaultTextColor(QColor("#426079"))
                name.setFont(QFont("Segoe UI", 10))
                label_width = name.boundingRect().width()
                level = next((i for i, right_edge in enumerate(label_right) if x > right_edge + 5),
                             min(range(3), key=lambda i: label_right[i]))
                name.setPos(x + 3, y + 38 + 20 * level)
                label_right[level] = x + 3 + label_width
                name.setData(0, "face")
                name.setData(1, f"{instance.id}:{face.id}")

        dim_row = 0
        for dimension in project.dimensions:
            a = self._face_positions.get((dimension.first.instance_id, dimension.first.face_id))
            b = self._face_positions.get((dimension.second.instance_id, dimension.second.face_id))
            if a is None or b is None:
                continue
            y = min(a.y(), b.y()) - 75 - (dim_row % 3) * 27
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

        xmin = min(all_x) if all_x else 0
        xmax = max(all_x) if all_x else 500
        scene.setSceneRect(xmin - 120, -105, max(500, xmax - xmin + 240), len(visible) * TRACK + 160)

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
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._pan_start = None
            self.unsetCursor()
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
        picked = self._entity_at(event.pos())
        if picked:
            self.edit_requested.emit(*picked)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
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
