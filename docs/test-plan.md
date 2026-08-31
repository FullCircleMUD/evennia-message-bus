# Test plan

Every test case the library commits to covering, and the test function that covers it. The library is
built test-first: cases are agreed here, tests are written against them, then the implementation is
written to pass. The **Test function** column is the auditable trail — it is filled in as each test is
written, so an empty cell means the case is agreed but not yet covered.

Case IDs are stable and referenceable. Do not renumber; retire an ID rather than reuse it. Every test
function carries its case ID as its docstring, so the trail reads in both directions.

All test functions live in `src/evennia_message_bus/tests.py`.

| Prefix | Covers |
|---|---|
| `CF` | Config accessor and the boot-time check |
| `RT` | Database placement — the router |
| `MR` | The `Message` row |
| `MT` | The `MessageType` class contract |
| `RG` | The handler registry |
| `SD` | `send` — including payload validation |
| `PO` | `poll` |
| `LB` | Library-shipped message types |
| `PI` | `process_inbox` — one lifecycle cycle |
| `LG` | Logging |
| `LP` | `start_message_bus` — the polling loop |
| `TX` | Transaction nesting across the two databases |
| `XC` | Cross-cutting |

## The shape being tested

A consumer declares one class per message type. The class owns the kind, the timeout, the payload
contract, whether it may address its own instance, the send call and the handler:

```python
class PlayerArrived(MessageType):
    kind = "player_arrived"
    timeout = 10
    payload_keys = ("player_ref", "from_room")
    can_address_self = False

    def handle(self, message) -> bool:   # True = done, False = not yet
        ...

register(PlayerArrived)
PlayerArrived.send("shard1", {"player_ref": ..., "from_room": ...})
```

The library owns every write to the row. `delete` never appears in consumer code — the poll loop
deletes on a truthy return, leaves the row on a falsy one, and on `age > cls.timeout` sends an
`undeliverable_reply` and drops it.

## Fixtures

Unlike `evennia-targeting`, this suite needs a database — a bus is a table. It runs two SQLite aliases
and the library's own router, mirroring a real consumer install.

| Fixture | Purpose |
|---|---|
| Two test aliases, `default` and the bus alias | Without both plus the router, a test for `send` would write to `default` and pass for the wrong reason |
| Distinct `TEST["NAME"]` shared-cache URIs | Two aliases both saying `:memory:` look like one database to Django's runner, which then treats the second as a mirror — the router would appear to work while both pointed at the same file. `evennia-archive` hit this; see its `tests/test_settings.py` |
| **Registry isolation** | Registration is global state. Every test that registers must snapshot the registry and restore it on teardown, or a type leaks into the next test and RG/PI cases pass or fail depending on ordering |
| `RecordingType` | Records every message passed to `handle`, returns a verdict set per test |
| `AlwaysHandle` / `AlwaysDefer` / `Raising` / `Unimplemented` | Fixed outcomes: `True`, `False`, an arbitrary exception, and the base `NotImplementedError` |
| `override_settings` | `MESSAGEBUS_INSTANCE_ID`, per test |
| Direct `created_at` writes | Ageing a message past its timeout. Tests must not sleep; they set the timestamp |
| A captured log sink | Asserting the `LG` cases without writing to a real log directory |
| `twisted.internet.task.Clock` | Driving `LoopingCall` without a live reactor |

Two structural notes that fall out of the cases below:

- **The boot check must be a callable the tests can invoke.** By the time a test runs the app registry
  is already built and `AppConfig.ready()` has been and gone. So `ready()` stays a one-liner
  delegating to a checking function, and CF-02..CF-05 test that function directly.
- **`TX` cases need `TransactionTestCase`.** Django's `TestCase` wraps each test in an atomic block,
  which masks the commit behaviour those cases assert. Everything else uses `TestCase`.

## CF — config accessor and the boot check

