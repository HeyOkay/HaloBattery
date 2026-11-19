"""Cancelled generations cannot briefly publish before their replacements finish."""
import threading
import time
import unittest

from halo.polling import PollingRuntime
from providers.base import DeviceStatus, Provider


class PhaseProvider(Provider):
    name = "generation_fake"

    def __init__(self):
        self.calls = 0
        self.alias_calls = 0
        self.first_entered, self.second_entered = threading.Event(), threading.Event()
        self.first_release, self.second_release = threading.Event(), threading.Event()

    def poll(self):
        self.calls += 1
        if self.calls == 1:
            self.first_entered.set()
            if not self.first_release.wait(1):
                raise RuntimeError("first fake poll gate timed out")
            level = 50
        else:
            self.second_entered.set()
            if not self.second_release.wait(1):
                raise RuntimeError("second fake poll gate timed out")
            level = 75
        return [DeviceStatus("generation:key", "Fake device", level, source=self.name)]

    def legacy_key_aliases(self):
        self.alias_calls += 1
        return {"legacy:key": "generation:key"}


class GenerationPublicationTests(unittest.TestCase):
    def test_superseded_poll_never_publishes_between_reenable_and_replacement(self):
        provider = PhaseProvider()
        runtime = PollingRuntime([provider], lambda full: None)
        try:
            runtime.configure(set())
            runtime.request_poll()
            self.assertTrue(provider.first_entered.wait(1))
            runtime.configure({provider.name})
            self.assertEqual(runtime.consume().hid, [])
            runtime.configure(set())
            provider.first_release.set()
            # The second call can enter only after the old publication path has
            # completed. Hold its result so 75% cannot hide a transient 50%.
            self.assertTrue(provider.second_entered.wait(1))
            between = runtime.consume()
            self.assertEqual(between.hid, [])
            self.assertEqual(between.fresh_keys, set())
            self.assertEqual(between.completed, set())
            self.assertEqual(between.aliases, {})
            self.assertEqual(provider.alias_calls, 0)

            provider.second_release.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                runtime.pump()
                stats = runtime.stats()
                if not stats["running"] and not stats["queued"]:
                    break
                time.sleep(.001)
            else:
                self.fail("replacement fake poll did not complete")
            current = runtime.consume()
            self.assertEqual([status.level for status in current.hid], [75])
            self.assertEqual(current.fresh_keys, {"generation:key"})
            self.assertEqual(current.completed, {provider.name})
            self.assertEqual(current.aliases, {"legacy:key": "generation:key"})
            self.assertEqual(provider.calls, 2)
            self.assertEqual(provider.alias_calls, 1)
            again = runtime.consume()
            self.assertEqual([status.level for status in again.hid], [75])
            self.assertFalse(again.fresh_keys)
            self.assertFalse(again.completed)
            self.assertFalse(again.aliases)
        finally:
            provider.first_release.set()
            provider.second_release.set()
            self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
