# Core atrativos subpackage (Phase G — kernel moves).
#
# Entity-agnostic, dependency-safe atrativos primitives that belong to the core
# engine rather than a collection lane. Core NEVER imports brave.lanes /
# brave.domains / brave.tasks (D-18); this package only reaches down into
# brave.core.* and brave.observability.
