# SPDX-License-Identifier: BSD-3-Clause
"""Test runner for evennia-message-bus.

Runs the library's unit tests against tests/test_settings.py — no gamedir
required. Invoke from the library root:

    python runtests.py
"""
import os
import sys

import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")
    django.setup()

    # Evennia's lazy ``Command``/``CmdSet`` exports are populated by
    # ``evennia._init()``, which the real entry points call after
    # ``django.setup()``. The test runner has no such entry point, so it
    # calls it here.
    import evennia
    evennia._init()

    from django.conf import settings
    from django.test.utils import get_runner

    runner = get_runner(settings)()
    failures = runner.run_tests(["evennia_message_bus"])
    sys.exit(bool(failures))
