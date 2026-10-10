"""Reading and writing spreadsheets (CSV and Excel) safely."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from aggtool.errors import InputFormatError, SchemaError
from aggtool.profile import KEY_FIELD, SourceSpec

CSV_EXTENSIONS = {".csv", ".txt"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
SUPPORTED_EXTENSIONS = CSV_EXTENSIONS | EXCEL_EXTENSIONS

# Reports often have title or banner rows above the real header.
HEADER_SEARCH_ROWS = 25
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_XLSX_MAGIC = b"PK\x03\x04"


def _norm_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def extension_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise InputFormatError(
            f"'{filename}' is not a supported file type. Use one of: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )
    return ext


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _raw_frames(data: bytes, filename: str) -> list[tuple[str, pd.DataFrame]]:
    """Return (sheet name, grid of strings without a header) for each sheet."""
    ext = extension_of(filename)
    if not data:
        raise InputFormatError(f"'{filename}' is empty.")
    try:
        if ext in CSV_EXTENSIONS:
            text = _decode_csv(data)
            try:
                dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
                    text[:4096], delimiters=",;\t|"
                )
            except csv.Error:
                dialect = csv.excel
            # The csv module tolerates ragged rows (banner lines above the
            # header), which pandas' parser rejects.
            rows = list(csv.reader(io.StringIO(text), dialect))
            width = max((len(r) for r in rows), default=0)
            frame = pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)
            return [("csv", frame)]
        if not data.startswith(_XLSX_MAGIC):
            raise InputFormatError(f"'{filename}' is not a valid Excel workbook.")
        sheets = pd.read_excel(
            io.BytesIO(data), sheet_name=None, header=None, dtype=str, engine="openpyxl"
        )
        return [(name, df.fillna("")) for name, df in sheets.items()]
    except InputFormatError:
        raise
    except Exception as exc:  # pandas/openpyxl raise many types for bad files
        raise InputFormatError(f"Could not read '{filename}': {exc}") from exc


def read_source(data: bytes, filename: str, spec: SourceSpec) -> pd.DataFrame:
    """Read a report and return a frame with canonical column names.

    The returned frame has one column per field in ``spec.columns`` (all
    strings, blanks as ""), plus ``_row`` holding the 1-based row number in the
    original file so dropped rows can be traced back.
    """
    wanted = {field: {_norm_header(a) for a in aliases} for field, aliases in spec.columns.items()}
    key_aliases = wanted[KEY_FIELD]
    seen_headers: list[str] = []

    for _sheet, grid in _raw_frames(data, filename):
        for idx in range(min(HEADER_SEARCH_ROWS, len(grid))):
            row = [_norm_header(v) for v in grid.iloc[idx].tolist()]
            if not key_aliases & set(row):
                continue
            mapping: dict[str, int] = {}
            for field, aliases in wanted.items():
                for pos, header in enumerate(row):
                    if header in aliases:
                        mapping[field] = pos
                        break
            missing = [f for f in wanted if f not in mapping]
            if missing:
                found = [str(v) for v in grid.iloc[idx].tolist() if str(v).strip()]
                raise SchemaError(
                    f"{spec.label} ('{filename}') is missing column(s) "
                    + ", ".join(
                        f"{f} (expected one of: {', '.join(spec.columns[f])})" for f in missing
                    )
                    + f". Found: {', '.join(found)}"
                )
            body = grid.iloc[idx + 1 :]
            out = pd.DataFrame(
                {field: body.iloc[:, pos].astype(str).str.strip() for field, pos in mapping.items()}
            )
            out.insert(0, "_row", [i + 1 for i in body.index])
            # Drop fully blank rows (trailing empty lines, spacer rows).
            out = out[(out.drop(columns="_row") != "").any(axis=1)]
            return out.reset_index(drop=True)
        if len(grid):
            seen_headers.extend(str(v) for v in grid.iloc[0].tolist() if str(v).strip())

    raise SchemaError(
        f"{spec.label} ('{filename}'): no header row with a short code column "
        f"(expected one of: {', '.join(spec.columns[KEY_FIELD])}). "
        f"First row seen: {', '.join(seen_headers[:12]) or '(empty)'}"
    )


def sanitize_cell(value: object) -> object:
    """Neutralise spreadsheet formula injection (CWE-1236)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def sanitize_frame(df: pd.DataFrame) -> pd.DataFrame:
    out: pd.DataFrame = df.apply(lambda col: col.map(sanitize_cell))
    return out


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return sanitize_frame(df).to_csv(index=False).encode("utf-8-sig")


def to_xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in sheets.items():
            sanitize_frame(df).to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for col_cells in ws.columns:
                width = max(
                    (len(str(c.value)) for c in col_cells if c.value is not None), default=8
                )
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(width + 2, 10), 60)
    return buffer.getvalue()
