"""Real factory admission through App startup, without polling any device."""
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
import unittest
from unittest import mock

from test_hide_rename import hb
from halo.polling import PollingRuntime
from halo import hid_transport


class FactoryStartupTests(unittest.TestCase):
    def test_real_application_factory_starts_native_coordinator_with_two_workers(self):
        providers = hb.make_providers()
        app = hb.App.__new__(hb.App)
        app.providers = providers
        app.wake = SimpleNamespace(set=mock.Mock(), bluetooth=mock.Mock())
        app.usb_signature = mock.Mock(side_effect=AssertionError("no discovery at startup"))
        transport = SimpleNamespace(install=mock.Mock(),
            provider_scope=lambda owner: nullcontext(None),
            transport_idle=lambda owner: True, cancel_owner=lambda owner: None)
        with ExitStack() as stack:
            for provider in providers:
                stack.enter_context(mock.patch.object(provider, "poll",
                    side_effect=AssertionError("startup must not poll real devices")))
            stack.enter_context(mock.patch.object(hb, "native_transport_enabled", return_value=True))
            stack.enter_context(mock.patch.object(hb, "hid", SimpleNamespace()))
            stack.enter_context(mock.patch.object(hid_transport, "TransportRuntime", return_value=transport))
            app.start_native_transport()
            runtime = app._polling_runtime
            try:
                self.assertEqual(len(runtime._provider_order), len(providers))
                self.assertEqual({id(runtime._slots[key].provider) for key in runtime._provider_order},
                                 {id(provider) for provider in providers})
                self.assertEqual(runtime.stats()["workers"], 2)
                self.assertEqual(runtime.stats()["running"], 0)
                self.assertEqual(runtime.stats()["queued"], 0)
                transport.install.assert_called_once_with(hb.hid)
            finally:
                self.assertTrue(runtime.close(.5))

    def test_capacity_is_bounded_and_duplicate_instances_are_refused(self):
        providers = [SimpleNamespace(name=f"fake-{i}") for i in range(128)]
        runtime = PollingRuntime(providers, lambda full: None)
        try:
            self.assertEqual(len(runtime._provider_order), 128)
            self.assertEqual(runtime.stats()["workers"], 2)
        finally:
            self.assertTrue(runtime.close(.5))
        with self.assertRaises(ValueError):
            PollingRuntime(providers + [SimpleNamespace(name="excess")], lambda full: None)
        with self.assertRaises(ValueError):
            PollingRuntime([providers[0], providers[0]], lambda full: None)
