"""Shared WhatsApp conversation machinery (Phase G — kernel moves).

Holds the reusable, transport-facing WhatsApp owner-validation conversation:

  - ``schemas``      — ``ConversationExtractionResult`` (DeepSeek/instructor 2nd-layer
                       validator for owner replies). Dependency-free.
  - ``conversation`` — pure conversation primitives: ``ConversationState`` (LangGraph
                       state), opt-out keyword detection, and the post-node routing
                       predicates. Dependency-free (imports nothing from brave.*).
  - ``agent``        — the compiled LangGraph WhatsApp agent: graph nodes,
                       ``_compliant_send`` (the single gate→send call site), and
                       ``build_graph``.

Per the D-18 import rule, ``brave.shared`` MUST NOT import ``brave.domains``
(``brave.lanes``) or ``brave.tasks``.

KNOWN DEVIATION (tracked follow-up): ``agent._finalize_node`` still imports
``brave.core.models`` / ``brave.core.rio.routing`` and reaches ``brave.core`` via
``brave.compliance`` at call time. The tasks-layer coupling (``push_attraction_task``)
has been inverted to an injected ``push_confirmed_fn`` callback so no ``brave.tasks``
import remains here. Fully severing the residual ``brave.core`` / ``brave.compliance``
edges requires lifting the finalize (re-score → promote → push) body into the calling
domain — deferred to a later Phase G step.
"""
