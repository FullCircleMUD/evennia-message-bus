# SPDX-License-Identifier: BSD-3-Clause
"""Settings accessors and the boot-time checks.

The library reads one setting, ``MESSAGEBUS_INSTANCE_ID`` — this instance's
name on the bus. Peers address messages to it, and it is how the bus knows
which rows are ours to process.

Where the bus *database* lands is not decided here: ``db_spec.py`` declares
the alias to ``evennia-database-cascade``, and the cascade resolves it from
the environment. What this module keeps is the checking and the describing —
``check_instance_id`` and ``check_bus_database`` are called from
``AppConfig.ready()`` so a misconfigured consumer cannot boot, and
``describe_bus_database`` names the resolved database for the startup log
line. They are plain functions rather than logic inlined into ``ready()`` so
they can be called — and tested — on their own.
"""

import os

from django.core.exceptions import ImproperlyConfigured

#: Seconds a message may sit deferred before the bus gives up on it and
#: replies undeliverable. Overridden per message type by setting ``timeout``
#: on the class, which is where the knowledge of what it is waiting for
#: lives.
DEFAULT_TIMEOUT = 10

SETTING_NAME = "MESSAGEBUS_INSTANCE_ID"

#: The DATABASES alias the bus table lives on. Declared to
#: evennia-database-cascade by db_spec.py, which derives the DATABASES
#: entry, the router and the migration list from it.
BUS_ALIAS = "messagebus"


def _log_refusal(message: str) -> None:
    """Write a refusal to the log before its raise, at ERROR.

    The log line and the exception carry the same text: a reader who has the
    traceback learns nothing new from the log, and a reader who has only the
    log is not worse off.

    The import is deliberately inside the function. ``log.py`` may import this
    module for a settable filename, and a module-scope import here would close
    that cycle on declaration order.
    """
    from .log import bus_log

    bus_log(message, level="ERROR")


def get_instance_id() -> str | None:
    """Return this instance's bus identity, or ``None`` if unset."""
    from django.conf import settings

    return getattr(settings, SETTING_NAME, None)


def check_instance_id() -> str:
    """Return the instance id, raising if it is missing or blank.

    Raises ``ImproperlyConfigured`` naming the setting. A consumer hitting
    this has installed the library and not configured it; the message has to
    say which setting, because there is nothing else to go on.
    """
    value = get_instance_id()
    if value is None or not str(value).strip():
        # Unset and blank are different mistakes — a blank one is usually an
        # environment variable that did not expand — and a reader who has only
        # the log needs to be able to tell them apart.
        state = (
            "is not set"
            if value is None
            else f"is set to {value!r}, which is blank"
        )
        message = (
            f"{SETTING_NAME} {state}. evennia-message-bus needs a name for "
            f"this instance so peers can address messages to it. Set "
            f"{SETTING_NAME} in your settings to a string unique across every "
            f"instance sharing the bus database. A sharded consumer can set "
            f"{SETTING_NAME} = SHARD_ID."
        )
        _log_refusal(message)
        raise ImproperlyConfigured(message)
    return str(value)


def _database_identity(entry):
    """What decides whether two DATABASES entries are one database.

    ``TEST["NAME"]`` is part of it — the `evennia-archive` pattern. Under
    Django's test runner that is the database an alias actually uses, and two
    aliases can share a ``NAME`` of ``:memory:`` while pointing at genuinely
    separate test databases — which is exactly what this library's own suite
    does.
    """
    test = entry.get("TEST") or {}
    return tuple(
        entry.get(key) for key in ("ENGINE", "NAME", "HOST", "PORT")
    ) + (test.get("NAME"),)


def check_bus_database() -> None:
    """Refuse a bus alias that resolves to the game's own database.

    The spec refuses the shared rung, but an explicit
    ``DATABASE_URL_MESSAGEBUS`` pointed at an instance's game database
    resolves cleanly — and the bus is then part of the very instance it
    exists to be independent of. Caught here, at boot, rather than presenting
    as a healthy game whose bus is not a bus.

    The comparison is engine, name, host and port. Two entries reaching one
    database under different hostnames pass it, so the constraint is the
    deployment's to hold as well.
    """
    from django.conf import settings

    databases = getattr(settings, "DATABASES", {})
    bus = databases.get(BUS_ALIAS)
    default = databases.get("default")
    if not bus or not default:
        # No bus alias means the cascade's own boot check has the better
        # message; nothing useful to compare here.
        return

    if _database_identity(bus) == _database_identity(default):
        # Name and host only. This entry holds credentials parsed out of a
        # URL and the message goes to a log file — the CF-14 rule, on a
        # second channel.
        name = bus.get("NAME") or "?"
        host = bus.get("HOST")
        where = f"{name!r} on {host!r}" if host else f"{name!r}"
        message = (
            f"the {BUS_ALIAS!r} database is the game's own database ({where}). "
            f"The bus is the transport between instances, so it must not live "
            f"inside any one instance's database. Point "
            f"DATABASE_URL_MESSAGEBUS at a database of its own, or unset it to "
            f"fall back to a local messagebus.db3 file."
        )
        _log_refusal(message)
        raise ImproperlyConfigured(message)


def describe_bus_database() -> str:
    """One phrase naming the bus database, for the startup log line.

    Two instances that should share a bus are confirmed by reading two
    startup lines, so what matters here is identity — the database's name
    and host. Which environment variable placed it there is the cascade's
    knowledge, recorded in ``cascade.log``.

    Reports the database name and host only. The resolved entry holds
    credentials parsed out of a URL and they must never reach a log file.
    """
    from django.conf import settings

    databases = getattr(settings, "DATABASES", {})
    bus = databases.get(BUS_ALIAS) or {}
    name = bus.get("NAME") or "?"
    host = bus.get("HOST")

    # Instances share a SQLite bus by symlinking one file into each gamedir,
    # so each has a different path to the same database. Report the target,
    # or two logs describing one file would disagree.
    if "sqlite" in str(bus.get("ENGINE", "")) and name != "?":
        name = os.path.realpath(name)
        return f"{name!r} (local file)"

    return f"{name!r} on {host!r}" if host else f"{name!r}"
