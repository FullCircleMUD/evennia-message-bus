# CLAUDE.md

> **Project-wide working rules and cross-repo context live in the FCM umbrella repo's `CLAUDE.md`**,
> loaded automatically when you work from the umbrella root. If you opened this repo directly instead
> of via the umbrella, relaunch from the umbrella root for the full context. This file holds only this
> repo's specific instructions.

Instructions for Claude (and other LLM agents) working in this repository.

## What this project is

`evennia-message-bus` lets separate [Evennia](https://www.evennia.com/) instances send messages to each
other through a database of its own. The instances need not be shards of one game — vanilla Evennia is
the assumed case. Tagline: **"Let your Evennia servers talk to each other."**

For the big-picture overview, read [README.md](README.md).
For the design wiki, read [docs/INDEX.md](docs/INDEX.md).

## Project status

**Working, no consumer game yet.** The suite passes and a round trip runs between the demo instances
in `examples/`. Untried on PostgreSQL. For current state read [docs/progress.md](docs/progress.md).

## Where to read first

1. [README.md](README.md) — what it is and the problem it solves.
2. [docs/design.md](docs/design.md) — how it works and why.
3. [docs/test-plan.md](docs/test-plan.md) — every case covered. **Behavioural change starts here**,
   not in the code.
4. [docs/progress.md](docs/progress.md) — what exists and what proves it.

`libraries/evennia-shards/docs/cross-shard-message-bus.md` is the implementation this one generalises
— useful for the transport and the rationale. Its `obj_msg` / `account_msg` / `room_msg` /
`flush_from_cache` kinds do **not** carry over; they read primary keys out of payloads, which only
works inside a shared database.

## Load-bearing architectural principles

Every implementation decision must respect them.

1. **The library does not own game concepts.** Rooms, characters, items and zones belong to the
   consumer game. The library provides a transport.
2. **No FCM-specific assumptions.** Written alongside work on FullCircleMUD. Zone names, economy
   concepts, NFT references, FCM typeclass names — all stay in FCM. Default to "consumer concern" when
   uncertain.
3. **Vanilla Evennia first.** This is not a shards component. It must be fully useful between two
   ordinary Evennia instances that know nothing about sharding. Anything requiring a shared game
   database is out.
4. **Identity is the consumer's problem, and the bus never reads a payload.** Routing is on
   `to_instance` and `kind` only. Separate instances have separate databases, so a primary key means
   nothing across them; a consumer needing to name an object across instances mints its own identifier,
   puts it in the payload, and resolves it in its own handler.
5. **Routing is an argument, not derived.** The sender names the destination instance. The library
   never queries to work out where something lives — that is only possible with a shared database, and
   we do not assume one.
6. **Fail at boot, not at runtime.** A missing `MESSAGEBUS_INSTANCE_ID` stops the game starting, in
   `AppConfig.ready()`. A misconfigured bus must never present as a healthy game with a quietly broken
   transport.
7. **Test-first.** A case lands in [docs/test-plan.md](docs/test-plan.md), then a test, then the code.
   The linter errors on a test the plan does not name, and on a plan entry naming a test that does not
   exist.

## Out of scope

Decided as questions arise. Rulings so far:

- **Cross-instance object identity.** The library does not mint, map or resolve identifiers for game
  objects. See principle 4.
- **Message kinds that address a game object.** Nothing shipped may read a pk — or any other
  identifier — out of a payload.
- **Persistent messaging.** No mail, no store-and-forward. See "What it is not" in
  [docs/design.md](docs/design.md).
- **Peer discovery.** Instances are named by a source-controlled constant on each side.
- **Concurrent draining.** One poller per instance id, documented rather than enforced with row locks.

## Working conventions

- **Behavioural change starts in the test plan.** Add the case, write the test, then implement. Fill
  the **Test function** column when the test exists — it is a coverage claim and the linter checks it
  both ways.
- **Editing design docs.** Update `docs/` whenever a decision is made or refined. Capture the *why*.
  Index new documents in [docs/INDEX.md](docs/INDEX.md).
- **Human-facing docs are for humans.** Short paragraphs, bullets, no walls of text. Complete, not
  padded.
- **Don't put implementation detail in this file or README.** Link out to `docs/` instead.
- **License.** BSD 3-Clause. Source files carry an SPDX header on line one
  (`# SPDX-License-Identifier: BSD-3-Clause`).

## Documentation discipline (load-bearing)

Documents in `docs/` reflect decisions **actually discussed and agreed with the project owner**. They
are not a place to forward-design from first principles or extrapolate reasonable defaults.

**Rules:**

1. **Only capture what was discussed and agreed.** If the conversation establishes a principle, do not
   extrapolate it into specifics that were not raised — API shapes, naming conventions, adoption
   checklists.
2. **Flag open questions explicitly.** Write `[TBD — needs discussion: <what is open>]` so a future
   session picks the topic up deliberately rather than inheriting an unagreed assumption.
3. **Smaller is better.** Three discussed points captured faithfully beat three discussed points plus
   seven invented ones. Resist filling out sections "for completeness".

This matters more here than in a mature library. Much is still open, and the shards bus is a tempting
source of answers to questions nobody has asked yet — copying one of its decisions across is an
invention unless it has been discussed here.

## Repository layout

```
evennia-message-bus/
├── CLAUDE.md                  # this file
├── README.md
├── LICENSE                    # BSD 3-Clause
├── pyproject.toml
├── runtests.py                # standalone test runner (no consumer gamedir needed)
├── docs/                      # design wiki (humans + LLMs)
├── examples/                  # demo gamedirs — two instances sharing one bus
├── src/
│   └── evennia_message_bus/   # library code (src layout)
│       ├── __init__.py        # lazy public API
│       ├── apps.py            # AppConfig — refuses the boot with no instance id
│       ├── config.py          # settings accessors, DEFAULT_TIMEOUT
│       ├── db_router.py       # MessageBusRouter, BUS_ALIAS
│       ├── models.py          # Message
│       ├── registry.py        # register / get_type
│       ├── types.py           # MessageType + the library-shipped types
│       ├── bus.py             # poll / delete / process_inbox / start_message_bus
│       ├── log.py             # bus_log — the make_logger binding
│       ├── errors.py          # MessageBusError
│       ├── migrations/
│       └── tests.py           # unit tests (run via runtests.py)
└── tests/                     # standalone test settings (test_settings.py, urls.py)
```

## Tools and environment

- Python 3.10+ (pinned via `pyproject.toml`).
- Evennia is the only runtime dependency.
- Tests run through Django's test runner via `python runtests.py` — not pytest.
- Development uses a dedicated venv at `venv/` (gitignored), independent of any consumer game.
