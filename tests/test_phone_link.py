"""Synthetic Phone Link cache tests; never read linked-device/private data."""
import json
import math
from pathlib import Path
import shutil
import unittest
from unittest import mock

import _sandbox
from providers import phone_link as P

_sandbox.install_guards()

ONE = "11111111-1111-4111-8111-111111111111"
TWO = "22222222-2222-4222-8222-222222222222"
NOW = 1800000000.0


def identity(name="Tablet", ident=ONE, os_name="Android", linked=True):
    return {"IsLinked": linked, "Metadata": {"Id": ident, "Metadata": {
        "DisplayName": name, "OsName": os_name, "ModelName": "test-model"}}}


def metadata(*entries):
    return {"DeviceMetadatas": {"shared-account-not-a-device": list(entries)}}


def card(level=46, *, name="Tablet", charging=False):
    label = f"Battery {level}% remaining" + (", charging" if charging else "")
    columns = [{"type": "Column"} for _ in range(8)]
    columns[4]["items"] = [{"type": "Image", "tooltip": label}]
    columns[6]["items"] = [{"type": "Container", "tooltip": label}]
    return {"type": "AdaptiveCard", "speak": name + " - Connected to your PC",
            "body": [{} for _ in range(5)] + [{"type": "ColumnSet", "columns": columns},
                {"type": "Container", "text": "PRIVATE-NOTIFICATION Battery 99% remaining",
                 "url": "https://private.invalid/DO-NOT-READ"}]}


def snapshot(value=None, identities=None, modified=NOW):
    return P.CacheSnapshot(value if value is not None else card(),
                           identities if identities is not None else metadata(identity()), modified)


