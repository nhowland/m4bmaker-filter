# ADR-0005 (part of O-05/§14.3): Job Orchestrator persistence design

**Status:** Decided for implementation purposes — informal, pending
explicit Contributor confirmation, same footing as ADR-0004 (module
layout). Uses only `sqlite3` (stdlib) — no new dependency, no network
activity, so this does not carry the same "wait for approval" weight PRD
§17.1 rule 5 assigns to runtime/model dependencies; still flagged here for
visibility since PRD §14.3 explicitly calls the persistence contract an
open decision.
**Related PRD items:** §11 (all), §14.2, §14.3, D-15 through D-18

## Decision

Split persistence exactly along the line PRD §14.3 itself suggests:
**SQLite holds job state, transition history, and per-chunk durability
tracking. Transcript *content* (the actual recognized words) stays in the
versioned JSON `.m4bt.json` artifact** (`transcript.py`, written via
`storage.write_json_atomic`). SQLite never duplicates word content — it
only ever records "chunk N of job J is committed," never what chunk N
*contains*. This means the database and the JSON artifact can never
disagree about a word's text or timestamp, only (in the worst case, a
crash between the two writes) about whether a chunk is marked committed —
a much narrower, and closed, class of inconsistency than PRD §14.3 warns
against ("Do not allow an index/artifact mismatch to silently create a
completed job").

## Schema

```sql
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    progress_message TEXT NOT NULL DEFAULT '',
    progress_fraction REAL,
    error_code TEXT,
    error_message TEXT,
    resource_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    timestamp TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE transcription_chunks (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    chunk_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    owned_start_ms INTEGER NOT NULL,
    owned_end_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    committed_at TEXT,
    PRIMARY KEY (job_id, chunk_index)
);
```

## Rationale for each choice

- **`resource_json` as a JSON blob, not per-job-type columns.** The five
  job types (§11.1) have different resource needs (a `TranscriptionJob`
  needs a source path/model name; a `RenderJob` needs a render-plan
  reference). A wide, mostly-NULL table or five separate tables both add
  real complexity for no query benefit at this scale — nothing here needs
  to `WHERE resource_json->>'model' = ...` at SQL level. **Explicitly
  flagged as an MVP simplification, not a permanent design**: if a later
  gate needs to query on a resource field, that field should get promoted
  to a real column then, not before.
- **`transition()` (the only way to change `jobs.state`) re-validates
  against `jobs.py`'s `ALLOWED_TRANSITIONS` before writing.** This makes
  the state machine's rules enforced at the persistence boundary, not just
  hoped-for by callers — an attempted invalid transition raises
  `InvalidJobTransition` and writes nothing, rather than silently
  corrupting stored state.
- **Every transition also inserts a `job_events` row, in the same
  transaction.** Satisfies PRD §11.5 ("Persist user-visible job events")
  and gives pause-latency measurement (D-16's 30-second target) something
  real to measure against later — a `RUNNING`→`PAUSING`→`PAUSED` sequence's
  timestamps are already recorded.
- **`transcription_chunks` is separate from the transcript JSON**, even
  though the JSON `TranscriptSegment.status` field already carries a
  similar `COMPLETED`/`PENDING`/`FAILED` status. This is deliberate, not
  redundant: resuming a paused job needs to answer "which chunk do I start
  at?" cheaply and without parsing a potentially very large JSON file (up
  to 20 hours of transcript). The SQLite row answers that in a single
  indexed lookup; the JSON remains the authoritative source for the
  chunk's actual *content* once it's known to be committed.
- **No WAL mode, no multi-process locking design yet.** PRD §14.3 asks for
  "lock behavior across multiple app instances" — out of scope for this
  pass, since nothing in G1-G3 so far runs more than one process against
  the database. Flagged as a real gap for G5 (when the GUI actually opens
  a persistent connection) rather than silently assumed away.

## What this unblocks

With this in place, `transcription_orchestrator.py` can implement genuine
durable pause/resume: a chunk transcription commits atomically (JSON write
+ SQLite row, in that order — see the module for the exact ordering
rationale), a pause request stops scheduling new chunks after the current
one finishes, and a resumed job re-reads `transcription_chunks` to find the
first uncommitted index rather than restarting from zero.

## What remains open

- Multi-instance locking (deferred above).
- Migration strategy for `schema_meta`'s version beyond "schema_version=1
  exists" — no migration has ever been needed yet, so no migration runner
  has been written; PRD §14.3's "schema migration" requirement stays
  unresolved until a real second schema version exists to migrate to.
- Full render-recovery persistence (PRD O-05) is untouched by this ADR —
  it covers `TranscriptionJob` durability only, since that's the job type
  chunking.py's algorithm serves. `RenderJob` recovery is a G4 concern.
