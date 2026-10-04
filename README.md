# Technology Prediction

A collection of Jupyter notebooks and Python scripts for **technology intelligence, forecasting, and bibliometric/scientometric analysis**. The project ingests bibliographic data (Web of Science exports, patent data, etc.), builds co-occurrence and collaboration networks, applies topic modeling and NLP preprocessing, and performs trend forecasting and SDG (Sustainable Development Goal) classification to support technology-prediction research.

## Project Structure

```
tecknology prediction/
├── BERTopic/                                          # Topic modeling with BERTopic
├── co_occurrence_network_analysis/                    # WoS bibliometric co-occurrence networks
├── Giant to macro analysis/                           # Macro-level community structure metrics
├── network_topology_metrics_analysis/                 # Network topology metrics & visualization
├── Networks/                                          # Patent collaboration/co-classification networks
├── NLP/                                                # Text preprocessing pipeline
├── SDG/                                                # SDG classification of WoS records
├── openalex/                                           # OpenAlex API CLI: harvest scholarly data
├── techintel/                                          # Shared library + end-to-end pipeline
├── n8n/                                                # n8n workflow: scheduled pipeline + verification
├── .github/workflows/ci.yml                            # CI: tests, pipeline run, Makefile targets
├── Trend Analysis and Forecasting for Technology Intelligence/  # Trend/forecasting models
├── Makefile                                            # Task runner (GNU make)
├── tasks.ps1                                           # Task runner (Windows, mirrors the Makefile)
├── requirements.txt                                    # Superset of notebook/script dependencies
└── .gitignore                                          # Generated outputs, caches, local config
```

## Modules

