"""
Golden Music — v3.0 thin entry point.

The application now lives in the `gm/` package; this file only exists so
double-clicking it on Windows still starts the player exactly as before.
"""
from main import main

if __name__ == "__main__":
    raise SystemExit(main())
