"""StackLab 1D desktop entry point and headless engineering analysis CLI."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


def _number(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.6g}"


def main(argv: list[str] | None = None) -> int:
    # Windows shells may still use cp1252, which cannot encode the delta
    # symbol in an automatically generated dimensional-chain equation.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")
    if sys.version_info < (3, 11):
        print(f"StackLab 1D requires Python 3.11 or newer; this interpreter is "
              f"{sys.version.split()[0]} at {sys.executable}. Run run.bat from the project folder "
              "to use its isolated environment.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description="StackLab 1D mechanical tolerance analysis")
    parser.add_argument("project", nargs="?", help="A .stack1d project to open")
    parser.add_argument("--analyze", action="store_true", help="Run analysis in the terminal without a GUI")
    parser.add_argument("--requirement", help="Analyze one requirement ID (default: all requirements)")
    parser.add_argument("--methods", help="Comma-separated methods; defaults to each requirement's analysis case")
    parser.add_argument("--samples", type=int, help="Monte Carlo sample count")
    parser.add_argument("--seed", type=int, help="Monte Carlo random seed")
    parser.add_argument("--sigma-level", type=float, help="RSS report half-width in standard deviations")
    parser.add_argument("--report", type=Path, help="Export a PDF engineering report (requires --analyze)")
    parser.add_argument("--csv", type=Path, help="Export CSV analysis tables (requires --analyze)")
    parser.add_argument("--xlsx", type=Path, help="Export XLSX analysis tables (requires --analyze)")
    args = parser.parse_args(argv)
    if args.analyze and not args.project:
        parser.error("--analyze requires a .stack1d project")
    if (args.report or args.csv or args.xlsx) and not args.analyze:
        parser.error("--report, --csv and --xlsx require --analyze")
    methods_override = (tuple(method.strip() for method in args.methods.split(",") if method.strip())
                        if args.methods is not None else None)
    if methods_override is not None and (
        not methods_override or any(method not in {"worst_case", "rss", "monte_carlo"}
                                    for method in methods_override)
    ):
        parser.error("--methods must contain worst_case, rss or monte_carlo")
    if args.samples is not None and args.samples < 1:
        parser.error("--samples must be positive")
    if args.seed is not None and args.seed < 0:
        parser.error("--seed must be non-negative")
    if args.sigma_level is not None and args.sigma_level <= 0:
        parser.error("--sigma-level must be positive")

    from stacklab.services import ProjectService

    service = ProjectService()
    try:
        if args.project:
            service.open(args.project)
        if args.analyze:
            from stacklab.reporting import export_csv, export_pdf, export_xlsx

            requirements = ([requirement for requirement in service.project.requirements
                             if requirement.id == args.requirement] if args.requirement else service.project.requirements)
            if not requirements:
                print("No matching functional requirement is defined in this project.", file=sys.stderr)
                return 2
            results = []
            for requirement in requirements:
                case = next((item for item in service.project.analysis_cases
                             if item.requirement_id == requirement.id), None)
                methods = methods_override or tuple(case.methods if case else requirement.methods)
                samples = args.samples if args.samples is not None else case.samples if case else 10_000
                seed = args.seed if args.seed is not None else case.seed if case else 0
                sigma_level = (args.sigma_level if args.sigma_level is not None else
                               case.sigma_level if case else 3.0)
                result = service.analyze(requirement.id, methods=methods, samples=samples,
                                         seed=seed, sigma_level=sigma_level)
                results.append(result)
                print(f"{requirement.name} [{requirement.id}]")
                if result.chain.equation:
                    console_equation = result.chain.equation.replace("·", "*").replace("Δ", "delta")
                    print(f"  Chain: {console_equation}")
                if result.worst_case is not None:
                    wc = result.worst_case
                    print(f"  Worst case: nominal {_number(wc.nominal)}, min {_number(wc.minimum)}, max {_number(wc.maximum)} {service.project.unit}")
                    print(f"  Requirement: {'pass' if wc.meets_limits else 'fail' if wc.meets_limits is False else 'unspecified'}")
                if result.rss is not None:
                    rss = result.rss
                    print(f"  RSS: mean {_number(rss.mean)}, sigma {_number(rss.std)}, {rss.sigma_level:g}-sigma interval {_number(rss.lower)} to {_number(rss.upper)} {service.project.unit}")
                if result.monte_carlo is not None:
                    mc = result.monte_carlo
                    print(f"  Monte Carlo: {mc.valid_samples}/{mc.requested_samples} assembled, mean {_number(mc.mean)}, std {_number(mc.std)} {service.project.unit}")
                    print(f"  Overall failure probability: {_number(mc.overall_failure_probability)}")
                for diagnostic in result.diagnostics:
                    print(f"  {diagnostic.severity}: {diagnostic.message}")
            if args.report:
                export_pdf(args.report, service.project, results)
                print(f"PDF: {args.report}")
            if args.csv:
                export_csv(args.csv, service.project, results)
                print(f"CSV: {args.csv}")
            if args.xlsx:
                export_xlsx(args.xlsx, service.project, results)
                print(f"XLSX: {args.xlsx}")
            return 0

        try:
            from PySide6.QtWidgets import QApplication
            from stacklab.ui import MainWindow
        except (ImportError, OSError) as exc:
            print("StackLab 1D could not load the Qt desktop library in this Python environment.\n"
                  f"Interpreter: {sys.executable}\n"
                  "Run run.bat from the project folder, which uses the project's isolated .venv.\n"
                  f"Details: {exc}", file=sys.stderr)
            return 1
        application = QApplication(sys.argv[:1] + ([] if argv is None else argv))
        application.setApplicationName("StackLab 1D")
        application.setOrganizationName("StackLab")
        window = MainWindow(service)
        window.show()
        return application.exec()
    except Exception as exc:
        if args.analyze:
            from stacklab.compiler import ModelError
            from stacklab.persistence import ProjectFormatError
            if not isinstance(exc, (ModelError, ProjectFormatError, ValueError, FileNotFoundError)):
                logging.exception("Unexpected StackLab analysis failure")
            print(f"Analysis failed: {exc}", file=sys.stderr)
        else:
            logging.exception("StackLab could not start")
            print(f"StackLab could not start: {exc}", file=sys.stderr)
        return 2 if args.analyze and isinstance(exc, (ModelError, ValueError)) else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
