"""Re-export shim (Phase G): brave.lanes.tripadvisor.schemas -> brave.domains.tripadvisor.schemas.

The module moved to ``brave.domains.tripadvisor.schemas`` (Phase G). This historical
import path is kept importable via a ``sys.modules`` alias, so existing call-sites
AND string-path monkeypatches (e.g. ``patch("brave.lanes.tripadvisor.schemas.<name>")``) and
module-object grabs (``import brave.lanes.tripadvisor.schemas as m``) all transparently resolve
to the moved module object — patches land on the code that actually runs.

See docs/ultraplan-refactor-brave.md (Phase G) and tests/unit/test_domain_boundaries.py.
"""

import sys

from brave.domains.tripadvisor import schemas as _module

sys.modules[__name__] = _module
