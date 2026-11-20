"""Cached metadata and error reporting use fake handles/completions only."""
import unittest
from unittest import mock

import test_experimental_hid as fixtures
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from halo.native_io import Completion


class CachedReportMetadataTests(unittest.TestCase):
    def setUp(self):
        self.api = fixtures.Api()
        self.runtime = TransportRuntime(api=self.api, timeout=.5)
        self.device = HidDevice(self.runtime, metadata=fixtures.Metadata(ReportCaps(0, 0, 65)))
        self.addCleanup(lambda: self.assertTrue(self.runtime.close(.5)))

    def test_metadata_requires_open_and_returns_a_copy_without_native_work(self):
        with self.assertRaises(ValueError):
            self.device.get_report_lengths()
        self.device.open_path(b'fake-feature-collection')
        with mock.patch.object(self.runtime, 'invoke', side_effect=AssertionError('metadata queued native work')):
            result = self.device.get_report_lengths()
            self.assertEqual(result, {'input': 0, 'output': 0, 'feature': 65})
            result['feature'] = 1
            self.assertEqual(self.device.get_report_lengths()['feature'], 65)
        self.device.close()
        with self.assertRaises(ValueError):
            self.device.get_report_lengths()

    def test_feature_failure_preserves_errno_and_reports_request_context(self):
        self.device.open_path(b'fake-feature-collection')
        def failed_ioctl(*args, **kwargs):
            request = fixtures.Request(b'')
            request.completion = Completion(False, 87, 0, b'', False)
            return request
        with mock.patch.object(self.api, 'create_ioctl', side_effect=failed_ioctl), self.runtime.provider_scope('wlmouse') as scope:
            with self.assertRaises(OSError) as caught:
                self.device.get_feature_report(0, 64)
        self.assertEqual(caught.exception.errno, 87)
        self.assertIn('report=0, buffer=64, collection=65', str(caught.exception))
        self.assertTrue(scope.failed)
        self.assertIn('buffer=64', scope.error)
