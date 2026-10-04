"""End-to-end analysis pipeline: WoS/CSV records -> networks -> metrics -> report.

This is the orchestration layer that the four analysis notebooks performed by
hand. Each stage is one of the :mod:`techintel` primitives, wired in the order
the notebooks chained them:

1. **Ingest**   - :func:`load_records` (tagged ``.txt``, delimited ``.txt``/``.csv``, ``.xlsx``)
2. **Build**    - :func:`techintel.graphs.build_cooccurrence_graph`, one graph per :class:`NetworkSpec`
3. **Reduce**   - :func:`techintel.graphs.get_giant_component` (optional, per network)
4. **Analyze**  - :func:`techintel.metrics.macro_analysis` +
   :func:`techintel.communities.detect_communities`
5. **Report**   - GraphML per network, ``metrics.csv``, ``summary.txt``, ``manifest.json``

Run it as a module::

    python -m techintel.pipeline --input records.txt --out out/run1
    python -m techintel.pipeline --demo 80 --out out/demo

Exit codes match ``Networks/Networks.py`` so the pipeline is safe to chain in a
shell, a Makefile or n8n:

* ``0`` - every requested network was built and analysed
* ``1`` - a partial run: some specs were skipped (missing column) or every
  network came out empty
* ``2`` - fatal: bad arguments, missing/unreadable input, or nothing to parse
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import networkx as nx
import pandas as pd

from . import communities, demo, graphs, metrics, wos

__all__ = [
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_FATAL",
    "UNLABELLED_COMMUNITY",
    "NetworkSpec",
    "NetworkResult",
    "PipelineResult",
    "DEFAULT_NETWORK_SPECS",
    "load_records",
    "build_networks",
    "analyze_graph",
    "run_pipeline",
    "main",
]

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2

#: ``community`` value given to nodes that fall outside the giant component.
#: Clustering runs on the giant component, so those nodes are not in the
#: partition; without a sentinel the full GraphML would export them unlabelled
#: and Gephi would render them as a colourless "community".
UNLABELLED_COMMUNITY = -1


@dataclass(frozen=True)
class NetworkSpec:
    """One co-occurrence network to build.

    ``separator`` is the intra-cell delimiter. Note that WoS ``Authors`` is
    ``|``-separated while almost every other field uses ``;`` - using the wrong
    one silently yields a single mega-node per record.
    """

    name: str
    column: str
    separator: str = ";"
    description: str = ""


#: The networks the notebooks build, in the order they build them.
DEFAULT_NETWORK_SPECS: Tuple[NetworkSpec, ...] = (
    NetworkSpec("authors", "Authors", "|", "Author co-authorship"),
    NetworkSpec("keywords", "Keywords", ";", "Keyword co-occurrence"),
    NetworkSpec("subject_categories", "Subject_Category", ";", "WoS subject-category co-occurrence"),
    NetworkSpec("countries", "Countries", ";", "Country collaboration"),
    NetworkSpec("organisations", "C3", ";", "Organisation co-affiliation"),
)

#: Files above this many bytes are not scanned when sniffing the input format.
_SNIFF_BYTES = 65536


def _jsonable(value: Any) -> Any:
    """Recursively convert a value into something ``json.dump`` accepts.

    ``nan``/``inf`` become ``None`` (JSON has no NaN literal), NumPy scalars
    become Python scalars, and sets become sorted lists.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    # pandas / numpy scalars
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item())
        except (ValueError, TypeError):  # pragma: no cover - defensive
            pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return str(value)


def _relative_to(path: Optional[Path], root: Path) -> Optional[str]:
    """Render a manifest path relative to ``root`` so the bundle is portable.

    Absolute (or cwd-relative) paths in a manifest break as soon as the bundle is
    uploaded as a CI artifact or opened from a different directory, so every path
    written into ``manifest.json`` is stored relative to the output directory.
    """
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - path deliberately outside the bundle
        return path.as_posix()


def _looks_tagged(text: str) -> bool:
    """True when ``text`` looks like a WoS **tagged** export rather than a table.

    A tagged line is a two-character tag in column 0 followed by a space, e.g.
    ``PT J`` or ``AF Piwowar, Heather``. A delimited export's header row starts
    with a column *name*, which in practice is longer than two characters, so
    requiring the space at index 2 separates the two formats reliably.
    """
    checked = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line) >= 3 and line[0].isalpha() and line[2] == " " and line[1].isalnum():
            return True
        checked += 1
        if checked >= 20:
            break
    return False