class PhoneLinkTests(unittest.TestCase):
    def poll(self, value):
        provider = P.PhoneLinkProvider(snapshot_reader=lambda: value, clock=lambda: NOW)
        return provider.poll(), provider

    def test_only_selected_second_entry_gets_its_per_device_key(self):
        values, _ = self.poll(snapshot(identities=metadata(identity("Phone", TWO), identity(),
                                                         identity("PC", os_name="Windows"))))
        self.assertEqual([(s.key, s.name, s.level, s.kind, s.online) for s in values],
                         [("phone_link:" + ONE, "Tablet", 46, "phone", True)])

    def test_zero_and_full_are_valid_without_inventing_charging(self):
        for level in (0, 46, 100):
            values, _ = self.poll(snapshot(card(level)))
            self.assertEqual(values[0].level, level)
            self.assertFalse(values[0].charging)
            self.assertFalse(values[0].charging_known)
        values, _ = self.poll(snapshot(card(100, charging=True)))
        self.assertTrue(values[0].charging)
        self.assertTrue(values[0].charging_known)

    def test_expired_connected46_is_unknown_offline_identity_not_a_percentage(self):
        for modified in (NOW - P.INITIAL_CACHE_MAX_AGE - 1, NOW - 32 * 86400, NOW + 6):
            values, provider = self.poll(snapshot(modified=modified))
            self.assertEqual(len(values), 1)
            value = values[0]
            self.assertEqual((value.key, value.level, value.online, value.charging_known),
                             ("phone_link:" + ONE, None, False, False))
            self.assertEqual(value.ui_message, "state.phone_link_stale")
            self.assertFalse(value.polling_stale)
            self.assertNotIn("46", value.approx)
            self.assertNotIn("PRIVATE", " ".join(provider.diagnostics()))

    def test_unchanged_file_retains_level_without_becoming_a_fresh_measurement(self):
        now = [NOW]
        provider = P.PhoneLinkProvider(snapshot_reader=lambda: snapshot(), clock=lambda: now[0])
        self.assertEqual(provider.poll()[0].level, 46)
        now[0] += 301
        for elapsed in (0, 4 * 3600, 24 * 3600):
            now[0] += elapsed
            status = provider.poll()[0]
            self.assertEqual(status.level, 46)
            self.assertFalse(status.online)
            self.assertFalse(status.charging)
            self.assertFalse(status.charging_known)
            self.assertFalse(status.polling_paused)
            self.assertEqual(status.ui_message, "state.phone_link_last_reported")
            self.assertEqual(status.approx, "")
        other = P.PhoneLinkProvider(snapshot_reader=lambda: snapshot(), clock=lambda: now[0])
        self.assertIsNone(other.poll()[0].level, "an old cache must not seed a new session")

    def test_initial_cache_age_boundaries_keep_recent_last_reported_level(self):
        for age, online, level in ((300, True, 46), (301, False, 46),
                                    (10800, False, 46), (10801, False, None)):
            with self.subTest(age=age):
                values, _ = self.poll(snapshot(modified=NOW-age))
                self.assertEqual((values[0].level, values[0].online), (level, online))

    def test_old_changed_signature_cannot_borrow_previous_admission(self):
        changes = [snapshot(card(12)), snapshot(card(charging=True)),
                   snapshot(modified=NOW+1),
                   snapshot(card(name="Phone"), metadata(identity("Phone", TWO)))]
        for changed in changes:
            now = [NOW]; current = [snapshot()]
            provider = P.PhoneLinkProvider(snapshot_reader=lambda: current[0], clock=lambda: now[0])
            self.assertEqual(provider.poll()[0].level, 46)
            now[0] += 4*3600; current[0] = changed
            self.assertIsNone(provider.poll()[0].level)
            current[0] = snapshot()
            self.assertIsNone(provider.poll()[0].level, "a rejected selection must clear prior admission")

    def test_missing_invalid_and_unlinked_cache_clear_retention(self):
        invalid = [None, snapshot(identities=metadata(identity(linked=False))),
                   snapshot(identities=metadata(identity(), identity(ident=TWO))),
                   snapshot(card("oops")), snapshot(modified=math.nan), snapshot(modified=NOW+6)]
        for value in invalid:
            now = [NOW]; current = [snapshot()]
            provider = P.PhoneLinkProvider(snapshot_reader=lambda: current[0], clock=lambda: now[0])
            provider.poll(); current[0] = value; provider.poll()
            current[0] = snapshot(); now[0] += 4*3600
            self.assertIsNone(provider.poll()[0].level)
        reader = mock.Mock(side_effect=[snapshot(), OSError("locked"), snapshot()])
        now = [NOW]; provider = P.PhoneLinkProvider(snapshot_reader=reader, clock=lambda: now[0])
        provider.poll(); provider.poll(); now[0] += 4*3600
        self.assertIsNone(provider.poll()[0].level)

    def test_selection_changes_never_transfer_level_or_key(self):
        current = [snapshot()]
        provider = P.PhoneLinkProvider(snapshot_reader=lambda: current[0], clock=lambda: NOW)
        first = provider.poll()[0]
        current[0] = snapshot(card(12, name="Phone"), metadata(identity("Phone", TWO)),
                              NOW - P.INITIAL_CACHE_MAX_AGE - 1)
        second = provider.poll()[0]
        self.assertNotEqual(first.key, second.key)
        self.assertEqual(second.name, "Phone")
        self.assertIsNone(second.level)
        current[0] = snapshot(card(12, name="Phone"), metadata(identity("Phone", TWO)))
        self.assertEqual(provider.poll()[0].level, 12)

    def test_name_change_preserves_uuid_key(self):
        a, _ = self.poll(snapshot())
        b, _ = self.poll(snapshot(card(name="Renamed"), metadata(identity("Renamed"))))
        self.assertEqual(a[0].key, b[0].key)

    def test_shared_or_invalid_identity_and_unlinked_pc_never_become_a_phone(self):
        cases = [metadata(identity(), identity(ident=TWO)), metadata(identity(os_name="Windows")),
                 metadata(identity(os_name=None)), metadata(identity(linked=1)),
                 metadata(identity(linked=False)), metadata(identity(ident="not-a-uuid")),
                 metadata(identity(ident="00000000-0000-0000-0000-000000000000")),
                 metadata(identity(name="Other")), {"DeviceMetadatas": []}]
        for identities in cases:
            with self.subTest(identities=identities):
                self.assertEqual(self.poll(snapshot(identities=identities))[0], [])

    def test_duplicate_identical_registration_is_one_identity(self):
        values, _ = self.poll(snapshot(identities=metadata(identity(), identity())))
        self.assertEqual(len(values), 1)

    def test_missing_bad_or_conflicting_battery_has_unknown_selected_identity(self):
        cases = [card(level) for level in (-1, 101, 255, True, 46.0, "oops")]
        bad = card(); bad["body"][5]["columns"][4]["items"][0]["type"] = "TextBlock"; cases.append(bad)
        bad = card(); bad["body"][5]["columns"][6]["items"][0]["tooltip"] = "Battery 12% remaining"; cases.append(bad)
        bad = card(); bad["body"][5]["columns"][4]["items"][0]["tooltip"] = "Batterie 46 % restante"; cases.append(bad)
        bad = card(); bad["body"] = []; cases.append(bad)
        for value in cases:
            with self.subTest(value=value):
                values, _ = self.poll(snapshot(value))
                self.assertIsNone(values[0].level)
                self.assertFalse(values[0].online)
                self.assertEqual(values[0].ui_message, "state.phone_link_unavailable")

    def test_unrecognized_connection_or_root_is_not_fuzzy_matched(self):
        for speak in ("Tablet - Disconnected", "Tablet - Not Connected to your PC", "Tablet", None, 4):
            value = card(); value["speak"] = speak
            self.assertEqual(self.poll(snapshot(value))[0], [])
        value = card(); value["type"] = "Other"
        self.assertEqual(self.poll(snapshot(value))[0], [])

    def test_notification_percentage_never_substitutes_for_status_row(self):
        value = card()
        for index in (4, 6):
            value["body"][5]["columns"][index]["items"][0]["tooltip"] = "Unavailable"
        value["body"][6]["text"] = "Battery 99% remaining"
        values, provider = self.poll(snapshot(value))
        self.assertIsNone(values[0].level)
        self.assertNotIn("99", " ".join(provider.diagnostics()))

    def test_invalid_time_never_accepts_a_level(self):
        for modified in (None, True, math.nan, math.inf, "today"):
            values, _ = self.poll(snapshot(modified=modified))
            self.assertIsNone(values[0].level)

    def test_exceptions_are_generic_and_sandbox_assertions_are_not_swallowed(self):
        for error in (OSError("PRIVATE-DATA"), ValueError("PRIVATE-DATA"), RecursionError("PRIVATE")):
            provider = P.PhoneLinkProvider(snapshot_reader=mock.Mock(side_effect=error))
            self.assertEqual(provider.poll(), [])
            self.assertNotIn("PRIVATE", " ".join(provider.diagnostics()))
        with self.assertRaisesRegex(AssertionError, "real Phone Link"):
            P.PhoneLinkProvider().poll()

    def test_construction_does_not_read_state_and_diagnostics_are_copied(self):
        reader = mock.Mock()
        provider = P.PhoneLinkProvider(snapshot_reader=reader)
        reader.assert_not_called()
        values, provider = self.poll(snapshot())
        self.assertEqual(values[0].source, "phone_link")
        provider.diagnostics().clear()
        self.assertTrue(provider.diagnostics())
        self.assertEqual(provider.legacy_key_aliases(), {})


class LocalReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(_sandbox.mkdtemp(prefix="phone-link-fixture-"))
        self.addCleanup(shutil.rmtree, self.root)
        for relative, value in ((P.CARD_PATH, card()), (P.IDENTITY_PATH, metadata(identity()))):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8-sig")

    def test_reader_only_uses_explicit_temporary_package_and_accepts_bom(self):
        result = P._read_snapshot(self.root)
        self.assertIsInstance(result, P.CacheSnapshot)
        self.assertEqual(P.battery_from_card(result.card), (46, None))
        self.assertEqual(P.selected_identity(result.card, result.metadata)[0], "phone_link:" + ONE)

    def test_missing_file_is_an_ordinary_unavailable_snapshot(self):
        (self.root / P.CARD_PATH).unlink()
        self.assertIsNone(P._read_snapshot(self.root))

    def test_invalid_duplicate_nonfinite_and_oversize_json_is_refused(self):
        for raw in (b"", b"{", b'{"type":"one","type":"two"}', b'{"x":NaN}',
                    b" " * (P.MAX_BYTES + 1), b"\xff"):
            (self.root / P.CARD_PATH).write_bytes(raw)
            with self.subTest(length=len(raw)), self.assertRaises((ValueError, UnicodeError)):
                P._read_snapshot(self.root)

    def test_structural_limits_do_not_depend_on_forbidden_content(self):
        value = {}
        for _ in range(26):
            value = {"x": value}
        (self.root / P.CARD_PATH).write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            P._read_snapshot(self.root)
        with self.assertRaises(ValueError):
            P._bounded_shape([0] * 12001)

    def test_stat_race_retries_at_most_once(self):
        with mock.patch.object(P, "_read_document", side_effect=OSError("producer replacement")) as reader:
            with self.assertRaises(OSError):
                P._read_snapshot(self.root)
        self.assertEqual(reader.call_count, 2)

    def test_sandbox_rejects_default_or_unrelated_explicit_paths(self):
        with self.assertRaises(AssertionError):
            P._read_snapshot()
        with self.assertRaises(AssertionError):
            P._read_snapshot(Path.home())


if __name__ == "__main__":
    unittest.main()
