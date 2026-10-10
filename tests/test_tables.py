from __future__ import annotations

import io

import pandas as pd
import pytest

from aggtool.engine import normalize_code
from aggtool.errors import InputFormatError, SchemaError
from aggtool.tables import read_source, sanitize_cell, to_csv_bytes
from tests.conftest import xlsx_bytes


def test_reads_xlsx_with_banner_rows_on_second_sheet(profile):
    data = xlsx_bytes(
        {
            "Cover": [["Report"], ["nothing here"]],
            "Data": [
                ["WMC SC Report", None, None],
                ["Start Application Addresses", "Provider Name", "Content Provider Name"],
                [22222, "Agg", "CP"],
                [None, None, None],
                [33333.0, "Agg2", "CP2"],
            ],
        }
    )
    df = read_source(data, "report.xlsx", profile.primary)
    assert [normalize_code(c) for c in df["short_code"]] == ["22222", "33333"]
    assert df["_row"].tolist() == [3, 5]


def test_header_matching_ignores_case_and_spacing(profile):
    data = b"  short   CODE ,COMPANY\n22222,Lessee\n"
    df = read_source(data, "c.csv", profile.lookup)
    assert df[["short_code", "lease_holder"]].values.tolist() == [["22222", "Lessee"]]


def test_semicolon_delimited_and_cp1252(profile):
    data = "Short Code;Company\n22222;Café Ltd\n".encode("cp1252")
    df = read_source(data, "c.csv", profile.lookup)
    assert df["lease_holder"].tolist() == ["Café Ltd"]


def test_missing_column_message_lists_expected_and_found(profile):
    with pytest.raises(SchemaError, match=r"lease_holder.*Company.*Found: Short Code, Owner"):
        read_source(b"Short Code,Owner\n22222,x\n", "c.csv", profile.lookup)


def test_no_header_row(profile):
    with pytest.raises(SchemaError, match="no header row"):
        read_source(b"a,b\n1,2\n", "c.csv", profile.lookup)


@pytest.mark.parametrize(
    ("data", "name", "match"),
    [
        (b"x", "c.pdf", "not a supported file type"),
        (b"", "c.csv", "empty"),
        (b"not a zip", "c.xlsx", "not a valid Excel"),
        (b"PK\x03\x04garbage", "c.xlsx", "Could not read"),
    ],
)
def test_bad_files(profile, data, name, match):
    with pytest.raises(InputFormatError, match=match):
        read_source(data, name, profile.lookup)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("=1+1", "'=1+1"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"), ("ok", "ok"), (5, 5)],
)
def test_formula_injection_neutralised(value, expected):
    assert sanitize_cell(value) == expected


def test_csv_output_is_sanitised():
    out = to_csv_bytes(pd.DataFrame({"a": ['=HYPERLINK("http://x")']}))
    assert pd.read_csv(io.BytesIO(out))["a"].tolist() == ['\'=HYPERLINK("http://x")']
