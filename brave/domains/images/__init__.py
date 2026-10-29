"""Image lane: Wikidata + Commons photos → the Brave archive → norteia-api (P1).

Not a collection source (no SourceDomain, never in the registry): it enriches Rio
entities that already exist and NEVER writes ``normalized`` — no Mar denylist entry, no
route_by_score, no Mar re-push. Photos travel through their own outbox
(``entity_images.pushed_at``).

matching.py and policy.py are pure; agent.py does the I/O.
"""
