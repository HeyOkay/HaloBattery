"""Mock-only PRO X3 SUPERSTRIKE coverage; hardware validation is still required."""
from test_logitech import L, FakeHidpp, LogitechTestCase, entry


class PROX3Tests(LogitechTestCase):
    @staticmethod
    def _mouse(charging_status=0):
        return FakeHidpp({L.F_UNIFIED: (6, {1: [73, 0, charging_status]}),
                          L.F_NAME: (3, {2: [3]})}, name="PRO X3 SUPERSTRIKE")

    def test_c54f_receiver_on_ff43(self):
        infos = [entry(0xC54F, 0xFF43, 0x0301, b"short", "LIGHTSPEED"),
                 entry(0xC54F, 0xFF43, 0x0302, b"long", "LIGHTSPEED")]
        self.use(infos, {b"long": ({1: self._mouse()}, b"short")})
        [state] = L.LogitechProvider().poll()
        self.assertEqual((state.name, state.level, state.kind),
                         ("PRO X3 SUPERSTRIKE", 73, "mouse"))
        self.assertTrue(any(path == b"long" and packet[1] == 1
                            for path, packet in self.bus.writes))

    def test_c54f_charging_on_ff43(self):
        infos = [entry(0xC54F, 0xFF43, 0x0302, b"long", "LIGHTSPEED")]
        self.use(infos, {b"long": ({1: self._mouse(charging_status=1)}, None)})
        [state] = L.LogitechProvider().poll()
        self.assertEqual(state.level, 73)
        self.assertTrue(state.charging)

    def test_c54f_charge_complete_on_ff43(self):
        infos = [entry(0xC54F, 0xFF43, 0x0302, b"long", "LIGHTSPEED")]
        self.use(infos, {b"long": ({1: self._mouse(charging_status=3)}, None)})
        [state] = L.LogitechProvider().poll()
        self.assertEqual(state.level, 73)
        self.assertTrue(state.charging)

    def test_c0a9_wired_mouse(self):
        infos = [entry(0xC0A9, 0xFF43, 0x0302, b"wired", "PRO X3")]
        self.use(infos, {b"wired": ({0xFF: self._mouse()}, None)})
        [state] = L.LogitechProvider().poll()
        self.assertEqual((state.level, state.charging), (73, False))

    def test_unknown_pid_does_not_use_ff43(self):
        infos = [entry(0xC539, 0xFF43, 0x0302, b"long", "LIGHTSPEED")]
        self.use(infos, {b"long": ({1: self._mouse()}, None)})
        self.assertEqual(L.LogitechProvider().poll(), [])
        self.assertEqual(self.bus.writes, [])

    def test_prefers_ff00_when_both_long_collections_exist(self):
        infos = [entry(0xC54F, 0xFF00, 2, b"original", "LIGHTSPEED"),
                 entry(0xC54F, 0xFF43, 0x0302, b"alternate", "LIGHTSPEED")]
        paths = list(L.LogitechProvider._collections(infos).values())
        self.assertEqual(paths[0][2], b"original")

    def test_mixed_short_ff00_long_ff43(self):
        infos = [entry(0xC54F, 0xFF00, 1, b"old_short", "LIGHTSPEED"),
                 entry(0xC54F, 0xFF43, 0x0302, b"new_long", "LIGHTSPEED")]
        self.use(infos, {b"new_long": ({1: self._mouse()}, b"old_short")})
        self.assertEqual(L.LogitechProvider().poll()[0].level, 73)
