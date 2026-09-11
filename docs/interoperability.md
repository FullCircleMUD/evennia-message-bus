# Interoperability

This library against every sibling library in `libraries/`.

What this library does that could constrain a sibling:

- Registers a **Django app** with its own **database alias**, declared to `evennia-database-cascade`
- Runs a **Twisted `LoopingCall`** on the Server reactor to poll for messages
- **Raises at `AppConfig.ready()`** if `MESSAGEBUS_INSTANCE_ID` is unset or the bus alias resolves to
  the game's own database, which stops the whole game booting, not just this library

It touches no `ObjectDB` row, resolves no game object, and never reads inside a payload.

## evennia-ai-memory

**No coupling.** Neither imports the other.

Both write to an alias of their own, so a consumer running both has two routers in
`DATABASE_ROUTERS`. This library's router is derived by `evennia-database-cascade` from its spec and
answers only for `evennia_message_bus` models — the requirement a hand-written sibling router must
also meet (return `None` for every app it does not own) is documented in ai-memory's own
`interoperability.md`.

## evennia-archive

**No coupling.** Neither imports the other.

Both declare their alias to `evennia-database-cascade` and neither ships a router of its own.
Archive's second database is a schema clone of the game; this library's is a small table of its own.
Separate aliases, no rows in common, neither reads the other's.

## evennia-database-cascade

**Hard dependency.** `db_spec.py` declares the bus alias to it — refusing the shared rung, because
the bus must be independent of any one instance's database — and the cascade derives the `DATABASES`
entry, the router and the migration list from that declaration. The library ships no router and no
resolution code of its own, and does not run without the cascade: `pyproject.toml` declares it.
Nothing flows the other way: the cascade knows nothing about the bus.

## evennia-logging-extension

**Hard dependency.** `log.py` binds `bus_log` through its `make_logger`, and every line the library
emits goes through that binding to `messagebus.log`. The library does not run without it —
`pyproject.toml` declares it. Nothing flows the other way: the extension knows nothing about the bus.

## evennia-message-bus

This library.

## evennia-mob-spawner

**No coupling.** Neither imports the other. This library resolves no game object and holds no reference
to a spawned mob, so a despawn invalidates nothing. A consumer signalling about a mob across instances
puts its own identifier in the payload — see the identity rule in [design.md](design.md).

## evennia-shards

**No coupling, and a deliberate overlap.** Neither imports the other, and this is not a shards
component — vanilla Evennia is the assumed case.

Shards ships its own cross-shard bus, of which this library is a generalisation. A consumer running
both has **two polling loops** in the Server process, against two tables in two databases. That works
and is wasteful. Until it is decided whether one replaces the other, treat them as separate systems and
do not route one through the other.

Neither of shards' recurring constraints reaches here:

- **Tenancy** is installed on `ObjectDB` only. This library's data is its own, on a separate alias — no
  `shard_id` column to scope, no auto-stamp to lose.
- **The off-thread rule** lands elsewhere. The poll loop runs on the reactor thread, where the tenant
  context already is, and the library dispatches nothing off it. A consumer handler doing off-thread
  work owns that wrap itself, as it would anywhere.

A sharded consumer sets `MESSAGEBUS_INSTANCE_ID = SHARD_ID`. The settings stay independent — this
library never reads `SHARD_ID`, and shards never reads `MESSAGEBUS_INSTANCE_ID`.

## evennia-targeting

**No coupling.** Neither imports the other. Targeting filters candidate lists already in hand and
queries nothing of this library's; this library resolves no game objects.

## evennia-world-builder

**No coupling.** Neither imports the other. World-builder writes rooms, exits and objects into the game
database; this library writes message rows in a different one and never touches an `ObjectDB` row.

## evennia-yaml-reader

**No coupling.** Neither imports the other. Payloads are built by consumer code and stored as
structured data on a row; nothing here parses YAML.
