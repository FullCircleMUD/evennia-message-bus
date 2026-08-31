# SPDX-License-Identifier: BSD-3-Clause
"""Exceptions raised by evennia-message-bus."""


class MessageBusError(Exception):
    """Raised when the bus refuses a request.

    Covers registration conflicts, malformed payloads, and sends the bus
    will not perform (a blank destination, or addressing your own instance
    without opting in). Configuration problems raise Django's
    ``ImproperlyConfigured`` instead — those are settings errors, not
    call-site errors.
    """
