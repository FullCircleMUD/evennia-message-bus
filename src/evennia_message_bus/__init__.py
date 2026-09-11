# SPDX-License-Identifier: BSD-3-Clause
"""evennia-message-bus: messaging between separate Evennia instances.

Declare one class per message type, register it, and send::

    from evennia_message_bus import MessageType, register, start_message_bus

    class PlayerArrived(MessageType):
        kind = "player_arrived"
        payload_keys = ("player_ref",)

        def handle(self, message) -> bool:
            restore(message.payload["player_ref"])
            return True

    register(PlayerArrived)
    PlayerArrived.send("instance-b", {"player_ref": "..."})

See docs/design.md for the whole design.

Names are resolved lazily. Importing the package runs while Django is still
building its app registry, and eagerly importing anything that touches
models would raise ``AppRegistryNotReady``.
"""

__version__ = "0.0.1"

_LAZY = {
    "MessageType": ("evennia_message_bus.types", "MessageType"),
    "Ping": ("evennia_message_bus.types", "Ping"),
    "PingReceived": ("evennia_message_bus.types", "PingReceived"),
    "UnknownKind": ("evennia_message_bus.types", "UnknownKind"),
    "UndeliverableReply": ("evennia_message_bus.types", "UndeliverableReply"),
    "register": ("evennia_message_bus.registry", "register"),
    "get_type": ("evennia_message_bus.registry", "get_type"),
    "registered_kinds": ("evennia_message_bus.registry", "registered_kinds"),
    "start_message_bus": ("evennia_message_bus.bus", "start_message_bus"),
    "process_inbox": ("evennia_message_bus.bus", "process_inbox"),
    "poll": ("evennia_message_bus.bus", "poll"),
    "get_instance_id": ("evennia_message_bus.config", "get_instance_id"),
    "DEFAULT_TIMEOUT": ("evennia_message_bus.config", "DEFAULT_TIMEOUT"),
    "BUS_ALIAS": ("evennia_message_bus.config", "BUS_ALIAS"),
    "MessageBusError": ("evennia_message_bus.errors", "MessageBusError"),
    "Message": ("evennia_message_bus.models", "Message"),
}

__all__ = sorted(_LAZY) + ["__version__"]


def __getattr__(name):
    try:
        module_path, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    value = getattr(import_module(module_path), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))