| ID | Case | Test function |
|---|---|---|
| CF-01 | `get_instance_id()` returns `MESSAGEBUS_INSTANCE_ID` | `ConfigTest.test_returns_the_configured_instance_id` |
| CF-02 | The check raises `ImproperlyConfigured` when the setting is absent | `ConfigTest.test_check_raises_when_setting_absent` |
| CF-03 | The check raises when the setting is present but blank | `ConfigTest.test_check_raises_when_setting_blank` |
| CF-04 | The check passes when the setting is a non-empty string | `ConfigTest.test_check_passes_when_setting_present` |
| CF-05 | The raised message names `MESSAGEBUS_INSTANCE_ID` — a consumer must not have to guess | `ConfigTest.test_check_message_names_the_setting` |
| CF-06 | `AppConfig.ready()` calls the check, so an unconfigured consumer cannot boot | `ConfigTest.test_app_ready_calls_the_check` |

## RT — database placement

| ID | Case | Test function |
|---|---|---|
| RT-01 | A `Message` write lands in the bus alias | `RouterTest.test_write_lands_in_the_bus_alias` |
| RT-02 | A `Message` read comes from the bus alias | `RouterTest.test_read_comes_from_the_bus_alias` |
| RT-03 | `allow_migrate` is `True` for this app on the bus alias | `RouterTest.test_allow_migrate_true_on_the_bus_alias` |
| RT-04 | `allow_migrate` is `False` for this app on `default` — the table must not appear in the game database | `RouterTest.test_allow_migrate_false_on_default` |
| RT-05 | `allow_migrate` returns `None` for a foreign app | `RouterTest.test_allow_migrate_none_for_a_foreign_app` |
| RT-06 | `db_for_read` / `db_for_write` return `None` for a foreign model | `RouterTest.test_db_for_read_and_write_none_for_a_foreign_model` |

RT-04 is the case that matters most operationally. Get it wrong and a plain `evennia migrate` creates
the bus table inside each instance's own game database — every instance then polls a private bus that
works perfectly and reaches nobody.

RT-05 and RT-06 are the constraint `evennia-ai-memory` documents in its `interoperability.md`: a
consumer can have several library routers in `DATABASE_ROUTERS` at once, and a router that answers for
somebody else's model silently captures their queries.

## MR — the `Message` row

| ID | Case | Test function |
|---|---|---|
| MR-01 | `created_at` is set by the database server, not the sending process's Python clock | `MessageRowTest.test_created_at_comes_from_the_database` |
| MR-02 | A payload nested several levels deep round-trips unchanged | `MessageRowTest.test_nested_payload_round_trips_unchanged` |
| MR-03 | `from_instance` may be null | `MessageRowTest.test_from_instance_may_be_null` |

MR-01 is why the timestamp is not `auto_now_add`. That stamps from the sender's clock and the age is
then computed against the receiver's; between independent machines, a sender whose clock runs slow has
every message it sends look already-expired on arrival. Every instance shares the bus database, so
taking the timestamp from the database server gives them one clock for free.

## MT — the `MessageType` class contract

| ID | Case | Test function |
|---|---|---|
| MT-01 | The base `handle()` raises `NotImplementedError` | `MessageTypeTest.test_base_handle_raises_not_implemented` |
| MT-02 | `timeout` falls back to the library default when a subclass does not set it | `MessageTypeTest.test_timeout_falls_back_to_the_library_default` |
| MT-03 | A subclass setting `timeout` uses its own value | `MessageTypeTest.test_subclass_timeout_overrides_the_default` |
| MT-04 | `payload_keys` defaults to requiring nothing | `MessageTypeTest.test_payload_keys_defaults_to_requiring_nothing` |
| MT-05 | Two subclasses hold independent `timeout` and `payload_keys` — no leakage through the base | `MessageTypeTest.test_subclasses_hold_their_attributes_independently` |
| MT-06 | Registering a subclass that declares no `kind` raises — the check is at registration, not at class definition, so abstract intermediate subclasses stay legal | `MessageTypeTest.test_subclass_without_a_kind_is_rejected` |
| MT-07 | `can_address_self` defaults to `False` | `MessageTypeTest.test_can_address_self_defaults_to_false` |
| MT-08 | A subclass setting `can_address_self = True` holds it independently of every other type | `MessageTypeTest.test_can_address_self_is_held_per_subclass` |

## RG — the handler registry

