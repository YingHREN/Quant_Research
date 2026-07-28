# Unified Benchmark Resumable Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a content-addressed, checksummed SQLite cache that safely reuses unified downside benchmark statistical and rule predictions without changing research results.

**Architecture:** A pure JSON/zlib DataFrame codec provides non-executable serialization. An immutable SQLite artifact store owns identity, checksum verification, transactions, inspection, and pruning. The benchmark runner computes explicit data/config/code fingerprints, restores only fully validated stages, delays writes until the report succeeds, and records all cache decisions in its manifest.

**Tech Stack:** Python 3.9, pandas, NumPy, SQLite, canonical JSON, zlib, SHA-256, `unittest`.

## Global Constraints

- Cache database is `data/unified_benchmark_cache.db`; never write cache rows to any price, online analysis, or shadow database.
- Cache only `statistical_predictions` and `rule_predictions`; labels, strata, metrics, promotion gates, and reports are recomputed every run.
- Do not add PyArrow, pickle, msgpack, or another dependency.
- Cache identity must include content, point-in-time assignments, cohort, model configuration, versions, relevant tracked source content, and dirty-worktree state.
- A dirty relevant worktree disables cache reads and writes.
- Corruption, schema mismatch, SQLite failure, or write failure must not alter benchmark output.
- Stage artifacts are immutable. Exact retries are idempotent; conflicting payloads fail closed.
- New artifacts are committed only after the report publishes successfully.
- `--no-cache` and `--rebuild-cache` are mutually exclusive.
- Cache status and elapsed time are engineering telemetry, never prediction evidence.
- Use red-green-refactor and commit each task independently.

---

### Task 1: Safe DataFrame Bundle Codec

**Files:**
- Create: `research/benchmark_cache_codec.py`
- Create: `tests/test_benchmark_cache_codec.py`

**Interfaces:**
- Produces:
  - `CACHE_CODEC = "typed-json-v1+zlib"`
  - `encode_frame_bundle(frames: Mapping[str, pd.DataFrame]) -> tuple[bytes, str, int]`
  - `decode_frame_bundle(payload: bytes, expected_checksum: str, *, maximum_uncompressed_bytes: int = 1_000_000_000) -> dict[str, pd.DataFrame]`

- [ ] **Step 1: Write failing round-trip and deterministic encoding tests**

```python
def test_codec_round_trips_nullable_types_dates_tuples_and_missing_values():
    source = {
        "ridge_down": pd.DataFrame(
            {
                "ticker": ["AAA", "BBB"],
                "observation_date": pd.to_datetime(
                    ["2026-01-02", "2026-01-05"]
                ),
                "horizon": pd.Series([5, 20], dtype="int64"),
                "predicted_event": pd.Series(
                    [True, pd.NA], dtype="boolean"
                ),
                "predicted_score": [0.7, np.nan],
                "evidence": [("volume", "trend"), tuple()],
            }
        )
    }
    first_payload, first_checksum, first_rows = encode_frame_bundle(source)
    second_payload, second_checksum, second_rows = encode_frame_bundle(source)
    assert first_payload == second_payload
    assert first_checksum == second_checksum
    assert first_rows == second_rows == 2
    restored = decode_frame_bundle(first_payload, first_checksum)
    pd.testing.assert_frame_equal(restored["ridge_down"], source["ridge_down"])
```

