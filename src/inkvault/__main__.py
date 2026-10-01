"""`python -m inkvault`: how the scheduled job starts InkVault (pythonw on Windows, so no window opens)."""
import sys

from .cli import main

sys.exit(main())
