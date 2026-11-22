"""Single acknowledged AJ159 exchange against fake native I/O only."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_experimental_hid as native
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from providers import ajazz as A
from halo.native_io import Completion

OWNER_70 = bytes.fromhex("05 00 00 46 01 00 01 02") + bytes(57)

class AJ159NativeApi(native.Api):
    def __init__(self):
        super().__init__()
        self.report, self.report_count = OWNER_70, 65
        self.error = None

    def create_ioctl(self, owner, code, **kwargs):
        if kwargs["output_size"] != 65 or kwargs["input_data"] != b"\x05" + bytes(64):
            raise AssertionError("unexpected report ID or deliberately invalid feature length")
        if isinstance(self.error, TimeoutError):
            raise self.error
        if self.error:
            self.ioctls.append((code, kwargs))
            request = native.Request(bytes(65))
            request.completion = Completion(False, 87, 0, b"", False)
            return request
        return super().create_ioctl(owner, code, **kwargs)

    def create_read(self, owner, size):
        raise AssertionError("feature-only AJ159 must not read input")

    def create_write(self, owner, data):
        raise AssertionError("AJ179 output query must not reach AJ159")


class Aj159DiagnosticTests(unittest.TestCase):
    def test_strict_status_boundaries_and_no_unrelated_opcode(self):
        for level in (1, 70, 100):
            self.assertEqual(A.parse_aj159_diagnostic([5, 0, 0, level]), level)
        for data in (None, [], [5, 0, 0], [5, 0, 0, 0], [5, 0, 0, 101],
                     [5, 0, 0, True], [0x99, 0, 0, 70], [5, 1, 0, 70]):
            self.assertIsNone(A.parse_aj159_diagnostic(data))

    def test_caps_sized_single_exchange_and_no_normal_reader_calls(self):
        from unittest.mock import Mock
        info = dict(vendor_id=0x3151, product_id=0x402D, interface_number=2,
                    usage_page=0xFFFF, usage=2, path=b"test-only")
        for size in (65, 1041):
            dev = Mock()
            dev.get_report_lengths.return_value = {"feature": size}
            dev.send_feature_report.return_value = size
            dev.get_feature_report.return_value = OWNER_70
            reader = A.AjazzProvider()
            with patch.object(A, "hid", SimpleNamespace(device=lambda: dev)), patch.object(A.time, "sleep"):
                self.assertEqual(reader._diagnose_aj159(info), (70, True))
            dev.send_feature_report.assert_called_once_with(b"\0\xf7" + bytes(size-2))
            dev.get_feature_report.assert_called_once_with(5, size)
            dev.read.assert_not_called()
            dev.write.assert_not_called()
            dev.close.assert_called_once()

    def test_invalid_caps_partial_write_and_disappearance_refuse(self):
        from unittest.mock import Mock
        info = dict(vendor_id=0x3151, product_id=0x402D, interface_number=2,
                    usage_page=0xFFFF, usage=2, path=b"test-only")
        for size, count in ((None, 65), (True, 65), (3, 3), (32769, 32769), (65, 64)):
            dev = Mock()
            dev.get_report_lengths.return_value = {"feature": size}
            dev.send_feature_report.return_value = count
            with patch.object(A, "hid", SimpleNamespace(device=lambda: dev)):
                self.assertEqual(A.AjazzProvider()._diagnose_aj159(info), (None, False))
            dev.get_feature_report.assert_not_called()
            dev.close.assert_called_once()
        dev = Mock()
        dev.open_path.side_effect = OSError("disconnected")
        with patch.object(A, "hid", SimpleNamespace(device=lambda: dev)):
            self.assertEqual(A.AjazzProvider()._diagnose_aj159(info), (None, False))
        dev.close.assert_called_once()