- [ ] **Step 2: Run the codec test and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_benchmark_cache_codec -v
```

Expected: import failure for `research.benchmark_cache_codec`.

- [ ] **Step 3: Implement the typed canonical JSON schema**

Encode every frame as:

```python
{
    "schema_version": "benchmark-frame-bundle-v1",
    "frames": [
        {
            "name": "ridge_down",
            "columns": [
                {"name": "ticker", "dtype": "object", "kind": "string"},
                {
                    "name": "observation_date",
                    "dtype": "datetime64[ns]",
                    "kind": "datetime",
                },
            ],
            "records": [["AAA", "2026-01-02T00:00:00"], ...],
        }
    ],
}
```

Support only string/object scalars, finite or missing numeric values, nullable
Boolean, integer, datetime, and tuple/list values. Reject dictionaries,
arbitrary objects, duplicate frame names, duplicate columns, timezone-aware
datetimes, infinities, and non-string frame keys. Sort frame names but preserve
column and row order. Serialize with `sort_keys=True`, compact separators,
`allow_nan=False`, UTF-8, then zlib-compress and SHA-256 the compressed bytes.

- [ ] **Step 4: Write failing corruption and decompression-limit tests**

```python
def test_codec_rejects_checksum_truncation_schema_and_expansion_limit():
    payload, checksum, _ = encode_frame_bundle({"ridge_down": frame()})
    with pytest.raises(ValueError, match="checksum"):
        decode_frame_bundle(payload + b"x", checksum)
    with pytest.raises(ValueError, match="compressed payload"):
        decode_frame_bundle(payload[:-3], sha256(payload[:-3]).hexdigest())
    with pytest.raises(ValueError, match="maximum"):
        decode_frame_bundle(payload, checksum, maximum_uncompressed_bytes=8)
```

Use `unittest` assertions rather than pytest in the actual test file.

- [ ] **Step 5: Implement streaming bounded decompression and strict decode**

Use `zlib.decompressobj()` in bounded chunks. Stop before output exceeds
`maximum_uncompressed_bytes`; require `decompressor.eof`, no unused trailing
bytes, valid UTF-8, valid JSON, exact schema keys, and supported dtype/kind
combinations. Reconstruct exact pandas dtypes and re-encode the restored bundle
in tests to prove canonical identity.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
./venv/bin/python -m unittest tests.test_benchmark_cache_codec -v
git diff --check
```

Commit:

```bash
git add research/benchmark_cache_codec.py tests/test_benchmark_cache_codec.py
git commit -m "research: add safe benchmark cache codec"
```

---

### Task 2: Immutable SQLite Artifact Store

**Files:**
- Create: `research/unified_benchmark_cache.py`
- Create: `tests/test_unified_benchmark_cache.py`

**Interfaces:**
- Consumes:
  - `encode_frame_bundle`
  - `decode_frame_bundle`
- Produces:
  - `BenchmarkCacheIdentity`
  - `BenchmarkCacheArtifact`
  - `BenchmarkCacheRead`
  - `UnifiedBenchmarkCacheStore.read(identity) -> BenchmarkCacheRead`
  - `UnifiedBenchmarkCacheStore.commit(artifacts, *, repair_corrupt: bool = False) -> int`
  - `UnifiedBenchmarkCacheStore.status() -> pd.DataFrame`
  - `UnifiedBenchmarkCacheStore.verify() -> pd.DataFrame`
  - `UnifiedBenchmarkCacheStore.prune(keep_per_stage: int, apply: bool = False) -> pd.DataFrame`

- [ ] **Step 1: Write failing identity and immutable commit tests**

```python
def identity(stage="statistical_predictions"):
    return BenchmarkCacheIdentity(
        study_version="unified-downside-walkforward-v2",
        stage=stage,
        database_fingerprint="a" * 64,
        assignment_fingerprint="b" * 64,
        config_fingerprint="c" * 64,
        code_fingerprint="d" * 64,
        dependency_artifact_key=None,
        schema_version="unified-benchmark-cache-v1",
    )

def test_commit_is_atomic_idempotent_and_rejects_conflicting_payload():
    store = UnifiedBenchmarkCacheStore(database)
    artifact = BenchmarkCacheArtifact.from_frames(
        identity(), {"ridge_down": frame()}
    )
    assert store.commit([artifact]) == 1
    assert store.commit([artifact]) == 0
    conflicting = replace(
        artifact,
        payload=artifact.payload + b"x",
        payload_checksum=sha256(artifact.payload + b"x").hexdigest(),
    )
    with self.assertRaisesRegex(ValueError, "conflict"):
        store.commit([conflicting])
```

