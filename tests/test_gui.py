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
    def test_sketch_edit_preview_and_undo_workflow(self):
        from stackup.gui import BodyDialog, DimensionDialog, FitDialog, GapDialog, StackupApp
        app = StackupApp()

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
