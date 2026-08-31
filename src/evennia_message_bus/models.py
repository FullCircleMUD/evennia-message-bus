# SPDX-License-Identifier: BSD-3-Clause
"""The bus row.

One table, in a database of its own. A sender inserts; the addressee polls,
processes, and the library deletes. Transient communication, not storage —
see docs/design.md § What it is not.
"""

from django.db import models
from django.db.models.functions import Now


class Message(models.Model):
    """One bus message: a row addressed to one instance.

    ``created_at`` is stamped by the **database server**, not by the sending
    process. The age of a message is computed on the receiving instance, so
    a Python-side timestamp would be read against a different clock — and a
    sender running slow would have everything it sends look already-expired
    on arrival. Every instance shares this database, so taking the time from
    here gives them one clock for free.

    ``payload`` is opaque. The bus routes on ``to_instance`` and ``kind``
    and never looks inside it; what identifies a game object across
    instances is the consumer's to decide and the consumer's to resolve.
    """

    created_at = models.DateTimeField(db_default=Now(), db_index=True)
    to_instance = models.CharField(max_length=64, db_index=True)
    from_instance = models.CharField(max_length=64, null=True, blank=True)
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)

    class Meta:
        app_label = "evennia_message_bus"
        indexes = [models.Index(fields=["to_instance", "created_at"])]

    def __str__(self):
        return f"{self.kind} {self.from_instance!r} -> {self.to_instance!r}"
