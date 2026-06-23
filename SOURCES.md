# Pipeline Data Sources

Index of all external data sources ingested by Pipeline Brave.

> **Note on ToS-violation sources:** Sources marked "ToS violation" are operated
> under a documented-risk posture: low-rate, operator-gated, not on the autonomous
> Celery beat, no PII stored. See the source's `data/<source>/README` for the
> full risk register, mitigations, and LGPD basis before enabling these sources.

| Source | Requirements | origem_value | License / Terms | Cost | Notes |
|--------|-------------|:------------:|-----------------|------|-------|
| mtur | DEST-01 | 100 | MTUR public/gov data (federal open data, CC-BY) | Free | Official BR municipal tourism classification (Portaria MTur 9/2025). Bundled CSV seed; offline-safe. See `data/mtur/README`. |
| places | ATR-02, ATR-03, ATR-04 | 60 | Google Maps Platform ToS — per-request, metered | ~USD 0.003/request (Places Details) | First-party Google-validated canonical. `place_id` persisted per Google ToS. `google-maps-places` (New) client only — legacy Places API is deprecated. See `brave/clients/places.py`. |
| tripadvisor | TA-01, TA-02 | 65 | **ToS violation — operator-gated only** | Free (infra + proxy only) | Aggregate review signals (review_count, rating, most_recent_review_at) — no PII. LGPD basis: legitimate interest (Art. 7º IX). Human promote-override required before Mar. Not on autonomous beat; requires `RUN_REAL_EXTERNALS=1`. See `data/tripadvisor/README`. |
