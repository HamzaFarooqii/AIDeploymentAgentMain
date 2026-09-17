"""
Shared pytest configuration for the backend-python test suite.

Deliberately minimal: each test file already does its own
`sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`
to make the `app` package importable, and those hacks are left untouched.

This conftest exists as a project-wide safety net so the same "backend-python/
is importable" behavior holds even for collection paths where a given test
file's own sys.path hack hasn't executed yet (e.g. pytest importing/collecting
a module before its top-level statements run in some edge case, or a future
test file that omits the hack) and independent of pytest.ini's `pythonpath`
setting. It's intentionally idempotent and side-effect-free otherwise.
"""

import os
import sys

_BACKEND_PYTHON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _BACKEND_PYTHON_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_PYTHON_ROOT)
