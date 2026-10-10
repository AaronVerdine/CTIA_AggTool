from __future__ import annotations

import json

from aggtool.cli import main
from tests.conftest import SAMPLES


def test_run_xlsx_and_report(tmp_path, capsys):
    out, report = tmp_path / "out.xlsx", tmp_path / "r.json"
    code = main(
        [
            "run",
            "--primary",
            str(SAMPLES / "tmo_wmc_sc_report.csv"),
            "--lookup",
            str(SAMPLES / "csca_report.csv"),
            "--out",
            str(out),
            "--report",
            str(report),
        ]
    )
    assert code == 0
    assert out.read_bytes().startswith(b"PK")
    assert json.loads(report.read_text())["summary"]["output_rows"] == 6
    assert "6 codes" in capsys.readouterr().out


def test_run_csv(tmp_path):
    out = tmp_path / "out.csv"
    args = ["run", "--primary", str(SAMPLES / "tmo_wmc_sc_report.csv")]
    args += ["--lookup", str(SAMPLES / "csca_report.csv"), "--out", str(out)]
    assert main(args) == 0
    assert out.read_text(encoding="utf-8-sig").startswith("Short Code,Aggregator")


def test_errors_are_friendly(tmp_path, capsys):
    assert main(["run", "--primary", "missing.csv", "--lookup", "x.csv"]) == 2
    assert "File not found" in capsys.readouterr().err


def test_profiles_command(capsys):
    assert main(["profiles"]) == 0
    assert "tmo-csca" in capsys.readouterr().out