def _sniff_separator(head: str) -> str:
    """Guess the delimiter of a table export from its header row."""
    first_line = next((line for line in head.splitlines() if line.strip()), "")
    return "\t" if first_line.count("\t") > first_line.count(",") else ","


def load_records(path: Union[str, Path], *, encoding: str = "utf-8") -> pd.DataFrame:
    """Read a bibliographic export into a DataFrame.

    The format is detected from the content, not just the extension, because WoS
    exports both tagged and tab-delimited data as ``.txt``:

    * ``.xlsx`` / ``.xls``  -> ``pandas.read_excel``
    * tagged ``.txt``       -> :func:`techintel.wos.parse_wos_file`
    * anything else         -> :func:`techintel.wos.read_wos_csv` with a sniffed delimiter

    The result is then passed through :func:`techintel.wos.normalize_wos_columns`,
    so a raw WoS "Full Record" export (long headers, ``;``-delimited authors and
    addresses) yields the same schema as a tagged export.

    Raises ``FileNotFoundError`` for a missing file and ``ValueError`` when the
    file parses to zero records.
    """
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(
            f"Input file not found: {source}\n"
            f"Pass an existing WoS/CSV export via --input, or use --demo to "
            f"generate synthetic records."
        )

    if source.suffix.lower() in (".xlsx", ".xls"):
        data = pd.read_excel(source)
    else:
        with open(source, "r", encoding=encoding, errors="replace") as handle:
            head = handle.read(_SNIFF_BYTES)
        if _looks_tagged(head):
            data = wos.parse_wos_file(source, encoding=encoding)
        else:
            data = wos.read_wos_csv(source, sep=_sniff_separator(head))

    if data.empty:
        raise ValueError(
            f"{source} was read successfully but contains no records. "
            f"Check that it is a complete export (a truncated or header-only "
            f"file has nothing to analyse)."
        )

    # A raw WoS "Full Record" export names its columns differently and uses ";"
    # where the pipeline splits on "|". Normalising here means every caller gets
    # the same schema, instead of silently building one network out of five.
    return wos.normalize_wos_columns(data)


def build_networks(
    data: pd.DataFrame,
    specs: Sequence[NetworkSpec] = DEFAULT_NETWORK_SPECS,
    *,
    dedupe: bool = True,
    sanitize: bool = True,
) -> Tuple[Dict[str, nx.Graph], List[str]]:
    """Build one co-occurrence graph per spec.

    Returns ``(graphs, skipped)`` where ``skipped`` lists the spec names whose
    column is absent from ``data``. ``dedupe`` and ``sanitize`` are forwarded to
    :func:`techintel.graphs.build_cooccurrence_graph`.
    """
    built: Dict[str, nx.Graph] = {}
    skipped: List[str] = []
    for spec in specs:
        if spec.column not in data.columns:
            skipped.append(spec.name)
            continue
        built[spec.name] = graphs.build_cooccurrence_graph(
            data,
            spec.column,
            separator=spec.separator,
            dedupe=dedupe,
            sanitize=sanitize,
            name=spec.name,
            description=spec.description or spec.column,
        )
    return built, skipped


def analyze_graph(
    graph: nx.Graph,
    *,
    cluster_guard: Tuple[Optional[int], Optional[int]] = (5000, 20000),
    seed: int = 42,
    top_n: int = 10,
    weight: str = "weight",
) -> Dict[str, Any]:
    """Run the metrics + community stage on a single graph.

    ``cluster_guard`` is ``(max_nodes, max_edges)``; community detection is
    skipped (returning ``{}``) above either bound, because the modularity passes
    are superlinear. Betweenness centrality is exact for small graphs and pivoted
    (``k``) for larger ones, for the same reason - see
    :func:`techintel.communities.betweenness_pivots`.
    """
    max_nodes, max_edges = cluster_guard
    node_count = graph.number_of_nodes()

    if node_count == 0:
        top_nodes: List[Tuple[Any, int]] = []
        top_bridges: List[Tuple[Any, float]] = []
    else:
        top_nodes = sorted(graph.degree, key=lambda item: item[1], reverse=True)[:top_n]
        top_bridges = communities.top_bridges(
            graph,
            n=top_n,
            k=communities.betweenness_pivots(graph),
            seed=seed,
        )

    return {
        "metrics": metrics.macro_analysis(graph),
        "communities": communities.detect_communities(
            graph, weight=weight, seed=seed, max_nodes=max_nodes, max_edges=max_edges
        ),
        "top_nodes": [(str(node), int(degree)) for node, degree in top_nodes],
        "top_bridges": [(str(node), float(score)) for node, score in top_bridges],
    }


