# SPDX-License-Identifier: BSD-3-Clause
"""Message types — the surface a consumer writes against.

One class per message type. The class owns the kind, the timeout, the
payload contract, whether it may address its own instance, the send call and
the handler, so the kind string appears once and the two sides of a message
type are written together instead of in two files that can drift.

The library owns every write to the row. A handler returns ``True`` for done
or ``False`` for not-yet; the poll loop deletes, defers, or times out. The
word ``delete`` never appears in consumer code.
"""

import json

from .config import DEFAULT_TIMEOUT, get_instance_id
from .errors import MessageBusError
from .log import bus_log
from .registry import register


class MessageType:
    """Base class for a message type.

    Subclass it, set ``kind``, implement ``handle``, and register it::

        class PlayerArrived(MessageType):
            kind = "player_arrived"
            timeout = 30
            payload_keys = ("player_ref",)

            def handle(self, message) -> bool:
                restore(message.payload["player_ref"])
                return True

        register(PlayerArrived)
        PlayerArrived.send("instance-b", {"player_ref": "..."})
    """

    #: The string this type is addressed and dispatched by. Required.
    kind: str | None = None

    #: Seconds this type may sit deferred before the bus gives up and
    #: replies undeliverable. Set it here, on the only code that knows what
    #: it is waiting for — a handoff legitimately takes longer than a
    #: notification.
    timeout: int = DEFAULT_TIMEOUT

    #: Keys ``send`` requires in the payload. A floor, not a schema: extra
    #: keys are fine, missing ones are refused before anything is written.
    payload_keys: tuple = ()

    #: Whether this type may address its own instance. Off by default,
    #: because a self-addressed message is nearly always two instances
    #: sharing an instance id.
    can_address_self: bool = False

    # -- receiving ---------------------------------------------------

    def handle(self, message) -> bool:
        """Process one message. ``True`` = done, ``False`` = not yet.

        Returning ``False`` leaves the row for the next poll; the loop gives
        up once the message is older than this class's ``timeout``.

        The base raises ``NotImplementedError``. The loop treats that the
        same as an unregistered kind — rejected immediately rather than
        deferred, because it will still be unimplemented on the next poll.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.handle() is not implemented."
        )

    # -- sending -----------------------------------------------------

    @classmethod
    def send(cls, to_instance, payload=None, from_instance=None):
        """Insert one row addressed to ``to_instance``. Returns the row.

        Validates before writing: the destination must be a non-blank string
        that is not this instance (unless ``can_address_self``), every key in
        ``payload_keys`` must be present, and the payload must be
        JSON-serialisable. Nothing is written if any of that fails.
        """
        from .models import Message

        if not cls.kind:
            raise MessageBusError(
                f"{cls.__name__} declares no 'kind' and cannot be sent."
            )
        if not to_instance or not str(to_instance).strip():
            raise MessageBusError(
                f"send() needs a destination instance; got {to_instance!r}."
            )

        sender = from_instance if from_instance is not None else get_instance_id()
        if to_instance == sender and not cls.can_address_self:
            raise MessageBusError(
                f"{cls.__name__} refused to send to {to_instance!r}, which is "
                f"this instance's own MESSAGEBUS_INSTANCE_ID. If two instances "
                f"are configured with the same MESSAGEBUS_INSTANCE_ID, that is "
                f"the bug — each will consume the other's messages. If this "
                f"type genuinely means to address itself, set "
                f"can_address_self = True on it."
            )

        payload = {} if payload is None else payload
        missing = [key for key in cls.payload_keys if key not in payload]
        if missing:
            raise MessageBusError(
                f"{cls.kind!r} payload is missing required key(s): "
                f"{', '.join(sorted(missing))}."
            )
        try:
            json.dumps(payload)
        except (TypeError, ValueError) as err:
            raise MessageBusError(
                f"{cls.kind!r} payload is not JSON-serialisable: {err}. The "
                f"payload crosses a database between two processes, so it can "
                f"only carry data — pass an identifier the other instance can "
                f"resolve, not a live object."
            ) from err

        return Message.objects.create(
            kind=cls.kind,
            payload=payload,
            to_instance=to_instance,
            from_instance=sender,
        )


# ---------------------------------------------------------------------
# Library-shipped types
#
# Registered by the library into the same registry a consumer uses. They
# are always present because we put them there, not because they are
# special — and a consumer can subclass any of them to react.
# ---------------------------------------------------------------------


class PingReceived(MessageType):
    """The reply to a ping. Consumed silently.

    Opted into self-addressing because the reply to a self-ping is itself
    self-addressed.
    """

    kind = "ping_received"
    can_address_self = True

    def handle(self, message) -> bool:
        return True


class Ping(MessageType):
    """Diagnostic round trip. Replies ``ping_received``, echoing the payload.

    Self-addressing is allowed, following the networking meaning: pinging a
    peer asks whether that instance is reachable, pinging yourself asks
    whether your own bus is alive and dispatching. They are different
    questions and both are worth being able to ask.
    """

    kind = "ping"
    can_address_self = True

    def handle(self, message) -> bool:
        if not message.from_instance:
            # No return address. Received, but there is nobody to answer.
            return True
        PingReceived.send(
            message.from_instance,
            {"original_pk": message.pk, "echo": message.payload},
        )
        return True


class UnknownKind(MessageType):
    """A peer telling us it has no handler for a kind we sent it.

    This is the line that closes the common debug. Someone sends a message,
    nothing happens, and they look in their own log — it has to say that the
    peer has never heard of this kind, not merely that something failed.
    """

    kind = "unknown_kind"

    def handle(self, message) -> bool:
        rejected = message.payload.get("kind")
        bus_log(
            f"{message.from_instance!r} does not handle kind {rejected!r} — "
            f"it has no handler registered for it. The message was dropped "
            f"at that end.",
            level="WARN",
        )
        return True


class UndeliverableReply(MessageType):
    """A message we sent aged out at the far end without being handled."""

    kind = "undeliverable_reply"

    def handle(self, message) -> bool:
        payload = message.payload
        bus_log(
            f"{message.from_instance!r} could not deliver our "
            f"{payload.get('original_kind')!r} message "
            f"(reason: {payload.get('reason')!r}).",
            level="WARN",
        )
        return True


for _cls in (Ping, PingReceived, UnknownKind, UndeliverableReply):
    register(_cls)
