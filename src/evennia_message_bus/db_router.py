# SPDX-License-Identifier: BSD-3-Clause
"""Database router for evennia-message-bus.

The bus lives in a database of its own, shared by every instance that talks
on it. This router sends the library's models there and answers ``None`` for
everything else.

Answering ``None`` for foreign models is not politeness, it is a
requirement. A consumer can have several library routers in
``DATABASE_ROUTERS`` at once; Django takes the first non-``None`` answer, so
a router that answers for somebody else's model silently captures their
queries and sends them to the wrong database.

``allow_migrate`` returning ``False`` on every other alias is what keeps the
bus table out of each instance's own game database. Without it, a plain
``evennia migrate`` creates the table locally and every instance polls a
private bus that works perfectly and reaches nobody.
"""

APP_LABEL = "evennia_message_bus"

#: The DATABASES alias the bus table lives on. A consumer declares this
#: alias in their settings pointing at the shared bus database.
BUS_ALIAS = "messagebus"


class MessageBusRouter:
    """Routes ``evennia_message_bus`` models to the bus alias."""

    def _is_ours(self, model) -> bool:
        return model._meta.app_label == APP_LABEL

    def db_for_read(self, model, **hints):
        return BUS_ALIAS if self._is_ours(model) else None

    def db_for_write(self, model, **hints):
        return BUS_ALIAS if self._is_ours(model) else None

    def allow_relation(self, obj1, obj2, **hints):
        # The bus holds no foreign key to anything. Expressing no opinion
        # leaves other routers free to answer for their own models.
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label != APP_LABEL:
            return None
        return db == BUS_ALIAS
