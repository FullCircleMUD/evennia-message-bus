# SPDX-License-Identifier: BSD-3-Clause
"""The database alias this library declares to evennia-database-cascade.

The cascade derives the ``DATABASES`` entry, the router and the migration
list from this declaration — the library ships no router and no resolution
code of its own.

``allow_sharing_common_db=False`` is the library's position, not a
table-collision constraint: the bus is the transport *between* instances,
so it must not live inside any one instance's database. A deployment that
wants the bus on a shared server says so explicitly with
``DATABASE_URL_MESSAGEBUS``.

On the consumer's settings path, so it imports nothing from Django.
"""

from evennia_database_cascade import AliasSpec

from .config import BUS_ALIAS

SPEC = AliasSpec(
    app_label="evennia_message_bus",
    alias=BUS_ALIAS,
    allow_sharing_common_db=False,
)
