"""One test per README rule, plus the joins the original prototype got wrong."""

from __future__ import annotations

import pandas as pd
import pytest

from aggtool.engine import aggregate, aggregate_files, normalize_code
from aggtool.profile import Profile
from aggtool.tables import read_source

PRIMARY_HEADER = ["Start Application Addresses", "Provider Name", "Content Provider Name"]
LOOKUP_HEADER = ["Short Code", "Company"]


def run(profile: Profile, primary_rows: list[list[str]], lookup_rows: list[list[str]]):
    def frame(header: list[str], rows: list[list[str]], spec):
        text = "\n".join(",".join(r) for r in [header, *rows]).encode()
        return read_source(text, "x.csv", spec)

    return aggregate(
        frame(PRIMARY_HEADER, primary_rows, profile.primary),
        frame(LOOKUP_HEADER, lookup_rows, profile.lookup),
        profile,
    )


def codes(result) -> list[str]:
    return result.combined["Short Code"].tolist()


def test_output_columns_in_readme_order(profile):
    result = run(profile, [["22222", "Agg", "CP"]], [["22222", "Lessee"]])
    assert list(result.combined.columns) == [
        "Short Code",
        "Aggregator",
        "Content Provider",
        "Lease Holder",
    ]


def test_lease_holder_joined_by_code_not_row_position(profile):
    # The prototype paired rows by position, giving 22222 the lease holder of 66666.
    result = run(
        profile,
        [["22222", "A", "CP2"], ["66666", "B", "CP6"]],
        [["66666", "Six Corp"], ["22222", "Two Corp"]],
    )
    pairs = dict(zip(result.combined["Short Code"], result.combined["Lease Holder"], strict=True))
    assert pairs == {"22222": "Two Corp", "66666": "Six Corp"}


@pytest.mark.parametrize(
    ("code", "kept"),
    [
        ("12", False),
        ("333", False),
        ("4444", False),
        ("55555", True),
        ("666666", True),
        ("7777777", False),
        ("88888888", False),
        ("999999999", False),
    ],
)
def test_only_five_and_six_digit_codes_kept(profile, code, kept):
    result = run(profile, [[code, "A", "CP"]], [])
    assert (code in codes(result)) is kept
    if not kept:
        assert result.dropped["Reason"].tolist() == ["invalid_code_length"]


def test_email_addresses_removed_without_crashing(profile):
    # The prototype raised ValueError merging str and int columns here.
    result = run(profile, [["user@example.com", "A", "CP"], ["22222", "A", "CP"]], [["22222", "L"]])
    assert codes(result) == ["22222"]
    assert result.dropped["Reason"].tolist() == ["email_address"]


def test_non_numeric_codes_removed(profile):
    result = run(profile, [["ABCDE", "A", "CP"]], [])
    assert result.dropped["Reason"].tolist() == ["non_numeric_code"]


def test_blank_content_provider_removed(profile):
    result = run(profile, [["22222", "A", ""], ["33333", "A", "  "]], [])
    assert codes(result) == []
    assert set(result.dropped["Reason"]) == {"missing_content_provider"}


def test_duplicates_removed_keeping_first_complete_row(profile):
    result = run(
        profile,
        [["22222", "A", ""], ["22222", "B", "CP"], ["22222", "C", "CP"]],
        [],
    )
    assert result.combined[["Short Code", "Aggregator"]].values.tolist() == [["22222", "B"]]
    assert result.dropped["Reason"].tolist() == ["missing_content_provider", "duplicate_code"]


def test_dropped_rows_point_at_source_line(profile):
    result = run(profile, [["22222", "A", "CP"], ["333", "A", "CP"]], [])
    # Line 1 is the header, so the second data row is line 3.
    assert result.dropped["Source Row"].tolist() == [3]


def test_sorted_numerically(profile):
    result = run(profile, [["666666", "A", "CP"], ["99999", "A", "CP"], ["10000", "A", "CP"]], [])
    assert codes(result) == ["10000", "99999", "666666"]


@pytest.mark.parametrize(
    ("raw", "expected"), [(" 22222 ", "22222"), ("22222.0", "22222"), ("02222", "02222")]
)
def test_normalize_code(raw, expected):
    assert normalize_code(raw) == expected


def test_excel_float_codes_join(profile):
    result = run(profile, [["22222.0", "A", "CP"]], [["22222", "Lessee"]])
    assert result.combined["Lease Holder"].tolist() == ["Lessee"]


def test_reconciliation_outputs(profile):
    result = run(
        profile,
        [["22222", "A", "CP"], ["33333", "A", "CP"]],
        [["22222", "L1"], ["22222", "L2"], ["44444", "L4"], ["22222", "L1"]],
    )
    assert result.unmatched_primary["Short Code"].tolist() == ["33333"]
    assert result.unmatched_lookup["Short Code"].tolist() == ["44444"]
    assert result.lookup_conflicts["Lease Holder"].tolist() == ["L1", "L2", "L1"]
    # First registry row wins.
    assert result.combined.set_index("Short Code").loc["22222", "Lease Holder"] == "L1"
    s = result.summary
    assert (s["matched"], s["no_lease_holder"], s["not_on_carrier"], s["registry_conflicts"]) == (
        1,
        1,
        1,
        1,
    )


def test_identical_registry_duplicates_are_not_conflicts(profile):
    result = run(profile, [["22222", "A", "CP"]], [["22222", "L1"], ["22222", "L1"]])
    assert result.lookup_conflicts.empty


def test_sample_files_end_to_end(profile, primary_bytes, lookup_bytes):
    result = aggregate_files(primary_bytes, "tmo.csv", lookup_bytes, "csca.csv", profile)
    assert codes(result) == ["12345", "22222", "31313", "40404", "98765", "666666"]
    assert result.summary["dropped_by_reason"] == {
        "invalid_code_length": 2,
        "missing_content_provider": 1,
        "duplicate_code": 1,
        "email_address": 1,
    }
    report = result.report()
    assert set(report) == {
        "summary",
        "dropped",
        "unmatched_primary",
        "unmatched_lookup",
        "lookup_conflicts",
    }
    sheets = pd.read_excel(__import__("io").BytesIO(result.to_xlsx()), sheet_name=None, dtype=str)
    assert list(sheets) == [
        "Combined List",
        "No Lease Holder",
        "Not On Carrier",
        "Registry Conflicts",
        "Dropped Rows",
        "Summary",
    ]
    assert sheets["Combined List"]["Short Code"].tolist() == codes(result)
