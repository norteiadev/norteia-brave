"""Shared, dependency-free primitives for the Brave collector.

This is the lowest layer of the package: it holds cross-cutting types that any
other layer (core, lanes, tasks, api) may import. Per the import rule (D-18),
``brave.shared`` MUST NOT import ``brave.core``, ``brave.lanes``, or
``brave.tasks`` — it stays at the bottom of the dependency graph.
"""
