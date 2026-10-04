"""Test suite for the `openalex` module.

Run from the repository root with either runner:

    python -m pytest openalex/tests
    python -m unittest discover -s openalex/tests -t .

The tests never touch the network: API calls are stubbed with recorded payloads.
"""

import os
import sys

# Make `flatten` / `openalex_client` / `openalex_cli` importable exactly the way
# the CLI imports them in script mode, so tests exercise the real code paths.
_MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)
