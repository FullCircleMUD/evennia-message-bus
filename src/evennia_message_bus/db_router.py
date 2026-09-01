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

It refuses the mirror case too — a foreign app on the bus alias. The bus
database holds one table, and ``migrate --database=messagebus`` would
otherwise find no objection to Evennia's own apps and clone the whole game
schema into it. Declining work on our own alias is safe in a way answering
for a foreign app elsewhere would not be.
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
            # Refuse a foreign app on our own alias; express no opinion
            # anywhere else. Declining work on our alias is safe; answering
            # for a foreign app on someone else's alias would capture their
            # router's decision.
            return False if db == BUS_ALIAS else None
        return db == BUS_ALIAS
