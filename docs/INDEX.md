# Index

Every document in this wiki. An un-indexed document is invisible, so index new ones as they land.

## Using the library

| Document | What it covers |
|---|---|
| [installing.md](installing.md) | Everything a consumer does to get the bus running — the apps, the instance id, the cascade's call, sharing the database, migrating, starting the loop |

## Design

| Document | What it covers |
|---|---|
| [design.md](design.md) | How the library works and why — the bus database, instance identity, message types, the lifecycle, transactions |

## Project state

| Document | What it covers |
|---|---|
| [progress.md](progress.md) | Milestone log, newest first — what became true and what proves it |
| [test-plan.md](test-plan.md) | Every case the library covers and the test covering it |

## Integration

| Document | What it covers |
|---|---|
| [interoperability.md](interoperability.md) | This library against every sibling in `libraries/` |

## Archive

Historical context, not authoritative. Material in [`archive/`](archive/) is preserved per the
"don't delete; supersede" principle.

- **[archive/messagebus-settings.md](archive/messagebus-settings.md)** — the original install
  walkthrough, from when a consumer hand-wrote the `DATABASES` entry and appended the library's own
  router. Both belong to `evennia-database-cascade` now, so the live instructions are
  [installing.md](installing.md). Kept for the reasoning it carries on instance-id uniqueness and on
  sharing one SQLite bus by symlink, which the current document states more briefly.
