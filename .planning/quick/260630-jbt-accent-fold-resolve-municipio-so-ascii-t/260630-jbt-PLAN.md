---
phase: quick-260630-jbt
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - brave/lanes/tripadvisor/ibge.py
  - tests/unit/lanes/tripadvisor/test_ibge.py
autonomous: true
requirements: [TA-03]

must_haves:
  truths:
    - "resolve_municipio('Maringa', 'PR', records).nome == 'Maringá' (pure accent miss, now resolved)"
    - "resolve_municipio('Carambei', 'PR', records).nome == 'Carambeí' (pure accent miss, now resolved)"
    - "resolve_municipio('Curitiba', 'PR', records).ibge_code == '4106902' (exact name still matches)"
    - "resolve_municipio('ZZZFantasia', 'PR', records) is None (no over-matching from fold)"
    - "Returned record always carries the original accented nome from IBGE (fold is not mutated back)"
    - "All 214 existing TA unit tests still pass"
  artifacts:
    - path: brave/lanes/tripadvisor/ibge.py
      provides: "_fold_accents helper + patched resolve_municipio fuzzy block + corrected docstring"
      contains: "_fold_accents"
    - path: tests/unit/lanes/tripadvisor/test_ibge.py
      provides: "4 new accent-fold test cases inside TestResolveMunicipio"
      contains: "test_ibge_accent_fold_maringa"
  key_links:
    - from: "_fold_accents"
      to: "resolve_municipio Step 2"
      via: "applied to name + each choice before process.extractOne"
      pattern: "_fold_accents"
    - from: "uf_records[index]"
      to: "returned IbgeMunicipio"
      via: "index into original (unfolded) uf_records list"
      pattern: "uf_records\\[index\\]"
---

<objective>
Accent-fold both query name and candidate choices inside resolve_municipio so ASCII
TripAdvisor city names (e.g. "Maringa", "Carambei") match their accented IBGE
counterparts ("Maringá", "Carambeí") with a score of 100 instead of 85.7.

Purpose: A real dense-UF test (Paraná, 60 attractions) showed 54/60 IBGE matches;
5 failures were pure accent misses because rapidfuzz default_process does NOT fold
diacritics — the code comment falsely claimed it did. This fix raises linkage from ~90%
to ~98% on that sample.

Output: patched ibge.py (unicodedata fold helper + corrected Step 2 comment) and 4 new
test cases in test_ibge.py.
</objective>

<execution_context>
@/Users/leandro/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@/Users/leandro/Projects/norteia/norteia-brave/brave/lanes/tripadvisor/ibge.py
@/Users/leandro/Projects/norteia/norteia-brave/tests/unit/lanes/tripadvisor/test_ibge.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add _fold_accents helper and patch resolve_municipio Step 2</name>
  <files>brave/lanes/tripadvisor/ibge.py</files>
  <behavior>
    - _fold_accents("Maringa") == "Maringa"  (no-op on already-ASCII)
    - _fold_accents("Maringá") == "Maringa"  (strips acute from a)
    - _fold_accents("Carambeí") == "Carambei"
    - _fold_accents("São Paulo") == "Sao Paulo"
    - resolve_municipio("Maringa", "PR", real_records).nome == "Maringá"  (was None before)
    - resolve_municipio("Carambei", "PR", real_records).nome == "Carambeí"  (was None before)
    - resolve_municipio("Curitiba", "PR", real_records).ibge_code == "4106902"  (regression guard)
    - resolve_municipio returns original accented nome, not the folded string
  </behavior>
  <action>
Add unicodedata to the existing imports block (it is Python stdlib, no new dependency).

Add this module-level helper directly above the Municipality resolver section comment
(around line 107, just before resolve_municipio):

    import unicodedata  # add to top-level imports

    def _fold_accents(s: str) -> str:
        """Strip combining diacritical marks (Unicode category Mn) after NFKD decomposition.

        This is the explicit accent-fold step used by resolve_municipio — default_process
        alone does NOT remove diacritics (it only lowercases and strips non-alphanumeric
        ASCII punctuation). Without this, 'Maringa' vs 'Maringá' scores 85.7 < 88.
        """
        return "".join(
            ch for ch in unicodedata.normalize("NFKD", s) if unicodedata.category(ch) != "Mn"
        )

In resolve_municipio, replace Step 2 (the fuzzy match block) as follows:

