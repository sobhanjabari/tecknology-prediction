# techintel — shared analysis helpers

The code that the notebooks and `Networks/Networks.py` all need, extracted from what used to be copy-pasted copies.

| Module | Contents |
|---|---|
| `wos.py` | Web of Science **tagged-format** parser (`parse_wos_file`), `C3` cleanup, country extraction, robust **CSV** loader (`read_wos_csv`, comma- or tab-delimited), raw-export normalisation (`normalize_wos_columns`), Excel export |
| `graphs.py` | GraphML load/save, giant-component extraction, `split_field` (`people` vs `codes`), `build_cooccurrence_graph` |
| `communities.py` | Louvain partitioning, modularity, community labelling, cliques, core/periphery, betweenness centrality |
| `metrics.py` | `v/e/p/u` counts, alpha/beta/gamma, degree stats & heterogeneity, average shortest path, triangles, transitivity, clustering, assortativity, `macro_analysis`, `summarize_graph` |
| `demo.py` | Seeded **synthetic WoS data** — no data files needed for a demo, a test or CI |
| `pipeline.py` | End-to-end orchestration: ingest → networks → giant component → communities + metrics → report bundle (CLI: `python -m techintel.pipeline`) |
| `verify.py` | Validates a pipeline output bundle (CLI: `python -m techintel.verify DIR`) |
| `tests/` | 238 offline tests |

## Usage

