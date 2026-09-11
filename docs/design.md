# Design

How the library works and why. Every behaviour here has a case in [test-plan.md](test-plan.md).

## What it is

A way for separate Evennia instances to send messages to each other. They need not be shards of one
game — vanilla Evennia is the assumed case. Two servers install the library, point at a common bus
database, and exchange messages.

The transport comes from the cross-shard bus in `evennia-shards`, generalised. Replacing that one
later is a possible outcome, not the purpose.

## What it is not

Real-time transport between running instances. **Not a message store.**

- A message not handled within its type's timeout is dropped, and the sender gets an
  `undeliverable_reply`. It does not wait for an instance that might come back.
- A kind nobody handles is rejected immediately, not held in case a handler appears.
- Rows are deleted on process, so the table stays small with no cleanup job.

Persistent player-facing messaging — in-game mail, "leave a note for character X" — is a consumer
concern on the consumer's own storage. It is not this with a longer timeout.

Worth stating plainly because the name invites the wrong idea: "message bus between servers" sounds
like something that queues. Making it one would mean an unbounded table, cleanup machinery and
mail-shaped expectations — a different library.

## The bus database

A `messages` table on a `DATABASES` alias of its own. Instances insert rows addressed to each other
and poll for their own.

The alias, its `DATABASES` entry, its router and its migration list all come from
`evennia-database-cascade`: `db_spec.py` declares the alias and the cascade derives the rest, so
routing and migration cannot disagree. The library ships no router and no resolution code.

The spec refuses the shared `DATABASE_URL` rung — **the bus must be independent of any one
instance's database.** The bus is the transport *between* instances; a bus living inside instance
A's database is part of instance A, and instances with databases of their own would each resolve a
private bus that works perfectly and reaches nobody. So the bus lands on `DATABASE_URL_MESSAGEBUS`
or on a local `messagebus.db3` file, never on the game's database by default. A deployment that
genuinely wants the bus on a shared server says so explicitly, with `DATABASE_URL_MESSAGEBUS`.

The backstop for the explicit case is the library's own: `check_bus_database()` refuses the boot
when the bus alias resolves to the game's own database — an engine/name/host/port identity
comparison, `evennia-archive`'s pattern.

**Migrating.** `evennia cascade_migrate` covers the game and the bus in one command. The bus
database is shared, so only the first instance's migrate does work; later ones find the rows in
`django_migrations` and no-op. Evennia passes `migrate` through to Django as a deliberate command
rather than running it at startup, so instances cannot race.

**Sharing.** Locally, a symlink per gamedir back to one file — the same way FCM's view gamedirs reach a
shared `archive.db3`. Deployed, every instance sets `DATABASE_URL_MESSAGEBUS` to the same value.

Whether two instances actually share a bus is confirmed from the startup lines: each names the
resolved database — a SQLite path resolved through symlinks, so two lines describing one file agree.
Name and host only, never credentials. Which environment variable placed the database is the
cascade's knowledge, recorded in `cascade.log`.

## Instance identity

Rows are addressed `to_instance` / `from_instance`. `server` was rejected — it collides with Evennia's
own Server-vs-Portal naming.

Each instance sets `MESSAGEBUS_INSTANCE_ID`. A sharded consumer sets it to `SHARD_ID` and everything
works unchanged. Peers are named by a source-controlled constant on each side; there is no discovery
surface, because nothing has needed one.

**A missing id stops the boot.** The check runs in `AppConfig.ready()` — the earliest hook the library
controls, during `django.setup()` — and raises `ImproperlyConfigured`. `ready()` delegates to a plain
function so the check is testable on its own.

Accepted consequence: `ready()` runs in every process that loads the app registry, including every
`manage.py` command. So the setting must exist before the bus can be migrated. No
install-now-configure-later path.

**The timestamp comes from the database**, not the sending process. Age is computed on the *receiving*
instance, so a Python-side stamp would be read against a different clock — a sender running slow would
have everything it sends look already-expired on arrival. Every instance shares the bus database, so
taking the time from there gives them one clock free.

## Payload identity is the consumer's

The bus routes on `to_instance` and `kind`, and never reads inside `payload`.

Separate instances have separate databases, so a primary key means nothing across them. A consumer
needing to name an object across instances mints its own identifier, puts it in the payload, and
resolves it in its own handler.

Routing follows the same rule: the destination is an argument to `send`, never derived. Shards could
look up which shard owned a row only because shards share one database.

## One class per message type

```python
class PlayerArrived(MessageType):
    kind = "player_arrived"
    timeout = 30
    payload_keys = ("player_ref",)
    can_address_self = False

    def handle(self, message) -> bool:
        ...

register(PlayerArrived)
PlayerArrived.send("instance-b", {"player_ref": "..."})
```

The destination is an argument, not an attribute — it belongs to the call, not the type.