OLD Step 2 block (lines ~150-162):
    # Step 2: rapidfuzz fuzzy match (processor=default_process handles case normalization
    # and accent-agnostic comparison — 'Sao Paulo' ↔ 'São Paulo', 'salvador' ↔ 'Salvador')
    choices = [r.nome for r in uf_records]
    result = process.extractOne(
        name,
        choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
        processor=rfuzz_utils.default_process,
    )
    if result is not None:
        _matched_name, _score, index = result
        return uf_records[index]

NEW Step 2 block (exact replacement):
    # Step 2: rapidfuzz fuzzy match — accent-folded query + choices so pure diacritic
    # differences score 100 instead of ~85 (e.g. 'Maringa' ↔ 'Maringá').
    # NOTE: rapidfuzz default_process does NOT fold accents — that step is done
    # explicitly here via _fold_accents (unicodedata NFKD + strip Mn).
    # processor=default_process then handles case normalisation and non-alnum stripping.
    folded_name = _fold_accents(name)
    choices = [_fold_accents(r.nome) for r in uf_records]
    result = process.extractOne(
        folded_name,
        choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
        processor=rfuzz_utils.default_process,
    )
    if result is not None:
        _matched_name, _score, index = result
        return uf_records[index]  # original accented record — fold is never written back

Do NOT change the haversine fallback (Step 3), the return None (Step 4), the function
signature, or any default parameter values (max_distance_km=15.0 TA-15 invariant).
Fix the module-level docstring Step 2 line to say "rapidfuzz token_sort_ratio with
explicit accent-folding via unicodedata (NFKD)" — do not leave the old false claim.
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_ibge.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>All existing test_ibge tests pass and _fold_accents is importable from ibge.py.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Add 4 accent-fold regression tests to test_ibge.py</name>
  <files>tests/unit/lanes/tripadvisor/test_ibge.py</files>
  <behavior>
    - test_ibge_accent_fold_maringa: "Maringa" PR → nome == "Maringá"
    - test_ibge_accent_fold_carambei: "Carambei" PR → nome == "Carambeí"
    - test_ibge_exact_match_still_works_curitiba: "Curitiba" PR → ibge_code == "4106902"
    - test_ibge_accent_fold_no_overmatch: "ZZZFantasia" PR → None (fold must not over-match)
    - All 4 are inside TestResolveMunicipio class
    - Fixture uses inline PR records (no CSV file I/O in unit tests), consistent with _make_records() pattern
  </behavior>
  <action>
Do NOT touch MINIMAL_CSV or _make_records(). TestLoadIbgeCsv::test_load_ibge_csv_from_file
asserts len(records) == 5; adding rows to MINIMAL_CSV would break that assertion.

Instead, add a second module-level CSV constant and a dedicated builder immediately after
the existing _make_records() function (after line 46, before the first test class):

    PR_ROWS_CSV = """\
    ibge_code,nome,uf,lat,lng
    4106902,Curitiba,PR,-25.4195,-49.2646
    4104659,Carambeí,PR,-24.9152,-50.0986
    4115200,Maringá,PR,-23.4205,-51.9333
    """


    def _make_pr_records() -> list[IbgeMunicipio]:
        """Build 3 Paraná IbgeMunicipio records for accent-fold tests (TA-03).

        Separate from _make_records() / MINIMAL_CSV so TestLoadIbgeCsv::test_load_ibge_csv_from_file
        len(records) == 5 assertion is never disturbed.
        """
        lines = PR_ROWS_CSV.strip().split("\n")
        _header, *rows = lines
        result = []
        for row in rows:
            ibge_code, nome, uf, lat, lng = row.split(",")
            result.append(
                IbgeMunicipio(
                    ibge_code=ibge_code,
                    nome=nome,
                    uf=uf,
                    lat=float(lat),
                    lng=float(lng),
                )
            )
        return result