### 1. `BERTopic/`
`BERTopic.ipynb` — Topic modeling on a corpus (e.g., quantum-technology literature) using [BERTopic](https://maartengr.github.io/BERTopic/), which combines BERT embeddings, UMAP dimensionality reduction, HDBSCAN clustering, and class-based TF-IDF to produce interpretable topic clusters with keyword representations.

### 2. `co_occurrence_network_analysis/`
Builds and analyzes five bibliometric co-occurrence networks from a Web of Science export: **country collaboration, co-authorship, institutional co-affiliation, subject-category overlap, and keyword/technology co-occurrence**. The WoS parsing and graph helpers now come from [`techintel/`](#shared-library-techintel) rather than being defined in the notebook. See the module's own [README](co_occurrence_network_analysis/README.md) for full details, required inputs, and known bug fixes vs. the original notebook.

### 3. `Giant to macro analysis/`
`Giant to macro analysis.ipynb` — Loads a GraphML network, runs Louvain community detection, and computes **macro-structural metrics** (alpha/beta/gamma indices, average degree, heterogeneity, transitivity, assortativity, triangle count, isolated nodes) for each detected community, summarized in a pandas DataFrame. Metrics and community detection come from `techintel.metrics` and `techintel.communities`.

### 4. `network_topology_metrics_analysis/`
`network_topology_metrics_analysis.ipynb` — Loads a GraphML file and computes a comprehensive set of network-science topology metrics (structural counts, alpha/beta/gamma indices, global transitivity vs. average clustering coefficient, degree distribution, etc.), then visualizes the graph and its degree distribution. All metrics come from `techintel.metrics`.

### 5. `Networks/`
`Networks.py` — A standalone Python script that builds **patent collaboration and co-classification networks** from a CSV export (e.g., Lens.org patent data): inventor co-authorship, applicant/owner collaboration, and IPC/CPC co-classification (full and 4-digit prefix), including time-sliced IPC networks (2000–2010, 2011–2020, 2021–2025). Outputs GraphML files and structural summary reports (density, transitivity, clustering, connected components, top-degree nodes) to a `results/` folder. Graph building, clustering and summaries come from `techintel`.

Run it via:
```bash
python "Networks/Networks.py" --csv "acoustic sensor.csv"
```

It exits `0` on a complete run, `1` when the time-sliced networks had to be skipped (no year column), and `2` for a fatal input problem (missing or empty CSV), so it is safe to use in a pipeline. Note that the bundled `Networks/acoustic sensor.csv` is a **0-byte placeholder** — supply your own export via `--csv`.

### 6. `NLP/`
`NLP.ipynb` — A text-preprocessing pipeline for NLP tasks: deduplicates records, removes emails/numbers/punctuation, tokenizes, builds bigram/trigram phrase models, lemmatizes, and stems text, storing each processing stage as a separate DataFrame column for flexible use in downstream models (e.g., topic modeling).

### 7. `SDG/`
`SDG.ipynb` — Classifies Web of Science records against the **17 UN Sustainable Development Goals** using keyword matching over Title/Abstract/Keywords fields. Produces SDG coverage statistics, a bar chart, and a bipartite paper↔SDG network exported as GraphML for tools like Gephi.

### 8. `openalex/`
A dependency-free (standard-library-only) command-line client for the [OpenAlex API](https://help.openalex.org/api/) that searches and exports scholarly data (250M+ works, authors, sources, institutions, topics, funders) as **CSV / XLSX / JSONL / JSON**. It handles cursor paging (beyond the 10,000-record page cap), retries with backoff on 429/5xx, the "polite pool" `mailto`, Excel-safe UTF-8 CSV output, and can rebuild abstracts from OpenAlex's inverted index. Results are **streamed** to disk, so `--limit 0` exports of millions of records don't exhaust memory.

Its `harvest` subcommand is the integration point for the rest of this repo: it writes a **Web-of-Science-shaped file** that `co_occurrence_network_analysis/`, `NLP/NLP.ipynb`, and `SDG/SDG.ipynb` can consume directly, with no column renaming.

| File | Purpose |
| --- | --- |
| `openalex_cli.py` | Command-line interface (`search`, `harvest`, `get`, `count`, `group-by`, `autocomplete`, `author-works`, `cited-by`, `references`) |
| `openalex_client.py` | Reusable API client: cursor paging, retries, filters, progress hook, `chunked()` helper |
| `flatten.py` | Flattens nested JSON into CSV rows (works, authors, sources, institutions), rebuilds abstracts, defines the WoS schemas |
| `tests/` | 128 offline tests — no network, no API key |

Run it via:
```bash
# General search / export
python "openalex/openalex_cli.py" search works -q "machine learning in medicine" \
    --filter "publication_year:>2020,is_oa:true" --sort cited_by_count:desc \
    --limit 500 --abstract --csv out/ml_medicine.csv

# Notebook-ready WoS-shaped export
python "openalex/openalex_cli.py" harvest works -q "digital twin" --since 2015 \
    --require-abstract --limit 0 --csv out/digital_twin_wos.csv
```

Tests (offline):
```bash
python -m pytest openalex/tests
```

See the module's own [README](openalex/README.md) for the full command reference, the WoS schemas, and the filter cheat-sheet.

### 9. `Trend Analysis and Forecasting for Technology Intelligence/`
A comprehensive forecasting notebook covering: exploratory trend analysis; growth/CAGR/acceleration and log transforms; Mann-Kendall, Sen's slope, and Spearman trend tests; linear OLS diagnostics; nonlinear curve fitting (polynomial, exponential, power law, logarithmic, logistic, Gompertz); structural break detection (Chow test, `ruptures`); model evaluation (R², RMSE, MAE, MAPE, SSE, AIC, BIC); out-of-sample and expanding-window validation; ARIMA forecasting; and final model comparison for technology-trend decision support.

## Shared library (`techintel/`)

Several notebooks and `Networks/Networks.py` need the same three things: parsing Web of Science exports, building co-occurrence graphs, and computing network-structure metrics. That code used to be copy-pasted into each file (the metric functions existed in **three** slightly different versions, and the Louvain helpers in **three** more). It now lives in one package:

| Module | Contents |
| --- | --- |
| `techintel/wos.py` | WoS **tagged-format** parser (`parse_wos_file`), `C3` cleanup, country extraction, and a robust WoS **CSV** loader (`read_wos_csv`) |
| `techintel/graphs.py` | GraphML load/save, giant-component extraction, field splitting (`people` vs `codes`), and co-occurrence graph construction |
| `techintel/communities.py` | Louvain partitioning (NetworkX built-in *or* `python-louvain`), modularity, community labelling, cliques, core/periphery, betweenness centrality |
| `techintel/metrics.py` | `v/e/p/u` counts, alpha/beta/gamma indices, degree stats & heterogeneity, average shortest path, triangles, transitivity, clustering, assortativity, and `summarize_graph` |
| `techintel/demo.py` | Seeded **synthetic WoS data** — lets a demo, a test or CI run with no data files at all |
| `techintel/pipeline.py` | **End-to-end orchestration** (see below) |
| `techintel/verify.py` | Validates a pipeline output bundle |
| `techintel/tests/` | **238 offline tests** — no network, no data files |

Dependencies are only **`pandas` + `networkx`**. `python-louvain` and `openpyxl` are optional and degrade gracefully: Louvain falls back to `networkx.community.louvain_communities` (so the previously-unmet `python-louvain` requirement is no longer needed), and Excel export raises an actionable `ImportError` instead of a crash.

Notebooks pick the package up through a small bootstrap in their first code cell that walks upward to find the repository root, so it works whether you launch Jupyter from the notebook folder, the repo root, or Colab.

Tests:
```bash
python -m pytest techintel/tests
python -m unittest discover -s techintel/tests -t .
```

See [`techintel/README.md`](techintel/README.md) for the API and the list of deliberate behaviour changes.

### Behaviour fixes applied during the extraction

| Fix | Where | Old behaviour |
| --- | --- | --- |
| Country extraction | `wos.extract_country_from_address` | `parts[-1].strip()[:-1]` ate the last character, so `"WA 98195 USA"` became `"WA 98195 US"` and the `USA`/`Turkey` normalisations **never matched** |
| Co-occurrence counting | `graphs.build_cooccurrence_graph` | Duplicate items in a row produced self-loops and double-counted pairs (`"a; b; a"` → self-loop `a-a` **and** `a-b` weight 2) |
| Edgeless graphs | `communities.detect_communities` | NetworkX modularity raised `ZeroDivisionError` for every algorithm on a graph with no edges |
| Empty graphs | `metrics.summarize_graph` | `nx.transitivity` / `nx.average_clustering` raised on an empty graph |
| Edgeless graphs | `Networks.py` | Same `ZeroDivisionError`, reported as four clustering errors per network |
| Exit status | `Networks.py` | Skip paths were bare `return`s, so a partial run still exited `0` |
| Missing/empty input | `Networks.py` | A 0-byte CSV produced a raw `pandas.errors.EmptyDataError` traceback |

## Pipeline

The analysis notebooks below form a chain: parse a WoS export → build co-occurrence networks → reduce to giant components → detect communities and compute topology metrics. `techintel/pipeline.py` runs that whole chain as a single command, so you don't have to drive four notebooks by hand.

```bash
# Synthetic data — nothing to download, fully reproducible.
python -m techintel.pipeline --demo 120 --out out/demo

# A real export: tagged .txt, delimited .csv/.txt or .xlsx (detected from content).
python -m techintel.pipeline --input records.txt --out out/run1

# A raw Web of Science "Full Record" CSV export is accepted as-is: the long
# headers and ";"-delimited authors/addresses are normalised on load.
python -m techintel.pipeline --input cleaned_combined_data.csv --out out/real

# A subset of networks, without giant-component reduction.
python -m techintel.pipeline --input records.txt --specs authors,keywords --no-giant
```

It builds five networks — `authors` (`|`-separated), `keywords`, `subject_categories`, `countries` and `organisations` — and writes:

```
out/demo/
├── manifest.json     # machine-readable record of the run (paths relative to the bundle)
├── metrics.csv       # one row per network: sizes, macro metrics, Louvain modularity
├── summary.txt       # summarize_graph output per network
└── graphml/          # <name>.graphml + <name>_giant.graphml, `community` attribute attached
```

The `community` node attribute means the exported GraphML can be opened in Gephi and coloured by community immediately — step 8 of the workflow below.

Exit codes are `0` (clean), `1` (partial — a column was missing, or every network came out empty) and `2` (fatal), matching `Networks/Networks.py`, so it composes with a shell, the Makefile or n8n. Validate a bundle afterwards with:

```bash
python -m techintel.verify out/demo     # 0 = complete, 1 = inconsistent, 2 = missing
```

### Task runners

`make` and PowerShell entry points for the same handful of commands:

```bash
make help          # list targets
make test          # pytest, both suites
make demo          # pipeline on synthetic data -> out/demo
make pipeline INPUT=records.txt OUT=out/run1
make networks      # Networks/Networks.py
make compile       # byte-compile every Python file (syntax check)
make clean
```

```powershell
.\tasks.ps1 test
.\tasks.ps1 demo -DemoN 200
.\tasks.ps1 pipeline -InputFile records.txt -Out out/run1
```

`tasks.ps1` mirrors the Makefile target-for-target, because GNU `make` is often absent on Windows.

### Scheduling with n8n

[`n8n/techintel-pipeline.json`](n8n/techintel-pipeline.json) is an [n8n](https://n8n.io/) workflow that runs the pipeline on a schedule, verifies the bundle, and fails the execution if either step goes wrong:

```
Manual / Schedule Trigger ──► Run pipeline ──► Pipeline succeeded? ──► Verify bundle ──► Bundle valid? ──► Read manifest ──► Done
```

Import it with `n8n import:workflow --input=n8n/techintel-pipeline.json`. See [`n8n/README.md`](n8n/README.md).

## Typical Workflow

1. **Prepare data** — Export raw bibliographic/patent records (Web of Science, Lens.org, etc.), or harvest them straight from the [OpenAlex API](https://help.openalex.org/api/) with `openalex/openalex_cli.py`. Use `harvest` to get a WoS-shaped CSV that the notebooks below read without any column renaming.
2. **Preprocess text** — Use `NLP/NLP.ipynb` to clean and normalize abstracts/keywords.
3. **Build networks** — Use `co_occurrence_network_analysis/` or `Networks/Networks.py` to construct collaboration/co-occurrence graphs (GraphML).
4. **Analyze structure** — Use `network_topology_metrics_analysis/` and `Giant to macro analysis/` to compute topology and community-level metrics.
5. **Model topics** — Use `BERTopic/` for topic discovery on text corpora.
6. **Classify against SDGs** — Use `SDG/` to map records to Sustainable Development Goals.
7. **Forecast trends** — Use `Trend Analysis and Forecasting for Technology Intelligence/` to model and forecast technology growth trajectories.
8. **Visualize** — Open exported `.graphml` files in [Gephi](https://gephi.org/) for network layout (ForceAtlas2 recommended), sizing by degree centrality, and community coloring.

Steps 3 and 4 can also be run as one unattended command — see [Pipeline](#pipeline) above, which builds the networks, reduces them to their giant components, detects communities, computes the topology metrics and writes a report bundle. Its GraphML output carries the community labels, so step 8 works directly on it.

## Requirements

Different notebooks have different dependencies; install what you need per module. A machine-readable superset lives in [`requirements.txt`](requirements.txt):

```
pandas
numpy
openpyxl
matplotlib
networkx
python-louvain        # imported as community.community_louvain
spacy (+ en_core_web_sm model)
nltk (+ stopwords, wordnet, punkt corpora)
gensim
bertopic
umap-learn
sentence-transformers
hdbscan
scikit-learn
scipy
statsmodels
ruptures
tabulate
wordcloud
```

The `openalex/` module is an exception: it uses only the Python standard library (Python 3.8+) and needs **no third-party packages** — not even for its test suite (`python -m unittest discover -s openalex/tests -t .`). `openpyxl` is only needed if you want its optional `--xlsx` output.

The `techintel/` shared library needs just **`pandas` + `networkx`**. Its optional extras are `python-louvain` (Louvain falls back to the NetworkX implementation, so this is no longer required) and `openpyxl` (Excel export only).

Some notebooks were originally authored for **Google Colab** (using `google.colab.drive` and `!pip install`) — update file paths and remove/adjust Colab-specific cells when running locally or in Jupyter/VS Code. The maintained notebooks (`NLP/NLP.ipynb`, `network_topology_metrics_analysis/`, `co_occurrence_network_analysis/`, `Giant to macro analysis/`, `SDG/`) have already been made path-portable.

## Tests

All tests are **offline** — no network, no data files. The pipeline tests run on seeded synthetic
records written to a temp directory, and the n8n tests parse the workflow JSON:

```bash
python -m pytest openalex/tests techintel/tests        # 364 passed, 2 skipped
python -m unittest discover -s openalex/tests -t .      # 128 tests
python -m unittest discover -s techintel/tests -t .     # 238 tests
```

The two skips are the `openpyxl`-dependent Excel tests; `openpyxl` is not installed here, and the tests assert the graceful-degradation path instead.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs three jobs:

| Job | What it does |
| --- | --- |
| `tests` | Both suites under both runners, on Python 3.10/3.11/3.12 (Ubuntu) plus one Windows leg |
| `pipeline` | Runs the pipeline end-to-end on synthetic data, verifies the bundle, asserts it contains the expected files, checks that `verify` *rejects* a tampered bundle, checks `Networks.py` still exits `2` on the 0-byte placeholder, and uploads the bundle as an artifact |
| `makefile` | Runs the Makefile targets (`help`, `compile`, `demo`, `clean`) and checks `make pipeline` fails cleanly when `INPUT` is unset |

> This directory is not currently a git repository, so the workflow does not run yet — it is committed and ready for the first `git init` + push.

## Notes

- Several notebooks expect input data files (e.g., WoS text/CSV exports, `Series2.xlsx`, `Digital Twin.xlsx`, `Quantum-IEEE.xlsx`) that are **not included** in this repository; update the relevant path variables (`DATA_DIR`, `INPUT_FILE`, `GRAPHML_PATH`, etc.) to point at your own data.
- `cleaned_combined_data.csv` / `.xlsx` (≈232 MB) sit at the repository root. No code reads them — they are kept only as a **schema reference** for the standard WoS long-form export, which is why `openalex harvest --schema wos` produces matching column names. They are now covered by [`.gitignore`](.gitignore).
- GraphML outputs from one module (e.g., `co_occurrence_network_analysis/`) are often used as inputs to others (e.g., `network_topology_metrics_analysis/`, `Giant to macro analysis/`), so it's helpful to keep a consistent output directory across the pipeline.
- Generated artifacts (`openalex/out/`, `out/`, `results/`, `__pycache__/`, `.ipynb_checkpoints/`, `.pytest_cache/`, `.openalexrc`) are covered by [`.gitignore`](.gitignore).

### Superseded Colab originals

Three folders contain both a **maintained** notebook and the **raw Colab export** it was cleaned up from. The maintained file is the one to edit; the `NNNNNNNNNNNNNNNN_*.ipynb` file is a frozen original kept only for reference and still contains `/content/...` paths and inline helper definitions:

| Folder | Maintained | Superseded original |
| --- | --- | --- |
| `co_occurrence_network_analysis/` | `co_occurrence_network_analysis.ipynb` | `3291813249343758082_53974082580038.ipynb` |
| `network_topology_metrics_analysis/` | `network_topology_metrics_analysis.ipynb` | `-4456583429129363709_52843820151717.ipynb` |
| `NLP/` | `NLP.ipynb` | `-483081773745889536_685263241683825.ipynb` |

Deleting the superseded originals (~13 MB) is safe once you no longer need them for comparison.