- [ ] **Step 2: Run the store test and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_unified_benchmark_cache -v
```

- [ ] **Step 3: Implement validated identities and schema**

`BenchmarkCacheIdentity.artifact_key` is SHA-256 over canonical identity JSON.
Validate stage membership, 64-character fingerprints, optional dependency key,
and nonblank versions. `rule_predictions` requires a dependency artifact key;
`statistical_predictions` forbids one.

Create `benchmark_cache_artifacts` exactly as defined in the design. Store
canonical identity JSON, compressed payload, checksum, row count, size, and
created timestamp. Enable WAL, foreign keys, and `busy_timeout=5000`.

- [ ] **Step 4: Implement transactional multi-artifact commit**

Validate every artifact and every existing key before inserts. Use one
`BEGIN IMMEDIATE` transaction. Exact checksum retry is idempotent. A different
checksum for an existing valid key raises `ValueError` and rolls back all
pending rows. With `repair_corrupt=True`, replacement is allowed only after the
store rereads the exact existing row and proves checksum, codec, schema, or
semantic corruption; repair happens inside the same transaction. SQLite
failures become stable `RuntimeError` messages without raw SQL or paths.

- [ ] **Step 5: Write and pass read/verify/corruption tests**

```python
def test_read_returns_hit_or_corrupt_without_executing_payload():
    store.commit([artifact])
    hit = store.read(identity())
    assert hit.status == "hit"
    pd.testing.assert_frame_equal(hit.frames["ridge_down"], frame())
    tamper_payload(database, artifact.identity.artifact_key)
    corrupt = store.read(identity())
    assert corrupt.status == "miss_corrupt"
    assert corrupt.frames is None
```

`read` returns `miss` for absent keys and `miss_corrupt` for checksum, codec,
identity, or semantic failures. `verify` visits every row and returns stable
status/reason columns without mutating the database.

- [ ] **Step 6: Write and pass safe prune tests**

```python
def test_prune_is_preview_only_until_apply():
    preview = store.prune(keep_per_stage=1)
    assert preview["would_delete"].sum() == 2
    assert len(store.status()) == 3
    applied = store.prune(keep_per_stage=1, apply=True)
    assert applied["deleted"].sum() == 2
    assert len(store.status()) == 1
```

Sort within each stage by `created_at DESC, artifact_key DESC`. Validate
`keep_per_stage` as a positive integer. Delete only exact artifact keys selected
by the preview algorithm.

- [ ] **Step 7: Run focused tests and commit**

```bash
./venv/bin/python -m unittest \
  tests.test_benchmark_cache_codec \
  tests.test_unified_benchmark_cache -v
git diff --check
git add research/unified_benchmark_cache.py \
  tests/test_unified_benchmark_cache.py
git commit -m "research: add immutable unified benchmark cache store"
```

---

### Task 3: Fingerprints and Runner Integration

**Files:**
- Modify: `research/run_unified_downside_benchmark.py`
- Modify: `tests/test_run_unified_downside_benchmark.py`
- Create: `tests/test_unified_benchmark_cache_integration.py`

**Interfaces:**
- Adds to `BenchmarkConfig`:
  - `cache_database: Path = Path("data/unified_benchmark_cache.db")`
  - `cache_enabled: bool = True`
  - `rebuild_cache: bool = False`
- Adds to `BenchmarkDependencies`:
  - `database_fingerprint: Callable[[BenchmarkInputs, BenchmarkConfig], str]`
  - `assignment_fingerprint: Callable[[BenchmarkInputs], str]`
  - `code_fingerprint: Callable[[], tuple[str, bool]]`
  - `cache_store_factory: Callable[[Path], UnifiedBenchmarkCacheStore]`
- Produces:
  - `_database_fingerprint(inputs, config) -> str`
  - `_assignment_fingerprint(inputs) -> str`
  - `_config_fingerprint(config, inputs, stage) -> str`
  - cache manifest under `manifest["cache"]`

- [ ] **Step 1: Write failing same-size price-revision fingerprint test**

```python
def test_database_fingerprint_changes_for_same_shape_price_revision():
    first = inputs()
    second_prices = first.prices.copy()
    second_prices.loc[0, "Close"] += 0.01
    second = replace(first, prices=second_prices)
    assert _database_fingerprint(first, config()) != _database_fingerprint(
        second, config()
    )