Why a class rather than one handler branching on `kind`: the kind string appears once, both sides of a
message type are written together instead of in two files that drift, and the timeout sits with the
only code that knows what it waits for. That last point is why there is no timeout setting.

**Registration.** A second, unrelated class claiming a taken kind raises. A **subclass** replaces the
registered type — which is how a consumer hooks a library type: subclass it, call `super().handle()`,
add its own behaviour, register it. The library's own types go in the same registry; they are present
because we put them there, not because they are special.

**The library owns every write to the row.** `delete` never appears in consumer code. Handler-owned
deletion was rejected: a consumer who does the work and forgets the `delete()` line gets the message
reprocessed twice a second forever, side effects and all, with nothing raising.

**Sending to yourself raises**, unless the type sets `can_address_self`. The refusal earns its place by
what it catches — two instances sharing an id is a brutal bug, each eating the other's messages with
nothing pointing at the cause. The raise names that as the likely reason, which is what makes an
overridable check safe: the teaching is in the error, not in the check being unavoidable.

`ping` and `ping_received` take the opt-in, following the networking meaning. Pinging a peer asks
whether it is reachable; pinging yourself asks whether your own bus is alive. Different questions, both
worth asking. `ping_received` needs it too — the reply to a self-ping is itself self-addressed.

## Lifecycle

Per message, the poll loop:

1. Polls rows addressed to this instance, oldest first.
2. Looks up the registered type for `kind`.
3. Calls `handle(message)`.
4. `True` → delete the row.
5. `False` → if older than `cls.timeout`, reply `undeliverable_reply` and delete; otherwise leave it.

Two cases skip the timeout entirely. **No registered type**, and **a type whose `handle` is still the
base `NotImplementedError`**, both get an immediate `unknown_kind` reply. Neither can succeed later, so
deferring only reaches the same answer twenty polls on.

An exception from `handle` is caught, logged with its traceback, and treated as a defer. It must not
propagate: an error escaping the Twisted `LoopingCall` stops it, and the bus goes quietly dead while
the game looks healthy.

`undeliverable_reply` and `unknown_kind` are consumed by library handlers, so neither can provoke a
reply to a reply.

## What ships

- The `messages` table, the `db_spec.py` declaration to `evennia-database-cascade`, and
  send / poll / delete
- `MessageType` and the registry
- The polling loop, started from the consumer's `at_server_start()`. It refuses to start with no
  instance id, or against an unmigrated bus database — one failure at startup rather than an error
  logged twice a second forever. It logs one line on the way up, naming the instance, the interval and
  the registered kinds.
- `ping`, `ping_received`, `unknown_kind`, `undeliverable_reply`
- Its own `messagebus.log`

**No shipped type addresses a game object.** Shards' `obj_msg`, `account_msg`, `room_msg` and
`flush_from_cache` all read a pk out of a payload and only mean anything inside a shared database.

## Transactions across two databases

A consumer committing game work and a bus send together writes the nesting itself, following
`design/database.md` § "Work that spans two databases" in the umbrella. The rule that makes it atomic:
**once a block opens the next one, it does no further work.** Any failure then unwinds the chain.

The residual is a crash, or a commit failing, between the inner commit and the outer. No two-phase
commit exists to close it. **Put the bus write in the outer block**, so the orphan is a missed message
rather than one about work that was rolled back.

The residual is accepted because the separation is not optional: instances with their own game
databases cannot share a bus table any other way. A crash costs one message; the alternative costs the
capability.

The library cannot enforce this from inside `send()` — it is a rule consumers follow at their own call
sites.

## One poller per instance id

One loop per instance, and no two processes sharing an id. Documented rather than enforced: no row
locking, no `SELECT ... FOR UPDATE SKIP LOCKED`. Nothing has needed concurrent draining, and it would
be real machinery plus a test block for a case no consumer has.

## The first consumer

Independent Evennia instances handing characters between them.

A character leaving instance A is archived with its possessions, deleted from A's game database, and
reconstructed on B's. State travels through the shared archive and ownership databases; the bus carries
the coordination — *this character is arriving, here is its archive identifier*.

That confirms four decisions:

- **Identity sits where we put it.** The cross-instance identifier is the archive id, minted by another
  library and carried in the payload.
- **The bus write belongs in the outer block.** A handoff message committing while the delete rolls
  back leaves the character live on both instances with duplicated possessions. The reverse leaves it
  archived and momentarily nowhere. One is a data-integrity failure, the other an interruption.
- **`undeliverable_reply` is load-bearing.** A has already deleted the character, so it must find out if
  B never restored it.
- **The per-type timeout earns its place.** A handoff takes longer than a notification, and the class is
  where a consumer says so.

Topology: the game database is per-instance and private. The bus, archive and ownership databases are
shared. Only the game database is ever rebuilt.
