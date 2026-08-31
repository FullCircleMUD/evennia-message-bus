# Message bus settings

What a consumer does to install this library — four settings entries, one migrate command and one line
in a server hook — why each is needed, and the two entries that will silently break things if they are
copied carelessly.

Everything here is built and covered by the test suite. The demo gamedirs under `examples/` follow this
document verbatim, so these instructions are what gets exercised rather than a paraphrase of them.

**Order matters.** Working through it end to end, for two instances:

1. Declare the settings in **both** gamedirs, with a different `MESSAGEBUS_INSTANCE_ID` in each.
2. Symlink the bus database so both point at one file — *before* migrating either.
3. Migrate each game database, and the bus database once.
4. Register a message type and start the loop in both.
5. Send a `ping` between them.

The sections below follow that order.

## What a consumer declares

Four entries, in each instance's `server/conf/settings.py`:

```python
# 1. The app
INSTALLED_APPS += ["evennia_message_bus"]

# 2. This instance's name on the bus. Mandatory — see below.
MESSAGEBUS_INSTANCE_ID = "instance-a"

# 3. The bus database — shared by every instance that talks on it
DATABASES["messagebus"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.path.join(GAME_DIR, "server", "messagebus.db3"),
}

# 4. The router — append, never assign. See below.
_BUS_ROUTER = "evennia_message_bus.db_router.MessageBusRouter"
DATABASE_ROUTERS = list(globals().get("DATABASE_ROUTERS", []))
if _BUS_ROUTER not in DATABASE_ROUTERS:
    DATABASE_ROUTERS.append(_BUS_ROUTER)
```

The router is a dotted-path string, exactly like an app entry — the class ships with the library and a
consumer never writes one.

## Naming the instance

`MESSAGEBUS_INSTANCE_ID` is how peers address this instance and how the bus knows which rows are ours
to process. It has no default, and **the game will not start without it** — the check runs in
`AppConfig.ready()`, during `django.setup()`, and raises `ImproperlyConfigured` naming the setting.

That includes every `manage.py` and `evennia` command, `migrate` among them. So the setting has to be
in place before you can create the bus table. There is no install-now-configure-later path; that is
deliberate, because a bus running under the wrong identity fails in ways that look like nothing at all.

**The id must be unique across every instance sharing the bus database.** Two instances with the same
id each consume the other's messages and roughly half of everything disappears, with nothing in any log
pointing at the cause. The library catches the common form of this: sending to your own id raises, with
a message naming the duplicate as the likely reason.

A consumer already running `evennia-shards` can set `MESSAGEBUS_INSTANCE_ID = SHARD_ID`. The two
settings are independent — this library never reads `SHARD_ID`.

## Why the router list is appended, never assigned

Evennia does not define `DATABASE_ROUTERS` at all, so a game with no routers of its own could get away
with a plain assignment. **A game that already has routers cannot.**

The failure is silent and expensive. A consumer with existing routers who writes
`DATABASE_ROUTERS = ["evennia_message_bus.db_router.MessageBusRouter"]` replaces their list rather
than extending it. Every model those routers were steering now falls back to `default` — no exception,
no warning, just reads and writes landing in the wrong database until someone notices.

The membership check also makes the block idempotent, so a settings module imported twice cannot stack
duplicate routers.

The router itself answers only for this library's models and returns `None` for everything else, so it
is safe to sit alongside other libraries' routers in any order.

## Pointing several instances at one bus

The bus only does anything when instances share the database. **Do this before migrating** — an
instance that migrates first creates its own bus file, and a symlink laid over it afterwards either
fails or discards a database.

**Locally**, symlink one file into every other gamedir. Only `game-a` holds a real file:

```
cd game-b/server
ln -s ../../game-a/server/messagebus.db3 messagebus.db3
```

The file does not exist yet — a symlink to a missing target is fine, and `migrate` creates the real
file through it.

**Deployed**, give each instance the same connection string instead:

```python
DATABASES["messagebus"] = dj_database_url.parse(os.environ["DATABASE_URL_MESSAGEBUS"])
```

Every instance keeps its own game database. Only the bus is shared.

## Migrating

Each instance migrates its own game database as normal. The bus database is migrated once:

```
cd game-a
evennia migrate                          # this instance's game database
evennia migrate --database=messagebus    # the shared bus, once across all instances
```

