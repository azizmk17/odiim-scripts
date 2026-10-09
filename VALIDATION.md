# Validation of version 1.0.0

The complete suite passed: **26 tests**, including the desktop workflow.
The randomized-chain test additionally checks 25 independently generated
dimension chains against their closed-form interval bounds.

Test environment: Linux, Python 3.12, SciPy 1.17.0, NumPy 2.3.5 and Tk 9.0.
The native GUI ran on a private virtual display. Its actual rendered interface
was captured and inspected. The sketch workflow exercised body creation,
dimension inputs, gap creation, fit editing, assembly-pose previews and undo/redo.

Core tests include free movement dependent on toleranced sizes, centered and
seated assembly conditions, internal features on a moving body, signed and
asymmetric chains, cancellation of shared faces, changed reference poses,
interference, unbounded gaps and contradictory specifications. Project-file
tests cover round-trips, bad inputs, cascading face removal, atomic writes and
HTML/CSV exports.

The Windows launcher is supplied; this environment did not execute it on
Windows. Python and Tcl/Tk are prerequisites. The model's limits are explained
in the README and in the app's Help.
