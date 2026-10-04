"""Shared helpers for the technology-intelligence notebooks in this repository.

These modules hold the code that was previously copy-pasted between notebooks:

* :mod:`techintel.wos`          - Web of Science tagged-file / CSV parsing
* :mod:`techintel.graphs`       - GraphML I/O and co-occurrence graph building
* :mod:`techintel.communities`  - community detection and labelling
* :mod:`techintel.metrics`      - network-structure metrics
* :mod:`techintel.demo`         - seeded synthetic WoS data (no data files needed)
* :mod:`techintel.pipeline`     - end-to-end orchestration of all of the above
* :mod:`techintel.verify`       - validates a pipeline output bundle

Dependencies: ``pandas`` and ``networkx``. Everything else is optional and
degrades gracefully (``openpyxl`` for Excel, ``python-louvain`` for Louvain).

The notebooks import these via the small bootstrap in the first code cell::

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path.cwd().parent))  # repo root
    from techintel import wos, graphs, metrics

The whole pipeline runs from the command line::

    python -m techintel.pipeline --demo 80 --out out/demo
"""

from __future__ import annotations

__all__ = ["wos", "graphs", "communities", "metrics", "demo", "pipeline", "verify"]
