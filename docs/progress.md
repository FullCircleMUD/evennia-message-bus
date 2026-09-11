# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-09-11 — Every refusal logs before it raises

Ten sites that used to raise silently now write to `messagebus.log` first. The boot refusals — a
missing or blank `MESSAGEBUS_INSTANCE_ID`, and a bus alias resolving to the game's own database —
plus the unmigrated-bus-table refusal, the three registration refusals, and the self-addressed
`send()`. Each logs at ERROR with the exception's own text, built once and used in both channels.

Two are behaviour changes rather than narration. `check_instance_id()` now distinguishes an unset
setting from one set to a blank string, since a blank one is usually an environment variable that
did not expand. And a message that times out **with** a return address now logs at WARN on the side
that gave it up — it replies `undeliverable_reply` and deletes, so the sender recorded the outcome
but the receiver's own log showed nothing, making an abandoned message indistinguishable from one
that never arrived.

The line carries what a diagnosis needs: pk, kind, peer, age against the type's timeout, the alias,
the class already holding a kind and the one refused. Never credentials — `check_bus_database()`
reports name and host only, which is CF-14's rule on a second channel.

Four of the five `send()` refusals stay silent, as do `poll`, `delete` and `_reply`'s guarded skips.
The reasons are in the test plan's `LG` prose so they read as decisions rather than gaps.

**What proves it**

LG-09 to LG-18, all reading `messagebus.log` back off disk — none mock `bus_log`, because a mocked
call site would let a binding pointed at the wrong file keep passing. 112 tests pass.

LG-12 (no password in the refusal line) was mutation-tested both ways: leaking the whole `DATABASES`
entry fails it on the password, and removing the log call fails it on the guard that requires a line
to exist at all. It had passed vacuously against an empty file before that guard was added.

## 2026-09-11 — The database is declared to evennia-database-cascade

`db_spec.py` carries the `AliasSpec`; the cascade derives the `DATABASES` entry, the router and the
migration list from it. `db_router.py` and the hand-rolled resolver are gone, and `dj-database-url`
with them. `BUS_ALIAS` lives in `config.py`.

The spec refuses the shared `DATABASE_URL` rung — a position, not a table-collision constraint: the
bus is the transport *between* instances, so it must not live inside any one instance's database.
The previous resolver treated the shared rung as legitimate; that was wrong for what this library is
for. A deployment that wants the bus on a shared server says so explicitly with
`DATABASE_URL_MESSAGEBUS` — and `check_bus_database()` refuses the boot if that points at the game's
own database (identity comparison including `TEST["NAME"]`, `evennia-archive`'s pattern).

`describe_bus_database()` reports identity only — name and host, SQLite paths resolved through
symlinks. Which rung placed the database is the cascade's knowledge, in `cascade.log`.

**What proves it**

The suite: 102 tests, with `tests/test_settings.py` resolving the alias through `configure()`, so
every run exercises the real consumer path. DS-01..DS-04 pin the spec and prove discovery,
resolution and routing end to end; CF-16 pins the boot refusal. CF-07..CF-10, CF-12 and RT-03..RT-07
retired with the code they asserted.

Live, on the demo gamedirs: `evennia cascade_migrate` rebuilt a deleted bus database with exactly
the bus table in it; both instances booted with startup lines naming one resolved file through the
symlink; a `test` round trip game-a → game-b → game-a; the unknown-kind reject path landing in both
logs; zero rows left on the bus.

## 2026-09-11 — Logging binds through evennia-logging-extension

`log.py` is the standard three-line binding: `bus_log = make_logger("messagebus.log")`. The bound name
and the filename are unchanged, so no call site moved. The extension is a hard dependency and is not
on PyPI — it needs an editable install from the sibling checkout in any venv that runs the library,
which `examples/requirements.txt` now does.

What the library gains is the pre-reactor window. The extension writes synchronously when there is no
reactor and hands to Evennia when there is one, so a line is no longer lost for being emitted too
early. Nothing in the library logs there yet.

**What proves it**

The suite: 107 tests. LG-07 now reads `messagebus.log` back off disk rather than mocking Evennia's
`log_file` — a mocked call site would have passed against a binding pointed at the wrong file.

`examples/game-a`, booted on a truncated log directory:

- One startup line, one timestamp, Evennia's format. `start_message_bus` runs in `at_server_start`,
  so the Server process writes it and the launcher and Portal do not.
- `bus_log(..., level="WARN")` and `bus_log(..., level="ERROR", trace=True)` in-game both landed, the
  second with the `ZeroDivisionError` traceback attached.
- No `pre-startup.log` and no other stray in `server/logs/`.

**Retired cases**

LG-01 and LG-05 asserted the old shim's own behaviour and are gone. LG-01's claim — lines reach the
library's own file — is what LG-07's read-back now proves.

## 2026-08-31 — The bus database resolves from the environment

`messagebus_database()` takes the first of `DATABASE_URL_MESSAGEBUS`, `DATABASE_URL`, or a local
SQLite path — the same shape as FCM's other aliases, so settings do not change between local and
deployed.

Rung two shares the game's database, which is right for instances already running against one
Postgres and wrong for instances with their own. No instance can tell those apart from its own
settings, so the resolver neither guesses nor warns; the startup line reports which rung it landed on
instead, and two logs side by side answer the question.

**What proves it**

CF-07..CF-15 cover the chain, its precedence, and the description. Both demo gamedirs use the resolver
and report the same resolved path, which they did not before — a SQLite bus is shared by symlink, so
the configured paths differ and only the resolved one is comparable.

**What is not proven**

Rungs one and two have never run against a real PostgreSQL. Everything so far is rung three.

## 2026-08-31 — Round trip between two live instances

Two Evennia instances, each with its own game database, exchanging messages through one shared bus.
Set up by following the settings document (now [archive/messagebus-settings.md](archive/messagebus-settings.md)) from a bare `evennia --init`, so
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
