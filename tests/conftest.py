from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from aggtool.profile import Profile, get_profile

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture
def profile() -> Profile:
    return get_profile("tmo-csca")


@pytest.fixture
def primary_bytes() -> bytes:
    return (SAMPLES / "tmo_wmc_sc_report.csv").read_bytes()


@pytest.fixture
def lookup_bytes() -> bytes:
    return (SAMPLES / "csca_report.csv").read_bytes()


def csv_bytes(rows: list[list[object]]) -> bytes:
    return pd.DataFrame(rows[1:], columns=rows[0]).to_csv(index=False).encode()


def xlsx_bytes(sheets: dict[str, list[list[object]]]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name, index=False, header=False)
    return buf.getvalue()
