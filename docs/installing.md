# Installing

What a consumer does to run this library — the apps, one name, one database call, one line in a
server hook — and why each is needed.

The demo gamedirs under `examples/` follow this document verbatim, so these instructions are what
gets exercised rather than a paraphrase of them.

## The bus needs a database of its own

The bus is the transport *between* instances, so it must not live inside any one instance's
database. The library declares that to `evennia-database-cascade`: the shared `DATABASE_URL` rung is
refused for its alias, so the bus lands on its own `DATABASE_URL_MESSAGEBUS` database or its own
local `messagebus.db3` file, never the game's.

The library also refuses to start when the bus and the game resolve to the same database — a
hand-set `DATABASE_URL_MESSAGEBUS` can reach what the cascade would have refused. The check compares
engine, name, host and port; two entries that reach one database under different hostnames would
pass it, so the constraint is yours to hold as well.

## Required settings

All in each instance's `server/conf/settings.py`:

| Setting | What it does | Without it |
|---|---|---|
| `INSTALLED_APPS` += the two apps | Loads the library and the cascade that places its database | Nothing runs — including the boot checks, which is why this one cannot be validated |
| `MESSAGEBUS_INSTANCE_ID` | This instance's name on the bus — how peers address it and how it knows which rows are its own | **Refused at boot**, in every process that loads the app registry, `migrate` included |
| The cascade's `configure()` call | Resolves the `messagebus` alias and its router from this library's own spec | **Refused at boot.** No `messagebus` entry means every call would raise `ConnectionDoesNotExist` at first use |

## Optional settings

**None.** The library reads no settings beyond `MESSAGEBUS_INSTANCE_ID`.

## 1. Install the package

```
pip install evennia-message-bus
```

The library declares its database to `evennia-database-cascade` and logs through
`evennia-logging-extension`; neither is on PyPI yet, so install both from their checkouts alongside:

```
pip install -e path/to/evennia-database-cascade
pip install -e path/to/evennia-logging-extension
```

## 2. Add the apps

```python
INSTALLED_APPS += ["evennia_message_bus", "evennia_database_cascade"]
```

Both: the library, and the cascade whose boot check verifies the alias it placed.

## 3. Name the instance

```python
MESSAGEBUS_INSTANCE_ID = "instance-a"
```

**The id must be unique across every instance sharing the bus database.** Two instances with the
same id each consume the other's messages and roughly half of everything disappears, with nothing in
any log pointing at the cause. The library catches the common form: sending to your own id raises,
with a message naming the duplicate as the likely reason.

There is no install-now-configure-later path — the check runs during `django.setup()`, so every
`evennia` command needs the setting in place, `migrate` included. That is deliberate: a bus running
under the wrong identity fails in ways that look like nothing at all.

## 4. Let the cascade place the database

This library ships its declaration — the alias, its SQLite fallback `messagebus.db3`, and the
independence constraint above. What remains for you is the cascade's one settings call and, per
deployment, an environment variable saying where the bus lives. Follow
[evennia-database-cascade's installing.md](../../evennia-database-cascade/docs/installing.md) from
step 4; nothing in it is bus-specific beyond one fact:

**The bus refuses the shared rung.** `DATABASE_URL` alone is an error for this alias — give it
`DATABASE_URL_MESSAGEBUS`, or set nothing and it lands in `server/messagebus.db3`.

## 5. Point every instance at one bus

The bus only does anything when instances share its database. **Do this before migrating** — an
instance that migrates first creates its own bus file, and a symlink laid over it afterwards either
fails or discards a database.

**Locally**, symlink one file into every other gamedir. Only `game-a` holds a real file:

```
cd game-b/server
ln -s ../../game-a/server/messagebus.db3 messagebus.db3
```

The file does not exist yet — a symlink to a missing target is fine, and migrating creates the real
file through it.

**Deployed**, set `DATABASE_URL_MESSAGEBUS` to the same value on every instance.

Every instance keeps its own game database. Only the bus is shared. Whether two instances actually
share one is confirmed from their startup lines: each names the resolved database, with a SQLite
path resolved through symlinks, so two lines describing one file agree.

## 6. Migrate

```
evennia cascade_migrate
```

One command: the game database, then `migrate --database messagebus` — and the same for any other
cascade-placed alias you have installed. The two-step form (`evennia migrate`, then
`evennia migrate --database messagebus`) does the same by hand.

The bus database is shared, so only the first instance's migrate does work there; later ones find
the rows in `django_migrations` and no-op. Every instance still migrates its own game database.

## 7. Start the loop

One line in `server/conf/at_server_startstop.py`:

```python
from evennia_message_bus import start_message_bus

def at_server_start():
    start_message_bus()
```

It returns the Twisted `LoopingCall` if you want to stop it later; the default interval is 0.5
seconds. It refuses to start if the instance id is unset or the bus table does not exist — one clear
failure at startup rather than an error logged twice a second for the life of the process.

**Run exactly one loop per instance id.** The library does not lock rows, because a message has one
addressee and that addressee is one process.

## 8. Declare a message type and check the bus

One class owns everything about a message type — see [design.md](design.md) § Message types.
Register it somewhere that runs on every instance, before `start_message_bus()` — **both ends need
the class**, because the sender needs `send` and the receiver needs `handle`.

Then send a `ping`:

```python
from evennia_message_bus import Ping

Ping.send("instance-b", {"token": "hello"})        # is that instance reachable?
Ping.send("instance-a", {"token": "me"})           # is my own loop dispatching? (self-ping)
```

A peer replies `ping_received`. The line worth knowing about in `messagebus.log` is the one a peer
sends back when it has no handler for a kind you sent — it usually means the class was registered on
one instance and not the other.

## What is not checked for you

- **`INSTALLED_APPS`.** Leave the library out and `AppConfig.ready()` never runs, so nothing
  validates anything and the library is simply inert.
- **Instance-id uniqueness across the bus.** Each instance sees only its own settings. The self-send
  refusal catches the common form; two *other* instances sharing an id is invisible to both.
- **That every instance points at the same bus database.** A wrong `DATABASE_URL_MESSAGEBUS`, or a
  missed symlink, resolves cleanly into a private bus that works perfectly and reaches nobody. The
  startup lines are the diagnostic — compare them.
- **The symlink-before-migrate ordering.** Migrate first and the symlink lands on an existing file;
  nothing warns.
- **One polling loop per instance id.** Nothing enforces it — two processes polling one id will both
  handle the same message.
- **Registration parity.** A kind registered on the sender and not the receiver surfaces at runtime
  as an `unknown_kind` reply in the sender's log, not at boot.
