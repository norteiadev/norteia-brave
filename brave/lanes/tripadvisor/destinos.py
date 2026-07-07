"""Re-export shim (Phase G): brave.lanes.tripadvisor.destinos -> brave.domains.tripadvisor.destinos.

The module moved to ``brave.domains.tripadvisor.destinos`` (Phase G). This historical
import path is kept importable via a ``sys.modules`` alias, so existing call-sites
AND string-path monkeypatches (e.g. ``patch("brave.lanes.tripadvisor.destinos.<name>")``) and
module-object grabs (``import brave.lanes.tripadvisor.destinos as m``) all transparently resolve
to the moved module object — patches land on the code that actually runs.

See docs/ultraplan-refactor-brave.md (Phase G) and tests/unit/test_domain_boundaries.py.
"""

import sys

from brave.domains.tripadvisor import destinos as _module

sys.modules[__name__] = _module
