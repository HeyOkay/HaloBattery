"""PRO X3 SUPERSTRIKE HID++ collection regression tests (no hardware required).

Run at the repository root:
    python -m unittest discover -s tests
"""
from test_logitech import LogitechTestCase, FakeHidpp, entry, L


class ProX3SuperstrikeTests(LogitechTestCase):
    def device(self):
        return FakeHidpp(
            {L.F_UNIFIED: (6, {1: [73, 0, 0]}), L.F_NAME: (3, {2: [3]})},
            name="PRO X3 SUPERSTRIKE",
        )

    def test_c54f_lightspeed_receiver(self):
        entries = [
            entry(0xC54F, 0xFF43, 0x0301, b"short", "LIGHTSPEED"),
            entry(0xC54F, 0xFF43, 0x0302, b"long", "LIGHTSPEED"),
        ]
        bus = self.use(entries, {b"long": ({1: self.device()}, b"short")})
        results = L.LogitechProvider().poll()
        self.assertEqual(len(results), 1)
        self.assertEqual((results[0].name, results[0].level),
                         ("PRO X3 SUPERSTRIKE", 73))
        self.assertTrue(any(path == b"long" and packet[1] == 1
                            for path, packet in bus.writes))

    def test_c54d_keeps_legacy_routing(self):
        infos = [entry(0xC54D, 0xFF43, 0x0302, b"alternate")]
        self.assertEqual(L.LogitechProvider._collections(infos), {})
        infos.append(entry(0xC54D, 0xFF00, 2, b"legacy"))
        self.assertEqual(list(L.LogitechProvider._collections(infos).values()),
                         [{2: b"legacy"}])

    def test_charging_and_full_status(self):
        for status in (1, 3):
            with self.subTest(status=status):
                mouse = self.device()
                mouse.features[L.F_UNIFIED] = (6, {1: [73, 0, status]})
                infos = [entry(0xC54F, 0xFF43, 0x0302, b"long")]
                self.use(infos, {b"long": ({1: mouse}, None)})
                [state] = L.LogitechProvider().poll()
                self.assertEqual(state.level, 73)
                self.assertTrue(state.charging)

    def test_c0a9_wired_mouse(self):
        entries = [entry(0xC0A9, 0xFF43, 0x0302, b"long", "PRO X3")]
        self.use(entries, {b"long": ({0xFF: self.device()}, None)})
        results = L.LogitechProvider().poll()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, 73)

    def test_unknown_pid_must_not_claim_ff43(self):
        entries = [entry(0xC539, 0xFF43, 0x0302, b"long", "LIGHTSPEED")]
        self.use(entries, {b"long": ({1: self.device()}, None)})
        self.assertEqual(L.LogitechProvider().poll(), [])

    def test_existing_ff00_collection_takes_priority(self):
        entries = [
            entry(0xC54F, 0xFF00, 2, b"ordinary", "LIGHTSPEED"),
            entry(0xC54F, 0xFF43, 0x0302, b"pro-x3", "LIGHTSPEED"),
        ]
        groups = L.LogitechProvider._collections(entries)
        self.assertEqual(next(iter(groups.values()))[2], b"ordinary")

    def test_ff00_priority_in_both_enumeration_orders(self):
        entries = [
            entry(0xC54F, 0xFF00, 1, b"short"),
            entry(0xC54F, 0xFF00, 2, b"long"),
            entry(0xC54F, 0xFF43, 0x0301, b"new-short"),
            entry(0xC54F, 0xFF43, 0x0302, b"new-long"),
        ]
        for ordered in (entries, list(reversed(entries))):
            with self.subTest(order=ordered):
                groups = L.LogitechProvider._collections(ordered)
                self.assertEqual(list(groups.values()), [{1: b"short", 2: b"long"}])

    def test_short_only_collection_sends_no_requests(self):
        entries = [entry(0xC54F, 0xFF43, 0x0301, b"short")]
        bus = self.use(entries, {})
        self.assertEqual(L.LogitechProvider().poll(), [])
        self.assertEqual(bus.writes, [])

    def test_other_vendor_usages_are_not_claimed(self):
        for usage in (0x0202, 0x0300, 0x0303, None):
            with self.subTest(usage=usage):
                entries = [entry(0xC54F, 0xFF43, usage, b"unrelated")]
                self.assertEqual(L.LogitechProvider._collections(entries), {})
