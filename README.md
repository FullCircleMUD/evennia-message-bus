# evennia-message-bus

Lets separate Evennia instances send messages to each other through a shared database.

## Status

**Working, no consumer game yet.** Every case in [docs/test-plan.md](docs/test-plan.md) has a passing
test, and a message round trip runs between two demo instances in `examples/`. Untried on PostgreSQL,
and not published. See [docs/progress.md](docs/progress.md) for what proves it.

## The problem it solves

Two Evennia servers cannot talk to each other. Each is a self-contained process with its own database,
and Evennia offers nothing for getting a message from one into the other.

That bites as soon as a game is more than one server — a hub and a set of worlds, a live game beside a
staging copy, an event server, a separate minigame instance. Each pair reinvents an ad-hoc channel,
usually badly.

## The approach

A `messages` table in a database of its own, shared by every instance taking part.

- **Sending** is an insert addressed to another instance
- **Receiving** is a poll for rows addressed to you, then process and delete
- A message carries a `kind` and a free-form payload, so a new message type is data, not a migration

A known technique — the outbox pattern, same shape as `pgmq` or `procrastinate`. No Redis, no broker,
no infrastructure beyond a database you already run.

```python
class PlayerArrived(MessageType):
    kind = "player_arrived"
    payload_keys = ("player_ref",)

    def handle(self, message) -> bool:
        restore_character(message.payload["player_ref"])
        return True

register(PlayerArrived)
PlayerArrived.send("instance-b", {"player_ref": "..."})
```

## Is this for you?

**Probably**, if you run more than one Evennia instance and they need to tell each other things.

**Probably not**, if you want durable player-facing messaging — in-game mail, or a tell that waits for
a character to log in. This is a real-time transport; an undeliverable message goes back to the sender
rather than being held.

**Worth knowing either way:** the bus never looks inside a message. If your instances need to refer to
the same character or room, inventing an identifier that means the same thing on both sides is your
job. Primary keys will not do it — separate instances have separate databases.

## Install

Not published yet. Editable install for development against a checkout:

```
git clone https://github.com/FullCircleMUD/evennia-message-bus.git
cd evennia-message-bus
python -m venv venv
# Activate the venv (platform-specific)
pip install evennia
pip install -e .
python runtests.py
```

Installing the package is not enough on its own — a consumer declares the app, the bus database, the
router and its own instance id in their settings. **See
[docs/messagebus-settings.md](docs/messagebus-settings.md) for what to add**, including the entry that
will silently break a game that already has database routers, and the reason the instance id is
mandatory before you can even migrate.

## Learn more

- [docs/messagebus-settings.md](docs/messagebus-settings.md) — how to install it
- [docs/design.md](docs/design.md) — how it works and why
- [docs/test-plan.md](docs/test-plan.md) — every case covered, and the test covering it
- [docs/interoperability.md](docs/interoperability.md) — against its sibling libraries
- [docs/INDEX.md](docs/INDEX.md) — the full wiki
- [CLAUDE.md](CLAUDE.md) — context for LLM agents working in this repo

## Licence

BSD 3-Clause. See [LICENSE](LICENSE).
