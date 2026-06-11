# Engineering Journal

Append-only narrative record of working sessions: what was tried, what
broke, what was measured, what was learned. One file per significant
session, named `YYYY-MM-DD.md`. If a day gets a second session, rename the
first to `-a` and continue `-b`, `-c`, ... so the files sort in order.

How this relates to the other documents:

- **Journal (here)** — append-only story. Never rewritten after the
  session; its value is being a true record. Corrections go in a later
  entry, not edits to an old one.
- **Decision records (`docs/decisions/`)** — append-only judgments (ADR
  pattern). When a session produces a real decision, it gets an ADR and the
  journal links to it.
- **Engineering plan (`docs/engineering-plan.md`)** — the one mutable
  document: current backlog and status. The journal records how items got
  onto it and what landing them revealed.
- **Research notes (`docs/research/`)** — reference material organized by
  topic rather than by date; measurements and protocol findings end up
  there, with the journal pointing at them.

Entries should favor what cannot be reconstructed from the git log:
dead ends, surprises, reasoning, measurements, and the operational context
around the commits.
