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

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

#: Seconds a message may sit deferred before the bus gives up on it and
#: replies undeliverable. Overridden per message type by setting ``timeout``
#: on the class, which is where the knowledge of what it is waiting for
#: lives.
DEFAULT_TIMEOUT = 10

SETTING_NAME = "MESSAGEBUS_INSTANCE_ID"


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
