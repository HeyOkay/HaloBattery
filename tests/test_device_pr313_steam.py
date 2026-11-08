"""Tests for providers/steam.py. No hardware is needed.

The tests replace the HID layer with a fake puck that sends the reports SDL's
Steam Triton driver describes: 0x43 with the charge state and the level, 0x79 /
0x46 for connect and disconnect, and 0x42 state reports. A fake clock replaces
time, so a listen window runs without real waiting.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import steam as S  # noqa: E402

PUCK = 0x1304        # Proteus dongle, as in issue #58
NEREID = 0x1305


def path_for(pid, iface):
    return (f"\\\\?\\hid#vid_28de&pid_{pid:04x}&mi_{iface:02x}"
            f"#7&8f0a1b2c&0&0000#{{4d1e55b2-f16f-11cf-88cb-001111000030}}").encode()


def battery(state, level, pad=46):
    return [0x43, state, level] + [0] * pad


def wireless(state):
    return [0x79, state] + [0] * 60


def state_report():
    return [0x42] + [0] * 63


# ------------------------------------------------------------------ fakes
class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakePuck:
    """frames: what one listen returns, in order, then nothing."""

    def __init__(self, frames=()):
        self.queue = [list(f) for f in frames]
        self.writes = []
        self.opened = 0

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, pucks, clock=None):
        self.pucks = pucks          # {path: FakePuck}
        self.clock = clock          # set = a frame read advances it (a ~250 Hz stream)

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.pucks:
                    raise OSError("cannot open")
                self.puck = bus.pucks[path]
                self.puck.opened += 1

            def set_nonblocking(self, on):
                pass

            def write(self, data):
                self.puck.writes.append(list(data))
                return len(data)

            def read(self, n):
                data = self.puck.on_read()
                if data and bus.clock is not None:
                    bus.clock.now += 0.004
                return data

            def close(self):
                pass

        return FakeDevice


def entry(pid, iface, usage_page=0xFF00, usage=0x0001):
    # the shape of the issue #58 diagnostics for the puck's collections
    return {"product_id": pid, "interface_number": iface, "usage_page": usage_page,
            "usage": usage, "path": path_for(pid, iface),
            "product_string": "Steam Controller Puck", "serial_number": "3d78b3a8716f4e02"}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (S.hid, S.hidlist, S.time)
        self.clock = Clock()
        S.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = S.SteamProvider()

    def tearDown(self):
        S.hid, S.hidlist, S.time = self._saved

    def poll(self, entries, pucks, advance=False):
        bus = FakeBus(pucks, clock=self.clock if advance else None)
        S.hid = types.SimpleNamespace(device=bus.device_class())
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()


# ------------------------------------------------------------------ parse


# ------------------------------------------------------------------ poll
class PollTest(ProviderTest):


    def test_the_read_stops_at_the_battery_report(self):
        # the state stream never idles: without a stop the loop ran the whole
        # window, holding the poll thread that every device shares
        class Endless(FakePuck):
            reads = 0

            def on_read(self):
                self.reads += 1
                return super().on_read() or state_report()

        puck = Endless([state_report()] * 3 + [battery(1, 64)])
        start = self.clock.now
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck}, advance=True)
        self.assertEqual([(r.level, r.charging, r.online) for r in res],
                         [(64, False, True)])
        self.assertEqual(puck.reads, 4)
        self.assertLess(self.clock.now - start, S.WINDOW)


if __name__ == "__main__":
    unittest.main()
