"""GUI workflow smoke test. Run on a desktop, or under Xvfb on Linux."""
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from stackup.examples import floating_block
from stackup.model import Project


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY") or os.sys.platform == "darwin", "A desktop display is required")
class GuiTests(unittest.TestCase):
    def test_custom_profile_dimension_placement_circle_and_connected_line(self):
        from stackup.gui import DimensionDialog, GapDialog, SketchDialog, StackupApp
        app = StackupApp(Project())

        def wait_for_analysis():
            deadline = time.monotonic() + 15
            while app.result is None and time.monotonic() < deadline:
                app.update()
                time.sleep(.02)
            self.assertIsNotNone(app.result)

        def click_world(x, y):
            origin = app.project.point(app.project.datum).x if app.project.points else 0
            sx, sy = app.screen(x - origin, y)
            app.canvas_down(SimpleNamespace(x=sx, y=sy))

        def apply_dialog(dialog):
            result = dialog.value()
            dialog.destroy()
            return result

        try:
            wait_for_analysis()
            app.scale, app.origin_x, app.origin_y = 12, 80, 80
            app.mode.set("profile")
            app.change_tool()
            vertices = [(0, 10), (30, 10), (30, 20), (20, 20), (20, 30), (0, 30)]
            with patch.object(SketchDialog, "show", apply_dialog):
                for x, y in vertices:
                    click_world(x, y)
                click_world(-3, 25)
                app.remove_draft_vertex()
                click_world(0, 10)  # close by selecting the first draft vertex
            self.assertEqual(len(app.project.sketches), 1)
            self.assertEqual(len(app.project.points), 6)
            self.assertFalse(app.project.dimensions)  # no implicit size allowances
            a, b, c, shoulder, shoulder_bottom, left_bottom = app.project.sketches[0].vertices

            app.mode.set("dimension")
            app.change_tool()
            with patch.object(DimensionDialog, "show", apply_dialog):
                click_world(0, 10)
                click_world(20, 20)  # dimension an internal feature
            self.assertIsNotNone(app.pending_annotation)
            click_world(10, 3)  # choose annotation position
            dimension = app.project.dimensions[0]
            self.assertEqual(dimension.annotation_y, 3)
            self.assertAlmostEqual(dimension.label_offset, 0)

            app.mode.set("gap")
            app.change_tool()
            with patch.object(GapDialog, "show", apply_dialog):
                click_world(0, 25)  # select the middle of a vertical face
                click_world(20, 27)
            wait_for_analysis()
            self.assertAlmostEqual(app.result.minimum, 19.9)
            self.assertAlmostEqual(app.result.maximum, 20.1)

            app.mode.set("select")
            app.change_tool()
            sx, sy = app.screen(10, 1.6)  # dimension text baseline
            app.canvas_down(SimpleNamespace(x=sx, y=sy))
            self.assertEqual(app.selected, ("dimension", dimension.id))
            app.canvas_move(SimpleNamespace(x=sx + 24, y=sy + 36))
            app.canvas_up(None)
            self.assertEqual(dimension.annotation_y, 6)
            self.assertEqual(dimension.label_offset, 2)
            app.undo()
            wait_for_analysis()
            self.assertEqual(app.project.dimensions[0].annotation_y, 3)
            app.redo()
            wait_for_analysis()
            self.assertEqual(app.project.dimensions[0].annotation_y, 6)

            def change_dimension(dialog):
                dialog.variables["nominal"].set("25")
                dialog.variables["lower"].set("-0.2")
                dialog.variables["upper"].set("0.2")
                return apply_dialog(dialog)

            app.selected = "dimension", app.project.dimensions[0].id
            with patch.object(DimensionDialog, "show", change_dimension):
                app.edit_selected()
            wait_for_analysis()
            self.assertAlmostEqual(app.result.minimum, 24.8)
            self.assertAlmostEqual(app.result.maximum, 25.2)
            self.assertEqual(app.project.dimensions[0].annotation_y, 6)
            self.assertAlmostEqual(app.result.nominal_positions[shoulder], 25)
            self.assertAlmostEqual(app.result.nominal_positions[shoulder_bottom], 25)

            app.mode.set("circle")
            app.change_tool()
            with patch.object(SketchDialog, "show", apply_dialog):
                click_world(40, 16)
                click_world(46, 16)
            circle = app.project.sketches[-1]
            left, center, right = circle.vertices
            with patch.object(DimensionDialog, "show", apply_dialog):
                app.add_dimension(left, right)
            with patch.object(GapDialog, "show", apply_dialog):
                app.edit_gap(center, right)
            wait_for_analysis()
            self.assertAlmostEqual(app.result.minimum, 2.95)
            self.assertAlmostEqual(app.result.maximum, 3.05)
            for pose in ["Minimum gap", "Maximum gap", "Reference pose"]:
                app.view.set(pose)
                app.draw()
                app.update_idletasks()

            app.mode.set("line")
            app.change_tool()
            with patch.object(SketchDialog, "show", apply_dialog):
                click_world(43, 16)
                click_world(50, 16)
            line = app.project.sketches[-1]
            self.assertEqual(line.vertices[0], center)
            self.assertEqual(len(app.project.points), 10)
        finally:
            app.closing = True
            app.executor.shutdown(wait=True, cancel_futures=True)
            if app.analysis_timer is not None:
                app.after_cancel(app.analysis_timer)
            app.destroy()

    def test_sketch_edit_preview_and_undo_workflow(self):
        from stackup.gui import BodyDialog, DimensionDialog, FitDialog, GapDialog, StackupApp
        app = StackupApp(floating_block())

        def wait_for_analysis():
            deadline = time.monotonic() + 15
            while app.result is None and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            self.assertIsNotNone(app.result)

        try:
            wait_for_analysis()
            self.assertAlmostEqual(app.result.maximum, 6.3)
            for view in ["Minimum gap", "Maximum gap", "Reference pose"]:
                app.view.set(view)
                app.draw()
                app.update_idletasks()
                self.assertTrue(app.canvas.find_all())

            dialog = FitDialog(app, app.project, app.project.fits[0])
            dialog.variables["mode"].set("Centered (equal gaps)")
            fit = dialog.value()
            dialog.destroy()
            app.checkpoint()
            app.project.fits[0] = fit
            app.refresh()
            wait_for_analysis()
            self.assertAlmostEqual(app.result.minimum, 2.85)
            app.undo()
            wait_for_analysis()
            self.assertAlmostEqual(app.result.minimum, 0)
            app.redo()
            wait_for_analysis()
            self.assertAlmostEqual(app.result.maximum, 3.15)

            app.replace_project(Project())
            app.mode.set("body")
            app.change_tool()

            def apply_real_dialog(dialog):
                value = dialog.value()
                dialog.destroy()
                return value

            with patch.object(BodyDialog, "show", apply_real_dialog):
                app.canvas_down(SimpleNamespace(x=100, y=200))
                app.canvas_down(SimpleNamespace(x=340, y=200))
            self.assertEqual(len(app.project.bodies), 1)
            self.assertEqual(len(app.project.dimensions), 1)
            left, right = [point.id for point in app.project.points]
            with patch.object(GapDialog, "show", apply_real_dialog):
                app.edit_gap(left, right)
            wait_for_analysis()
            width = app.project.dimensions[0].nominal
            self.assertAlmostEqual(app.result.minimum, width - 0.1)
            self.assertAlmostEqual(app.result.maximum, width + 0.1)

            dimension = DimensionDialog(app, app.project, existing=app.project.dimensions[0])
            dimension.variables["lower"].set("-0.25")
            edited = dimension.value()
            dimension.destroy()
            self.assertEqual(edited.lower, -0.25)
        finally:
            app.closing = True
            app.executor.shutdown(wait=True, cancel_futures=True)
            if app.analysis_timer is not None:
                app.after_cancel(app.analysis_timer)
            app.destroy()


if __name__ == "__main__":
    unittest.main()
