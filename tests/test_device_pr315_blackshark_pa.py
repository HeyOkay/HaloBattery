"""Tests for the PA-protocol cache fix, item 4 of the review in #62.

A "PA" headset (BlackShark V2 Pro 2023, Barracuda) that is switched off must cost one probe
per poll instead of one per vendor collection - and the collection that gets remembered must
be the one that actually speaks the protocol. `read_battery` cannot say which of the two
"no reading" situations it is in unless it distinguishes them: an interface that opened but
never accepted a command (which is also what a wrong collection looks like) must not be
cached, only an interface that accepted a command with the headset staying silent.

No hardware: PASession is replaced by a stub whose write/query behaviour is scripted.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import blackshark, razer  # noqa: E402


class StubSession:
    """One collection: whether it ever accepts a command, and what the battery query answers."""

    script = {}          # path -> (accepts, answer)

    def __init__(self, path, diag):
        if path == b"unopenable":
            raise OSError("cannot open")
        self.path, self.diag = path, diag
        self.accepts, self.answer = self.script.get(path, (True, None))

    def _write(self, frame):
        return self.accepts

    def query(self, cmd_id):
        return list(self.answer) if self.answer is not None else None

    def close(self):
        pass


def iface(path, page, usage=0x0001):
    return {"path": path, "interface_number": 3, "usage_page": page, "usage": usage,
            "product_id": 0x0555, "vendor_id": 0x1532}


class BarracudaProReceiverTest(unittest.TestCase):
    """Receiver 1532:0583 (issue #217) is read with the PA frames, like 0555/0556."""

    def test_0583_is_a_pa_receiver_and_a_wireless_candidate(self):
        self.assertIn(0x0583, blackshark.PA_PIDS)
        # #256: the Barracuda X (2022) publishes ff00:0001 on its receiver, so it is
        # routed through the same PA path and named from the table
        self.assertIn(0x0536, blackshark.PA_PIDS)
        self.assertEqual("Razer Barracuda X (2022)", razer.KNOWN[0x0536][0])
        self.assertTrue(razer.maybe_wireless(0x0583, "Razer Barracuda Pro 2.4"))

    def test_0583_is_polled_with_pa_frames_under_its_product_name(self):
        info = dict(product_id=0x0583, vendor_id=0x1532, serial_number="", path=b"p",
                    product_string="Razer Barracuda Pro 2.4", interface_number=3,
                    usage_page=0xFF00, usage=0x0001)
        p = razer.RazerProvider()
        with patch("providers.razer.hidlist.enumerate", return_value=[info]), \
                patch.object(p, "_poll_pa", return_value=(razer.STATUS_OK, 94, False)) as pa, \
                patch.object(p, "_poll_group") as generic:
            out = p.poll()
        self.assertEqual([(d.name, d.level) for d in out], [("Razer Barracuda Pro 2.4", 94)])
        pa.assert_called_once()
        generic.assert_not_called()

    def test_the_older_barracuda_provider_does_not_claim_0583(self):
        from providers import barracuda
        self.assertNotEqual(barracuda.PID, 0x0583)


class BarracudaXReceiverTest(unittest.TestCase):
    """Receiver 1532:0550 (#332, #264): the mouse request fails, so the PA frames are tried."""

    def info(self):
        return dict(product_id=0x0550, vendor_id=0x1532, serial_number="", path=b"p",
                    product_string="Razer Barracuda X 2.4", interface_number=3,
                    usage_page=0xFF00, usage=0x0001)

    def test_falls_back_to_pa_when_the_mouse_request_fails(self):
        p = razer.RazerProvider()
        with patch("providers.razer.hidlist.enumerate", return_value=[self.info()]),                 patch.object(p, "_poll_group", return_value=None),                 patch.object(p, "_poll_pa", return_value=(razer.STATUS_OK, 80, True)) as pa:
            out = p.poll()
        self.assertEqual([(d.name, d.level, d.charging) for d in out],
                         [("Razer Barracuda X 2.4", 80, True)])
        pa.assert_called_once()

    def test_a_working_mouse_style_reply_is_kept(self):
        p = razer.RazerProvider()
        with patch("providers.razer.hidlist.enumerate", return_value=[self.info()]),                 patch.object(p, "_poll_group", return_value=(razer.STATUS_OK, 55, False)),                 patch.object(p, "_poll_pa", return_value=None):
            out = p.poll()
        self.assertEqual(out[0].level, 55)


if __name__ == "__main__":
    unittest.main()
