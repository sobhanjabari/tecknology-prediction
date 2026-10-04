"""Test suite for the shared `techintel` package.

Run from the repository root with either runner:

    python -m pytest techintel/tests
    python -m unittest discover -s techintel/tests -t .

The tests are fully offline: they build tiny graphs in memory and parse a
literal Web of Science record. No network, no data files.
"""

import os
import sys

# Make the repository root importable so `from techintel import ...` works the
# same way it does from the notebooks, whatever the runner's cwd happens to be.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
