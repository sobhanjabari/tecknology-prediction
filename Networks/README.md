# Patent networks

The bundled `acoustic sensor.csv` is empty. Supply a populated patent CSV export
before running this script; no sample or synthetic patent data is substituted.

From the repository root in PowerShell:

```powershell
python .\Networks\Networks.py --csv "C:\data\patents.csv"
```

Relative CSV paths are resolved relative to the script directory, not the current
working directory. Outputs default to the `results` directory next to the script.

Supported network columns are `Inventors`, `Applicants`, `Owners`,
`IPCR Classifications`, and `CPC Classifications`. If a base network column is
missing, an empty graph and summary are still written for it. For time-sliced
IPC networks, include `IPCR Classifications` and a year
column such as `Publication Year`, or select a column with `--year-col`.
The configured time slices are 2000–2010, 2011–2020, and 2021–2025.

Exit codes:

- `0`: Complete run.
- `1`: Time-sliced networks could not be produced; base outputs may exist.
- `2`: Missing or empty input CSV.

Keep generated outputs and large private exports out of version control.