```

- [ ] **Step 2: Implement canonical input, assignment, and config fingerprints**

Hash sorted, typed pandas content with explicit columns and index metadata.
Include cohort/reference histories actually consumed by feature construction,
prices, regimes, assignments, feature order, horizons, folds, start date,
training threshold, neutral bands, downside thresholds, pressure regimes, and
version constants. Exclude `minimum_group_samples` from stage fingerprints.

`code_fingerprint()` hashes tracked contents selected by `git ls-files` and
runs `git status --porcelain`, restricted to the benchmark, feature, risk-rule,
and cache modules. Return the content-derived SHA-256 and a Boolean dirty flag.
This prevents documentation-only or merge-only commits from invalidating valid
model artifacts. Command failure returns dirty and disables caching.

- [ ] **Step 3: Write failing cold/hot integration test**

```python
def test_hot_run_skips_builders_and_matches_cold_results():
    calls = Counter()
    cold = run_benchmark(
        config(),
        dependencies=dependencies(calls, code=("e" * 64, False)),
    )
    assert calls == Counter(statistical=1, rules=1)
    calls.clear()
    hot = run_benchmark(
        config(),
        dependencies=dependencies(calls, code=("e" * 64, False)),
    )
    assert calls == Counter()
    pd.testing.assert_frame_equal(hot.metrics, cold.metrics)
    pd.testing.assert_frame_equal(
        hot.fold_comparisons, cold.fold_comparisons
    )
    assert hot.manifest["promotion_gate"] == cold.manifest["promotion_gate"]
```

- [ ] **Step 4: Implement cache lookup and delayed commit**

After `load_inputs`, compute identities. For each stage:

1. read artifact unless disabled/rebuild/dirty;
2. validate model keys, cohort, horizons, folds, specification and dtypes;
3. on hit use cached frames without calling the builder;
4. on miss or corruption call the builder and hold an uncommitted artifact;
5. build rule identity with statistical artifact key;
6. run labels, strata, metrics and report normally;
7. after `_publish_atomic` succeeds, commit both pending artifacts in one
   transaction; pass `repair_corrupt=True` only for explicit
   `--rebuild-cache`.

Cache read/write exceptions degrade to cold computation or
`cache_write_failed`; builder, label, evaluation, and report exceptions retain
their existing failure behavior.

- [ ] **Step 5: Add invalidation, dirty-tree, and failed-run tests**

Test separately:

- same-size price correction;
- assignment interval change;
- folds/horizons/feature order/rule version/code fingerprint change;
- `minimum_group_samples` change hits cache but produces fresh evaluation;
- dirty tree calls both builders and leaves store empty;
- evaluation failure leaves store empty;
- publish failure leaves store empty;
- corrupt statistical cache rebuilds both stages;
- corrupt rule cache reuses statistical and rebuilds only rules.

- [ ] **Step 6: Add CLI flags and validation**

Parser additions:

```python
parser.add_argument(
    "--cache-database",
    type=Path,
    default=Path("data/unified_benchmark_cache.db"),
)
parser.add_argument("--no-cache", action="store_true")
parser.add_argument("--rebuild-cache", action="store_true")
```

Reject both flags together. Ensure stdout/report JSON contains no environment
variables or secrets.

- [ ] **Step 7: Run focused and compatibility tests**

```bash
./venv/bin/python -m unittest \
  tests.test_run_unified_downside_benchmark \
  tests.test_unified_benchmark_cache_integration \
  tests.test_run_expanded_walkforward_study \
  tests.test_run_pressure_downside_study -v
git diff --check
```

- [ ] **Step 8: Commit**

```bash
git add research/run_unified_downside_benchmark.py \
  tests/test_run_unified_downside_benchmark.py \
  tests/test_unified_benchmark_cache_integration.py
git commit -m "research: resume unified benchmark from safe cache"
```

---

### Task 4: Cache Management CLI

**Files:**
- Create: `manage_unified_benchmark_cache.py`
- Create: `tests/test_manage_unified_benchmark_cache.py`
- Modify: `docs/dashboard.md`

**Interfaces:**
- Produces CLI commands:
  - `status`
  - `verify`
  - `prune --keep-per-stage N [--apply]`

- [ ] **Step 1: Write failing read-only status and verify CLI tests**

```python
def test_status_and_verify_emit_stable_json_without_secrets():
    store_with_artifact(database)
    status = run_cli("status", database)
    verify = run_cli("verify", database)
    assert status["ok"] is True
    assert status["artifact_count"] == 1
    assert verify["invalid_count"] == 0
    assert "API_KEY" not in json.dumps(status)
