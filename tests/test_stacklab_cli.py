"""User-facing command-line integration checks against packaged examples."""

from pathlib import Path

from main import main


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_cli_reports_fixed_chain_reference_values(capsys):
    code = main([str(EXAMPLES / "a-fixed-chain.stack1d"), "--analyze"])
    captured = capsys.readouterr()
    assert code == 0
    assert "min 0.55, max 1.45 mm" in captured.out
    assert "G = 1" in captured.out
    assert "rss:" not in captured.out
    assert "Traceback" not in captured.err


def test_cli_reports_inconsistent_loop_without_python_traceback(capsys):
    code = main([str(EXAMPLES / "d-inconsistent-loop.stack1d"), "--analyze", "--methods", "worst_case"])
    captured = capsys.readouterr()
    assert code == 2
    assert "no feasible solution" in captured.err
    assert "Traceback" not in captured.err
