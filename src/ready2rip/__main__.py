# SPDX-License-Identifier: GPL-3.0-or-later
"""Allow ``python3 -m ready2rip`` from a PYTHONPATH that includes ``src``."""

from ready2rip.main import main

raise SystemExit(main())
