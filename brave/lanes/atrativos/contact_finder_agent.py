"""Re-export shim (Phase G): brave.lanes.atrativos.contact_finder_agent -> brave.domains.mtur.contact.

The module moved to ``brave.domains.mtur.contact`` (Phase G). This historical
import path is kept importable via a ``sys.modules`` alias, so existing call-sites
AND string-path monkeypatches (e.g. ``patch("brave.lanes.atrativos.contact_finder_agent.<name>")``) and
module-object grabs (``import brave.lanes.atrativos.contact_finder_agent as m``) all transparently resolve
to the moved module object — patches land on the code that actually runs.

See docs/ultraplan-refactor-brave.md (Phase G) and tests/unit/test_domain_boundaries.py.
"""

import sys

from brave.domains.mtur import contact as _module

sys.modules[__name__] = _module
