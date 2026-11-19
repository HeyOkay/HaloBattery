"""Multiple same-PID Keychron receivers keep independent stable endpoint IDs."""
import unittest
from unittest import mock

from providers import keychron as K
from test_keychron import FakeDevice, reply


def endpoint(path, serial="", pid=0xd028):
    return dict(path=path, serial_number=serial, product_id=pid, vendor_id=0x3434,
                interface_number=1 if pid == 0xd026 else 4, usage_page=0xffc1, usage=1)


class MultiReceiverTests(unittest.TestCase):
    def setUp(self):
        self.infos, self.responses, self.opened, self.instances = [], {}, [], []
        self.provider = K.KeychronProvider()
        test = self
        class Device(FakeDevice):
            def __init__(self):
                super().__init__([], feature_result=-1)
                test.instances.append(self)
            def open_path(self, path):
                test.opened.append(path)
                value = test.responses.get(path, [])
                if isinstance(value, Exception):
                    raise value
                self.replies = list(value)
                super().open_path(path)
        for patch in (mock.patch.object(K.hid, "device", Device),
                      mock.patch.object(K.hidlist, "enumerate", lambda vid: self.infos),
                      mock.patch.object(K.time, "sleep", lambda seconds: None)):
            patch.start()
            self.addCleanup(patch.stop)

    def states(self, provider=None):
        return {s.key: (s.level, s.charging) for s in (provider or self.provider).poll()}

    def test_two_same_pid_receivers_both_read_their_own_levels(self):
        self.infos = [endpoint(b"receiver-a"), endpoint(b"receiver-b")]
        self.responses = {b"receiver-a": [reply(22)], b"receiver-b": [reply(81, True)]}
        state = self.states()
        self.assertEqual(len(state), 2)
        self.assertEqual(set(state.values()), {(22, False), (81, True)})
        self.assertEqual(self.opened, [b"receiver-a", b"receiver-b"])
        self.assertEqual(self.provider.legacy_key_aliases(), {})
        for instance in self.instances:
            self.assertEqual(instance.written, [bytes([0xb3, 6] + [0] * 62)])
            self.assertTrue(instance.closed)

    def test_failed_first_receiver_does_not_hide_second_or_create_ambiguous_alias(self):
        self.infos = [endpoint(b"receiver-a"), endpoint(b"receiver-b")]
        self.responses = {b"receiver-a": OSError("unavailable"), b"receiver-b": [reply(64)]}
        state = self.states()
        self.assertEqual(list(state.values()), [(64, False)])
        # Both physical candidates exist even though only one answers.
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_order_hotplug_and_restart_do_not_reassign_duplicate_serials(self):
        self.infos = [endpoint(b"receiver-a", "DUPLICATE"), endpoint(b"receiver-b", "DUPLICATE")]
        self.responses = {b"receiver-a": [reply(22)], b"receiver-b": [reply(81)]}
        both = self.states()
        self.infos.reverse()
        self.assertEqual(self.states(), both)
        self.infos = [endpoint(b"receiver-b", "DUPLICATE")]
        remaining = self.states()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining, {k:v for k,v in both.items() if v[0] == 81})
        self.assertEqual(self.states(K.KeychronProvider()), remaining)
        self.assertEqual(self.provider.legacy_key_aliases(), {"keychron:d028": next(iter(remaining))})

    def test_singleton_has_cached_unambiguous_migration_alias(self):
        self.infos = [endpoint(b"receiver-a")]
        self.responses = {b"receiver-a": [reply(100)]}
        state = self.states()
        key = next(iter(state))
        self.assertTrue(key.startswith("keychron:d028:"))
        self.assertNotEqual(key, "keychron:d028")
        self.assertEqual(self.provider.legacy_key_aliases(), {"keychron:d028": key})
        alias = self.provider.legacy_key_aliases()
        alias.clear()
        self.assertEqual(self.provider.legacy_key_aliases(), {"keychron:d028": key})

    def test_same_path_case_or_duplicate_descriptors_are_one_candidate(self):
        self.infos = [endpoint(b"RECEIVER-A"), endpoint(b"receiver-a"), endpoint(b"RECEIVER-A")]
        self.responses = {b"RECEIVER-A": [reply(55)]}
        state = self.states()
        self.assertEqual(len(state), 1)
        self.assertEqual(self.opened, [b"RECEIVER-A"])
        self.assertEqual(len(self.provider.legacy_key_aliases()), 1)

    def test_wrong_collections_do_not_make_singleton_alias_ambiguous(self):
        wrong = endpoint(b"keyboard-page")
        wrong.update(usage_page=1, usage=6)
        self.infos = [wrong, endpoint(b"receiver-a")]
        self.responses = {b"receiver-a": [reply(41)]}
        self.assertEqual(len(self.states()), 1)
        self.assertEqual(self.opened, [b"receiver-a"])
        self.assertEqual(len(self.provider.legacy_key_aliases()), 1)
