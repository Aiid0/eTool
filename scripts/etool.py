#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run the bundled eTool CLI from the caller's project directory."""

from pathlib import Path
import sys


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etool.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
