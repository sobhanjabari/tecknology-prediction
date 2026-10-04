# Bibliometric Co-occurrence Network Analysis

This project builds and analyzes several co-occurrence networks from a **Web of Science (WoS)** bibliographic export, intended for bibliometric / scientometric analysis (e.g., country collaboration, co-authorship, institutional collaboration, subject-category overlap, and keyword/technology trend analysis).

## Files

| File | Description |
|---|---|
| `co_occurrence_network_analysis.ipynb` | **Refactored, English, bug-fixed** notebook — use this one going forward. |
| `3291813249343758082_53974082580038.ipynb` | Original notebook (kept for reference; contains Persian comments and copy-paste bugs — see below). |

## What the Notebook Does

### 1. Data Preparation
- Mounts Google Drive (Google Colab environment).
- Parses a raw WoS tagged text export (e.g., `merged.txt`) into a structured pandas DataFrame with columns: `Abstract`, `Authors`, `C3` (raw affiliations), `DOI`, `Funding`, `Journal`, `Keywords`, `Year`, `Addresses`, `Title`, `Cite`, `Subject_Category`, and a derived `Countries` column.
- Saves the parsed data to `output_wos_file.xlsx`.

### 2. Shared Helper Functions

The building blocks now live in the repository-level [`techintel/`](../techintel) package, so they are shared with `SDG/`, `Giant to macro analysis/`, `network_topology_metrics_analysis/` and `Networks/Networks.py` instead of being redefined here:

| Function | Provided by |
|---|---|
| `parse_wos_file`, `clean_c3_field`, `extract_countries`, `save_to_excel`, `process_wos_to_excel` | `techintel.wos` |
| `build_cooccurrence_graph(df, column, separator=...)` | `techintel.graphs` |
| `load_graph`, `get_giant_component`, `extract_and_save_giant_component` | `techintel.graphs` |
| `label_communities`, `find_cliques`, `detect_core_periphery`, `betweenness_centrality` | `techintel.communities` |
| `louvain_partition`, `partition_modularity` | `techintel.communities` |

Only `analyze_graph(...)` (the full pipeline: Louvain → modularity → cliques → core/periphery → betweenness → save) remains defined in the notebook, since it is this notebook's orchestration rather than a shared primitive.

### 3. Five Co-occurrence Networks

| # | Network | Source column | Giant component step? | Final output |
|---|---|---|---|---|
| 1 | Country collaboration | `Countries` | No (analyzed directly) | `Countries_network_with_communities.graphml` |
| 2 | Co-authorship | `Authors` | Yes | `giant_component_authorship_network_with_communities.graphml` |
| 3 | Co-affiliation (institutions) | `C3` | Yes | `giant_component_affiliation_network_with_communities.graphml` |
| 4 | Subject Category | `Subject_Category` | Yes | `giant_component_Subject_Category_network_with_communities.graphml` |
| 5 | Keyword / Technology | `Keywords` (NLP-cleaned via spaCy + NLTK) | Yes | `giant_component_keywords_network_with_technologies_with_communities.graphml` |

For each network, after downloading the final `.graphml` file:
1. Open it in **Gephi**.
2. Try different layout algorithms — **ForceAtlas2** usually gives the best results for these networks.
3. Size nodes by **degree centrality** and rescale so node sizes are meaningful.
4. Enable node labels from the bottom panel and adjust font size/type as needed.
5. Use the printed **modularity**, **clique count**, **core/periphery counts**, and **top betweenness-centrality nodes** in your report.

## Bugs Fixed vs. the Original Notebook

1. **Subject Category giant-component/community cells pointed at the wrong file.** In the original notebook, the "Subject Category" giant-component extraction and community-analysis cells referenced `co_Funding_network.graphml` / `giant_component_Funding_network.graphml` — files that are never created anywhere in the notebook (a leftover from copy-pasting a previous section). These now correctly reference the Subject Category network files end-to-end.

2. **Keyword network communities cell analyzed the full graph instead of its giant component.** Every other section in the notebook analyzes the giant component, but the original keyword section's final analysis cell re-read the full `keywords_network_with_technologies.graphml` file. This has been made consistent — it now analyzes `giant_component_keywords_network_with_technologies.graphml`.

3. **Duplicated boilerplate consolidated.** The `analyze_graph` / Louvain / clique / core-periphery / betweenness logic (previously copy-pasted identically 5 times) and the giant-component extraction logic (previously copy-pasted 4 times) are now defined once as shared helper functions. This removes the root cause of bug #1 (editing one copy and forgetting the others) and makes future changes safer.

4. **Translated to English.** All Persian markdown/comment cells describing the workflow have been translated to English, and code comments/docstrings are in English throughout.

5. **Country extraction silently mis-parsed almost every address.** `extract_countries` did `parts[-1].strip()[:-1]`, chopping the last character off the country. `"WA 98195 USA"` became `"WA 98195 US"`, which meant the `if "USA" in country` and `if "Turkey" in country` normalisations **could never match** — so `USA`/`Turkiye` never appeared in the `Countries` column. The extraction now strips surrounding brackets instead (which is what the slice was reaching for) and is covered by tests.

6. **Co-occurrence counts were inflated, and rows with repeats created self-loops.** `build_cooccurrence_graph` paired items without de-duplicating them first, so a row like `"a; b; a"` produced a self-loop `a-a` **and** gave edge `a-b` weight 2. Items are now de-duplicated within a row (matching `Networks/Networks.py`); pass `dedupe=False` to reproduce the old counts. Expect edge weights in existing outputs to shift slightly.

7. **Edgeless graphs produced four clustering errors each.** NetworkX's modularity raises `ZeroDivisionError` on a graph with no edges, which surfaced as `louvain: ERROR: division by zero` etc. in the summary files. Clustering is now skipped cleanly for edgeless graphs.

## Requirements

```
pandas
openpyxl          (optional — only for the .xlsx intermediate file)
networkx
python-louvain    (optional — Louvain falls back to networkx.community.louvain_communities)
spacy             (+ en_core_web_sm model, keyword section only)
nltk              (+ stopwords corpus, keyword section only)
matplotlib
```

## Notes / Assumptions

- The keyword/technology section expects a `technologies.txt` file (one technology term per line, lowercase) at `/content/drive/MyDrive/SNA/technologies.txt` to tag keyword nodes as `technology` vs. `general`.
- The WoS raw export path is assumed to be `/content/drive/MyDrive/HPC/merged.txt`; update this path as needed for your own Google Drive layout.
