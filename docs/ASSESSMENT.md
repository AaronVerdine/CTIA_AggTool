# Assessment of the original prototype (`AGG_Logic.py`)

**Verdict: the prototype does not meet the README's goals.** It reads the two
reports and builds a frame with the right four column names, but the data in
that frame is wrong, none of the required filtering exists, and nothing is
returned or exported. Roughly a third of the requirements are started; none is
finished correctly.

Every finding below was reproduced by running the original file against small
synthetic TMO and CSCA CSVs (no sample data was supplied with the project).

## Requirement by requirement

| README requirement | Status | Evidence |
|---|---|---|
| Combine the TMO list and CSCA report into one document | ❌ Not met | `Agg_input_logic()` calls `Agg_Logic()` but discards its result and returns `None`. No file is ever written. |
| Output columns `Short Code \| Aggregator \| Content Provider \| Lease Holder` | ⚠️ Partial | Column names are correct, but see the next row for the values. |
| Pair the files despite different column titles (TMO `Start Application Addresses` ↔ CSCA code column) | ❌ Wrong | A merge is computed (`merged_data_df`) and then ignored. `ComboList()` copies `Lease Holder` from CSCA **by row position** (`reset_index(drop=True)`), so row 1 of TMO gets row 1 of CSCA's company regardless of short code. In testing, code `22222` was assigned the lease holder of `666666`. |
| `Company` → `Lease Holder`, far right | ⚠️ Partial | Renamed and positioned correctly, but values are misaligned (above). |
| Remove duplicate short codes | ❌ Not implemented | TODO comment only. Duplicates pass through. |
| Remove 3, 4, 7, 8+ digit codes | ❌ Not implemented | TODO only. A 3-digit code passed through in testing. |
| Remove email addresses | ❌ Not implemented | Worse: if the TMO code column contains any email while CSCA codes are numeric, pandas raises `ValueError: You are trying to merge on int64 and str columns` and the tool crashes. |
| Remove codes with no Content Provider | ❌ Not implemented | TODO only. Blank providers pass through as `NaN`. |
| Sort and export as `.csv`/`.xlsx` | ❌ Not implemented | TODO only. |
| Works with the Excel files the README describes | ❌ Not met | Only `.csv` is read. |

## Code-quality and robustness issues

1. **Input validation uses `assert`.** Asserts are stripped under `python -O`, so the check silently disappears.
2. **File handles leak and request write access.** `open(path, 'r+')` is never closed or used, and requires write permission on a file the tool only reads.
3. **`index_col=0` is fragile.** If the code column happens to be the first column it becomes the index and the merge raises `KeyError`.
4. **Codes are not normalised.** Codes read as numbers lose leading zeros and Excel exports turn `22222` into `22222.0`; whitespace is not trimmed. Joins silently miss.
5. **Interactive `input()` only.** The logic cannot be called from a script, a test, a scheduler or a web service.
6. **No tests, no logging, no error messages a user can act on, no `__main__` entry point.**
7. **Hard-coded to two specific report layouts.** Any column rename in either report breaks the tool.

## What is worth keeping

The intent and the domain mapping are sound: the README is a clear, testable
specification, and the pandas-based approach is appropriate for files of this
size. The rebuild keeps both, and turns the README rules into an explicit,
versioned configuration profile with a unit test per rule.
