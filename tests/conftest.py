"""Root test configuration.

Deliberately free of Home Assistant imports.  ``pytest-homeassistant-custom-
component`` pulls in ``homeassistant.runner``, which imports ``fcntl``, and
``homeassistant.util.resource``, which imports ``resource`` - neither exists on
Windows.  Keeping this file HA-free means the pure-logic suites (dates,
analytics, the endpoint plan, the poll simulation, the description checks)
still run anywhere::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --ignore=tests/ha

The Home Assistant integration tests live under ``tests/ha/`` with their own
conftest.  ``pytest`` with no flags runs everything, which is what CI does.
"""

from __future__ import annotations

import pathlib
import sys

# The integration is imported as ``custom_components.sharesight``, which only
# resolves when the repository root is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
