"""Thin wrapper — prefer `python -m malta_housing`."""

from malta_housing.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
