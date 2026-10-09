import math
import random
import unittest

from stackup.examples import fit_risk, floating_block, placement_shift, serial_chain
from stackup.model import Dimension, Gap, Point, Project
from stackup.solver import analyze


class SolverTests(unittest.TestCase):
    def assert_limits(self, result, nominal, minimum, maximum):
        self.assertEqual(result.status, "ok", result.message)
        self.assertAlmostEqual(result.nominal, nominal, places=7)
        self.assertAlmostEqual(result.minimum, minimum, places=7)
        self.assertAlmostEqual(result.maximum, maximum, places=7)

    def test_serial_stack_matches_signed_interval_arithmetic(self):
        self.assert_limits(analyze(serial_chain()), 5, 4.55, 5.45)

    def test_float_extrema_include_dimension_dependent_clearance(self):
        project = floating_block()
        result = analyze(project)
        self.assert_limits(result, 3, 0, 6.3)
        fit = result.fits[0]
        self.assertAlmostEqual(fit.clearance_min, 5.7)
        self.assertAlmostEqual(fit.clearance_max, 6.3)
        self.assertAlmostEqual(fit.shift_min, -3)
        self.assertAlmostEqual(fit.shift_max, 3)
        maximum = result.maximum_positions
        self.assertAlmostEqual(maximum["P3"], maximum["P1"])
        self.assertAlmostEqual(maximum["P4"] - maximum["P3"], 33.9)
        self.assertAlmostEqual(maximum["P2"] - maximum["P1"], 40.2)
        self.assertFalse(result.meets_gap_limits)

    def test_centering_shares_clearance_between_both_sides(self):
        result = analyze(floating_block("centered"))
        self.assert_limits(result, 3, 2.85, 3.15)
        for positions in (result.minimum_positions, result.maximum_positions):
            self.assertAlmostEqual(positions["P3"] - positions["P1"], positions["P2"] - positions["P4"])
        self.assertAlmostEqual(result.fits[0].shift_min, 0)
        self.assertAlmostEqual(result.fits[0].shift_max, 0)

    def test_left_and_right_seating_are_different_assembly_conditions(self):
        self.assert_limits(analyze(floating_block("left")), 6, 5.7, 6.3)
        self.assert_limits(analyze(floating_block("right")), 0, 0, 0)

    def test_known_mounting_float_changes_gap_limits(self):
        self.assert_limits(analyze(placement_shift()), 5, 4.2, 5.8)

    def test_shared_faces_cancel_the_same_tolerance(self):
        project = Project(points=[Point("P1", "A", 0, 0), Point("P2", "B", 10, 0), Point("P3", "C", 15, 0)],
                          dimensions=[Dimension("D1", "A to B", "P1", "P2", 10, -1, 1), Dimension("D2", "B to C", "P2", "P3", 5, 0, 0)],
                          datum="P1", gap=Gap("P2", "P3"))
        self.assert_limits(analyze(project), 5, 5, 5)

    def test_internal_feature_on_centered_body_is_coupled_to_motion(self):
        project = floating_block("centered")
        project.points.append(Point("P5", "Internal feature", 13, 20))
        project.dimensions.append(Dimension("D3", "Internal offset", "P3", "P5", 10, -0.1, 0.1))
        project.gap = Gap("P5", "P2")
        self.assert_limits(analyze(project), 27, 26.75, 27.25)

    def test_asymmetric_tolerances_are_signed_correctly(self):
        project = serial_chain()
        project.dimensions[0].lower, project.dimensions[0].upper = -0.1, 0.3
        project.dimensions[1].lower, project.dimensions[1].upper = -0.2, 0.05
        self.assert_limits(analyze(project), 5, 4.7, 5.65)

    def test_coordinates_are_allowed_on_negative_side_of_datum(self):
        project = Project(points=[Point("P1", "Datum", 0, 0), Point("P2", "Left", -12, 0)],
                          dimensions=[Dimension("D1", "Signed distance", "P1", "P2", -12, -0.2, 0.2)],
                          datum="P1", gap=Gap("P1", "P2", minimum_allowed=None))
        self.assert_limits(analyze(project), -12, -12.2, -11.8)

    def test_datum_change_does_not_change_relative_gap(self):
        project = floating_block()
        before = analyze(project)
        project.datum = "P3"
        after = analyze(project)
        self.assertAlmostEqual(before.minimum, after.minimum)
        self.assertAlmostEqual(before.maximum, after.maximum)

    def test_moving_sketch_reference_changes_reference_not_extrema(self):
        project = floating_block()
        project.points[2].x, project.points[3].x = 1, 35
        self.assert_limits(analyze(project), 5, 0, 6.3)

    def test_some_sizes_interfere_even_when_a_feasible_gap_passes(self):
        result = analyze(fit_risk())
        self.assert_limits(result, 0, 0, 0.4)
        self.assertTrue(result.fits[0].fit_risk)
        self.assertTrue(result.meets_gap_limits)
        self.assertAlmostEqual(result.fits[0].clearance_min, -0.4)
        self.assertTrue(any("some permitted size" in warning for warning in result.warnings))

    def test_no_size_combination_fits_is_infeasible(self):
        project = floating_block()
        project.dimensions[1].nominal = 41
        result = analyze(project)
        self.assertEqual(result.status, "infeasible")
        self.assertIsNone(result.minimum)
        self.assertTrue(result.conflicts)

    def test_no_nominal_fit_does_not_hide_feasible_tolerance_combinations(self):
        project = floating_block()
        project.dimensions[0].nominal = 33.9
        result = analyze(project)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.nominal)
        self.assertAlmostEqual(result.minimum, 0)
        self.assertAlmostEqual(result.maximum, 0.2)
        self.assertTrue(result.warnings)

    def test_unlocated_body_is_reported_as_unbounded(self):
        project = floating_block()
        project.fits.clear()
        result = analyze(project)
        self.assertEqual(result.status, "unbounded")
        self.assertEqual(result.minimum, -math.inf)
        self.assertEqual(result.maximum, math.inf)
        self.assertIsNone(result.meets_gap_limits)

    def test_contradictory_dimension_loop_is_identified(self):
        project = serial_chain()
        project.dimensions.append(Dimension("D5", "Contradiction", "P1", "P2", 25, 0, 0))
        self.assertEqual(analyze(project).status, "infeasible")

    def test_contact_ties_separate_face_points(self):
        project = floating_block()
        project.fits.clear()
        project.dimensions.append(Dimension("D3", "Left contact", "P1", "P3", 0, 0, 0, "contact"))
        self.assert_limits(analyze(project), 6, 5.7, 6.3)

    def test_random_serial_chains_match_closed_form(self):
        rng = random.Random(17)
        for _ in range(25):
            lengths = [rng.uniform(1, 25) for _ in range(5)]
            lows = [-rng.uniform(0.01, 0.5) for _ in lengths]
            highs = [rng.uniform(0.01, 0.5) for _ in lengths]
            points = [Point("P0", "Datum", 0, 0)]
            dimensions = []
            for i, length in enumerate(lengths):
                points.append(Point(f"P{i+1}", "Face", points[-1].x + length, 0))
                dimensions.append(Dimension(f"D{i}", f"Leg {i}", f"P{i}", f"P{i+1}", length, lows[i], highs[i]))
            project = Project(points=points, dimensions=dimensions, datum="P0", gap=Gap("P0", "P5"))
            self.assert_limits(analyze(project), sum(lengths), sum(lengths) + sum(lows), sum(lengths) + sum(highs))


if __name__ == "__main__":
    unittest.main()
