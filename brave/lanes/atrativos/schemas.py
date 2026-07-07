"""Re-export shim (Phase G): brave.lanes.atrativos.schemas -> brave.domains.mtur.dtos.

The module moved to ``brave.domains.mtur.dtos`` (Phase G). This historical
import path is kept importable via a ``sys.modules`` alias, so existing call-sites
AND string-path monkeypatches (e.g. ``patch("brave.lanes.atrativos.schemas.<name>")``) and
module-object grabs (``import brave.lanes.atrativos.schemas as m``) all transparently resolve
to the moved module object — patches land on the code that actually runs.

See docs/ultraplan-refactor-brave.md (Phase G) and tests/unit/test_domain_boundaries.py.
"""

import sys

from brave.domains.mtur import dtos as _module

sys.modules[__name__] = _module
