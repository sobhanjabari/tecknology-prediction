# n8n workflow

[`techintel-pipeline.json`](techintel-pipeline.json) is an [n8n](https://n8n.io/) workflow that runs
the analysis pipeline and validates the result — the scheduled, unattended counterpart of running
`python -m techintel.pipeline` by hand.

## Import

```bash
n8n import:workflow --input=n8n/techintel-pipeline.json
```

Or in the UI: **Workflows → ⋯ → Import from File**.

## Shape

```
Manual Trigger ──┐
                 ├──► Run pipeline ──► Pipeline succeeded? ──true──► Verify bundle ──► Bundle valid? ──true──► Read manifest ──► Done
Schedule Trigger ┘                            │false                      │false
                                              ▼                           ▼
                                     Fail: pipeline failed       Fail: invalid bundle
```

| Node | Type | Purpose |
|---|---|---|
| `Manual Trigger` | `manualTrigger` | Run once on demand |
| `Schedule Trigger` | `scheduleTrigger` | Every 24 hours once the workflow is activated |
| `Run pipeline` | `executeCommand` | `python -m techintel.pipeline --demo 120 --out out/n8n` |
| `Pipeline succeeded?` | `if` | Branches on the pipeline exit code |
| `Verify bundle` | `executeCommand` | `python -m techintel.verify out/n8n` |
| `Bundle valid?` | `if` | Branches on the verify exit code |
| `Read manifest` | `readWriteFile` | Hands `manifest.json` to whatever you attach next |
| `Done` | `noOp` | Attachment point for delivery steps |
| `Fail: …` | `stopAndError` | Fails the execution so n8n's error workflow fires |

## Two details worth knowing

**`onError: continueRegularOutput` is required on both `ExecuteCommand` nodes.** n8n's
`ExecuteCommand` node *throws* when a command exits non-zero, so without this the exit code never
reaches the `if` node. With it, a successful command emits `{exitCode, stderr, stdout}` and a
failed one emits `{error}`. The `if` nodes therefore branch on *the presence of `exitCode`*, which
is the success signal.

**Exit `1` and exit `2` take the same branch.** Both mean "needs attention", and the precise code is
recorded in `manifest.json`. If you want to distinguish a partial run (`1`) from a fatal one (`2`),
add an `if` node downstream that reads `manifest.json` and compares `exit_code`.

## Working directory

`ExecuteCommand` runs in n8n's own working directory, not the repository. Either start n8n with the
repository as its working directory, or prepend a `cd` to both commands:

```
cd "<path-to-repo>" && python -m techintel.pipeline --demo 120 --out out/n8n
```

No absolute path is committed — `techintel/tests/test_n8n_workflow.py` asserts that, along with node
types, connection targets and parameter shapes.

## Real data

Swap the `--demo 120` flag for a real export:

```
python -m techintel.pipeline --input "<path-to-export>.txt" --out out/n8n
```

The pipeline detects tagged `.txt`, delimited `.csv`/`.txt` and `.xlsx` from the file's content.
