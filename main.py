"""Run the desktop app, or calculate a saved project without a GUI."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Odiim 1D Stackup — sketch dimensions, tolerances and assembly freedom")
    parser.add_argument("project", nargs="?", help="Optional .stackup.json file to open")
    parser.add_argument("--analyze", action="store_true", help="Calculate the supplied project in the terminal")
    parser.add_argument("--report", help="Write an HTML report; use with --analyze")
    args = parser.parse_args()
    try:
        from stackup.io import export_html, load_project, number
        from stackup.solver import analyze
    except ImportError as exc:
        print("Missing dependency: " + str(exc), file=sys.stderr)
        print("Install requirements first: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    if args.report and not args.analyze:
        parser.error("--report requires --analyze")
    if args.analyze and not args.project:
        parser.error("--analyze requires a saved project path")
    try:
        project = load_project(args.project) if args.project else None
        if args.analyze:
            result = analyze(project)
            print(f"{project.name}: {result.status}\n{result.message}")
            print(f"Reference: {number(result.nominal)} mm\nMinimum: {number(result.minimum)} mm\nMaximum: {number(result.maximum)} mm")
            for fit in result.fits:
                print(f"{fit.name}: size clearance {number(fit.clearance_min)} .. {number(fit.clearance_max)} mm; nominal-size shift {number(fit.shift_min)} .. {number(fit.shift_max)} mm")
            for message in result.warnings + result.conflicts:
                print("Note: " + message)
            if args.report:
                export_html(project, result, args.report)
                print("Report: " + args.report)
            return 0 if result.status == "ok" else 2
        from stackup.gui import StackupApp
        StackupApp(project).mainloop()
        return 0
    except (OSError, ValueError) as exc:
        print("Could not run project: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
