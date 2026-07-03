"""Re-export shim (Phase G): brave.lanes.tripadvisor.session -> brave.domains.tripadvisor.session.

The module moved to ``brave.domains.tripadvisor.session`` (Phase G). This historical
import path is kept importable via a ``sys.modules`` alias, so existing call-sites
AND string-path monkeypatches (e.g. ``patch("brave.lanes.tripadvisor.session.<name>")``) and
module-object grabs (``import brave.lanes.tripadvisor.session as m``) all transparently resolve
to the moved module object — patches land on the code that actually runs.

See docs/ultraplan-refactor-brave.md (Phase G) and tests/unit/test_domain_boundaries.py.
"""

import sys

from brave.domains.tripadvisor import session as _module

sys.modules[__name__] = _module
