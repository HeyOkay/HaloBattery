"""PA target authorization: fake HID collections only, no device access.

0550's collection metadata is from the original diagnostics attached to #332.
Its later whole-build confirmation does not identify a successful packet or
charging transition. Automatic writes are held until an exact exchange is sourced.
"""
import unittest
from unittest.mock import Mock, patch

from providers import blackshark, razer


def collection(pid, path=b"vendor", **changes):
    info = dict(vendor_id=0x1532, product_id=pid, serial_number="serial", path=path,
                product_string="Razer Barracuda X 2.4", interface_number=3,
                usage_page=0xFF00, usage=0x0001)
    info.update(changes)
    return info


class PaTargetAuthorizationTest(unittest.TestCase):
    def test_unknown_headset_names_do_not_authorize_pa_after_generic_failure(self):
        for name in ("Razer Barracuda Future", "Razer BlackShark Future"):
            with self.subTest(name=name):
                info = collection(0x0FFF, product_string=name)
                provider = razer.RazerProvider()
                with patch.object(razer.hidlist, "enumerate", return_value=[info]), \
                        patch.object(provider, "_poll_group", return_value=None) as generic, \
                        patch.object(provider, "_poll_pa") as pa:
                    self.assertEqual(provider.poll(), [])
                generic.assert_called_once()
                pa.assert_not_called()

    def test_direct_unknown_or_missing_pid_never_opens_pa(self):
        # Even an authorized-looking collection cannot override the group PID.
        for key in ((0x0FFF, "serial"), ("label-only",), ()):
            with self.subTest(key=key):
                provider = razer.RazerProvider()
                with patch.object(blackshark, "read_battery") as read, \
                        patch.object(blackshark.hid, "device") as opened:
                    self.assertIsNone(provider._poll_pa(key, [collection(0x0555)]))
                read.assert_not_called()
                opened.assert_not_called()

    def test_0550_rejects_collections_outside_reported_contract(self):
        wrong = (
            {"usage_page": 0x000C}, {"usage_page": 0xFF14}, {"usage": 0x0002},
            {"interface_number": 2}, {"interface_number": None},
            {"vendor_id": 0x9999}, {"product_id": 0x0555},
        )
        for changes in wrong:
            with self.subTest(changes=changes):
                provider = razer.RazerProvider()
                with patch.object(blackshark, "read_battery") as read, \
                        patch.object(blackshark.hid, "device") as opened:
                    self.assertIsNone(provider._poll_pa(
                        (0x0550, "serial"), [collection(0x0550, **changes)]))
                read.assert_not_called()
                opened.assert_not_called()


    def test_no_matching_vendor_collection_never_falls_back_to_standard_collections(self):
        for pid in (0x0550, 0x0555, 0x0556, 0x0557, 0x0583):
            with self.subTest(pid=pid):
                provider = razer.RazerProvider()
                infos = [collection(pid, usage_page=0x000C),
                         collection(pid, b"keyboard", usage_page=0x0001, usage=6)]
                with patch.object(blackshark, "read_battery") as read, \
                        patch.object(blackshark.hid, "device") as opened:
                    self.assertIsNone(provider._poll_pa((pid, "serial"), infos))
                read.assert_not_called()
                opened.assert_not_called()


    def test_primary_pa_models_keep_pa_first_and_generic_fallback_only_for_none(self):
        for pid in (0x0555, 0x0556, 0x0583):
            for pa_result, order in (
                    ((razer.STATUS_OK, 60, False), ["pa"]),
                    ((razer.STATUS_TIMEOUT, None, None), ["pa"]),
                    (None, ["pa", "generic"])):
                with self.subTest(pid=pid, pa_result=pa_result):
                    provider = razer.RazerProvider()
                    calls = Mock()
                    with patch.object(razer.hidlist, "enumerate", return_value=[collection(pid)]), \
                            patch.object(provider, "_poll_pa", return_value=pa_result) as pa, \
                            patch.object(provider, "_poll_group",
                                         return_value=(razer.STATUS_OK, 55, False)) as generic:
                        calls.attach_mock(pa, "pa")
                        calls.attach_mock(generic, "generic")
                        provider.poll()
                    self.assertEqual([call[0] for call in calls.mock_calls], order)

    def test_existing_0557_keeps_generic_first_then_pa(self):
        for pid in (0x0557,):
            for generic_result, order, level in (
                    ((razer.STATUS_OK, 55, False), ["generic"], 55),
                    (None, ["generic", "pa"], 60),
                    ((razer.STATUS_TIMEOUT, None, None), ["generic", "pa"], 60)):
                with self.subTest(pid=pid, generic_result=generic_result):
                    provider = razer.RazerProvider()
                    calls = Mock()
                    with patch.object(razer.hidlist, "enumerate", return_value=[collection(pid)]), \
                            patch.object(provider, "_poll_group", return_value=generic_result) as generic, \
                            patch.object(provider, "_poll_pa",
                                         return_value=(razer.STATUS_OK, 60, False)) as pa:
                        calls.attach_mock(generic, "generic")
                        calls.attach_mock(pa, "pa")
                        self.assertEqual(provider.poll()[0].level, level)
                    self.assertEqual([call[0] for call in calls.mock_calls], order)


    def test_0552_stays_with_macronix_provider_without_generic_or_pa_queries(self):
        provider = razer.RazerProvider()
        with patch.object(razer.hidlist, "enumerate", return_value=[collection(0x0552)]), \
                patch.object(provider, "_poll_group") as generic, \
                patch.object(provider, "_poll_pa") as pa:
            self.assertEqual(provider.poll(), [])
        generic.assert_not_called()
        pa.assert_not_called()

    def test_existing_pa_models_keep_their_vendor_page_candidates(self):
        for pid in (0x0555, 0x0556, 0x0557, 0x0583):
            for page in (0xFF00, 0xFF14):
                with self.subTest(pid=pid, page=page):
                    provider = razer.RazerProvider()
                    with patch.object(blackshark, "read_battery",
                                      return_value=("ok", 60, False)) as read:
                        self.assertEqual(provider._poll_pa(
                            (pid, "serial"), [collection(pid, usage_page=page)]),
                            (razer.STATUS_OK, 60, False))
                    read.assert_called_once()

    def test_0550_never_opens_hid_or_replays_cache_even_on_reported_collection(self):
        provider = razer.RazerProvider()
        key = (0x0550, "serial")
        provider._cache[("pa", *key)] = razer._Cand(b"old-pa", 0)
        provider._last["razer:0550:serial"] = (60, True, 1)
        with patch.object(razer.hidlist, "enumerate", return_value=[collection(0x0550)]), \
                patch.object(provider, "_poll_group") as generic, \
                patch.object(provider, "_poll_pa") as pa, \
                patch.object(razer.hid, "device") as opened:
            self.assertEqual(provider.poll(), [])
        generic.assert_not_called()
        pa.assert_not_called()
        opened.assert_not_called()
        self.assertNotIn(("pa", *key), provider._cache)



if __name__ == "__main__":
    unittest.main()