@dataclass
class NetworkResult:
    """Everything the pipeline produced for one network."""

    name: str
    column: str
    description: str
    graph: nx.Graph
    giant: Optional[nx.Graph]
    analysis: Dict[str, Any]
    summary: str
    graphml_path: Optional[Path] = None
    giant_graphml_path: Optional[Path] = None

    @property
    def target(self) -> nx.Graph:
        """The graph the analysis actually ran on (the giant component if enabled)."""
        return self.giant if self.giant is not None else self.graph

    @property
    def usable(self) -> bool:
        """A network with no edges has nothing to measure."""
        return self.target.number_of_edges() > 0


@dataclass
class PipelineResult:
    """The outcome of a whole run."""

    out_dir: Path
    records: int
    networks: List[NetworkResult] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    input_path: Optional[Path] = None
    demo: bool = False
    manifest_path: Optional[Path] = None
    metrics_path: Optional[Path] = None
    summary_path: Optional[Path] = None

    @property
    def usable(self) -> List[NetworkResult]:
        return [network for network in self.networks if network.usable]

    @property
    def exit_code(self) -> int:
        """``0`` clean run, ``1`` partial (nothing usable, or specs skipped)."""
        if not self.usable or self.skipped:
            return EXIT_PARTIAL
        return EXIT_OK


def _write_metrics(out_dir: Path, results: Sequence[NetworkResult]) -> Path:
    """Write one row per network: graph size, macro metrics and Louvain summary."""
    rows: List[Dict[str, Any]] = []
    for result in results:
        target = result.target
        row: Dict[str, Any] = {
            "network": result.name,
            "column": result.column,
            "description": result.description,
            "nodes": target.number_of_nodes(),
            "edges": target.number_of_edges(),
            "source_nodes": result.graph.number_of_nodes(),
            "source_edges": result.graph.number_of_edges(),
        }
        row.update(result.analysis["metrics"])
        louvain = result.analysis["communities"].get("louvain", {})
        row["community_count"] = louvain.get("count")
        row["modularity"] = louvain.get("modularity")
        rows.append(row)

    path = out_dir / "metrics.csv"
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False)
    else:
        # Keep the header so downstream tooling never has to special-case an
        # empty result set.
        path.write_text("network,nodes,edges\n", encoding="utf-8")
    return path


