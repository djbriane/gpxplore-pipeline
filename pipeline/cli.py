"""Single documented entrypoint for every pipeline stage.

    python3 -m pipeline.cli <stage> [options]

Stages: fetch, normalize, merge, validate, compact, publish, pipeline (all),
and blm-verify (a research helper - see the plan's blm-endpoint-verify task).
The Makefile wraps these; see README.md.
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import common, compact, fetch, ios_snapshot, merge, normalize, publish, validate
from .registry import get_source


def _cmd_fetch(args) -> int:
    fetch.run(args.source, live=args.live)
    return 0


def _cmd_normalize(args) -> int:
    normalize.run(args.source, snapshot=args.snapshot)
    return 0


def _cmd_merge(args) -> int:
    merge.run(snapshot=args.snapshot)
    return 0


def _cmd_validate(args) -> int:
    report = validate.run(snapshot=args.snapshot, near_zero_drop=args.near_zero_drop)
    return 0 if report["ok"] else 1


def _cmd_compact(args) -> int:
    compact.run(snapshot=args.snapshot)
    return 0


def _cmd_publish(args) -> int:
    publish.run(snapshot=args.snapshot, target=args.target, confirm=args.confirm)
    return 0


def _cmd_ios_snapshot(args) -> int:
    summary = ios_snapshot.run(snapshot=args.snapshot)
    return 1 if summary["over_soft_ceiling"] else 0


def _cmd_pipeline(args) -> int:
    print("== fetch ==")
    fetch.run(live=args.live)
    print("== normalize ==")
    normalize.run()
    print("== merge ==")
    merge.run()
    print("== validate ==")
    report = validate.run(near_zero_drop=args.near_zero_drop)
    if not report["ok"]:
        print("\nValidation FAILED - stopping before compact/publish.", file=sys.stderr)
        return 1
    print("== compact ==")
    compact.run()
    if args.publish_target:
        print("== publish ==")
        publish.run(target=args.publish_target, confirm=args.confirm)
    print("\nPipeline complete. Review data/reports/ and data/compact/ before publishing.")
    return 0


def _cmd_blm_verify(args) -> int:
    """Diff the candidate BLM live endpoint against the offline snapshot.

    Confirms row count and field overlap before anyone flips BLM to live mode.
    Degrades gracefully if the network is unavailable.
    """
    from .fetch import arcgis

    src = get_source("blm")
    url = src.live.get("url")
    if not url:
        print("No BLM live url configured in registry.json.")
        return 1

    # Offline baseline.
    offline = src.offline_path
    with open(offline, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        offline_fields = set(reader.fieldnames or [])
        offline_rows = sum(1 for _ in reader)
    print(f"Offline snapshot: {offline.name}")
    print(f"  rows: {offline_rows}")
    print(f"  fields: {len(offline_fields)}")

    print(f"\nProbing live endpoint: {url}")
    try:
        live_count = arcgis.count_only(url)
        field_map = arcgis.layer_field_map(url)
    except Exception as exc:  # network/endpoint issues shouldn't crash the tool
        print(f"  ERROR: could not reach endpoint: {exc}")
        print("  Recommendation: keep BLM in offline/manual-file mode for now.")
        return 1

    live_names = {n for n, _a in field_map}
    live_aliases = {a for _n, a in field_map}
    print(f"  live feature count: {live_count}")
    print(f"  live fields: {len(field_map)}")

    # The CSV export headers are the field ALIASES; the live geojson properties
    # use machine field NAMES. Compare offline headers against both.
    matched = offline_fields & (live_names | live_aliases)
    missing = offline_fields - (live_names | live_aliases)
    print(f"\nHeader match (offline vs live names+aliases): {len(matched)} matched; "
          f"{len(missing)} unmatched")
    if missing:
        print(f"  unmatched offline headers: {sorted(missing)}")
    print("\nMachine-name -> alias mapping needed for a live adapter:")
    for name, alias in field_map:
        if alias in offline_fields and name != alias:
            print(f"    {name:16s} -> {alias}")

    count_close = abs(live_count - offline_rows) <= max(50, offline_rows * 0.05)
    fields_ok = len(matched) >= len(offline_fields) * 0.6
    if count_close and fields_ok:
        print("\nRESULT: endpoint matches the snapshot (row count + field aliases). "
              "The BLM adapter maps the machine field names above, so `--live` is "
              "safe (fetch.live.confirmed=true in registry.json).")
        return 0
    print("\nRESULT: endpoint does NOT clearly match the snapshot "
          f"(count_close={count_close}, fields_ok={fields_ok}). "
          "Keep BLM offline and investigate the sibling MapServer/3 layer.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    sub = p.add_subparsers(dest="stage", required=True)

    pf = sub.add_parser("fetch", help="obtain raw source files")
    pf.add_argument("--source", help="single source id (default: all)")
    pf.add_argument("--live", action="store_true", help="fetch from live ArcGIS endpoints")
    pf.set_defaults(func=_cmd_fetch)

    pn = sub.add_parser("normalize", help="normalize raw -> canonical geojson")
    pn.add_argument("--source", help="single source id (default: all)")
    pn.add_argument("--snapshot", help="snapshot date tag (default: today)")
    pn.set_defaults(func=_cmd_normalize)

    pm = sub.add_parser("merge", help="merge all sources into one snapshot")
    pm.add_argument("--snapshot", help="snapshot date tag (default: today)")
    pm.set_defaults(func=_cmd_merge)

    pv = sub.add_parser("validate", help="validate merged snapshot (non-zero exit on failure)")
    pv.add_argument("--snapshot", help="snapshot to validate (default: latest)")
    pv.add_argument("--near-zero-drop", type=float, default=validate.NEAR_ZERO_DROP_DEFAULT,
                    help="warn if a source loses more than this fraction of records")
    pv.set_defaults(func=_cmd_validate)

    pc = sub.add_parser("compact", help="build app-facing CampRecord[] files")
    pc.add_argument("--snapshot", help="snapshot to compact (default: latest)")
    pc.set_defaults(func=_cmd_compact)

    pp = sub.add_parser("publish", help="write reviewable artifact; --confirm to stage externally")
    pp.add_argument("--snapshot", help="snapshot to publish (default: latest)")
    pp.add_argument("--target", help="external target dir (e.g. app public/data)")
    pp.add_argument("--confirm", action="store_true", help="actually write to --target")
    pp.set_defaults(func=_cmd_publish)

    pi = sub.add_parser("ios-snapshot", help="build gpxplore-ios's gzipped marker/detail snapshot")
    pi.add_argument("--snapshot", help="compact snapshot to build from (default: latest)")
    pi.set_defaults(func=_cmd_ios_snapshot)

    pl = sub.add_parser("pipeline", help="run fetch->normalize->merge->validate->compact")
    pl.add_argument("--live", action="store_true", help="fetch live where confirmed")
    pl.add_argument("--near-zero-drop", type=float, default=validate.NEAR_ZERO_DROP_DEFAULT)
    pl.add_argument("--publish-target", help="optionally stage to this dir after compact")
    pl.add_argument("--confirm", action="store_true", help="confirm external publish")
    pl.set_defaults(func=_cmd_pipeline)

    pb = sub.add_parser("blm-verify", help="diff candidate BLM live endpoint vs offline snapshot")
    pb.set_defaults(func=_cmd_blm_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
