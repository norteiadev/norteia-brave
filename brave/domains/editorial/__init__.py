"""Editorial lane: blog columns (curiosities, how_to_get_there, …) → norteia-api.

Not a collection source (no SourceDomain, never in the registry): it enriches Rio
attractions and destinations that already exist. Posts are crawled per UF, an LLM extracts
one claim per (entity, column) through the Message Batches API, each attraction is matched
to a Rio record by COORDINATE, one value per column is resolved and written to
``rio_records.normalized["editorial"]`` (attractions → the Mar outbox republishes) or to
``editorial_destinations`` (destinations → its own outbox in ``brave.core.mar.publication``).

crawler / schema / matching / resolver are pure where they can be; extract_batch and the
crawl do the I/O. D-18: no imports from other domains or brave.tasks.
"""
