# SPDX-License-Identifier: BSD-3-Clause
"""Settings accessors and the boot-time check.

The library reads one setting, ``MESSAGEBUS_INSTANCE_ID`` — this instance's
name on the bus. Peers address messages to it, and it is how the bus knows
which rows are ours to process.

``check_instance_id`` is called from ``AppConfig.ready()``, so a consumer
that installs the library without configuring it cannot boot. It is a plain
function rather than logic inlined into ``ready()`` so it can be called —
and tested — on its own.
"""

import os

import dj_database_url
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .db_router import BUS_ALIAS

#: Seconds a message may sit deferred before the bus gives up on it and
#: replies undeliverable. Overridden per message type by setting ``timeout``
#: on the class, which is where the knowledge of what it is waiting for
#: lives.
DEFAULT_TIMEOUT = 10

SETTING_NAME = "MESSAGEBUS_INSTANCE_ID"

#: Environment variable naming a database for the bus alone.
BUS_URL_ENV = "DATABASE_URL_MESSAGEBUS"

#: The game's own database URL. Used when the bus has no database of its
#: own, which shares the game's.
GAME_URL_ENV = "DATABASE_URL"


def get_instance_id() -> str | None:
    """Return this instance's bus identity, or ``None`` if unset."""
    return getattr(settings, SETTING_NAME, None)


def check_instance_id() -> str:
    """Return the instance id, raising if it is missing or blank.

    Raises ``ImproperlyConfigured`` naming the setting. A consumer hitting
    this has installed the library and not configured it; the message has to
    say which setting, because there is nothing else to go on.
    """
    value = get_instance_id()
    if not value or not str(value).strip():
        raise ImproperlyConfigured(
            f"{SETTING_NAME} is not set. evennia-message-bus needs a name for "
            f"this instance so peers can address messages to it. Set "
            f"{SETTING_NAME} in your settings to a string unique across every "
            f"instance sharing the bus database. A sharded consumer can set "
            f"{SETTING_NAME} = SHARD_ID."
        )
    return str(value)


def messagebus_database(sqlite_path: str) -> dict:
    """Resolve the bus database, for a consumer's ``DATABASES`` entry.

    Three rungs, in order:

    1. ``DATABASE_URL_MESSAGEBUS`` — the bus has a database of its own.
    2. ``DATABASE_URL`` — the bus shares the game's database.
    3. ``sqlite_path`` — a local file.

    Every rung is legitimate, and which one is *correct* depends on
    something no instance can see. Rung two is right for a consumer whose
    instances already run against one Postgres, and wrong for instances
    with databases of their own — each would get a private bus that works
    perfectly and reaches nobody. Resolving its own settings, an instance
    sees an identical picture either way; the difference exists only
    across instances.

    So this does not guess, and does not warn. ``describe_bus_database``
    puts the answer in the startup log instead, where two instances can
    be compared.
    """
    url = os.environ.get(BUS_URL_ENV) or os.environ.get(GAME_URL_ENV)
    if url:
        return dj_database_url.parse(url)
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": sqlite_path}


def describe_bus_database() -> str:
    """One phrase naming the bus database and where it came from.

    Written to the log at startup. Two instances that should share a bus
    are then confirmed by reading two log lines, rather than by reasoning
    about which environment variables were set where.

    Reports the database name and host only. The configuration holds
    credentials parsed out of a URL and they must never reach a log file.
    """
    databases = getattr(settings, "DATABASES", {})
    bus = databases.get(BUS_ALIAS) or {}
    name = bus.get("NAME") or "?"
    host = bus.get("HOST")

    # Instances share a SQLite bus by symlinking one file into each gamedir,
    # so each has a different path to the same database. Report the target,
    # or two logs describing one file would disagree.
    if "sqlite" in str(bus.get("ENGINE", "")) and name != "?":
        name = os.path.realpath(name)

    where = f"{name!r} on {host!r}" if host else f"{name!r}"

    if os.environ.get(BUS_URL_ENV):
        return f"{where} (from {BUS_URL_ENV})"

    default = databases.get("default") or {}
    identity = ("ENGINE", "NAME", "HOST", "PORT")
    if all(bus.get(key) == default.get(key) for key in identity):
        return f"{where} (shared with the game database)"

    if "sqlite" in str(bus.get("ENGINE", "")):
        return f"{where} (local file)"
    return where