| ID | Case | Test function |
|---|---|---|
| RG-01 | A registered type is findable by its kind | `RegistryTest.test_registered_type_is_findable_by_kind` |
| RG-02 | An unregistered kind is not found | `RegistryTest.test_unregistered_kind_is_not_found` |
| RG-03 | Registering a second, unrelated class for a kind already taken raises | `RegistryTest.test_duplicate_kind_from_an_unrelated_class_raises` |
| RG-04 | Registering a **subclass** of a registered type replaces it rather than raising | `RegistryTest.test_subclass_of_a_registered_type_replaces_it` |
| RG-05 | After a subclass replaces a type, the loop dispatches to the subclass | `RegistryTest.test_replacement_is_what_the_loop_dispatches_to` |
| RG-06 | Registering a class that is not a `MessageType` raises | `RegistryTest.test_registering_a_non_message_type_raises` |
| RG-07 | The library's own types are registered without any consumer action | `RegistryTest.test_library_types_are_registered_without_consumer_action` |
| RG-08 | A consumer subclass of a library type replaces it, and `super().handle()` still runs the library behaviour | `RegistryTest.test_consumer_subclass_of_a_library_type_composes_via_super` |

RG-04 and RG-08 are what let a consumer react to `unknown_kind` — alerting staff, incrementing a
counter, marking a peer degraded — without the library carrying a special case for its own types.

## SD — `send(to_instance, payload)`

| ID | Case | Test function |
|---|---|---|
| SD-01 | Inserts one row carrying the class's `kind`, the given payload, and `to_instance` | `SendTest.test_inserts_a_row_with_kind_payload_and_destination` |
| SD-02 | `from_instance` defaults to `get_instance_id()` | `SendTest.test_from_instance_defaults_to_this_instance` |
| SD-03 | Returns the created `Message` | `SendTest.test_returns_the_created_message` |
| SD-04 | A payload missing a declared `payload_keys` entry raises before any insert | `SendTest.test_missing_declared_payload_key_raises` |
| SD-05 | A key not named in `payload_keys` is allowed — the declaration is a floor, not a schema | `SendTest.test_extra_payload_key_is_allowed` |
| SD-06 | A non-JSON-serialisable payload raises before any insert, naming the kind | `SendTest.test_non_serialisable_payload_raises_naming_the_kind` |
| SD-07 | Nothing is written when validation fails | `SendTest.test_nothing_is_written_when_validation_fails` |
| SD-08 | An empty payload is accepted when the class declares no `payload_keys` | `SendTest.test_empty_payload_accepted_when_no_keys_declared` |
| SD-09 | A blank `to_instance` raises | `SendTest.test_blank_destination_raises` |
| SD-10 | Sending to this instance's own ID raises, by default | `SendTest.test_sending_to_own_instance_raises_by_default` |
| SD-11 | That raise names a duplicate `MESSAGEBUS_INSTANCE_ID` as the likely cause | `SendTest.test_self_send_raise_names_a_duplicate_instance_id` |
| SD-12 | A type declaring `can_address_self = True` may send to its own instance | `SendTest.test_opted_in_type_may_address_its_own_instance` |
| SD-13 | One type opting in does not let any other type address itself | `SendTest.test_one_type_opting_in_does_not_free_the_others` |

SD-11 is the case that carries the design's weight. Two instances sharing an instance ID is a brutal
bug — each eats the other's messages, half of everything vanishes, and nothing points at the cause.
The self-send refusal catches it at the first send, but only if the message says so; otherwise
someone hitting it goes looking for a way past the check instead of finding the misconfiguration.
That is also why `can_address_self` being overridable is safe: the teaching is in the message, not in
the refusal being unavoidable.

## PO — `poll(instance_id=None)`

| ID | Case | Test function |
|---|---|---|
| PO-01 | Returns rows addressed to this instance | `PollTest.test_returns_rows_addressed_to_this_instance` |
| PO-02 | Excludes rows addressed to another instance | `PollTest.test_excludes_rows_for_another_instance` |
| PO-03 | Does not filter on `from_instance` — who sent it is irrelevant to delivery | `PollTest.test_does_not_filter_on_sender` |
| PO-04 | Orders oldest first by `created_at` | `PollTest.test_orders_oldest_first` |
| PO-05 | An empty inbox returns nothing | `PollTest.test_empty_inbox_returns_nothing` |
| PO-06 | An explicit `instance_id` overrides the default | `PollTest.test_explicit_instance_id_overrides_the_default` |
| PO-07 | Neither deletes nor mutates a row | `PollTest.test_does_not_delete_or_mutate` |

