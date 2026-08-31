# SPDX-License-Identifier: BSD-3-Clause
"""The message-type registry.

The poll loop dispatches from here: a row's ``kind`` is looked up, and the
class registered under it handles the message. A consumer registers its own
types alongside the library's.

A second, unrelated class claiming a kind already taken raises — two
handlers for one kind is a bug, and a silent overwrite would make it a very
quiet one. A **subclass** of the registered type replaces it instead, which
is how a consumer hooks a library type: subclass it, call
``super().handle(message)`` to keep the library behaviour, add your own, and
register the subclass.
"""

from .errors import MessageBusError

_REGISTRY: dict[str, type] = {}


def register(cls):
    """Register a ``MessageType`` subclass. Returns it, so it can decorate.

    Raises ``MessageBusError`` if the class is not a ``MessageType``, if it
    declares no ``kind``, or if a different type already holds that kind and
    this one is not a subclass of it.
    """
    from .types import MessageType

    if not isinstance(cls, type) or not issubclass(cls, MessageType):
        raise MessageBusError(
            f"register() takes a MessageType subclass; got {cls!r}."
        )
    kind = getattr(cls, "kind", None)
    if not kind or not str(kind).strip():
        raise MessageBusError(
            f"{cls.__name__} declares no 'kind'. A message type needs one to "
            f"be addressable — it is the string the receiving instance "
            f"dispatches on."
        )

    existing = _REGISTRY.get(kind)
    if existing is not None and existing is not cls and not issubclass(cls, existing):
        raise MessageBusError(
            f"kind {kind!r} is already registered to {existing.__name__}. Two "
            f"unrelated types cannot share a kind. To extend the registered "
            f"one, subclass it and register the subclass — that replaces it."
        )
    _REGISTRY[kind] = cls
    return cls


def get_type(kind: str):
    """Return the type registered for ``kind``, or ``None``."""
    return _REGISTRY.get(kind)


def registered_kinds() -> list[str]:
    """Every kind currently registered, sorted."""
    return sorted(_REGISTRY)


def snapshot() -> dict:
    """Copy the registry. Paired with ``restore`` for test isolation."""
    return dict(_REGISTRY)


def restore(snap: dict) -> None:
    """Replace the registry's contents with a previous ``snapshot``."""
    _REGISTRY.clear()
    _REGISTRY.update(snap)
