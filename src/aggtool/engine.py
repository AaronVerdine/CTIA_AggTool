"""The aggregation engine: pure functions over pandas frames, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from aggtool import __version__
from aggtool.profile import KEY_FIELD, Profile
from aggtool.tables import read_source, to_csv_bytes, to_xlsx_bytes

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TRAILING_ZERO = re.compile(r"^(\d+)\.0+$")

# Reasons a primary row can be dropped, in the order rules are applied.
REASON_EMPTY = "empty_code"
REASON_EMAIL = "email_address"
REASON_NON_NUMERIC = "non_numeric_code"
REASON_LENGTH = "invalid_code_length"
REASON_DUPLICATE = "duplicate_code"


def missing_reason(field_name: str) -> str:
    return f"missing_{field_name}"


def normalize_code(value: str) -> str:
    """Canonical text form of a short code.

    Trims whitespace and undoes the ``22222.0`` that Excel and pandas produce
    when a numeric column passes through a float.
    """
    text = str(value).strip()
    match = _TRAILING_ZERO.match(text)
    return match.group(1) if match else text


@dataclass
class AggregationResult:
    combined: pd.DataFrame
    dropped: pd.DataFrame
    unmatched_primary: pd.DataFrame
    unmatched_lookup: pd.DataFrame
    lookup_conflicts: pd.DataFrame
    summary: dict[str, Any] = field(default_factory=dict)

    def to_xlsx(self) -> bytes:
        summary_rows = pd.DataFrame(
            [(k, str(v)) for k, v in _flatten(self.summary).items()], columns=["Metric", "Value"]
        )
        return to_xlsx_bytes(
            {
                "Combined List": self.combined,
                "No Lease Holder": self.unmatched_primary,
                "Not On Carrier": self.unmatched_lookup,
                "Registry Conflicts": self.lookup_conflicts,
                "Dropped Rows": self.dropped,
                "Summary": summary_rows,
            }
        )

    def to_csv(self) -> bytes:
        return to_csv_bytes(self.combined)

    def report(self) -> dict[str, Any]:
        """JSON-serialisable audit report."""
        return {
            "summary": self.summary,
            "dropped": self.dropped.to_dict(orient="records"),
            "unmatched_primary": self.unmatched_primary.to_dict(orient="records"),
            "unmatched_lookup": self.unmatched_lookup.to_dict(orient="records"),
            "lookup_conflicts": self.lookup_conflicts.to_dict(orient="records"),
        }


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in d.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def _filter_primary(df: pd.DataFrame, profile: Profile) -> tuple[pd.DataFrame, pd.DataFrame]:
    rules = profile.filters
    df = df.copy()
    df[KEY_FIELD] = df[KEY_FIELD].map(normalize_code)
    reason = pd.Series("", index=df.index, dtype=object)

    def mark(mask: pd.Series, why: str) -> None:
        reason[(reason == "") & mask] = why

    code = df[KEY_FIELD]
    mark(code == "", REASON_EMPTY)
    if rules.drop_emails:
        mark(code.str.match(_EMAIL), REASON_EMAIL)
    if rules.numeric_only:
        mark(~code.str.fullmatch(r"\d+"), REASON_NON_NUMERIC)
    if rules.allowed_code_lengths:
        mark(~code.str.len().isin(rules.allowed_code_lengths), REASON_LENGTH)
    for name in rules.require_fields:
        mark(df[name] == "", missing_reason(name))
    # Deduplicate last, so a later complete row is not lost to an earlier
    # incomplete one.
    if rules.deduplicate:
        survivors = reason == ""
        dupes = survivors & code.where(survivors).duplicated(keep="first")
        mark(dupes, REASON_DUPLICATE)

    keep = reason == ""
    dropped = df.loc[~keep, ["_row", KEY_FIELD]].assign(reason=reason[~keep])
    return df.loc[keep], dropped


def _prepare_lookup(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    df[KEY_FIELD] = df[KEY_FIELD].map(normalize_code)
    df = df[df[KEY_FIELD] != ""]
    value_cols = [c for c in df.columns if c not in ("_row", KEY_FIELD)]
    # A code is conflicting when the registry lists it more than once with
    # different values; the first row wins and the conflict is reported.
    distinct = df.drop_duplicates(subset=[KEY_FIELD, *value_cols])
    conflict_codes = distinct[KEY_FIELD][distinct[KEY_FIELD].duplicated(keep=False)].unique()
    conflicts = df[df[KEY_FIELD].isin(conflict_codes)].sort_values([KEY_FIELD, "_row"])
    return df.drop_duplicates(subset=[KEY_FIELD], keep="first"), conflicts


def _sort_codes(df: pd.DataFrame, column: str) -> pd.DataFrame:
    numeric = pd.to_numeric(df[column], errors="coerce")
    return (
        df.assign(_n=numeric)
        .sort_values(["_n", column], kind="stable", na_position="last")
        .drop(columns="_n")
        .reset_index(drop=True)
    )


def aggregate(primary: pd.DataFrame, lookup: pd.DataFrame, profile: Profile) -> AggregationResult:
    """Apply a profile to two canonical frames (as returned by ``read_source``)."""
    kept, dropped = _filter_primary(primary, profile)
    registry, conflicts = _prepare_lookup(lookup)

    lookup_fields = [c for c in registry.columns if c not in ("_row", KEY_FIELD)]
    merged = kept.merge(
        registry.drop(columns="_row"), on=KEY_FIELD, how="left", indicator=True, validate="1:1"
    )
    for col in lookup_fields:
        merged[col] = merged[col].fillna("")
    matched = merged["_merge"] == "both"

    headers = {c.field: c.header for c in profile.output}
    combined = _sort_codes(merged[[c.field for c in profile.output]], KEY_FIELD).rename(
        columns=headers
    )

    primary_fields = [c for c in kept.columns if c not in ("_row",)]
    unmatched_primary = _sort_codes(merged.loc[~matched, primary_fields], KEY_FIELD).rename(
        columns=headers
    )
    on_carrier = set(kept[KEY_FIELD])
    unmatched_lookup = _sort_codes(
        registry.loc[~registry[KEY_FIELD].isin(on_carrier), [KEY_FIELD, *lookup_fields]],
        KEY_FIELD,
    ).rename(columns=headers)

    dropped_out = (
        dropped.rename(columns={"_row": "Source Row", KEY_FIELD: headers.get(KEY_FIELD, KEY_FIELD)})
        .rename(columns={"reason": "Reason"})
        .assign(Source=profile.primary.label)
        .reset_index(drop=True)
    )
    conflicts_out = conflicts.rename(columns={"_row": "Source Row", **headers}).reset_index(
        drop=True
    )

    summary: dict[str, Any] = {
        "profile": {"name": profile.name, "version": profile.version},
        "engine_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "primary_rows": len(primary),
        "lookup_rows": len(lookup),
        "output_rows": len(combined),
        "dropped_rows": len(dropped),
        "dropped_by_reason": {k: int(v) for k, v in dropped["reason"].value_counts().items()},
        "matched": int(matched.sum()),
        "no_lease_holder": int((~matched).sum()),
        "not_on_carrier": len(unmatched_lookup),
        "registry_conflicts": int(conflicts[KEY_FIELD].nunique()) if len(conflicts) else 0,
    }
    return AggregationResult(
        combined=combined,
        dropped=dropped_out,
        unmatched_primary=unmatched_primary,
        unmatched_lookup=unmatched_lookup,
        lookup_conflicts=conflicts_out,
        summary=summary,
    )


def aggregate_files(
    primary_bytes: bytes,
    primary_name: str,
    lookup_bytes: bytes,
    lookup_name: str,
    profile: Profile,
) -> AggregationResult:
    primary = read_source(primary_bytes, primary_name, profile.primary)
    lookup = read_source(lookup_bytes, lookup_name, profile.lookup)
    return aggregate(primary, lookup, profile)
