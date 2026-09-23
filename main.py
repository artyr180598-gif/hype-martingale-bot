"""Unified HYPE ULTIMATE entry point — thin wrapper around src.hype.cli"""

from __future__ import annotations

import sys

from src.hype.cli import main as hype_main


def main() -> int:
    return hype_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
