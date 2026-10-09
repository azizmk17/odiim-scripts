"""Check custom feature geometry against independent 1D calculations."""
import math
from pathlib import Path
import tempfile
import unittest

from stackup.drawing import scene, svg_sketch
from stackup.examples import floating_block, stepped_part
from stackup.io import export_html, load_project, save_project
from stackup.model import Dimension, Gap, Project
from stackup.solver import analyze


class SketchTests(unittest.TestCase):
    def test_driving_dimensions_update_the_profile_before_a_gap_is_selected(self):
        project = Project()
        shape = project.add_sketch([(0, 5), (20, 5), (20, 15), (0, 15)])
        a, b, c, d = shape.vertices
        project.dimensions = [Dimension("D1", "Width", a, b, 25, -.2, .2)]
        result = analyze(project)
        self.assertEqual(result.status, "incomplete")
        self.assertIsNone(result.minimum)
        self.assertAlmostEqual(result.nominal_positions[b] - result.nominal_positions[a], 25)
        self.assertAlmostEqual(result.nominal_positions[c], result.nominal_positions[b])
        self.assertAlmostEqual(result.nominal_positions[d], result.nominal_positions[a])

    def test_conflicting_dimensions_are_reported_before_a_gap_is_selected(self):
        project = Project()
        shape = project.add_sketch([(0, 5), (20, 5), (20, 15), (0, 15)])
        a, b, c, d = shape.vertices
        project.dimensions = [Dimension("D1", "Width", a, b, 20),
                              Dimension("D2", "Contradictory width", d, c, 25)]
        result = analyze(project)
        self.assertEqual(result.status, "infeasible")
        self.assertTrue(result.conflicts)

    def test_vertical_faces_share_their_dimension_at_both_vertices(self):
        project = Project()
        shape = project.add_sketch([(0, 5), (30, 5), (30, 15), (0, 15)])
        a, b, c, d = shape.vertices
        project.dimensions = [Dimension("D1", "Width", a, b, 30, -.2, .3)]
        project.gap = Gap(d, c)
        result = analyze(project)
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.minimum, 29.8)
        self.assertAlmostEqual(result.maximum, 30.3)
        for positions in (result.nominal_positions, result.minimum_positions, result.maximum_positions):
            self.assertAlmostEqual(positions[a], positions[d])
            self.assertAlmostEqual(positions[b], positions[c])

    def test_internal_shoulder_inherits_the_same_free_assembly_shift(self):
        result = analyze(stepped_part())
        self.assertAlmostEqual(result.nominal, 13)
        self.assertAlmostEqual(result.minimum, 9.85)  # part width min minus shoulder max
        self.assertAlmostEqual(result.maximum, 16.25)  # slot width max minus shoulder min
        self.assertAlmostEqual(result.fits[0].shift_min, -3)
        self.assertAlmostEqual(result.fits[0].shift_max, 3)

    def test_custom_part_all_seating_conditions(self):
        cases = {"left": (15.75, 16.25), "right": (9.85, 10.15), "centered": (12.8, 13.2)}
        for mode, (minimum, maximum) in cases.items():
            with self.subTest(mode=mode):
                result = analyze(stepped_part(mode))
                self.assertEqual(result.status, "ok")
                self.assertAlmostEqual(result.minimum, minimum)
                self.assertAlmostEqual(result.maximum, maximum)

    def test_circle_edge_on_a_floating_part_includes_half_diameter_variation(self):
        project = stepped_part()
        project.gap = Gap(project.sketches[2].vertices[2], project.sketches[0].vertices[1])
        result = analyze(project)
        # Right seated: W - center_offset - diameter/2 = 18 ± .2.
        # Left seated: slot - center_offset - diameter/2 = 24 ± .3.
        self.assertAlmostEqual(result.nominal, 21)
        self.assertAlmostEqual(result.minimum, 17.8)
        self.assertAlmostEqual(result.maximum, 24.3)

    def test_circle_diameter_dimension_drives_radius_without_double_counting(self):
        project = Project()
        circle = project.add_sketch([(0, 10), (20, 10)], kind="circle")
        left, center, right = circle.vertices
        project.dimensions = [Dimension("D1", "Diameter", left, right, 20, -.2, .4)]
        project.gap = Gap(center, right)
        result = analyze(project)
        self.assertAlmostEqual(result.minimum, 9.9)
        self.assertAlmostEqual(result.maximum, 10.2)
        for positions in (result.minimum_positions, result.maximum_positions):
            self.assertAlmostEqual(2 * positions[center], positions[left] + positions[right])

    def test_circle_radius_dimension_drives_its_diameter(self):
        project = Project()
        circle = project.add_sketch([(0, 10), (20, 10)], kind="circle")
        left, center, right = circle.vertices
        project.dimensions = [Dimension("D1", "Radius", left, center, 10, -.1, .1)]
        project.gap = Gap(left, right)
        result = analyze(project)
        self.assertAlmostEqual(result.minimum, 19.8)
        self.assertAlmostEqual(result.maximum, 20.2)

    def test_sketch_pixels_do_not_supply_hidden_dimensional_bounds(self):
        project = Project()
        shape = project.add_sketch([(0, 0), (20, 0), (20, 5), (0, 5)])
        project.gap = Gap(shape.vertices[0], shape.vertices[1])
        result = analyze(project)
        self.assertEqual(result.status, "unbounded")
        self.assertFalse(math.isfinite(result.minimum))
        self.assertFalse(math.isfinite(result.maximum))

    def test_separate_profiles_do_not_create_accidental_assembly_contacts(self):
        project = Project()
        first = project.add_sketch([(0, 0), (20, 0), (20, 5), (0, 5)])
        second = project.add_sketch([(0, 0), (20, 0), (20, 5), (0, 5)])
        project.dimensions = [Dimension("D1", "First width", first.vertices[0], first.vertices[1], 20),
                              Dimension("D2", "Second width", second.vertices[0], second.vertices[1], 20)]
        project.gap = Gap(first.vertices[1], second.vertices[1])
        self.assertTrue(set(first.vertices).isdisjoint(second.vertices))
        self.assertEqual(analyze(project).status, "unbounded")

    def test_connected_lines_reuse_selected_features_and_preserve_shared_endpoints(self):
        project = Project()
        first = project.add_sketch([(0, 5), (20, 5)], kind="line")
        second = project.add_sketch([(20, 5), (30, 10)], kind="line", reuse=[first.vertices[1], None])
        project.dimensions = [Dimension("D1", "First", *first.vertices, 20, -.1, .1),
                              Dimension("D2", "Second horizontal extent", *second.vertices, 10, -.2, .2)]
        project.gap = Gap(first.vertices[0], second.vertices[1])
        result = analyze(project)
        self.assertAlmostEqual(result.minimum, 29.7)
        self.assertAlmostEqual(result.maximum, 30.3)
        shared = first.vertices[1]
        project.remove_sketch(second.id)
        self.assertIn(shared, {p.id for p in project.points})
        self.assertEqual(len(project.points), 2)
        project.validate()

    def test_dimension_annotation_changes_do_not_change_limits(self):
        project = stepped_part()
        before = analyze(project)
        project.dimensions[2].annotation_y = 60
        project.dimensions[2].label_offset = -5
        after = analyze(project)
        self.assertAlmostEqual(before.minimum, after.minimum)
        self.assertAlmostEqual(before.maximum, after.maximum)
        label = next(s for s in scene(project) if s.kind == "text" and s.tag == "dimension:D3")
        self.assertAlmostEqual(label.coordinates[1], 58.6)

    def test_custom_sketches_and_annotations_survive_save_and_report_export(self):
        project = stepped_part()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.stackup.json"
            save_project(project, path)
            restored = load_project(path)
            self.assertEqual(project.to_dict(), restored.to_dict())
            svg = svg_sketch(restored)
            self.assertIn("<polygon", svg)
            self.assertIn("<ellipse", svg)
            self.assertIn("Shoulder", restored.gap.name)
            report = Path(folder) / "report.html"
            export_html(restored, analyze(restored), report)
            self.assertIn("16.250 mm", report.read_text())
            self.assertIn("Stepped part", report.read_text())

    def test_legacy_projects_open_without_losing_existing_constraints(self):
        data = floating_block().to_dict()
        data["schema_version"] = 1
        data.pop("sketches")
        for dimension in data["dimensions"]:
            dimension.pop("annotation_y")
            dimension.pop("label_offset")
        restored = Project.from_dict(data)
        self.assertEqual(restored.schema_version, 2)
        self.assertAlmostEqual(analyze(restored).maximum, 6.3)

    def test_invalid_geometry_reference_is_rejected(self):
        project = stepped_part()
        project.sketches[0].columns[0].append("missing")
        with self.assertRaises(ValueError):
            project.validate()

    def test_conflicting_vertical_face_dimension_is_reported(self):
        project = Project()
        shape = project.add_sketch([(0, 0), (10, 0), (10, 5), (0, 5)])
        a, b, c, d = shape.vertices
        project.dimensions = [Dimension("D1", "Contradictory face offset", a, d, 2, 0, 0)]
        project.gap = Gap(a, b)
        result = analyze(project)
        self.assertEqual(result.status, "infeasible")
        self.assertTrue(any("Geometry" in label for label in result.conflicts))

    def test_deleting_custom_part_removes_its_gap_and_fit_constraints(self):
        project = stepped_part()
        project.remove_sketch(project.sketches[1].id)
        self.assertIsNone(project.gap)
        self.assertEqual(project.fits, [])
        project.validate()


if __name__ == "__main__":
    unittest.main()
