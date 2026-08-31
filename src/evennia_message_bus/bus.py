# SPDX-License-Identifier: BSD-3-Clause
"""The poll loop and the message lifecycle.

Per message the loop polls for rows addressed to this instance, looks up the
registered type, calls ``handle``, and then owns every write to the row:
delete on ``True``, leave on ``False``, and on ``age > timeout`` reply
undeliverable and drop it.

Two cases short-circuit before the timeout applies. A kind with no
registered type, and a registered type whose ``handle`` is still the base
``NotImplementedError``, are both rejected immediately — neither can succeed
later, so deferring only reaches the same answer twenty polls further on.
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.utils import timezone

from .config import check_instance_id, get_instance_id
from .db_router import BUS_ALIAS
from .log import bus_log
from .models import Message
from .registry import get_type, registered_kinds
from .types import UndeliverableReply, UnknownKind


def poll(instance_id: str | None = None):
    """Rows addressed to ``instance_id``, oldest first.

    Defaults to this instance. Returns a lazy QuerySet — the caller may
    filter or count further. Neither deletes nor mutates; the loop does
    that after a handler has had its say.

    The primary key breaks ties on ``created_at``, so ordering is
    deterministic even for rows the database stamped in the same tick.
    """
    if instance_id is None:
        instance_id = get_instance_id()
    return Message.objects.filter(to_instance=instance_id).order_by("created_at", "id")


def delete(message) -> None:
    """Drop a processed row.

    Thin by design, so the send / poll / delete surface reads consistently
    and so anything that later belongs on the delete path — metrics,
    bookkeeping — lands in one place.
    """
    message.delete()


def bus_table_exists() -> bool:
    """Whether the bus table is present on the bus alias.

    Checked once at startup rather than discovered as an error logged twice
    a second for the life of the process.
    """
    with connections[BUS_ALIAS].cursor():
        names = connections[BUS_ALIAS].introspection.table_names()
    return Message._meta.db_table in names


def _reply(cls, message, payload) -> None:
    """Send a library reply back to a message's sender, if that is possible.

    A message with no return address, or one that came from this instance,
    gets no reply — there is nobody to tell, or telling them is a loop.
    """
    sender = message.from_instance
    if not sender or sender == get_instance_id():
        return
    cls.send(sender, payload)


def _reject(message, reason: str) -> None:
    """Answer a message that can never be handled, and drop it."""
    bus_log(
        f"no handler for kind {message.kind!r} from {message.from_instance!r} "
        f"({reason}); replying unknown_kind and dropping it",
        level="WARN",
    )
    _reply(UnknownKind, message, {"kind": message.kind, "payload": message.payload})
    delete(message)


def process_inbox() -> int:
    """Run one cycle. Returns the number of messages a handler processed.

    Rejected and timed-out messages are not counted — the number is what got
    done, not what got cleared.

    A pure function, so the lifecycle is testable without a reactor; the
    polling loop is only a ``LoopingCall`` around it.
    """
    processed = 0
    now = timezone.now()

    for message in list(poll()):
        cls = get_type(message.kind)
        if cls is None:
            _reject(message, "kind not registered")
            continue

        try:
            handled = bool(cls().handle(message))
        except NotImplementedError:
            _reject(message, f"{cls.__name__}.handle() is not implemented")
            continue
        except Exception:
            # Never let this escape. An exception reaching the LoopingCall
            # stops it, and the bus then goes quietly dead while the game
            # looks healthy.
            bus_log(
                f"{cls.__name__}.handle() raised on message pk={message.pk} "
                f"kind={message.kind!r}; deferring it",
                level="ERROR",
                trace=True,
            )
            handled = False

        if handled:
            delete(message)
            processed += 1
            continue

        age = (now - message.created_at).total_seconds()
        if age <= cls.timeout:
            continue

        if message.from_instance and message.from_instance != get_instance_id():
            _reply(
                UndeliverableReply,
                message,
                {
                    "original_kind": message.kind,
                    "original_payload": message.payload,
                    "reason": "timeout",
                },
            )
        else:
            bus_log(
                f"message pk={message.pk} kind={message.kind!r} timed out with "
                f"no usable return address ({message.from_instance!r}); "
                f"dropping it without a reply",
                level="WARN",
            )
        delete(message)

    return processed


def _tick():
    """One loop iteration, guarded so nothing can kill the LoopingCall."""
    try:
        return process_inbox()
    except Exception:
        bus_log("process_inbox() raised; the loop continues", level="ERROR", trace=True)
        return 0


def start_message_bus(interval: float = 0.5, clock=None):
    """Start polling. Call once from the consumer's ``at_server_start()``.

    Returns the Twisted ``LoopingCall`` so a consumer can stop it. Refuses to
    start without an instance id, or against a bus database that has not been
    migrated — both are one clear failure at startup rather than an error
    logged twice a second forever.

    ``clock`` is a testing seam: pass a ``twisted.internet.task.Clock`` to
    drive the loop without a reactor. Production leaves it alone.
    """
    from twisted.internet.task import LoopingCall

    instance = check_instance_id()
    if not bus_table_exists():
        raise ImproperlyConfigured(
            f"the bus table is missing from the {BUS_ALIAS!r} database. Run "
            f"`evennia migrate --database={BUS_ALIAS}` before starting the "
            f"message bus."
        )

    loop = LoopingCall(_tick)
    if clock is not None:
        loop.clock = clock
    loop.start(interval, now=False)

    # Every other line this library writes is a fault. Without one on the
    # way up, an empty messagebus.log means either "running, nothing
    # notable" or "never started", and nobody can tell which. Evennia
    # timestamps it, so the line also anchors the bus in time against
    # server.log when reading back through a long log.
    bus_log(
        f"message bus started: instance {instance!r}, polling every "
        f"{interval}s, kinds registered: {', '.join(registered_kinds())}"
    )
    return loop
