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

## evennia-calendar

**No coupling.** Neither imports the other. Calendar holds no state — every process derives the same
date from the same clock — so there is nothing for it to send and nothing for it to receive.

## evennia-database-cascade

**Hard dependency.** `db_spec.py` declares the bus alias to it — refusing the shared rung, because
the bus must be independent of any one instance's database — and the cascade derives the `DATABASES`
entry, the router and the migration list from that declaration. The library ships no router and no
resolution code of its own, and does not run without the cascade: `pyproject.toml` declares it.
Nothing flows the other way: the cascade knows nothing about the bus.

## evennia-equipment

**No coupling.** Neither imports the other. Equipment holds its state on one object in one instance
and publishes nothing. A consumer moving an equipped character between instances moves it through the
archive, not as a payload here — this library never reads inside one.

## evennia-llm-service

**No coupling.** Neither imports the other. The llm-service has neither persistence nor a loop, and
holds nothing that would need to reach another instance.

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

## evennia-portal-multiplex

**No coupling.** Neither imports the other. Multiplex owns no tables and no alias; this library owns
both.

**Both name instances, and nothing checks the two names agree.** Multiplex reads
`MULTIPLEX_INSTANCE_ID` and this library reads `MESSAGEBUS_INSTANCE_ID`. A consumer running both
should alias one to the other rather than maintain two names for one thing; multiplex's
`installing.md` carries the line. If they drift, a session is addressed by one name and routed by
another, and the symptom is traffic arriving at the default instance while everything above believes
it moved.

**They also work at different speeds, by construction.** Multiplex hands a session over a live AMP
link; a bus message crosses a database and waits for a polling interval. An arriving session can beat
the message that describes it, and the consumer's handler has to tolerate that — the bus offers no
ordering guarantee against anything outside itself.

## evennia-scaling

**Hard dependency, in the other direction.** Scaling depends on this library; this library knows
nothing about scaling — nothing here imports it, and no shipped message type refers to a character
move. Scaling
carries its handoff between instances that share no game database, so the receiving instance learns
about a transfer independently of the session about to arrive.

**An instance is named once.** Scaling declares no id for the instance it runs on; it reads this
library's `MESSAGEBUS_INSTANCE_ID`. What it declares is which *other* instances exist, and those names
have to match the ids the bus routes by. Nothing can check that across instances, so a mismatch is a
message addressed to a name nobody answers to — see principle 5 in [../CLAUDE.md](../CLAUDE.md):
routing is an argument, never derived.

## evennia-shards

**No coupling, and shards is deprecated.** Neither imports the other, and this is not a shards
component — vanilla Evennia is the assumed case.

**Use `evennia-scaling`, not shards.** Shards is retired from service and yanked from PyPI once
scaling is implemented; nothing new should be built against it. Shards ships its own cross-shard bus,
of which this library is a generalisation — a consumer running both has **two polling loops** in the
Server process, against two tables in two databases, and the answer is to move off shards rather than
to route one bus through the other.

Neither of shards' recurring constraints reaches here, for as long as a consumer still runs it:

- **Tenancy** is installed on `ObjectDB` only. This library's data is its own, on a separate alias — no
  `shard_id` column to scope, no auto-stamp to lose.
- **The off-thread rule** lands elsewhere. The poll loop runs on the reactor thread, where the tenant
  context already is, and the library dispatches nothing off it. A consumer handler doing off-thread
  work owns that wrap itself, as it would anywhere.

A sharded consumer sets `MESSAGEBUS_INSTANCE_ID = SHARD_ID`. The settings stay independent — this
library never reads `SHARD_ID`, and shards never reads `MESSAGEBUS_INSTANCE_ID`.

## evennia-survival

**No coupling.** Neither imports the other. Hunger and thirst travel between instances as Attributes
on the character, through the archive — nothing about them is ever a message.

## evennia-targeting

**No coupling.** Neither imports the other. Targeting filters candidate lists already in hand and
queries nothing of this library's; this library resolves no game objects.

## evennia-world-builder

**No coupling.** Neither imports the other. World-builder writes rooms, exits and objects into the game
database; this library writes message rows in a different one and never touches an `ObjectDB` row.

## evennia-yaml-reader

**No coupling.** Neither imports the other. Payloads are built by consumer code and stored as
structured data on a row; nothing here parses YAML.

## fcm-telemetry-spawn

**No coupling today.** Neither imports the other, and nothing shipped here knows about spawning.

Whether a spawn run is coordinated across instances is telemetry-spawn's question, not this
library's, and it is open on that side. If the answer is yes, this library is the transport and
telemetry-spawn declares its own message type — see principle 1 in [../CLAUDE.md](../CLAUDE.md): the
bus does not own game concepts.

## fcm-xrpl

**No coupling.** Neither imports the other, and no shipped message type refers to an on-chain
holding.

**Both put a router in `DATABASE_ROUTERS`**, so a consumer running the pair has two. This library's
is derived by `evennia-database-cascade` from its spec; fcm-xrpl ships its own `db_router.py`, which
must return `None` for every app it does not own or it silently captures this library's queries.

A character moving between instances carries its on-chain holdings, and the bus is what coordinates
that move — but what crosses the bus is the consumer's identifier, resolved by the consumer at each
end. `[TBD — needs discussion: whether fcm-xrpl has anything to say to the bus directly, or only to
the archive the handoff triggers. Open on both sides.]`
