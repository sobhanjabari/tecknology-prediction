"""Validate a pipeline output bundle.

``python -m techintel.pipeline`` writes ``manifest.json``, ``metrics.csv``,
``summary.txt`` and a ``graphml/`` directory. This module checks that a bundle is
actually complete and internally consistent: the report files agree with the
manifest, every GraphML file parses, and no network came out empty.

It is what CI and the n8n workflow assert on, and it is safe to run by hand after
a long pipeline run::

    python -m techintel.verify out/demo

Exit codes: ``0`` the bundle is complete, ``1`` at least one problem was found,
``2`` the output directory or manifest is missing entirely.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import networkx as nx
import pandas as pd

__all__ = ["EXIT_OK", "EXIT_INVALID", "EXIT_MISSING", "verify_bundle", "main"]

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_MISSING = 2

#: Keys every manifest must carry.
_REQUIRED_MANIFEST_KEYS = ("tool", "records", "networks", "exit_code", "skipped_networks")

#: Keys every network entry must carry.
_REQUIRED_NETWORK_KEYS = ("name", "column", "nodes", "edges")


def _resolve(root: Path, raw: Optional[str]) -> Optional[Path]:
    """Resolve a manifest path against the bundle root.

    Manifest paths are stored relative to the output directory (see
    :func:`techintel.pipeline._relative_to`), but an absolute path is honoured
    too so bundles produced by older runs still validate.
    """
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else root / path


def verify_bundle(out_dir: Union[str, Path]) -> Tuple[List[str], Dict[str, Any]]:
    """Check a pipeline output bundle.

    Returns ``(problems, summary)``. ``problems`` is empty when the bundle is
    complete; ``summary`` carries the headline numbers for reporting.
    """
    root = Path(out_dir)
    summary: Dict[str, Any] = {"out_dir": str(root)}

    if not root.is_dir():
        return [f"output directory not found: {root}"], summary

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return [f"manifest.json not found in {root}"], summary
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json is not valid JSON: {exc}"], summary
    if not isinstance(manifest, dict):
        return ["manifest.json must contain a JSON object"], summary

    problems: List[str] = []
    for key in _REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            problems.append(f"manifest is missing the '{key}' key")

    summary["tool"] = manifest.get("tool")
    summary["records"] = manifest.get("records")
    summary["demo"] = manifest.get("demo")

    networks = manifest.get("networks") or []
    if not isinstance(networks, list):
        return problems + ["manifest 'networks' must be a list"], summary
    if not networks:
        problems.append("manifest lists no networks")
    summary["networks"] = len(networks)

    skipped = manifest.get("skipped_networks") or []
    if skipped:
        problems.append(f"pipeline reported skipped networks: {skipped}")

    empty = [entry.get("name") for entry in networks if not (entry.get("nodes") and entry.get("edges"))]
    if empty:
        problems.append(f"network(s) came out empty: {empty}")
    summary["empty_networks"] = empty

    # ---------------------------------------------------------- metrics.csv
    metrics_path = root / "metrics.csv"
    if not metrics_path.is_file():
        problems.append("metrics.csv not found")
    else:
        try:
            metrics = pd.read_csv(metrics_path)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            problems.append(f"metrics.csv could not be parsed: {exc}")
        else:
            if len(metrics) != len(networks):
                problems.append(
                    f"metrics.csv has {len(metrics)} data row(s) but the manifest "
                    f"lists {len(networks)} network(s)"
                )
            missing_columns = {"network", "nodes", "edges"} - set(metrics.columns)
            if missing_columns:
                problems.append(f"metrics.csv is missing column(s): {sorted(missing_columns)}")
            summary["metrics_rows"] = int(len(metrics))

    # ----------------------------------------------------------- summary.txt
    summary_path = root / "summary.txt"
    if not summary_path.is_file():
        problems.append("summary.txt not found")
    elif not summary_path.read_text(encoding="utf-8").strip():
        problems.append("summary.txt is empty")

    # -------------------------------------------------------------- GraphML
    checked = 0
    for entry in networks:
        if not isinstance(entry, dict):
            problems.append(f"network entry is not an object: {entry!r}")
            continue
        name = entry.get("name", "?")
        for key in _REQUIRED_NETWORK_KEYS:
            if key not in entry:
                problems.append(f"network '{name}' is missing the '{key}' key")
        for key in ("graphml", "giant_graphml"):
            path = _resolve(root, entry.get(key))
            if path is None:
                continue
            if not path.is_file():
                problems.append(f"network '{name}': {key} file not found at {path}")
                continue
            try:
                graph = nx.read_graphml(path)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                problems.append(f"network '{name}': {path.name} is not readable GraphML: {exc}")
                continue
            if graph.number_of_nodes() == 0:
                problems.append(f"network '{name}': {path.name} contains no nodes")
            checked += 1
    summary["graphml_files"] = checked

    return problems, summary


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""
    return argparse.ArgumentParser(
        prog="python -m techintel.verify",
        description=(
            "Check that an output bundle written by `python -m techintel.pipeline` "
            "is complete and internally consistent."
        ),
        epilog="Exit codes: 0 = complete, 1 = problems found, 2 = bundle missing.",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = build_parser()
    parser.add_argument(
        "out_dir", metavar="DIR",
        help="output directory produced by `python -m techintel.pipeline`",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="print nothing on success",
    )
    args = parser.parse_args(argv)

    problems, summary = verify_bundle(args.out_dir)

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        print(f"\n{len(problems)} problem(s) found in {args.out_dir}", file=sys.stderr)
        root = Path(args.out_dir)
        # A missing bundle is a different failure from an inconsistent one, and
        # n8n/CI branch on the difference.
        if not root.is_dir() or not (root / "manifest.json").is_file():
            return EXIT_MISSING
        return EXIT_INVALID

    if not args.quiet:
        print(f"[OK] Bundle is complete: {summary['out_dir']}")
        print(f"     tool    : {summary.get('tool')}")
        print(f"     records : {summary.get('records')}")
        print(
            f"     networks: {summary.get('networks')} "
            f"({summary.get('graphml_files')} GraphML file(s) parsed)"
        )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
