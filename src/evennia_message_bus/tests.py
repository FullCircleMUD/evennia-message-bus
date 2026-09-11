# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for evennia-message-bus.

Every test's docstring is the case ID it covers in docs/test-plan.md. The
plan is the spec; these tests are written against it and the implementation
is written to pass them.

Run via ``python runtests.py`` from the library root.
"""
import json
import os
from unittest import TestCase as PlainTestCase
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from twisted.internet.task import Clock

import evennia_message_bus
from evennia_message_bus import bus, config, registry
from evennia_message_bus.config import (
    DEFAULT_TIMEOUT,
    check_instance_id,
    get_instance_id,
)
from evennia_message_bus.config import BUS_ALIAS
from evennia_message_bus.errors import MessageBusError
from evennia_message_bus.models import Message
from evennia_message_bus.registry import get_type, register
from evennia_message_bus.types import (
    MessageType,
    Ping,
    PingReceived,
    UndeliverableReply,
    UnknownKind,
)

SELF_ID = "instance-a"
PEER_ID = "instance-b"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class AlwaysHandle(MessageType):
    kind = "always_handle"

    def handle(self, message):
        return True


class AlwaysDefer(MessageType):
    kind = "always_defer"

    def handle(self, message):
        return False


class Raising(MessageType):
    kind = "raising"

    def handle(self, message):
        raise RuntimeError("boom")


class Unimplemented(MessageType):
    """Registered, but never implements handle — inherits NotImplementedError."""

    kind = "unimplemented"


class Recording(MessageType):
    """Records what it was handed; verdict is set per test."""

    kind = "recording"
    seen = []
    pks = []
    verdict = True

    def handle(self, message):
        Recording.seen.append(message)
        # Captured now: the loop deletes the row on a truthy return, and
        # Django nulls the instance's pk when it does.
        Recording.pks.append(message.pk)
        return Recording.verdict


class Keyed(MessageType):
    kind = "keyed"
    payload_keys = ("alpha", "beta")

    def handle(self, message):
        return True


class SlowType(MessageType):
    kind = "slow"
    timeout = 99

    def handle(self, message):
        return False


class SelfAddressable(MessageType):
    kind = "self_addressable"
    can_address_self = True

    def handle(self, message):
        return True


def clear_logs():
    """Empty LOG_DIR's files so a line read back was written by this test.

    Truncated, never removed: Evennia's ``_open_log_file`` caches the handle
    after the first write, and removing the file leaves that handle appending
    to an unlinked inode — every later line silently vanishes. An append-mode
    handle seeks to the end on each write, so a truncated file stays live.
    """
    for name in os.listdir(settings.LOG_DIR):
        if name.endswith(".log"):
            with open(os.path.join(settings.LOG_DIR, name), "w"):
                pass


def read_back_log(filename):
    """Everything written to one file under LOG_DIR, or "" if it is absent."""
    path = os.path.join(settings.LOG_DIR, filename)
    if not os.path.exists(path):
        return ""
    with open(path) as handle:
        return handle.read()


class BusTestCase(TestCase):
    """Base for DB-backed cases: both aliases, and an isolated registry.

    Registration is global state. Without the snapshot/restore, a type
    registered by one test leaks into the next and the RG and PI cases pass
    or fail depending on ordering.
    """

    databases = {"default", BUS_ALIAS}

    def setUp(self):
        snap = registry.snapshot()
        self.addCleanup(registry.restore, snap)
        Recording.seen = []
        Recording.pks = []
        Recording.verdict = True

    def make(self, kind, payload=None, to_instance=SELF_ID, from_instance=PEER_ID):
        """Insert a row directly, bypassing send()'s validation."""
        return Message.objects.create(
            kind=kind,
            payload=payload if payload is not None else {},
            to_instance=to_instance,
            from_instance=from_instance,
        )

    def age(self, message, seconds):
        """Backdate a row so it reads as older than its type's timeout.

        Tests must never sleep; they move the timestamp instead.
        """
        from datetime import timedelta

        from django.utils import timezone

        Message.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - timedelta(seconds=seconds)
        )
        message.refresh_from_db()
        return message


# --------------------------------------------------------------------------
# CF — config accessor and the boot check
# --------------------------------------------------------------------------


