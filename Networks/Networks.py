"""
Build patent collaboration/co-classification networks from a CSV file and write
GraphML plus structural summary reports for each graph.

Default behavior:
- Reads a CSV located next to this script (default name: acoustic sensor.csv)
- Writes outputs to ./results (next to this script)

Also builds three IPC co-classification networks for time slices:
- 2000–2010
- 2011–2020
- 2021–2025

Usage (run from the directory containing this file):
    python Networks.py
    python Networks.py --csv "acoustic sensor.csv"
    python Networks.py --csv "acoustic sensor.csv" --year-col "Publication Year"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable, Optional, Tuple

import pandas as pd

# Shared helpers live in techintel/ at the repository root (one level up).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from techintel import communities, graphs, metrics  # noqa: E402


def normalize_code(code: str) -> str:
    """Normalize classification codes by stripping whitespace."""
    if not code or not isinstance(code, str):
        return ""
    return code.strip()


def to_4digit_prefix(code: str) -> str:
    """Return the 4-char prefix portion of an IPC/CPC code (section+class), e.g., A61K."""
    normalized = normalize_code(code)
    if not normalized:
        return ""
    base = normalized.split("/")[0]
    alnum = "".join(ch for ch in base if ch.isalnum())
    return alnum[:4] if len(alnum) >= 4 else alnum


def find_year_column(df: pd.DataFrame) -> Optional[str]:
    """Heuristically find a year column in common Lens-like exports."""
    candidates = [
        "Publication Year",
        "Publication year",
        "Pub Year",
        "Year",
        "Priority Year",
        "Application Year",
        "Filing Year",
        "Grant Year",
    ]
    for c in candidates:
        if c in df.columns:
            return c
    return None


def coerce_year_series(df: pd.DataFrame, year_col: str) -> pd.Series:
    """Extract a 4-digit year from a column and convert to Int64."""
    s = df[year_col].astype(str).str.extract(r"(\d{4})", expand=False)
    return pd.to_numeric(s, errors="coerce").astype("Int64")


def filter_by_year_range(df: pd.DataFrame, year_col: str, start: int, end: int) -> pd.DataFrame:
    """Return df filtered to years in [start, end], inclusive."""
    years = coerce_year_series(df, year_col)
    mask = years.notna() & (years >= start) & (years <= end)
    return df.loc[mask].copy()


def ensure_output_dir(base: Path, out_name: str = "results") -> Path:
    out_dir = base / out_name
    out_dir.mkdir(exist_ok=True)
    return out_dir


def safe_slug(name: str) -> str:
    s = name.strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_()-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "network"


def save_graph(graph, out_dir: Path, prefix: str) -> Path:
    """Save a graph as GraphML in `out_dir`, named after `prefix`."""
    return graphs.save_graph(graph, out_dir / f"{safe_slug(prefix)}.graphml")


def save_summary(text: str, out_dir: Path, prefix: str) -> Path:
    path = out_dir / f"{safe_slug(prefix)}_summary.txt"
    path.write_text(text, encoding="utf-8")
    return path


def build_and_write(
    df: pd.DataFrame,
    *,
    out_dir: Path,
    label: str,
    column: str,
    split_kind: str,
    transform: Optional[Callable[[str], str]] = None,
    description: str = "",
    cluster_guard: Tuple[int, int] = (5000, 50000),
) -> None:
    """Build a graph, save immediately, then optionally compute clustering and overwrite summary."""
    graph = graphs.build_cooccurrence_graph(
        df,
        column,
        split_kind=split_kind,
        transform=transform,
        sanitize=True,
        description=description or label,
    )

    # Always save immediately (so outputs appear even if clustering is slow)
    save_graph(graph, out_dir, label)
    save_summary(metrics.summarize_graph(graph, cluster_info={}), out_dir, label)

    print(f"[OK] {label}: nodes={graph.number_of_nodes():,} edges={graph.number_of_edges():,} (saved)")

    # Optional clustering (guarded). `detect_communities` returns {} when the
    # graph is empty or exceeds either guard, so the guard logic lives there.
    max_nodes, max_edges = cluster_guard
    cluster_info = communities.detect_communities(graph, max_nodes=max_nodes, max_edges=max_edges)
    if cluster_info:
        save_summary(metrics.summarize_graph(graph, cluster_info), out_dir, label)
        print(f"     clustering: done (guard {max_nodes} nodes / {max_edges} edges)")
    else:
        print(f"     clustering: skipped (guard {max_nodes} nodes / {max_edges} edges)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="acoustic sensor.csv", help="CSV filename (relative to this script) or absolute path")
    p.add_argument("--out", default="results", help="Output folder name (created next to this script)")
    p.add_argument("--year-col", default="", help="Year column name (optional). If empty, auto-detect.")
    p.add_argument("--no-4digit", action="store_true", help="If set, do not build 4-digit IPC/CPC networks.")
    p.add_argument("--skip-clustering", action="store_true", help="Skip community detection entirely.")
    p.add_argument("--cluster-max-nodes", type=int, default=5000)
    p.add_argument("--cluster-max-edges", type=int, default=50000)
    return p.parse_args()


def main() -> int:
    """Run the pipeline.

    Returns a process exit code: ``0`` for a complete run, ``1`` when the
    time-sliced networks had to be skipped, and ``2`` for a fatal input problem
    (missing/empty CSV). Previously every skip path was a bare ``return``, so the
    script exited 0 even when it produced only part of the promised output.
    """
    args = parse_args()
    base = Path(__file__).resolve().parent

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = base / csv_path

    print(f"[INFO] Script dir: {base}")
    print(f"[INFO] CSV path:   {csv_path}")

    if not csv_path.exists():
        print(f"[ERROR] {csv_path} is required to build the networks.")
        print("        Place a CSV export next to this script, or pass --csv PATH.")
        return 2

    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        print(f"[ERROR] {csv_path} is empty (it has no header row), so nothing can be built.")
        print("        Re-export the CSV, or pass --csv PATH to a populated file.")
        return 2

    if df.empty:
        print(f"[ERROR] {csv_path} has a header but no data rows.")
        return 2

    out_dir = ensure_output_dir(base, args.out)

    print(f"[INFO] Output dir: {out_dir}")
    print(f"[INFO] Rows={len(df):,} Cols={len(df.columns)}")
    # print("[INFO] Columns:", list(df.columns))  # uncomment if you want to inspect headers

    cluster_guard = (args.cluster_max_nodes, args.cluster_max_edges)
    if args.skip_clustering:
        cluster_guard = (0, 0)  # forces skip

    # -----------------------------
    # Base networks
    # -----------------------------
    configs = [
        ("Inventor co-authorship", "Inventors", "people", None),
        ("Applicant collaboration", "Applicants", "people", None),
        ("Owner collaboration", "Owners", "people", None),
        ("IPC (full)", "IPCR Classifications", "codes", normalize_code),
        ("CPC (full)", "CPC Classifications", "codes", normalize_code),
    ]

    if not args.no_4digit:
        configs.extend(
            [
                ("IPC (4-digit)", "IPCR Classifications", "codes", to_4digit_prefix),
                ("CPC (4-digit)", "CPC Classifications", "codes", to_4digit_prefix),
            ]
        )

    for label, column, split_kind, transform in configs:
        build_and_write(
            df,
            out_dir=out_dir,
            label=label,
            column=column,
            split_kind=split_kind,
            transform=transform,
            description=label,
            cluster_guard=cluster_guard,
        )

    # -----------------------------
    # NEW: Time-sliced IPC networks
    # -----------------------------
    year_col = args.year_col.strip() or find_year_column(df)
    if not year_col:
        print("[WARN] No year column found. Skipping time-sliced IPC networks.")
        return 1
    if year_col not in df.columns:
        print(f"[WARN] Year column '{year_col}' not in CSV. Skipping time-sliced IPC networks.")
        return 1

    ipc_col = "IPCR Classifications"
    if ipc_col not in df.columns:
        print(f"[WARN] Missing '{ipc_col}'. Skipping time-sliced IPC networks.")
        return 1

    slices = [
        ("IPC (full) 2000-2010", 2000, 2010, normalize_code),
        ("IPC (full) 2011-2020", 2011, 2020, normalize_code),
        ("IPC (full) 2021-2025", 2021, 2025, normalize_code),
    ]
    if not args.no_4digit:
        slices.extend(
            [
                ("IPC (4-digit) 2000-2010", 2000, 2010, to_4digit_prefix),
                ("IPC (4-digit) 2011-2020", 2011, 2020, to_4digit_prefix),
                ("IPC (4-digit) 2021-2025", 2021, 2025, to_4digit_prefix),
            ]
        )

    print(f"[INFO] Using year column for slicing: {year_col}")
    for label, start, end, transform in slices:
        df_slice = filter_by_year_range(df, year_col, start, end)
        desc = f"{label} (years {start}-{end}, n={len(df_slice):,})"
        build_and_write(
            df_slice,
            out_dir=out_dir,
            label=label,
            column=ipc_col,
            split_kind="codes",
            transform=transform,
            description=desc,
            cluster_guard=cluster_guard,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