From a notebook (the bootstrap in each notebook's first cell does this for you):

```python
import sys
from pathlib import Path

# Walk up to the repository root, then import.
for candidate in (Path.cwd(), *Path.cwd().parents):
    if (candidate / "techintel" / "__init__.py").exists():
        sys.path.insert(0, str(candidate))
        break

from techintel import communities, graphs, metrics, wos
```

From a script:

```python
from techintel import graphs, metrics

g = graphs.load_graph("network.graphml", remove_self_loops=True)
print(metrics.summarize_graph(g, communities.detect_communities(g)))
```

## Dependencies

Only **`pandas`** and **`networkx`** are required.

| Optional | Effect if missing |
|---|---|
| `python-louvain` | `louvain_partition(backend="auto")` falls back to `networkx.community.louvain_communities`. Forcing `backend="python-louvain"` raises an actionable `ImportError`. |
| `openpyxl` | `wos.save_to_excel` raises an actionable `ImportError`; CSV output is unaffected. |

## Pipeline

`python -m techintel.pipeline` chains the stages the notebooks used to run by hand — the same
five networks `co_occurrence_network_analysis/` builds, reduced to their giant components, then
measured and clustered — and writes a self-describing report bundle.

```bash
# Synthetic data: nothing to download, fully reproducible.
python -m techintel.pipeline --demo 120 --out out/demo

# A real export. Format is detected from the content, not the extension.
python -m techintel.pipeline --input records.txt  --out out/run1
python -m techintel.pipeline --input records.csv  --out out/run1
python -m techintel.pipeline --input records.xlsx --out out/run1

# A subset of networks, and no giant-component reduction.
python -m techintel.pipeline --input records.txt --specs authors,keywords --no-giant
python -m techintel.pipeline --list-specs
```

Default networks, in build order:

| Name | Column | Separator |
|---|---|---|
| `authors` | `Authors` | `\|` |
| `keywords` | `Keywords` | `;` |
| `subject_categories` | `Subject_Category` | `;` |
| `countries` | `Countries` | `;` |
| `organisations` | `C3` | `;` |

`Authors` is `|`-separated in Web of Science while every other field uses `;`. Splitting authors
on `;` silently fuses each record's authors into one node, so the two are kept distinct.

### Raw WoS exports

A Web of Science **"Full Record" CSV export** does not use those column names or delimiters: it
carries long headers (`Author Keywords`, `WoS Categories`, `Affiliations`, `Publication Year`) and
separates authors and address blocks with `;`. Loading one as-is therefore builds only the
co-authorship network — with every author list collapsed into a single node — and skips the other
four, while still reporting success.

`load_records` runs every input through `wos.normalize_wos_columns`, which:

* renames the long headers onto the schema above (`Author Keywords` → `Keywords`,
  `WoS Categories` → `Subject_Category`, `Affiliations` → `C3`, …);
* re-joins `;`-delimited author lists and address blocks with the pipeline's delimiters;
* derives the two columns a raw export never carries, `Countries` and `C3`, from `Addresses`.

A column that is already named correctly is left untouched, so tagged exports and
already-normalised CSVs are unaffected.

### Output bundle

```
out/demo/
├── manifest.json          # machine-readable record of the whole run
├── metrics.csv            # one row per network: sizes, macro metrics, Louvain modularity
├── summary.txt            # summarize_graph output per network
└── graphml/
    ├── authors.graphml             # full graph, `community` node attribute attached
    ├── authors_giant.graphml       # giant component
    └── …                           # two files per network
```

The `community` attribute is written onto the nodes, so the exported GraphML can be dropped
straight into Gephi and coloured by community. Clustering runs on the giant component, so nodes
that fall outside it are not in any partition; they are given the sentinel value `-1`
(`pipeline.UNLABELLED_COMMUNITY`) rather than being exported with no `community` attribute at all,
which would show up in Gephi as a colourless "community". Paths inside `manifest.json` are relative
to the output directory, so the bundle stays valid when moved or uploaded as a CI artifact.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | every requested network was built, analysed and non-empty |
| `1` | partial run — a spec's column was absent, or every network came out empty |
| `2` | fatal — bad arguments, missing/unreadable input, or nothing to parse |

These match `Networks/Networks.py`, so the two compose in a shell, a Makefile or n8n.

### Verifying a bundle

```bash
python -m techintel.verify out/demo          # 0 = complete, 1 = inconsistent, 2 = missing
```

It checks that `metrics.csv`, `summary.txt` and every GraphML file referenced by `manifest.json`
actually exist, parse, and agree with the manifest. CI and the n8n workflow both use it.

## Tests

```bash
python -m pytest techintel/tests
python -m unittest discover -s techintel/tests -t .
```

Both runners are supported and no test touches the network or the filesystem outside a temp directory.

## Behaviour notes

These are deliberate divergences from the notebook copies this package replaced. Each is covered by a test.

- **`extract_country_from_address`** — the original did `parts[-1].strip()[:-1]`, eating the country's last character, so `"WA 98195 USA"` became `"WA 98195 US"` and the `USA` / `Turkey` normalisations never fired. Brackets are now stripped instead.
- **`build_cooccurrence_graph`** — de-duplicates items within a row (`dedupe=True`, the default). The notebook copy did not, so `"a; b; a"` produced a self-loop `a-a` and gave `a-b` weight 2. Pass `dedupe=False` for the old counts.
- **`alpha_beta_gamma`** — `alpha` requires `v >= 3` (the strictest of the three notebook variants; one of them returned a meaningless `0.0` at `v = 2`).
- **`detect_communities`** — returns `{}` for an empty *or edgeless* graph, because NetworkX modularity raises `ZeroDivisionError` on the latter.
- **`summarize_graph`** — guards the empty-graph case, where `nx.transitivity` raises.
- **`macro_analysis`** — undefined quantities are `nan` rather than `None`, so the dict feeds straight into a `pandas.DataFrame`.
- **`wos.parse_wos_file`** — the vestigial `ISI_Keywords` column (initialised to `""`, never read or written) is no longer emitted.
- **`wos.normalize_wos_columns`** — a raw Web of Science "Full Record" CSV export used to load without error and then build only the co-authorship network, with every author list fused into one node. Long headers are now renamed, `;`-delimited author and address fields are re-split into the pipeline's delimiters, and `Countries` / `C3` are derived from `Addresses`. Columns already named correctly are left alone.
- **`wos.read_wos_csv`** — reads with `low_memory=False`. The chunked default infers dtypes per chunk, so on a large export a page-number column produced a `DtypeWarning` and a dtype that depended on where the chunk boundaries fell.
- **Demo data** — institutions are now tied to themes, and one theme is deliberately separate from the rest. Previously institutions were drawn from the whole pool regardless of theme, so over a hundred records almost every pair co-occurred: the country and organisation graphs were complete (density `1.0`, modularity `0`) and *every* network was a single connected component, which made each `_giant.graphml` a byte-identical copy of its full graph.
- **`pipeline.UNLABELLED_COMMUNITY`** — nodes outside the giant component are not in the Louvain partition, so they used to be exported with no `community` attribute. They now carry the sentinel `-1`.
- **`communities.betweenness_pivots`** — the pivot count for approximate betweenness is now scaled to a `pivots * edges` budget (`BETWEENNESS_PIVOT_BUDGET`, 5 M edge visits, clamped to 20–200) instead of always being 200. On a real 49 000-record export the `authors` giant component has 65 000 nodes and 430 000 edges, where the fixed 200 pivots spent over 110 s in one call; 20 pivots takes 11.6 s. Graphs up to 500 nodes are still solved exactly, and anything under ~25 000 edges still gets the full 200 pivots, so the demo output is unchanged.
- **`metrics.summarize_graph`** — the average shortest path is only computed when the largest component has at most `AVERAGE_PATH_EXACT_NODES` (5 000) nodes. `nx.average_shortest_path_length` runs a BFS from *every* node, so on the same real export's `authors` component it is ~3e10 operations; the run used to sit there indefinitely. Above the cap the line reads `skipped (component has N nodes; …)` instead.