class ConfigTest(PlainTestCase):
    @override_settings(MESSAGEBUS_INSTANCE_ID="somewhere")
    def test_returns_the_configured_instance_id(self):
        """CF-01"""
        self.assertEqual(get_instance_id(), "somewhere")

    @override_settings(MESSAGEBUS_INSTANCE_ID=None)
    def test_check_raises_when_setting_absent(self):
        """CF-02"""
        with self.assertRaises(ImproperlyConfigured):
            check_instance_id()

    @override_settings(MESSAGEBUS_INSTANCE_ID="   ")
    def test_check_raises_when_setting_blank(self):
        """CF-03"""
        with self.assertRaises(ImproperlyConfigured):
            check_instance_id()

    @override_settings(MESSAGEBUS_INSTANCE_ID="instance-a")
    def test_check_passes_when_setting_present(self):
        """CF-04"""
        check_instance_id()

    @override_settings(MESSAGEBUS_INSTANCE_ID=None)
    def test_check_message_names_the_setting(self):
        """CF-05"""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            check_instance_id()
        self.assertIn("MESSAGEBUS_INSTANCE_ID", str(ctx.exception))

    def test_app_ready_calls_the_check(self):
        """CF-06"""
        app = apps.get_app_config("evennia_message_bus")
        with mock.patch(
            "evennia_message_bus.config.check_instance_id"
        ) as checked:
            app.ready()
        checked.assert_called_once()

    def test_check_refuses_a_bus_on_the_game_database(self):
        """CF-16"""
        game = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "fcm",
            "HOST": "db.internal",
            "PORT": "5432",
        }
        databases = {"default": dict(game), "messagebus": dict(game)}
        with mock.patch.dict(settings.DATABASES, databases, clear=True):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                config.check_bus_database()
        self.assertIn("DATABASE_URL_MESSAGEBUS", str(ctx.exception))


SQLITE_PATH = "/tmp/messagebus.db3"

PG_BUS_ENTRY = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "fcm_bus",
    "HOST": "db.internal",
    "PORT": "5432",
    "USER": "u",
    "PASSWORD": "secret",
}

PG_GAME_ENTRY = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "fcm",
    "HOST": "db.internal",
    "PORT": "5432",
    "USER": "u",
    "PASSWORD": "secret",
}


class DatabaseDescriptionTest(PlainTestCase):
    def describe(self, bus, default=None):
        # patch.dict rather than override_settings: Django warns that
        # overriding DATABASES can behave unexpectedly, and this only needs
        # the mapping the function reads.
        databases = {"default": default or {}, "messagebus": bus}
        with mock.patch.dict(settings.DATABASES, databases, clear=True):
            return config.describe_bus_database()

    def test_names_the_bus_database(self):
        """CF-11"""
        described = self.describe(dict(PG_BUS_ENTRY), default=dict(PG_GAME_ENTRY))
        self.assertIn("fcm_bus", described)
        self.assertIn("db.internal", described)

    def test_reports_a_local_file(self):
        """CF-13"""
        described = self.describe(
            {"ENGINE": "django.db.backends.sqlite3", "NAME": SQLITE_PATH}
        )
        self.assertIn(SQLITE_PATH, described)
        self.assertIn("local file", described)

    def test_sqlite_path_is_reported_resolved(self):
        """CF-15"""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "messagebus.db3")
            open(real, "w").close()
            link = os.path.join(tmp, "link.db3")
            os.symlink(real, link)

            engine = "django.db.backends.sqlite3"
            via_real = self.describe({"ENGINE": engine, "NAME": real})
            via_link = self.describe({"ENGINE": engine, "NAME": link})

        self.assertEqual(via_real, via_link)
        self.assertNotIn("link.db3", via_link)

    def test_never_reports_a_password(self):
        """CF-14"""
        described = self.describe(dict(PG_BUS_ENTRY), default=dict(PG_GAME_ENTRY))
        self.assertNotIn("secret", described)


# --------------------------------------------------------------------------
# RT — database placement
# --------------------------------------------------------------------------


class RouterTest(BusTestCase):
    def test_write_lands_in_the_bus_alias(self):
        """RT-01"""
        message = self.make("always_handle")
        self.assertEqual(message._state.db, BUS_ALIAS)

    def test_read_comes_from_the_bus_alias(self):
        """RT-02"""
        self.make("always_handle")
        self.assertEqual(Message.objects.all().db, BUS_ALIAS)


# --------------------------------------------------------------------------
# DS — the database spec
# --------------------------------------------------------------------------


