"""Command line interface: ``aggtool run``, ``aggtool profiles``, ``aggtool serve``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aggtool import __version__
from aggtool.engine import aggregate_files
from aggtool.errors import AggToolError
from aggtool.profile import builtin_profiles, get_profile, load_profile_file


def _cmd_run(args: argparse.Namespace) -> int:
    profile = (
        load_profile_file(args.profile_file) if args.profile_file else get_profile(args.profile)
    )
    primary, lookup = Path(args.primary), Path(args.lookup)
    for path in (primary, lookup):
        if not path.is_file():
            raise AggToolError(f"File not found: {path}")
    result = aggregate_files(
        primary.read_bytes(), primary.name, lookup.read_bytes(), lookup.name, profile
    )
    out = Path(args.out)
    out.write_bytes(result.to_csv() if out.suffix.lower() == ".csv" else result.to_xlsx())
    if args.report:
        Path(args.report).write_text(json.dumps(result.report(), indent=2, default=str))
    s = result.summary
    print(
        f"Wrote {out}: {s['output_rows']} codes "
        f"({s['dropped_rows']} dropped, {s['no_lease_holder']} without a lease holder, "
        f"{s['not_on_carrier']} registry codes not on carrier)."
    )
    return 0


def _cmd_profiles(_: argparse.Namespace) -> int:
    for p in builtin_profiles().values():
        print(f"{p.name} (v{p.version}): {p.description}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from aggtool.api.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aggtool", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Aggregate two report files.")
    run.add_argument("--primary", required=True, help="Carrier report (e.g. TMO WMC SC Report)")
    run.add_argument("--lookup", required=True, help="Registry report (e.g. CSCA report)")
    run.add_argument("--out", default="combined.xlsx", help="Output .xlsx or .csv")
    run.add_argument("--report", help="Optional JSON audit report path")
    group = run.add_mutually_exclusive_group()
    group.add_argument("--profile", default="tmo-csca", help="Built-in profile name")
    group.add_argument("--profile-file", help="Path to a custom profile YAML")
    run.set_defaults(func=_cmd_run)

    sub.add_parser("profiles", help="List built-in profiles.").set_defaults(func=_cmd_profiles)

    serve = sub.add_parser("serve", help="Run the API and web UI locally.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AggToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
