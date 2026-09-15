"""Shared pytest fixtures/path setup for the FlowTrace test suite.

Layout contract:
- tests/server/  tests for the Flask server modules (server.py, shifts.py, detection.py, manage_cli.py)
- tests/client/  tests for the Windows client modules (client/src/*.py)

Both source directories are put on sys.path here so test modules can simply
`import detection` / `import sync` etc.

Importing server.py reads config and initializes the SQLite database at import
time, so the environment is redirected to a throwaway location *before* any
test module imports it.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "client", "src"))

_TEST_DIR = tempfile.mkdtemp(prefix="flowtrace_tests_")
os.environ.setdefault("DATABASE_PATH", os.path.join(_TEST_DIR, "test.db"))
os.environ.setdefault("CONFIG_PATH", os.path.join(_TEST_DIR, "config.json"))
os.environ.setdefault("API_KEY", "")