class DatabaseSpecTest(PlainTestCase):
    """The AliasSpec this library declares, and the cascade's answer to it."""

    def test_the_spec_names_the_config_alias(self):
        """DS-01"""
        import inspect

        from evennia_message_bus import db_spec

        # Comparing the values proves nothing: Python interns short strings,
        # so two independent "messagebus" literals are the same object. The
        # fact worth pinning is that the spec holds no alias literal of its
        # own.
        source = inspect.getsource(db_spec)
        self.assertIn("from .config import", source)
        self.assertNotIn('alias="messagebus"', source)
        self.assertEqual(db_spec.SPEC.app_label, "evennia_message_bus")
        self.assertEqual(db_spec.SPEC.alias, config.BUS_ALIAS)

    def test_the_spec_refuses_the_shared_rung(self):
        """DS-02"""
        from evennia_message_bus.db_spec import SPEC

        self.assertFalse(SPEC.allow_sharing_common_db)

    def test_the_spec_refuses_foreign_tables(self):
        """DS-03"""
        from evennia_message_bus.db_spec import SPEC

        self.assertFalse(SPEC.allow_foreign_tables_in_own_db)

    def test_configure_resolves_and_routes_the_alias(self):
        """DS-04"""
        import tempfile

        from evennia_database_cascade import configure

        databases, routers = configure(
            {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            ["evennia_message_bus"],
            tempfile.gettempdir(),
            {},
        )
        self.assertIn(config.BUS_ALIAS, databases)
        self.assertTrue(
            databases[config.BUS_ALIAS]["NAME"].endswith("messagebus.db3")
        )
        routed = next(
            alias
            for alias in (router.db_for_write(Message) for router in routers)
            if alias is not None
        )
        self.assertEqual(routed, config.BUS_ALIAS)


# --------------------------------------------------------------------------
# MR — the Message row
# --------------------------------------------------------------------------


class MessageRowTest(BusTestCase):
    def test_created_at_comes_from_the_database(self):
        """MR-01"""
        from django.db.models.functions import Now

        field = Message._meta.get_field("created_at")
        # Not merely "set to something" — an unset db_default is
        # NOT_PROVIDED, so a None check would pass vacuously.
        self.assertIsInstance(field.db_default, Now)
        self.assertFalse(getattr(field, "auto_now_add", False))
        self.assertIsNotNone(self.make("always_handle").created_at)

    def test_nested_payload_round_trips_unchanged(self):
        """MR-02"""
        payload = {"a": [1, {"b": ["c", None, True]}], "d": {"e": {}}}
        message = self.make("always_handle", payload)
        message.refresh_from_db()
        self.assertEqual(message.payload, payload)

    def test_from_instance_may_be_null(self):
        """MR-03"""
        message = Message.objects.create(
            kind="always_handle", payload={}, to_instance=SELF_ID, from_instance=None
        )
        message.refresh_from_db()
        self.assertIsNone(message.from_instance)


# --------------------------------------------------------------------------
# MT — the MessageType class contract
# --------------------------------------------------------------------------


class MessageTypeTest(PlainTestCase):
    def test_base_handle_raises_not_implemented(self):
        """MT-01"""
        with self.assertRaises(NotImplementedError):
            Unimplemented().handle(object())

    def test_timeout_falls_back_to_the_library_default(self):
        """MT-02"""
        self.assertEqual(AlwaysHandle.timeout, DEFAULT_TIMEOUT)

    def test_subclass_timeout_overrides_the_default(self):
        """MT-03"""
        self.assertEqual(SlowType.timeout, 99)
        self.assertNotEqual(SlowType.timeout, DEFAULT_TIMEOUT)

    def test_payload_keys_defaults_to_requiring_nothing(self):
        """MT-04"""
        self.assertEqual(tuple(AlwaysHandle.payload_keys), ())

    def test_subclasses_hold_their_attributes_independently(self):
        """MT-05"""
        self.assertEqual(tuple(Keyed.payload_keys), ("alpha", "beta"))
        self.assertEqual(tuple(AlwaysHandle.payload_keys), ())
        self.assertEqual(AlwaysHandle.timeout, DEFAULT_TIMEOUT)
        self.assertEqual(SlowType.timeout, 99)

    def test_subclass_without_a_kind_is_rejected(self):
        """MT-06"""

        class NoKind(MessageType):
            def handle(self, message):
                return True

        with self.assertRaises(MessageBusError):
            register(NoKind)

    def test_can_address_self_defaults_to_false(self):
        """MT-07"""
        self.assertIs(AlwaysHandle.can_address_self, False)

    def test_can_address_self_is_held_per_subclass(self):
        """MT-08"""
        self.assertIs(SelfAddressable.can_address_self, True)
        self.assertIs(AlwaysHandle.can_address_self, False)


# --------------------------------------------------------------------------
# RG — the handler registry
# --------------------------------------------------------------------------


class RegistryTest(BusTestCase):
    def test_registered_type_is_findable_by_kind(self):
        """RG-01"""
        register(AlwaysHandle)
        self.assertIs(get_type("always_handle"), AlwaysHandle)

    def test_unregistered_kind_is_not_found(self):
        """RG-02"""
        self.assertIsNone(get_type("nobody_registered_this"))

    def test_duplicate_kind_from_an_unrelated_class_raises(self):
        """RG-03"""
        register(AlwaysHandle)

        class Impostor(MessageType):
            kind = "always_handle"

            def handle(self, message):
                return True

        with self.assertRaises(MessageBusError):
            register(Impostor)

    def test_subclass_of_a_registered_type_replaces_it(self):
        """RG-04"""
        register(AlwaysHandle)

        class Extended(AlwaysHandle):
            pass

        register(Extended)
        self.assertIs(get_type("always_handle"), Extended)

    def test_replacement_is_what_the_loop_dispatches_to(self):
        """RG-05"""
        register(Recording)

        class Louder(Recording):
            hits = 0

            def handle(self, message):
                Louder.hits += 1
                return True

        register(Louder)
        self.make("recording")
        bus.process_inbox()
        self.assertEqual(Louder.hits, 1)
        self.assertEqual(Recording.seen, [])

    def test_registering_a_non_message_type_raises(self):
        """RG-06"""

        class NotAType:
            kind = "not_a_type"

        with self.assertRaises(MessageBusError):
            register(NotAType)

    def test_library_types_are_registered_without_consumer_action(self):
        """RG-07"""
        for cls in (Ping, PingReceived, UnknownKind, UndeliverableReply):
            self.assertIs(get_type(cls.kind), cls)

    def test_consumer_subclass_of_a_library_type_composes_via_super(self):
        """RG-08"""

        class WatchedUnknownKind(UnknownKind):
            saw = []

            def handle(self, message):
                handled = super().handle(message)
                WatchedUnknownKind.saw.append(message.payload)
                return handled

        register(WatchedUnknownKind)
        self.assertIs(get_type(UnknownKind.kind), WatchedUnknownKind)
        self.make(UnknownKind.kind, {"kind": "whatever"})
        bus.process_inbox()
        self.assertEqual(WatchedUnknownKind.saw, [{"kind": "whatever"}])


# --------------------------------------------------------------------------
# SD — send
# --------------------------------------------------------------------------


class SendTest(BusTestCase):
    def test_inserts_a_row_with_kind_payload_and_destination(self):
        """SD-01"""
        AlwaysHandle.send(PEER_ID, {"x": 1})
        row = Message.objects.get()
        self.assertEqual(row.kind, "always_handle")
        self.assertEqual(row.payload, {"x": 1})
        self.assertEqual(row.to_instance, PEER_ID)

    def test_from_instance_defaults_to_this_instance(self):
        """SD-02"""
        AlwaysHandle.send(PEER_ID, {})
        self.assertEqual(Message.objects.get().from_instance, SELF_ID)

    def test_returns_the_created_message(self):
        """SD-03"""
        returned = AlwaysHandle.send(PEER_ID, {})
        self.assertIsInstance(returned, Message)
        self.assertEqual(returned.pk, Message.objects.get().pk)

    def test_missing_declared_payload_key_raises(self):
        """SD-04"""
        with self.assertRaises(MessageBusError):
            Keyed.send(PEER_ID, {"alpha": 1})

    def test_extra_payload_key_is_allowed(self):
        """SD-05"""
        Keyed.send(PEER_ID, {"alpha": 1, "beta": 2, "gamma": 3})
        self.assertEqual(Message.objects.get().payload["gamma"], 3)

    def test_non_serialisable_payload_raises_naming_the_kind(self):
        """SD-06"""
        with self.assertRaises(MessageBusError) as ctx:
            AlwaysHandle.send(PEER_ID, {"obj": object()})
        self.assertIn("always_handle", str(ctx.exception))

    def test_nothing_is_written_when_validation_fails(self):
        """SD-07"""
        for payload in ({"alpha": 1}, {"alpha": 1, "beta": object()}):
            with self.assertRaises(MessageBusError):
                Keyed.send(PEER_ID, payload)
        self.assertEqual(Message.objects.count(), 0)

    def test_empty_payload_accepted_when_no_keys_declared(self):
        """SD-08"""
        AlwaysHandle.send(PEER_ID, {})
        self.assertEqual(Message.objects.get().payload, {})

    def test_blank_destination_raises(self):
        """SD-09"""
        for destination in ("", "   ", None):
            with self.assertRaises(MessageBusError):
                AlwaysHandle.send(destination, {})

    def test_sending_to_own_instance_raises_by_default(self):
        """SD-10"""
        with self.assertRaises(MessageBusError):
            AlwaysHandle.send(SELF_ID, {})

    def test_self_send_raise_names_a_duplicate_instance_id(self):
        """SD-11"""
        with self.assertRaises(MessageBusError) as ctx:
            AlwaysHandle.send(SELF_ID, {})
        self.assertIn("MESSAGEBUS_INSTANCE_ID", str(ctx.exception))

    def test_opted_in_type_may_address_its_own_instance(self):
        """SD-12"""
        SelfAddressable.send(SELF_ID, {})
        self.assertEqual(Message.objects.get().to_instance, SELF_ID)

    def test_one_type_opting_in_does_not_free_the_others(self):
        """SD-13"""
        SelfAddressable.send(SELF_ID, {})
        with self.assertRaises(MessageBusError):
            AlwaysHandle.send(SELF_ID, {})


# --------------------------------------------------------------------------
# PO — poll
# --------------------------------------------------------------------------


class PollTest(BusTestCase):
    def test_returns_rows_addressed_to_this_instance(self):
        """PO-01"""
        mine = self.make("always_handle", to_instance=SELF_ID)
        self.assertEqual([m.pk for m in bus.poll()], [mine.pk])

    def test_excludes_rows_for_another_instance(self):
        """PO-02"""
        self.make("always_handle", to_instance=PEER_ID)
        self.assertEqual(list(bus.poll()), [])

    def test_does_not_filter_on_sender(self):
        """PO-03"""
        self.make("always_handle", from_instance="somewhere-else")
        self.make("always_handle", from_instance=None)
        self.assertEqual(bus.poll().count(), 2)

    def test_orders_oldest_first(self):
        """PO-04"""
        first = self.age(self.make("always_handle"), 30)
        second = self.age(self.make("always_handle"), 10)
        third = self.make("always_handle")
        self.assertEqual(
            [m.pk for m in bus.poll()], [first.pk, second.pk, third.pk]
        )

    def test_empty_inbox_returns_nothing(self):
        """PO-05"""
        self.assertEqual(list(bus.poll()), [])

    def test_explicit_instance_id_overrides_the_default(self):
        """PO-06"""
        theirs = self.make("always_handle", to_instance=PEER_ID)
        self.assertEqual([m.pk for m in bus.poll(PEER_ID)], [theirs.pk])

    def test_does_not_delete_or_mutate(self):
        """PO-07"""
        message = self.make("always_handle", {"x": 1})
        list(bus.poll())
        message.refresh_from_db()
        self.assertEqual(message.payload, {"x": 1})
        self.assertEqual(Message.objects.count(), 1)


# --------------------------------------------------------------------------
# LB — library-shipped message types
# --------------------------------------------------------------------------


class LibraryTypesTest(BusTestCase):
    def test_ping_replies_to_the_sender_echoing_the_payload(self):
        """LB-01"""
        self.make(Ping.kind, {"token": "abc"}, from_instance=PEER_ID)
        bus.process_inbox()
        reply = Message.objects.get()
        self.assertEqual(reply.kind, PingReceived.kind)
        self.assertEqual(reply.to_instance, PEER_ID)
        self.assertEqual(reply.payload["echo"], {"token": "abc"})

    def test_ping_without_a_sender_is_consumed_without_a_reply(self):
        """LB-02"""
        self.make(Ping.kind, {}, from_instance=None)
        bus.process_inbox()
        self.assertEqual(Message.objects.count(), 0)

    def test_ping_received_is_consumed_silently(self):
        """LB-03"""
        self.make(PingReceived.kind, {"echo": {}})
        self.assertEqual(bus.process_inbox(), 1)
        self.assertEqual(Message.objects.count(), 0)

    def test_unknown_kind_is_consumed_after_logging(self):
        """LB-04"""
        with mock.patch("evennia_message_bus.types.bus_log") as logged:
            self.make(UnknownKind.kind, {"kind": "mystery"})
            self.assertEqual(bus.process_inbox(), 1)
        self.assertEqual(Message.objects.count(), 0)
        self.assertTrue(logged.called)

    def test_undeliverable_reply_is_consumed_after_logging(self):
        """LB-05"""
        with mock.patch("evennia_message_bus.types.bus_log") as logged:
            self.make(
                UndeliverableReply.kind,
                {"original_kind": "x", "original_payload": {}, "reason": "timeout"},
            )
            self.assertEqual(bus.process_inbox(), 1)
        self.assertEqual(Message.objects.count(), 0)
        self.assertTrue(logged.called)

    def test_no_library_type_resolves_a_game_object(self):
        """LB-06"""
        import pathlib

        import evennia_message_bus as pkg

        root = pathlib.Path(pkg.__file__).parent
        for path in root.rglob("*.py"):
            if path.name == "tests.py":
                continue
            source = path.read_text()
            self.assertNotIn("ObjectDB", source, f"{path.name} references ObjectDB")
            self.assertNotIn("AccountDB", source, f"{path.name} references AccountDB")

    def test_ping_and_its_reply_may_address_this_instance(self):
        """LB-07"""
        self.assertIs(Ping.can_address_self, True)

    def test_self_addressed_ping_replies_to_this_instance(self):
        """LB-08"""
        self.make(Ping.kind, {"token": "loop"}, from_instance=SELF_ID)
        bus.process_inbox()
        reply = Message.objects.get()
        self.assertEqual(reply.kind, PingReceived.kind)
        self.assertEqual(reply.to_instance, SELF_ID)

    def test_ping_received_may_also_address_this_instance(self):
        """LB-09"""
        self.assertIs(PingReceived.can_address_self, True)


# --------------------------------------------------------------------------
# PI — process_inbox
# --------------------------------------------------------------------------


class ProcessInboxTest(BusTestCase):
    def test_handled_message_is_deleted_and_counted(self):
        """PI-01"""
        register(AlwaysHandle)
        self.make("always_handle")
        self.assertEqual(bus.process_inbox(), 1)
        self.assertEqual(Message.objects.count(), 0)

    def test_deferred_message_stays_and_is_not_counted(self):
        """PI-02"""
        register(AlwaysDefer)
        self.make("always_defer")
        self.assertEqual(bus.process_inbox(), 0)
        self.assertEqual(Message.objects.filter(kind="always_defer").count(), 1)

    def test_timed_out_message_replies_undeliverable_and_is_dropped(self):
        """PI-03"""
        register(AlwaysDefer)
        self.age(self.make("always_defer"), DEFAULT_TIMEOUT + 5)
        bus.process_inbox()
        self.assertFalse(Message.objects.filter(kind="always_defer").exists())
        reply = Message.objects.get(kind=UndeliverableReply.kind)
        self.assertEqual(reply.to_instance, PEER_ID)

    def test_undeliverable_payload_carries_the_original(self):
        """PI-04"""
        register(AlwaysDefer)
        self.age(self.make("always_defer", {"x": 1}), DEFAULT_TIMEOUT + 5)
        bus.process_inbox()
        payload = Message.objects.get(kind=UndeliverableReply.kind).payload
        self.assertEqual(payload["original_kind"], "always_defer")
        self.assertEqual(payload["original_payload"], {"x": 1})
        self.assertIn("reason", payload)

    def test_unregistered_kind_is_rejected_immediately(self):
        """PI-05"""
        self.make("nobody_handles_this", {"x": 1})
        bus.process_inbox()
        self.assertFalse(Message.objects.filter(kind="nobody_handles_this").exists())
        reply = Message.objects.get(kind=UnknownKind.kind)
        self.assertEqual(reply.to_instance, PEER_ID)
        self.assertEqual(reply.payload["kind"], "nobody_handles_this")

    def test_unimplemented_handler_is_rejected_immediately(self):
        """PI-06"""
        register(Unimplemented)
        self.make("unimplemented")
        bus.process_inbox()
        self.assertFalse(Message.objects.filter(kind="unimplemented").exists())
        self.assertTrue(Message.objects.filter(kind=UnknownKind.kind).exists())

    def test_other_exceptions_are_logged_and_deferred(self):
        """PI-07"""
        register(Raising)
        self.make("raising")
        with mock.patch("evennia_message_bus.bus.bus_log") as logged:
            self.assertEqual(bus.process_inbox(), 0)
        self.assertEqual(Message.objects.filter(kind="raising").count(), 1)
        self.assertTrue(
            any(call.kwargs.get("trace") for call in logged.call_args_list)
        )

    def test_one_raising_message_does_not_stop_the_cycle(self):
        """PI-08"""
        register(Raising)
        register(AlwaysHandle)
        self.age(self.make("raising"), 1)
        self.make("always_handle")
        with mock.patch("evennia_message_bus.bus.bus_log"):
            self.assertEqual(bus.process_inbox(), 1)
        self.assertFalse(Message.objects.filter(kind="always_handle").exists())

    def test_returns_the_handled_count_only(self):
        """PI-09"""
        register(AlwaysHandle)
        register(AlwaysDefer)
        self.make("always_handle")
        self.make("always_handle")
        self.make("always_defer")
        self.make("nobody_handles_this")
        self.assertEqual(bus.process_inbox(), 2)

    def test_processes_oldest_first(self):
        """PI-10"""
        register(Recording)
        first = self.age(self.make("recording", {"n": 1}), 30)
        second = self.age(self.make("recording", {"n": 2}), 10)
        bus.process_inbox()
        self.assertEqual(Recording.pks, [first.pk, second.pk])

    def test_timeout_is_read_from_the_handling_class(self):
        """PI-11"""
        register(SlowType)
        self.age(self.make("slow"), DEFAULT_TIMEOUT + 5)
        bus.process_inbox()
        self.assertEqual(Message.objects.filter(kind="slow").count(), 1)
        self.assertFalse(Message.objects.filter(kind=UndeliverableReply.kind).exists())

    def test_undeliverable_reply_never_generates_another(self):
        """PI-12"""
        self.age(
            self.make(
                UndeliverableReply.kind,
                {"original_kind": "x", "original_payload": {}, "reason": "timeout"},
            ),
            DEFAULT_TIMEOUT + 5,
        )
        with mock.patch("evennia_message_bus.types.bus_log"):
            bus.process_inbox()
        self.assertEqual(Message.objects.count(), 0)

    def test_unknown_kind_never_generates_another(self):
        """PI-13"""
        self.age(self.make(UnknownKind.kind, {"kind": "x"}), DEFAULT_TIMEOUT + 5)
        with mock.patch("evennia_message_bus.types.bus_log"):
            bus.process_inbox()
        self.assertEqual(Message.objects.count(), 0)

    def test_timed_out_message_without_a_sender_is_dropped_silently(self):
        """PI-14"""
        register(AlwaysDefer)
        self.age(
            self.make("always_defer", from_instance=None), DEFAULT_TIMEOUT + 5
        )
        with mock.patch("evennia_message_bus.bus.bus_log") as logged:
            bus.process_inbox()
        self.assertEqual(Message.objects.count(), 0)
        self.assertTrue(logged.called)

    def test_empty_inbox_returns_zero_and_writes_nothing(self):
        """PI-15"""
        self.assertEqual(bus.process_inbox(), 0)
        self.assertEqual(Message.objects.count(), 0)


# --------------------------------------------------------------------------
# LG — logging
# --------------------------------------------------------------------------


class LoggingTest(BusTestCase):
    def test_unknown_kind_logs_the_kind_and_the_sender(self):
        """LG-02"""
        self.make("nobody_handles_this", from_instance=PEER_ID)
        with mock.patch("evennia_message_bus.bus.bus_log") as logged:
            bus.process_inbox()
        emitted = " ".join(str(call) for call in logged.call_args_list)
        self.assertIn("nobody_handles_this", emitted)
        self.assertIn(PEER_ID, emitted)

    def test_unknown_kind_reply_logs_the_kind_and_the_peer(self):
        """LG-03"""
        with mock.patch("evennia_message_bus.types.bus_log") as logged:
            self.make(UnknownKind.kind, {"kind": "player_arrived"}, from_instance=PEER_ID)
            bus.process_inbox()
        emitted = " ".join(str(call) for call in logged.call_args_list)
        self.assertIn("player_arrived", emitted)
        self.assertIn(PEER_ID, emitted)

    def test_handler_exception_logs_at_error_with_a_traceback(self):
        """LG-04"""
        register(Raising)
        self.make("raising")
        with mock.patch("evennia_message_bus.bus.bus_log") as logged:
            bus.process_inbox()
        self.assertTrue(
            any(
                call.kwargs.get("level") == "ERROR" and call.kwargs.get("trace")
                for call in logged.call_args_list
            )
        )

    def test_start_logs_the_instance_and_interval(self):
        """LG-06"""
        with mock.patch("evennia_message_bus.bus.bus_log") as logged:
            loop = bus.start_message_bus(interval=0.5, clock=Clock())
            self.addCleanup(lambda: loop.running and loop.stop())
        emitted = " ".join(str(call) for call in logged.call_args_list)
        self.assertIn(SELF_ID, emitted)
        self.assertIn("0.5", emitted)
        self.assertTrue(
            any(
                call.kwargs.get("level", "INFO") == "INFO"
                for call in logged.call_args_list
            )
        )

    def test_start_line_reaches_the_log_file(self):
        """LG-07"""
        clear_logs()
        loop = bus.start_message_bus(interval=0.5, clock=Clock())
        self.addCleanup(lambda: loop.running and loop.stop())
        written = read_back_log("messagebus.log")
        self.assertIn(SELF_ID, written)
        self.assertIn("[INFO]", written)

    def test_start_line_names_the_bus_database(self):
        """LG-08"""
        with mock.patch(
            "evennia_message_bus.bus.describe_bus_database",
            return_value="'fcm_bus' on 'db.internal' (from DATABASE_URL_MESSAGEBUS)",
        ):
            with mock.patch("evennia_message_bus.bus.bus_log") as logged:
                loop = bus.start_message_bus(interval=0.5, clock=Clock())
                self.addCleanup(lambda: loop.running and loop.stop())
        emitted = " ".join(str(call) for call in logged.call_args_list)
        self.assertIn("fcm_bus", emitted)
        self.assertIn("DATABASE_URL_MESSAGEBUS", emitted)


# --------------------------------------------------------------------------
# LP — start_message_bus
# --------------------------------------------------------------------------


class LoopTest(BusTestCase):
    def start(self, clock, interval=0.5):
        loop = bus.start_message_bus(interval=interval, clock=clock)
        self.addCleanup(lambda: loop.running and loop.stop())
        return loop

    def test_returns_the_looping_call(self):
        """LP-01"""
        from twisted.internet.task import LoopingCall

        self.assertIsInstance(self.start(Clock()), LoopingCall)

    def test_calls_process_inbox_once_per_interval(self):
        """LP-02"""
        clock = Clock()
        with mock.patch("evennia_message_bus.bus.process_inbox") as processed:
            self.start(clock)
            clock.advance(0.5)
            clock.advance(0.5)
        self.assertEqual(processed.call_count, 2)

    def test_does_not_fire_immediately_on_start(self):
        """LP-03"""
        clock = Clock()
        with mock.patch("evennia_message_bus.bus.process_inbox") as processed:
            self.start(clock)
        self.assertEqual(processed.call_count, 0)

    def test_stopping_the_loop_stops_the_polling(self):
        """LP-04"""
        clock = Clock()
        with mock.patch("evennia_message_bus.bus.process_inbox") as processed:
            loop = self.start(clock)
            clock.advance(0.5)
            loop.stop()
            clock.advance(0.5)
        self.assertEqual(processed.call_count, 1)

    def test_an_exception_in_one_cycle_does_not_stop_the_loop(self):
        """LP-05"""
        clock = Clock()
        register(Raising)
        self.make("raising")
        with mock.patch("evennia_message_bus.bus.bus_log"):
            loop = self.start(clock)
            clock.advance(0.5)
            clock.advance(0.5)
        self.assertTrue(loop.running)

    @override_settings(MESSAGEBUS_INSTANCE_ID=None)
    def test_refuses_to_start_without_an_instance_id(self):
        """LP-06"""
        with self.assertRaises(ImproperlyConfigured):
            bus.start_message_bus(clock=Clock())

    def test_refuses_to_start_against_an_unmigrated_database(self):
        """LP-07"""
        with mock.patch(
            "evennia_message_bus.bus.bus_table_exists", return_value=False
        ):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                bus.start_message_bus(clock=Clock())
        self.assertIn("migrate", str(ctx.exception))


# --------------------------------------------------------------------------
# TX — transaction nesting
# --------------------------------------------------------------------------


class TransactionNestingTest(TransactionTestCase):
    """Django's TestCase wraps each test in its own atomic block, which masks
    the commit behaviour these cases assert. They need TransactionTestCase.
    """

    databases = {"default", BUS_ALIAS}

    def setUp(self):
        snap = registry.snapshot()
        self.addCleanup(registry.restore, snap)
        register(AlwaysHandle)

    def test_failing_bus_write_rolls_back_the_outer_game_work(self):
        """TX-01"""
        from evennia.objects.models import ObjectDB

        before = ObjectDB.objects.count()
        with self.assertRaises(MessageBusError):
            with transaction.atomic():
                ObjectDB.objects.create(db_key="tx-01-probe")
                with transaction.atomic(using=BUS_ALIAS):
                    AlwaysHandle.send(PEER_ID, {"bad": object()})
        self.assertEqual(ObjectDB.objects.count(), before)
        self.assertEqual(Message.objects.count(), 0)

    def test_both_blocks_succeeding_leaves_both_durable(self):
        """TX-02"""
        from evennia.objects.models import ObjectDB

        with transaction.atomic():
            ObjectDB.objects.create(db_key="tx-02-probe")
            with transaction.atomic(using=BUS_ALIAS):
                AlwaysHandle.send(PEER_ID, {"ok": True})
        self.assertTrue(ObjectDB.objects.filter(db_key="tx-02-probe").exists())
        self.assertEqual(Message.objects.count(), 1)


# --------------------------------------------------------------------------
# XC — cross-cutting
# --------------------------------------------------------------------------


class CrossCuttingTest(BusTestCase):
    def test_a_consumer_type_round_trips_end_to_end(self):
        """XC-01"""

        class PlayerArrived(MessageType):
            kind = "player_arrived"
            payload_keys = ("player_ref",)
            landed = []

            def handle(self, message):
                PlayerArrived.landed.append(message.payload["player_ref"])
                return True

        register(PlayerArrived)
        with override_settings(MESSAGEBUS_INSTANCE_ID=PEER_ID):
            PlayerArrived.send(SELF_ID, {"player_ref": "char-42"})
        self.assertEqual(bus.process_inbox(), 1)
        self.assertEqual(PlayerArrived.landed, ["char-42"])
        self.assertEqual(Message.objects.count(), 0)

    def test_a_message_is_visible_only_to_its_addressee(self):
        """XC-02"""
        with override_settings(MESSAGEBUS_INSTANCE_ID=PEER_ID):
            AlwaysHandle.send(SELF_ID, {})
        self.assertEqual(bus.poll(SELF_ID).count(), 1)
        self.assertEqual(bus.poll(PEER_ID).count(), 0)

    def test_payload_reaches_the_handler_unchanged(self):
        """XC-03"""
        register(Recording)
        payload = {"z": 1, "a": [None, False, {"deep": "value"}]}
        self.make("recording", payload)
        bus.process_inbox()
        self.assertEqual(Recording.seen[0].payload, payload)
        self.assertEqual(
            json.dumps(Recording.seen[0].payload, sort_keys=True),
            json.dumps(payload, sort_keys=True),
        )

    def test_library_imports_no_evennia_object_or_account(self):
        """XC-04"""
        import pathlib

        import evennia_message_bus as pkg

        root = pathlib.Path(pkg.__file__).parent
        for path in root.rglob("*.py"):
            if path.name == "tests.py":
                continue
            source = path.read_text()
            self.assertNotIn("evennia.objects", source, f"{path.name}")
            self.assertNotIn("evennia.accounts", source, f"{path.name}")


class TestPackageInstalls(PlainTestCase):
    """Smoke: the package imports and Django loads it as an app."""

    def test_version_is_exposed(self):
        """XC-05"""
        self.assertEqual(evennia_message_bus.__version__, "0.0.1")

    def test_registered_in_installed_apps(self):
        """XC-06"""
        self.assertIn("evennia_message_bus", settings.INSTALLED_APPS)

    def test_app_config_is_loaded(self):
        """XC-07"""
        self.assertEqual(
            apps.get_app_config("evennia_message_bus").name, "evennia_message_bus"
        )
