import json
from pathlib import Path
import tempfile
import unittest

from stackup.examples import floating_block
from stackup.io import export_csv, export_html, load_project, save_project
from stackup.model import Project
from stackup.solver import analyze


class ProjectTests(unittest.TestCase):
    def test_save_open_preserves_every_constraint(self):
        with tempfile.TemporaryDirectory() as directory:
            project = floating_block()
            path = Path(directory) / "assembly.stackup.json"
            save_project(project, path)
            self.assertEqual(project.to_dict(), load_project(path).to_dict())

    def test_dangling_references_are_rejected(self):
        data = floating_block().to_dict()
        data["dimensions"][0]["end"] = "missing"
        with self.assertRaises(ValueError):
            Project.from_dict(data)

    def test_inverted_tolerance_interval_is_rejected(self):
        data = floating_block().to_dict()
        data["dimensions"][0]["lower"] = 1
        with self.assertRaises(ValueError):
            Project.from_dict(data)

    def test_non_finite_input_is_rejected(self):
        data = floating_block().to_dict()
        data["points"][0]["x"] = float("nan")
        with self.assertRaises(ValueError):
            Project.from_dict(data)

    def test_deleting_shared_point_removes_dependent_constraints(self):
        project = floating_block()
        project.remove_point("P1")
        project.validate()
        self.assertEqual(project.datum, "P2")
        self.assertEqual(len(project.fits), 0)
        self.assertEqual(len(project.dimensions), 1)

    def test_export_report_contains_actual_extrema_and_escaped_labels(self):
        project = floating_block()
        project.name = "<script>alert(1)</script>"
        result = analyze(project)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            export_html(project, result, path)
            content = path.read_text()
            self.assertIn("6.300 mm", content)
            self.assertIn("Maximum-gap assembly", content)
            self.assertIn("<svg", content)
            self.assertNotIn("<script>", content)
            self.assertIn("&lt;script&gt;", content)
            export_csv(project, result, Path(directory) / "report.csv")

    def test_failed_validation_does_not_overwrite_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            project = floating_block()
            save_project(project, path)
            previous = path.read_bytes()
            project.dimensions[0].lower = 99
            with self.assertRaises(ValueError):
                save_project(project, path)
            self.assertEqual(path.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
