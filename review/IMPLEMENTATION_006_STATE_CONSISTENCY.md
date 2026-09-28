# I006: native state consistency

Base: `9718a9d` (I005, pushed to the existing main branch). No paid inference was
used in this iteration. Public WorkBench results predate these changes and are
not evidence of an I006 task-score improvement.

## Reproduced defects

[Before log](evidence/iteration-006-before.txt): four existing-path tests fail.
Alternating engines sharing a store lose a completed turn; `update_state` is not
persisted; a returned snapshot mutates internal state; a stream consumer can
change the engine's next input through the returned message reference.

## Changes and validation

- Reload authoritative single-agent state at operation entry; persist explicit
  updates; deep-copy results, interruptions, snapshots and update inputs.
- Introduce atomic version checks in the shared Memory store and a local SQLite
  backend. Retain deletion versions to reject stale generations. Reject conflicts
  without automatically retrying and invalidate local state after failed saves.
- Seed specialist-turn versions without asserting that the outer topology's
  state is already protected.
- Test contention, stale processes, committed state after actual process exit,
  and a native conversation continuing in another process. Test the limitation
  that a conflict after dispatch does not undo the external effect.

The [first implementation run](evidence/iteration-006-first-validation.txt)
retains two test-setup errors (an incorrect enum name), with 26 passes.
The corrected [expanded run](evidence/iteration-006-expanded-validation.txt)
records **52 passes**, including existing native topology and concurrency checks.
The sandbox-wide run is retained separately: its five failures/errors came from
blocked local HTTP servers and a blocked dependency download. The subsequent
[full-suite log](evidence/iteration-006-full-suite.txt) records the rerun with those
environment permissions. Type checking, lint and build logs remain alongside it.

## Open enterprise requirements

This is partial closure of C09, not all of state/recovery work. Redis/Postgres
versioned writes, outer topology state, exclusive/fenced ownership, distributed
budget reservations, durable effect intent/receipt/reconciliation, authenticated
approval consumption, storage migrations and production load/recovery acceptance
remain open. See the [migration and deployment limits](../docs/STATE_CONSISTENCY.md).
No new algorithm or exactly-once arbitrary tool guarantee is claimed.
