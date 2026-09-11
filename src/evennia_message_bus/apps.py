# SPDX-License-Identifier: BSD-3-Clause
"""Django AppConfig for evennia-message-bus.

Only loaded when the consumer adds ``evennia_message_bus`` to
``INSTALLED_APPS``.

``ready()`` refuses the boot if ``MESSAGEBUS_INSTANCE_ID`` is unset. This is
the earliest hook the library controls — it runs during ``django.setup()``,
before Evennia's ``_init()`` — so an installed-but-unconfigured consumer
fails to start rather than running with a silently broken bus.

It runs in every process that loads the app registry, including every
``manage.py`` command, so the setting has to be in place before the bus
database can be migrated. That is the deliberate price of failing at boot.
"""

from django.apps import AppConfig

from . import config


class EvenniaMessageBusConfig(AppConfig):
    name = "evennia_message_bus"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        config.check_instance_id()
        config.check_bus_database()
