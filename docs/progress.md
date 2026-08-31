# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-08-31 — Round trip between two live instances

Two Evennia instances, each with its own game database, exchanging messages through one shared bus.
Set up by following [messagebus-settings.md](messagebus-settings.md) from a bare `evennia --init`, so
the document is now what was exercised rather than a description of it.

**What proves it**

`examples/game-a` and `game-b`, on shifted ports, sharing `messagebus.db3` by symlink. A consumer
message type sent from game-a broadcast on game-b, and its reply broadcast back on game-a. A
self-addressed `ping` on game-a was consumed along with its own `ping_received`, which exercises the
self-reply path end to end.

The router held: `evennia_message_bus_message` exists in `messagebus.db3` and in neither instance's
`evennia.db3`, and both gamedirs resolve to the same inode.

**What the walkthrough corrected**

- The settings document migrated before sharing the database, which would have produced two private
  bus files and a symlink laid over one of them. Sharing now comes first.
- `start_message_bus` logged nothing, so an empty `messagebus.log` meant either "running fine" or
  "never started" with no way to tell. It now logs the instance id, the interval and the registered
  kinds (LG-06, LG-07).
- The log shim added its own timestamp on top of the one Evennia's `log_file` already writes. Removed;
  Evennia's is UTC and matches `server.log`.

**What is still not proven**

PostgreSQL — everything so far is SQLite. The timeout / `undeliverable_reply` path and `unknown_kind`
have unit coverage but have not been seen in a live pair.

## 2026-08-30 — Built, test-first, and green

The library does what [design.md](design.md) describes. Every case in [test-plan.md](test-plan.md) has
a test, and the suite passes.

**What exists**

- `Message` on its own database alias, with `MessageBusRouter` keeping it off the game database
- `MessageType` — one class per message type, owning the kind, timeout, payload contract,
  self-addressing policy, `send` and `handle`
- `register` / `get_type` — duplicate kind raises, a subclass replaces
- `process_inbox` and `start_message_bus` — the lifecycle and the Twisted polling loop
- `ping`, `ping_received`, `unknown_kind`, `undeliverable_reply` as library-registered types
- Its own `messagebus.log`

**What proves it**

`python runtests.py` runs the whole plan and passes. The plan's coverage trail is checked in both
directions by the library-standards linter: no case without a test, no test without a case.

Cases were written before the code, and the first run failed at `ModuleNotFoundError` on the router,
which is the point. Two defects surfaced in the tests rather than the implementation: a case comparing
primary keys on rows the loop had already deleted (Django nulls the pk on delete), and a `db_default`
assertion that would have passed against an unset field, since Django's sentinel is `NOT_PROVIDED`
rather than `None`.

**What is not proven**

No consumer. Nothing has run between two live instances, and nothing has run on PostgreSQL — the suite
uses two SQLite aliases. `db_default=Now()` and the `bus_table_exists` introspection are the two places
most likely to behave differently there.

## 2026-08-30 — Scaffolded

The repository exists in the shape every library under `libraries/` uses, and the design conversation
that preceded it is recorded.

**What exists**

- The standard layout: `pyproject.toml`, `runtests.py`, `README.md`, `CLAUDE.md`, `docs/`, `src/`
  layout, `tests/` settings.
- [design.md](design.md) — the separate bus database, `to_instance` / `from_instance` addressing,
  `MESSAGEBUS_INSTANCE_ID` with a boot-time check, payload identity as a consumer concern, and what
  the library will and will not ship.

**What proves it**

`pip install -e .` and `python runtests.py` both succeed in a dedicated venv.