Then add the following four test methods inside the existing TestResolveMunicipio class
(after the last haversine test, before the standalone test at the bottom). All four call
_make_pr_records(), NOT _make_records():

    # ---------------------------------------------------------------------------
    # Tests: resolve_municipio — accent-fold (TA-03 fix, 2026-06-30)
    # ---------------------------------------------------------------------------

    def test_ibge_accent_fold_maringa(self) -> None:
        """ASCII 'Maringa' must resolve to accented IBGE 'Maringá' (TA-03 accent fix).

        Before the _fold_accents fix, fuzz.token_sort_ratio('maringa', 'maringá') = 85.7
        which fell below the 88 threshold, causing a None return for 4 of 60 PR atrativos.
        """
        records = _make_pr_records()
        result = resolve_municipio("Maringa", "PR", records)
        assert result is not None, "Expected Maringá — accent fold must bridge ASCII→accented"
        assert result.nome == "Maringá"
        assert result.ibge_code == "4115200"

    def test_ibge_accent_fold_carambei(self) -> None:
        """ASCII 'Carambei' must resolve to accented IBGE 'Carambeí' (TA-03 accent fix)."""
        records = _make_pr_records()
        result = resolve_municipio("Carambei", "PR", records)
        assert result is not None, "Expected Carambeí — accent fold must bridge ASCII→accented"
        assert result.nome == "Carambeí"
        assert result.ibge_code == "4104659"

    def test_ibge_exact_match_still_works_curitiba(self) -> None:
        """Exact name 'Curitiba' (no accent needed) must still resolve after fold."""
        records = _make_pr_records()
        result = resolve_municipio("Curitiba", "PR", records)
        assert result is not None
        assert result.ibge_code == "4106902"

    def test_ibge_accent_fold_no_overmatch(self) -> None:
        """Accent-folding must not cause over-matching: 'ZZZFantasia' in PR must return None."""
        records = _make_pr_records()
        result = resolve_municipio("ZZZFantasia", "PR", records)
        assert result is None

Do NOT change the standalone test_resolve_municipio_default_max_distance_km_is_15 at
the bottom (TA-15 invariant guard).
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_ibge.py -x -q 2>&1 | tail -8</automated>
  </verify>
  <done>All tests in test_ibge.py pass including the 4 new accent-fold cases. No existing test regresses. Run count increased by 4. MINIMAL_CSV is unchanged; test_load_ibge_csv_from_file len(records)==5 assertion is untouched and passing.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| TripAdvisor → ibge.py | ASCII city name string crosses into fuzzy matcher; no PII, no auth |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-jbt-01 | Tampering | _fold_accents input | accept | Input is gtmData.cityName (string from TA HTML); no code execution path; NFKD is safe on arbitrary Unicode |
| T-jbt-02 | Information Disclosure | ibge_municipios.csv | accept | Public IBGE data, CC0 license, no PII |
</threat_model>

<verification>
Full TA unit suite must not regress:

    .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q 2>&1 | tail -10

Expected: 218+ passed (214 existing + 4 new), 0 failed.

Confirm _fold_accents strips correctly:

    .venv/bin/python -c "from brave.lanes.tripadvisor.ibge import _fold_accents; assert _fold_accents('Maringá') == 'Maringa'; assert _fold_accents('Carambeí') == 'Carambei'; print('fold OK')"

Confirm the false comment is gone — no line in ibge.py should claim default_process is accent-agnostic:

    grep -n "accent-agnostic" /Users/leandro/Projects/norteia/norteia-brave/brave/lanes/tripadvisor/ibge.py | grep -v "^#"
    # (should return empty)
</verification>

<success_criteria>
- _fold_accents("Maringá") == "Maringa" and _fold_accents("Carambeí") == "Carambei"
- resolve_municipio("Maringa", "PR", records) returns record with nome=="Maringá"
- resolve_municipio("Carambei", "PR", records) returns record with nome=="Carambeí"
- resolve_municipio("ZZZFantasia", "PR", records) returns None (no over-matching)
- resolve_municipio("Curitiba", "PR", records).ibge_code == "4106902" (regression guard)
- Returned record.nome is always the original accented IBGE name (fold not written back)
- All 214 pre-existing TA unit tests pass unchanged
- MINIMAL_CSV is untouched; TestLoadIbgeCsv::test_load_ibge_csv_from_file len(records)==5 passes
- unicodedata is the only new import (stdlib, no new package dependency)
- The false "accent-agnostic" claim is removed from the ibge.py comment
- Out of scope: Caiobá/district case (not an IBGE município — legitimately unmatched), coords/haversine changes, uf_geoids discovery, sweep changes, geo-linkage itself
</success_criteria>

<output>
Create `.planning/quick/260630-jbt-accent-fold-resolve-municipio-so-ascii-t/260630-jbt-SUMMARY.md` when done.
</output>