## LB — library-shipped message types

| ID | Case | Test function |
|---|---|---|
| LB-01 | `ping` replies `ping_received` to `from_instance`, echoing the payload | `LibraryTypesTest.test_ping_replies_to_the_sender_echoing_the_payload` |
| LB-02 | `ping` with no `from_instance` is consumed without a reply | `LibraryTypesTest.test_ping_without_a_sender_is_consumed_without_a_reply` |
| LB-03 | `ping_received` is consumed silently | `LibraryTypesTest.test_ping_received_is_consumed_silently` |
| LB-04 | `unknown_kind` is consumed after logging, so a rejected send always ends somewhere | `LibraryTypesTest.test_unknown_kind_is_consumed_after_logging` |
| LB-05 | `undeliverable_reply` is consumed after logging | `LibraryTypesTest.test_undeliverable_reply_is_consumed_after_logging` |
| LB-06 | No library type resolves a game object — asserted statically: the source references neither `ObjectDB` nor `AccountDB` | `LibraryTypesTest.test_no_library_type_resolves_a_game_object` |
| LB-07 | `ping` declares `can_address_self = True` | `LibraryTypesTest.test_ping_and_its_reply_may_address_this_instance` |
| LB-08 | A self-addressed `ping` produces a `ping_received` back to this instance | `LibraryTypesTest.test_self_addressed_ping_replies_to_this_instance` |
| LB-09 | `ping_received` declares `can_address_self = True`, so the reply to a self-ping can be sent at all | `LibraryTypesTest.test_ping_received_may_also_address_this_instance` |

LB-06 is the guard on the identity principle. `evennia-shards` ships `obj_msg`, `account_msg`,
`room_msg` and `flush_from_cache`, all of which read a primary key out of a payload. None carry over,
and this case is what stops one drifting back in.

LB-07..LB-09 follow the networking meaning of ping. Pinging a peer and pinging yourself answer two
different questions — "is that instance reachable" and "is my own bus alive and dispatching" — and
both are worth being able to ask. LB-09 is the one that is easy to miss: the reply to a self-ping is
itself a self-addressed message, so `ping_received` needs the same opt-in or the handler raises on its
own rule while trying to answer.

## PI — `process_inbox()`

| ID | Case | Test function |
|---|---|---|
| PI-01 | `handle` returns `True` → the row is deleted and counted as processed | `ProcessInboxTest.test_handled_message_is_deleted_and_counted` |
| PI-02 | `handle` returns `False`, message younger than the timeout → the row stays, uncounted | `ProcessInboxTest.test_deferred_message_stays_and_is_not_counted` |
| PI-03 | `handle` returns `False`, message older than the timeout → `undeliverable_reply` to `from_instance`, original deleted | `ProcessInboxTest.test_timed_out_message_replies_undeliverable_and_is_dropped` |
| PI-04 | The `undeliverable_reply` payload carries the original kind, the original payload, and a reason | `ProcessInboxTest.test_undeliverable_payload_carries_the_original` |
| PI-05 | A kind with no registered type → `unknown_kind` reply **immediately**, row deleted, no waiting out the timeout | `ProcessInboxTest.test_unregistered_kind_is_rejected_immediately` |
| PI-06 | A registered type whose `handle` is unimplemented → same immediate reject path as PI-05 | `ProcessInboxTest.test_unimplemented_handler_is_rejected_immediately` |
| PI-07 | Any other exception from `handle` is logged with its traceback and treated as a defer | `ProcessInboxTest.test_other_exceptions_are_logged_and_deferred` |
| PI-08 | One message raising does not stop the rest of the cycle | `ProcessInboxTest.test_one_raising_message_does_not_stop_the_cycle` |
| PI-09 | Returns the count of messages the handler processed; rejected and timed-out ones are not counted | `ProcessInboxTest.test_returns_the_handled_count_only` |
| PI-10 | Messages are processed oldest first | `ProcessInboxTest.test_processes_oldest_first` |
| PI-11 | The timeout is read from the handling class, not from settings | `ProcessInboxTest.test_timeout_is_read_from_the_handling_class` |
| PI-12 | An `undeliverable_reply` never generates another one | `ProcessInboxTest.test_undeliverable_reply_never_generates_another` |
| PI-13 | An `unknown_kind` never generates another `unknown_kind` | `ProcessInboxTest.test_unknown_kind_never_generates_another` |
| PI-14 | A timed-out message with no `from_instance` is dropped and logged, with no reply | `ProcessInboxTest.test_timed_out_message_without_a_sender_is_dropped_silently` |
| PI-15 | An empty inbox returns 0 and writes nothing | `ProcessInboxTest.test_empty_inbox_returns_zero_and_writes_nothing` |

