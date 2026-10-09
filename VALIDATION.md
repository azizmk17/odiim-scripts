# Validation of version 1.1.0

The complete suite passed: **44 tests**, including two desktop workflows.
The randomized-chain test additionally checks 25 independently generated
dimension chains against their closed-form interval bounds.

Test environment: Linux, Python 3.12, SciPy 1.17.0, NumPy 2.3.5 and Tk 9.0.
The native GUI ran on a private virtual display. Its actual rendered interface
was captured and inspected. The workflows exercised rectangle creation,
custom profile drawing and closure, removing a draft vertex, dimensioning an
internal shoulder, selecting a vertical face, placing and dragging annotations,
editing driving dimensions, circle drawing / radius analysis, connected lines,
gap creation, fit editing, assembly-pose previews and undo/redo.

Core tests include free movement dependent on toleranced sizes, centered and
seated assembly conditions, internal features on a moving body, signed and
asymmetric chains, cancellation of shared faces, changed reference poses,
interference, unbounded gaps and contradictory specifications. Project-file
tests cover round-trips, bad inputs, cascading face removal, atomic writes and
HTML/CSV exports.

Seventeen new analytical and file tests check shared-x profile faces,
independently drawn parts, intentionally shared line endpoints, circle-center
geometry, half-diameter variation, custom outline exports, movable annotations,
schema-1 migration, removed shapes, and conflicting geometric constraints.
Driving dimensions update the nominal sketch before a gap is selected;
contradictory dimensions are reported at that stage too.
The stepped-part example is checked independently for free, left-seated,
right-seated and centered assembly conditions. Its free shoulder-gap interval
is **9.85–16.25 mm**; a gap measured from its hole's right edge is
**17.8–24.3 mm**, including diameter variation and assembly movement.

The Windows launcher is supplied; this environment did not execute it on
Windows. Python and Tcl/Tk are prerequisites. The model's limits are explained
in the README and in the app's Help.
