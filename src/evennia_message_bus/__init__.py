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

# The map itself lives in config.py with every other module-level constant.
# It is plain data, so importing it here costs nothing and resolves nothing:
# the names below are still imported only when something asks for one.
from .config import LAZY_EXPORTS  # noqa: E402

__all__ = sorted(LAZY_EXPORTS) + ["__version__"]


def __getattr__(name):
    try:
        module_path, attr = LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    value = getattr(import_module(module_path), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(LAZY_EXPORTS))