PI-05 and PI-06 share a rule: if a message can never succeed, say so now rather than deferring to the
same answer twenty polls later. PI-12 and PI-13 are the loop guards — a reply that could itself be
rejected would ping-pong between two instances forever.

## LG — logging

| ID | Case | Test function |
|---|---|---|
| LG-01 | Lines go to the library's own `messagebus.log`, not Evennia's main log | `LoggingTest.test_lines_go_to_the_libraries_own_log_file` |
| LG-02 | Receiving an unknown kind logs a warning naming the kind and the sender | `LoggingTest.test_unknown_kind_logs_the_kind_and_the_sender` |
| LG-03 | Receiving an `unknown_kind` reply logs a warning naming the kind and the peer that rejected it | `LoggingTest.test_unknown_kind_reply_logs_the_kind_and_the_peer` |
| LG-04 | A handler exception logs at ERROR with the traceback attached | `LoggingTest.test_handler_exception_logs_at_error_with_a_traceback` |
| LG-05 | The shim is a silent no-op outside an Evennia engine, so tests need no log directory | `LoggingTest.test_shim_is_a_no_op_outside_an_evennia_engine` |
| LG-06 | Starting the loop logs at INFO, naming the instance id and the interval | `LoggingTest.test_start_logs_the_instance_and_interval` |
| LG-07 | That startup line reaches `messagebus.log` itself — asserted through the shim rather than at the call site | `LoggingTest.test_start_line_reaches_the_log_file` |

LG-03 is the line that closes the common debug. Someone sends a message, nothing happens, and they
look in their own log — it has to say "that peer has never heard of this kind", not just that
something failed. The pattern is `evennia-shards`' `log.py`; its `trace=True` parameter exists for
exactly LG-04.

LG-06 exists because every other line here is written when something goes wrong, which leaves an empty
`messagebus.log` meaning either "running fine, nothing notable" or "never started". Those are not the
same thing and an operator cannot tell them apart. One line at startup makes the file's absence mean
something, and puts the instance id in writing — the value that is worst to diagnose when two
instances accidentally share it.

LG-07 is the same behaviour asserted one layer down. LG-06 patches `bus_log`, so it proves the call is
made with the right content and nothing about whether it lands anywhere; LG-07 patches Evennia's
`log_file` instead, so the line has to travel through the shim to pass. The pair is deliberate: the
startup line's whole value is being *in the file*, and a test that stops at the call site would let a
broken shim keep passing.

Lines carry no timestamp of their own. Evennia's `log_file` already prefixes every line with one, in
UTC, matching `server.log` — so a bus line and a server line read against each other with no offset,
and adding our own would stamp every line twice.

## LP — `start_message_bus(interval)`

| ID | Case | Test function |
|---|---|---|
| LP-01 | Returns the `LoopingCall`, so a consumer can stop it | `LoopTest.test_returns_the_looping_call` |
| LP-02 | Calls `process_inbox` once per interval | `LoopTest.test_calls_process_inbox_once_per_interval` |
| LP-03 | Does not fire immediately on start | `LoopTest.test_does_not_fire_immediately_on_start` |
| LP-04 | Stopping the returned loop stops the polling | `LoopTest.test_stopping_the_loop_stops_the_polling` |
| LP-05 | An exception inside one cycle does not stop the loop — the next interval still fires | `LoopTest.test_an_exception_in_one_cycle_does_not_stop_the_loop` |
| LP-06 | Refuses to start when the instance ID is unset, as a backstop to the boot check | `LoopTest.test_refuses_to_start_without_an_instance_id` |
| LP-07 | Refuses to start when the bus table does not exist, naming the migrate command | `LoopTest.test_refuses_to_start_against_an_unmigrated_database` |

