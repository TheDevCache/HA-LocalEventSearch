"""Entry point for the Windows app (and for running it from a checkout: python windows/run.py)."""

import sys

from event_scout_app.main import main

if __name__ == "__main__":
    sys.exit(main())
