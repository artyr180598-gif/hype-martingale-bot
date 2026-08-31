"""Unified HYPE engine entry point.

The project now has one engine: ``v3`` (Futures Signal Intelligence).  ``main.py``
is only a thin backwards-compatible wrapper around ``python -m v3``.
"""

from __future__ import annotations

import sys

from v3.cli import main as v3_main


def main() -> int:
    return v3_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