Then for every other instance, its game database only:

```
cd game-b
evennia migrate
```

`--database=messagebus` is required on the bus migrate. Without it the command migrates `default`,
where the router refuses this app — so the bus table is never created, and the error surfaces later at
startup rather than here.

Running the bus migrate from a second instance is harmless: it finds the rows already in
`django_migrations` there and no-ops. Evennia does not migrate at startup — it passes `migrate`
through to Django as a deliberate command — so instances coming up cannot race each other.

The bus table must never appear in an instance's *game* database. The router returns `False` from
`allow_migrate` for every alias but `messagebus`, which is what prevents a plain `evennia migrate` from
creating a local copy. If that ever breaks, the symptom is a bus that works perfectly and reaches
nobody: each instance polls its own private table.

## Starting the loop

One line in `server/conf/at_server_startstop.py`:

```python
from evennia_message_bus import start_message_bus

def at_server_start():
    start_message_bus()
```

It returns the Twisted `LoopingCall` if you want to stop it later. The default interval is 0.5
seconds.

`start_message_bus()` refuses to start if the instance id is unset, or if the bus table does not exist
— the second turns an unmigrated database into one clear failure at startup rather than an error
logged twice a second for the life of the process.

**Run exactly one loop per instance id.** The library does not lock rows, because a message has one
addressee and that addressee is one process. Two processes polling the same id will both handle the
same message.

## Declaring a message type

One class owns everything about a message type — the kind, the timeout, the payload contract, the send
call and the handler:

```python
from evennia_message_bus import MessageType, register

class PlayerArrived(MessageType):
    kind = "player_arrived"           # required, unique across the bus
    timeout = 30                      # seconds; defaults to 10
    payload_keys = ("player_ref",)    # required keys, checked before sending

    def handle(self, message) -> bool:
        restore_character(message.payload["player_ref"])
        return True                   # True = done, False = not yet

register(PlayerArrived)
```

Register it somewhere that runs on every instance — `at_server_start()`, before
`start_message_bus()`, is the obvious place. **Both ends need the class**, because the sender needs
`send` and the receiver needs `handle`.

Then send:

```python
PlayerArrived.send("instance-b", {"player_ref": "..."})
```

Returning `False` from `handle` leaves the message for the next poll, and the bus gives up once it is
older than the class's `timeout`, replying `undeliverable_reply` to the sender. You never delete a
message yourself — the library owns every write to the row.

### What can go in a payload

Anything JSON can carry. Not live objects, and **not primary keys**: instances have separate game
databases, so `id=42` on one is an unrelated row on the other. If two instances need to mean the same
thing, minting an identifier they both understand is yours to do — the bus routes on the destination
and the kind, and never looks inside a payload.

## Combining a send with game work

To make a bus write atomic with work in your game database, nest the transactions, and follow the one
rule that makes it hold: **once a block opens the next one, it does no further work.**

```python
with transaction.atomic(using="messagebus"):
    PlayerArrived.send("instance-b", {"player_ref": ref})
    with transaction.atomic():
        character.delete()
    # nothing here
# nothing here
```

Any failure unwinds the whole chain. The residual is a crash between the two commits, which no
two-phase commit exists to close — the inner block is already durable. That is why the bus write goes
**outside**: a crash then loses a message, which a resend fixes, rather than committing a message about
work that was rolled back, which nothing fixes.

See `design/database.md` § "Work that spans two databases" in the umbrella for the full treatment.

## Checking it works

Send a `ping`. A peer replies `ping_received`; pinging your own instance id proves your own loop is
alive and dispatching, which is a different question and also worth asking:

```python
from evennia_message_bus import Ping

Ping.send("instance-b", {"token": "hello"})       # is that instance reachable?
Ping.send(MESSAGEBUS_INSTANCE_ID, {"token": "me"}) # is my own bus running?
```

## Where things get logged

The library writes to its own `messagebus.log` under `settings.LOG_DIR`, not the main server log.

The line worth knowing about is the one you get when a peer has no handler for something you sent:

```
[WARN] 'instance-b' does not handle kind 'player_arrived' — it has no handler
       registered for it. The message was dropped at that end.
```

That is what turns "the other instance isn't responding" into a two-second diagnosis. It usually means
the class was registered on one instance and not the other.