LP-05 is why handler exceptions are caught rather than propagated: an error escaping into the
`LoopingCall` stops it, and the bus then goes quietly dead while the game looks healthy. LP-07 turns
an unmigrated bus database from an error logged twice a second into one refusal at startup.

## TX — transaction nesting

The consumer writes the nesting at its own call site, so these cases assert that the pattern works
against this library's alias — not that the library enforces it. The rule, from `design/database.md`
§ "Work that spans two databases" in the umbrella: **once a block opens the next one, it does no
further work.** All of the outer block's work happens before the inner block opens, so a failure
anywhere unwinds the whole chain.

| ID | Case | Test function |
|---|---|---|
| TX-01 | A bus write that fails inside the nested block rolls back the outer game-database work | `TransactionNestingTest.test_failing_bus_write_rolls_back_the_outer_game_work` |
| TX-02 | Both blocks succeeding leaves both durable | `TransactionNestingTest.test_both_blocks_succeeding_leaves_both_durable` |

The residual is a crash, or a commit itself failing, in the window between the inner commit and the
outer one. There is no two-phase commit, so it cannot be closed — only accepted, which is the same
position `design/database.md` records for the XRPL alias. For a message bus the loss is one message
during a crash. Where the choice is free, the bus write goes in the outer block, so the residual is a
missed message rather than an invented one.

## XC — cross-cutting

| ID | Case | Test function |
|---|---|---|
| XC-01 | A consumer-defined type round-trips: declared, registered, sent by one instance, polled, dispatched, deleted | `CrossCuttingTest.test_a_consumer_type_round_trips_end_to_end` |
| XC-02 | Two instance IDs in one process — a message from A to B is visible to B's poll and invisible to A's | `CrossCuttingTest.test_a_message_is_visible_only_to_its_addressee` |
| XC-03 | The payload reaches the handler unchanged — the bus does not inspect, normalise or rewrite it | `CrossCuttingTest.test_payload_reaches_the_handler_unchanged` |
| XC-04 | The library imports nothing from `evennia.objects` or `evennia.accounts` — asserted statically over the source tree | `CrossCuttingTest.test_library_imports_no_evennia_object_or_account` |
| XC-05 | The package exposes `__version__` | `TestPackageInstalls.test_version_is_exposed` |
| XC-06 | Django loads the library as an installed app | `TestPackageInstalls.test_registered_in_installed_apps` |
| XC-07 | The library's own `AppConfig` is the one Django resolves | `TestPackageInstalls.test_app_config_is_loaded` |

XC-03 and XC-04 guard the identity principle: the bus routes on `to_instance` and `kind`, and
everything inside the payload is the consumer's.

## Open decisions

- **TX residual** — accepted as a known risk; whether a crash between commits is worth simulating in a
  test at all is unresolved, and probably not.

Settled and reflected above: the bus is a Django model on a second database alias with its own router
(RT); one class per message type owning kind, timeout, payload contract, self-addressing policy, send
and handle (MT); sending to your own instance ID raises by default, opt-out per type via
`can_address_self`, with the raise naming a duplicate instance ID as the likely cause (SD-10..SD-13); an
explicit registry where a duplicate kind raises but a subclass replaces (RG); the library owns every
write to the row, so `delete` never appears in consumer code (PI); a kind nobody handles is rejected
immediately rather than waited out (PI-05, PI-06); the timeout lives on the class, not in settings
(MT-02, PI-11); handler exceptions are caught, logged and deferred rather than propagating (PI-07,
LP-05); the row's timestamp comes from the database server (MR-01); the library logs to its own file
(LG); one poller per instance ID, documented rather than enforced with row locking; peers are named by
a source-controlled constant, so there is no discovery surface to test; and no shipped message type
resolves a game object (LB-06, XC-03, XC-04).