def _write_summary(
    out_dir: Path,
    results: Sequence[NetworkResult],
    skipped: Sequence[str],
    *,
    records: int,
    source_label: str,
) -> Path:
    """Write the human-readable report: one ``summarize_graph`` block per network."""
    lines = [
        "Technology-intelligence pipeline summary",
        "=" * 70,
        f"Source          : {source_label}",
        f"Records         : {records}",
        f"Networks built  : {len(results)}",
        f"Networks skipped: {len(skipped)}",
    ]
    if skipped:
        lines.append(f"  (column absent for: {', '.join(skipped)})")
    lines.append("")

    for result in results:
        lines.append("-" * 70)
        lines.append(
            f"[{result.name}] {result.description} "
            f"(column '{result.column}', giant={'yes' if result.giant is not None else 'no'})"
        )
        lines.append("-" * 70)
        lines.append(result.summary)
        lines.append("")

    path = out_dir / "summary.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_pipeline(
    input_path: Optional[Union[str, Path]] = None,
    out_dir: Union[str, Path] = "out",
    *,
    specs: Sequence[NetworkSpec] = DEFAULT_NETWORK_SPECS,
    giant: bool = True,
    cluster_guard: Tuple[Optional[int], Optional[int]] = (5000, 20000),
    seed: int = 42,
    top_n: int = 10,
    demo_count: Optional[int] = None,
) -> PipelineResult:
    """Run every stage and write the report bundle to ``out_dir``.

    Pass exactly one of ``input_path`` (a real export) or ``demo_count``
    (generate that many synthetic records). Returns a :class:`PipelineResult`;
    inspect :attr:`PipelineResult.exit_code` for the shell exit status.

    Outputs, all under ``out_dir``:

    ``graphml/<name>.graphml``
        The full co-occurrence graph, with a ``community`` node attribute
        attached so Gephi can colour by community directly.
    ``graphml/<name>_giant.graphml``
        The giant component, when ``giant`` is true.
    ``metrics.csv``
        One row per network: sizes, macro metrics and Louvain modularity.
    ``summary.txt``
        ``summarize_graph`` output per network.
    ``manifest.json``
        Machine-readable record of the whole run, for CI and n8n to assert on.
    """
    if (input_path is None) == (demo_count is None):
        raise ValueError("pass exactly one of input_path or demo_count")

    out_path = Path(out_dir)
    graphml_dir = out_path / "graphml"
    graphml_dir.mkdir(parents=True, exist_ok=True)

    if demo_count is not None:
        data = demo.demo_dataframe(demo_count, seed=seed)
        source_path: Optional[Path] = None
        source_label = f"synthetic demo data (n={demo_count}, seed={seed})"
    else:
        source_path = Path(input_path)  # type: ignore[arg-type]
        data = load_records(source_path)
        source_label = str(source_path)

    built, skipped = build_networks(data, specs)

    results: List[NetworkResult] = []
    for spec in specs:
        graph = built.get(spec.name)
        if graph is None:
            continue

        giant_graph = graphs.get_giant_component(graph) if giant else None
        target = giant_graph if giant_graph is not None else graph
        analysis = analyze_graph(target, cluster_guard=cluster_guard, seed=seed, top_n=top_n)

        # Label both copies so the exported GraphML carries communities.
        partition = communities.louvain_partition(target, seed=seed)
        if partition:
            communities.label_communities(graph, partition)
            if giant_graph is not None:
                communities.label_communities(giant_graph, partition)
            # Clustering ran on the giant component, so anything outside it is
            # absent from the partition. Give those nodes a sentinel rather than
            # exporting them with no `community` attribute at all.
            for node in graph.nodes:
                graph.nodes[node].setdefault("community", UNLABELLED_COMMUNITY)

        result = NetworkResult(
            name=spec.name,
            column=spec.column,
            description=spec.description or spec.column,
            graph=graph,
            giant=giant_graph,
            analysis=analysis,
            summary=metrics.summarize_graph(target, analysis["communities"]),
            graphml_path=graphs.save_graph(graph, graphml_dir / f"{spec.name}.graphml"),
        )
        if giant_graph is not None:
            result.giant_graphml_path = graphs.save_graph(
                giant_graph, graphml_dir / f"{spec.name}_giant.graphml"
            )
        results.append(result)

    pipeline_result = PipelineResult(
        out_dir=out_path,
        records=int(len(data)),
        networks=results,
        skipped=skipped,
        input_path=source_path,
        demo=demo_count is not None,
    )
    pipeline_result.metrics_path = _write_metrics(out_path, results)
    pipeline_result.summary_path = _write_summary(
        out_path, results, skipped, records=len(data), source_label=source_label
    )

    manifest = {
        "tool": "techintel.pipeline",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source_label,
        "demo": pipeline_result.demo,
        "seed": seed,
        "giant_component": giant,
        "records": len(data),
        "exit_code": pipeline_result.exit_code,
        "skipped_networks": list(skipped),
        "networks": [
            {
                "name": result.name,
                "column": result.column,
                "description": result.description,
                "nodes": result.target.number_of_nodes(),
                "edges": result.target.number_of_edges(),
                "source_nodes": result.graph.number_of_nodes(),
                "source_edges": result.graph.number_of_edges(),
                "graphml": _relative_to(result.graphml_path, out_path),
                "giant_graphml": _relative_to(result.giant_graphml_path, out_path),
                "analysis": result.analysis,
            }
            for result in results
        ],
    }
    manifest_path = out_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(_jsonable(manifest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    pipeline_result.manifest_path = manifest_path
    return pipeline_result


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser (separate so tests can introspect it)."""
    parser = argparse.ArgumentParser(
        prog="python -m techintel.pipeline",
        description=(
            "Run the full technology-intelligence analysis pipeline: parse a "
            "bibliographic export, build co-occurrence networks, reduce them to "
            "their giant components, detect communities, compute topology "
            "metrics and write a report bundle."
        ),
        epilog=(
            "Examples:\n"
            "  python -m techintel.pipeline --input records.txt --out out/run1\n"
            "  python -m techintel.pipeline --demo 80 --out out/demo\n"
            "  python -m techintel.pipeline --demo --specs authors,keywords\n"
            "\n"
            "Exit codes: 0 = ok, 1 = partial run, 2 = fatal."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i", metavar="PATH",
        help="WoS tagged .txt, delimited .csv/.txt, or .xlsx export",
    )
    parser.add_argument(
        "--demo", nargs="?", const=60, default=None, type=int, metavar="N",
        help="generate N synthetic records instead of reading --input (default N=60)",
    )
    parser.add_argument(
        "--out", "-o", default="out", metavar="DIR",
        help="output directory (default: out)",
    )
    parser.add_argument(
        "--specs", metavar="LIST",
        help="comma-separated subset of network names (default: all of them)",
    )
    parser.add_argument(
        "--list-specs", action="store_true",
        help="print the available networks and exit",
    )
    parser.add_argument(
        "--no-giant", action="store_true",
        help="analyse the full graphs instead of their giant components",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="RNG seed for Louvain and pivoted betweenness (default: 42)",
    )
    parser.add_argument(
        "--top-n", type=int, default=10,
        help="how many top nodes / bridges to record per network (default: 10)",
    )
    parser.add_argument(
        "--cluster-max-nodes", type=int, default=5000,
        help="skip community detection above this node count (default: 5000)",
    )
    parser.add_argument(
        "--cluster-max-edges", type=int, default=20000,
        help="skip community detection above this edge count (default: 20000)",
    )
    parser.add_argument(
        "--skip-clustering", action="store_true",
        help="skip community detection entirely (metrics only)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_specs:
        print("Available networks:")
        for spec in DEFAULT_NETWORK_SPECS:
            print(
                f"  {spec.name:<20} column={spec.column!r:<20} "
                f"separator={spec.separator!r}  {spec.description}"
            )
        return EXIT_OK

    specs: Sequence[NetworkSpec] = DEFAULT_NETWORK_SPECS
    if args.specs:
        known = {spec.name: spec for spec in DEFAULT_NETWORK_SPECS}
        requested = [name.strip() for name in args.specs.split(",") if name.strip()]
        unknown = [name for name in requested if name not in known]
        if unknown:
            print(f"[ERROR] Unknown network name(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"        Available: {', '.join(known)}", file=sys.stderr)
            return EXIT_FATAL
        specs = tuple(known[name] for name in requested)

    # An empty --input (e.g. `make pipeline` with INPUT left unset) counts as
    # absent, so the user gets the "pass exactly one of" message rather than a
    # confusing "file not found: ." from the loader.
    if bool(args.input) == (args.demo is not None):
        parser.print_usage(sys.stderr)
        print("[ERROR] pass exactly one of --input PATH or --demo [N]", file=sys.stderr)
        return EXIT_FATAL

    cluster_guard = (
        (0, 0) if args.skip_clustering else (args.cluster_max_nodes, args.cluster_max_edges)
    )

    try:
        result = run_pipeline(
            input_path=args.input or None,
            out_dir=args.out,
            specs=specs,
            giant=not args.no_giant,
            cluster_guard=cluster_guard,
            seed=args.seed,
            top_n=args.top_n,
            demo_count=args.demo,
        )
    except (FileNotFoundError, ValueError, ImportError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_FATAL

    print(f"[INFO] Records parsed : {result.records}")
    print(f"[INFO] Output dir     : {result.out_dir}")
    print("")
    header = f"  {'network':<20}{'nodes':>8}{'edges':>8}{'communities':>13}{'modularity':>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for network in result.networks:
        louvain = network.analysis["communities"].get("louvain", {})
        count = louvain.get("count", "-")
        modularity = louvain.get("modularity")
        modularity_text = f"{modularity:.4f}" if isinstance(modularity, float) else "-"
        print(
            f"  {network.name:<20}{network.target.number_of_nodes():>8}"
            f"{network.target.number_of_edges():>8}{str(count):>13}{modularity_text:>12}"
        )

    if result.skipped:
        print("")
        print(f"[WARN] Skipped (column absent): {', '.join(result.skipped)}")

    per_network = 1 if args.no_giant else 2
    print("")
    print(
        f"[INFO] Wrote {len(result.networks) * per_network} GraphML file(s) "
        f"+ metrics.csv, summary.txt, manifest.json"
    )
    print(f"[INFO] Manifest: {result.manifest_path}")

    if result.exit_code == EXIT_OK:
        print("[OK] Pipeline completed.")
    else:
        print("[WARN] Pipeline completed with gaps - see the warnings above.")
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
