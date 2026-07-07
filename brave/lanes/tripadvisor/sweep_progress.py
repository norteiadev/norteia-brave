"""Re-export shim (Phase G): brave.lanes.tripadvisor.sweep_progress -> brave.domains.tripadvisor.sweep_progress.

The module moved to ``brave.domains.tripadvisor.sweep_progress`` (Phase G). This historical
import path is kept importable via a ``sys.modules`` alias, so existing call-sites
AND string-path monkeypatches (e.g. ``patch("brave.lanes.tripadvisor.sweep_progress.<name>")``) and
module-object grabs (``import brave.lanes.tripadvisor.sweep_progress as m``) all transparently resolve
to the moved module object — patches land on the code that actually runs.

See docs/ultraplan-refactor-brave.md (Phase G) and tests/unit/test_domain_boundaries.py.
"""

import sys

from brave.domains.tripadvisor import sweep_progress as _module

sys.modules[__name__] = _module