```

- [ ] **Step 2: Implement parser and stable JSON responses**

Arguments:

```text
command
--database data/unified_benchmark_cache.db
--keep-per-stage 3
--apply
```

Only `prune` accepts `--apply` and `--keep-per-stage`. Known errors return exit
code 1 with `{"ok": false, "error_code": "benchmark_cache_command_failed"}`.
Do not include raw exceptions or absolute paths.

- [ ] **Step 3: Write failing preview/apply CLI tests**

```python
def test_prune_requires_explicit_apply():
    preview = run_cli("prune", database, "--keep-per-stage", "1")
    assert preview["deleted_count"] == 0
    assert preview["would_delete_count"] == 2
    applied = run_cli(
        "prune", database, "--keep-per-stage", "1", "--apply"
    )
    assert applied["deleted_count"] == 2
```

- [ ] **Step 4: Implement prune output and document operations**

Document cold run, hot run, `--no-cache`, `--rebuild-cache`, status, verify,
preview prune, apply prune, the dirty-worktree rule, and that cache files never
enter Git.

- [ ] **Step 5: Run focused tests and commit**

```bash
./venv/bin/python -m unittest \
  tests.test_manage_unified_benchmark_cache \
  tests.test_unified_benchmark_cache -v
git diff --check
git add manage_unified_benchmark_cache.py \
  tests/test_manage_unified_benchmark_cache.py docs/dashboard.md
git commit -m "research: manage unified benchmark cache safely"
```

---

### Task 5: Real Cold/Hot Verification and TODO Update

**Files:**
- Modify: `docs/modeling-todo.md`
- Do not add: `data/unified_benchmark_cache.db`

**Interfaces:**
- Produces verified local cache and timing evidence.

- [ ] **Step 1: Run full verification before real cache state**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/unified-cache-pre \
  ./venv/bin/python -m unittest discover -s tests -v
```

Expected: zero failures.

- [ ] **Step 2: Hash protected databases**

```bash
shasum -a 256 \
  data/research_prices.db data/prices.db \
  data/analysis_cache.db data/downside_shadow.db
```

Save the four hashes outside Git for post-run comparison.

- [ ] **Step 3: Run the real cold benchmark**

```bash
./venv/bin/python -m research.run_unified_downside_benchmark \
  --database data/research_prices.db \
  --cache-database data/unified_benchmark_cache.db \
  --output-directory reports
```

Expected manifest:

- cache enabled;
- both stages `miss_rebuilt`;
- `write_status=committed`;
- cohort remains 240;
- research results match the previous unified v2 report.

- [ ] **Step 4: Run the real hot benchmark**

Run the same command. Expected:

- both stages `hit`;
- statistical and rule builder work skipped;
- labels, evaluation, and publication still execute;
- metrics, fold comparisons and promotion gate match the cold run;
- only timing and cache telemetry differ.

- [ ] **Step 5: Verify cache and protected database hashes**

```bash
./venv/bin/python manage_unified_benchmark_cache.py \
  verify --database data/unified_benchmark_cache.db
shasum -a 256 \
  data/research_prices.db data/prices.db \
  data/analysis_cache.db data/downside_shadow.db
```

Expected: every cache artifact valid and all four protected hashes unchanged.

- [ ] **Step 6: Update the global TODO with measured evidence**

Record:

- cold and hot total time;
- per-stage hit/miss state;
- artifact count and bytes;
- checksum verification result;
- protected database hash equality;
- remaining work, if any.

- [ ] **Step 7: Run final verification**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/unified-cache-final \
  ./venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/unified-cache-compile \
  ./venv/bin/python -m compileall -q research web tests
git diff --check
```

- [ ] **Step 8: Commit documentation only**

```bash
git add docs/modeling-todo.md
git commit -m "docs: record unified benchmark cache verification"
```

Do not add the cache database, SQLite WAL/SHM files, or any provider secrets.
