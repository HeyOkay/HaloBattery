"""Provider failures must reach the application's configured logger."""
import time
import unittest

from halo.polling import PollingRuntime


class PollingLoggingTests(unittest.TestCase):
    def test_native_failure_reaches_app_logger(self):
        class Provider:
            name = 'wlmouse'
            def poll(self):
                raise OSError(87, 'native I/O failed')
        runtime = PollingRuntime([Provider()], lambda full: None)
        self.addCleanup(lambda: runtime.close(.5))
        with self.assertLogs('halo_battery', level='WARNING') as captured:
            runtime.request_poll()
            deadline = time.monotonic() + 2.
            while time.monotonic() < deadline and runtime._slots['wlmouse:0'].failures != 1:
                time.sleep(.002)
            self.assertEqual(runtime._slots['wlmouse:0'].failures, 1)
        self.assertTrue(any('wlmouse:0' in line and '87' in line for line in captured.output))
