"""Custom glyph integration: unchanged ring states, offline restoration and cancellable work."""
from dataclasses import replace
import io
import json
from pathlib import Path
import queue
import threading
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

from PIL import Image, ImageDraw
from halo import icons
from halo.iconify_host import IconCatalogJobs, attribution, candidate_preview, validate_request
from halo.settings_host import SettingsHost, _Session
from halo.settings_model import SettingsController


class Glyph:
    def render(self, size, fg, alpha=255, foreground_opacity=230/255):
        image = Image.new("RGBA", (size, size))
        ImageDraw.Draw(image).ellipse((size*.35, size*.3, size*.65, size*.7), fill=fg+(round(alpha*foreground_opacity),))
        return image


def request(action="search", id=1, **kw):
    return dict(type="catalog", protocol=1, id=id, action=action, deviceKey="mouse", query="mouse",
                start=0, size=20, iconId=None) | kw


class CustomRingTests(unittest.TestCase):
    def test_centre_replacement_preserves_every_outer_ring_pixel(self):
        glyph = Glyph()
        for size in (16, 20, 24, 32, 80):
            for light in (False, True):
                for level, charging, online, paused in ((None, False, False, False), (0, False, True, False),
                        (5, False, True, False), (100, False, True, False), (35, True, True, False),
                        (None, True, True, False), (None, False, True, True)):
                    for phase in (0, .37):
                        args = dict(size=size, light_taskbar=light, animation_phase=phase, force_active=paused)
                        normal = icons.render(level, charging, online, badge="", **args)
                        custom = icons.render(level, charging, online, custom_glyph=glyph, **args)
                        for y in range(size):
                            for x in range(size):
                                if (x+.5-size/2)**2 + (y+.5-size/2)**2 >= (.415*size)**2:
                                    self.assertEqual(custom.getpixel((x,y)), normal.getpixel((x,y)))
                        self.assertNotEqual(custom.tobytes(), normal.tobytes())

    def test_percentage_has_priority_and_charging_uses_same_frames(self):
        glyph = Glyph()
        self.assertEqual(icons.render(23, False, True, text="23", custom_glyph=glyph).tobytes(),
                         icons.render(23, False, True, text="23").tobytes())
        for level in (None, 40):
            frames = icons.charging_frames(level, True, size=16, custom_glyph=glyph)
            self.assertEqual(len(frames), icons.BREATH_FRAMES)
            for i in (0, 11, 89):
                expected = icons.render(level, True, True, pulse=icons.breath_level(i/icons.BREATH_FRAMES),
                    animation_phase=i/icons.BREATH_FRAMES if level is None else 0., size=16, custom_glyph=glyph)
                self.assertEqual(frames[i].tobytes(), expected.tobytes())

    def test_candidate_uses_real_state_and_forces_only_centre_visibility(self):
        state = (15, False, True, 20, True, "mouse", False, "15", False, 20, "default", False, False)
        result = candidate_preview(state, Glyph(), 20)
        expected = icons.render(15, False, True, 20, True, size=20, custom_glyph=Glyph())
        self.assertEqual(result.tobytes(), expected.tobytes())


class CatalogJobTests(unittest.TestCase):
    def setUp(self):
        self.responses = queue.Queue()
        self.model = Mock()
        self.service = Mock()
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.service.root = Path(temp.name)
        self.service._registry_key = "test-registry"
        self.model.custom_icon_in_use.return_value = False
        self.model.icon_catalog.return_value = self.service
        self.model.catalog_render_state.return_value = ((42, False, True, 20, False, "mouse", False, "", False, 20, "default", False, False), 20)
        self.asset = SimpleNamespace(id="a"*64, icon_id="lucide:mouse", glyph=Glyph(), metadata={"icon_id":"lucide:mouse"})
        self.service.prepare.return_value = self.asset
        self.service.preview_glyph.return_value = self.asset.glyph
        self.worker = IconCatalogJobs(self.model, self.responses.put, Mock(), lambda: True)
        def close_jobs():
            self.worker.close()
            self.worker._cleanup_done.wait(3)
        self.addCleanup(close_jobs)

    def test_schema_rejects_wrong_types_and_unbounded_inputs(self):
        validate_request(request())
        for kw in ({"id":True}, {"protocol":True}, {"size":True}, {"size":193}, {"start":-1},
                   {"query":"x"*101}, {"deviceKey":[]}, {"iconId":[]}, {"action":"bad"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): validate_request(request() | kw)
        with self.assertRaises(ValueError): validate_request(request("details"))
        validate_request(request("licenses", deviceKey=None))

    def test_network_work_is_cancellable_and_queued_search_is_latest_only(self):
        entered, release = threading.Event(), threading.Event()
        def search(query, **kw):
            entered.set(); release.wait(2)
            return dict(items=[], has_more=False)
        self.service.search.side_effect = search
        self.worker.submit(request())
        self.assertTrue(entered.wait(1))
        self.worker.submit(request(id=2, query="obsolete"))
        self.worker.submit(request(id=3, query="headset"))
        release.set()
        response = self.responses.get(timeout=2)
        self.assertEqual(response["id"], 3)
        self.assertEqual(response["query"], "headset")
        self.assertTrue(self.responses.empty())
        self.assertEqual(self.service.search.call_count, 2)

    def test_details_and_apply_and_restore_preserve_binding(self):
        self.worker.submit(request("details", iconId="lucide:mouse"))
        response = self.responses.get(timeout=2)
        self.assertEqual(response["detail"]["actualSize"], 20)
        self.assertIn("preview80", response["detail"])
        self.service.persist_selection.assert_not_called()
        self.worker.submit(request("apply", id=2, iconId="lucide:mouse"))
        self.assertTrue(self.responses.get(timeout=2)["applied"])
        self.service.persist_selection.assert_called_once_with(self.asset, cancel=unittest.mock.ANY)
        self.model.apply_custom_icon.assert_called_with("mouse", self.asset, revision=self.model.begin_custom_selection.return_value)
        self.worker.submit(request("restore", id=3))
        self.assertTrue(self.responses.get(timeout=2)["applied"])
        self.model.apply_custom_icon.assert_called_with("mouse", None, revision=self.model.begin_custom_selection.return_value)
        with self.assertRaises(ValueError): self.worker.submit(request(id=2))

    def test_cached_search_does_not_prefetch_and_unusable_icons_are_omitted(self):
        from halo.iconify_catalog import CatalogError
        self.service.search.return_value = dict(items=[{"id":"lucide:mouse"}, {"id":"lucide:bad"}],
                                                 has_more=True, next_start=50, offline=True, cached=True)
        self.service.preview_glyph.side_effect = [self.asset.glyph, CatalogError("Not cached", "offline")]
        self.worker.submit(request())
        response = self.responses.get(timeout=2)
        self.assertEqual(len(response["items"]), 1)
        self.assertEqual(response["nextStart"], 50)
        self.assertTrue(response["offline"])
        self.service.prefetch_sources.assert_not_called()
        self.service.prepare.assert_not_called()

    def test_notice_text_is_preserved_without_escaped_dict_representation(self):
        row = attribution({"icon_id":"codicon:mouse", "author":{"name":"Microsoft"},
                           "license":{"title":"CC BY 4.0", "url":"https://example.com"},
                           "notices":[{"name":"NOTICE", "text":"First\nSecond"}],
                           "modifications":"Monochrome"})
        self.assertEqual(row["notices"], "NOTICE\nFirst\nSecond\n\nMonochrome")
        self.assertEqual(row["author"], "Microsoft")

    def test_catalogue_dispatch_does_not_take_host_model_lock(self):
        host = SettingsHost(self.model)
        session = _Session("devices", "mouse"); host._session = session; session.ready.set()
        session.icon_jobs = Mock()
        with patch.object(host, "_model_lock", MagicMock()) as model_lock:
            host._handle(session, request())
            model_lock.__enter__.assert_not_called()
        session.icon_jobs.submit.assert_called_once()


# Import the existing app fixture after pure tests: it safely redirects APPDATA.
from test_hide_rename import HideRenameTestCase, dev, hb, make_app
REAL_DEVICE_ICON = hb.DeviceIcon


class CustomSelectionTests(HideRenameTestCase):
    def setup_model(self):
        app = make_app({"custom_icons":{}}); app.bt_cache=[]; app.hid_results=[]
        app.apply([dev()]); model=SettingsController(app)
        model._catalog=Mock()
        asset=SimpleNamespace(id="a"*64,icon_id="lucide:mouse",glyph=Glyph())
        return app,model,asset

    def test_apply_restore_and_standard_pictogram_keep_device_logic(self):
        app, model, asset = self.setup_model(); key=dev().key
        self.assertTrue(model.apply_custom_icon(key,asset))
        self.assertEqual(app.cfg["custom_icons"][key],asset.id)
        self.assertEqual(app.pictogram(dev()),"mouse")
        self.assertEqual(app.icon_render_state(dev())[-1],asset.id)
        self.assertEqual(model.snapshot()["devices"][0]["custom_icon_name"],"lucide:mouse")
        app.cfg["percent_in_icon"]=True
        self.assertEqual(len(app.icon_render_state(dev())),13)
        app.cfg["percent_in_icon"]=False; app.cfg["badges"]=False
        self.assertEqual(len(app.icon_render_state(dev())),13)
        app.cfg["badges"]=True
        model.set_device(key,"pictogram","speaker")
        self.assertNotIn(key,app.cfg["custom_icons"])
        model.apply_custom_icon(key,asset); self.assertTrue(model.apply_custom_icon(key,None))
        self.assertNotIn(key,app.cfg["custom_icons"])
        self.assertFalse(model.apply_custom_icon("gone",asset))

    def test_loaded_assets_and_frame_cache_remain_offline(self):
        app, model, asset = self.setup_model(); key=dev().key
        app.cfg["custom_icons"]={key:asset.id}; model._catalog.load.return_value=asset
        model.load_custom_icons(); model._catalog.load.assert_called_once_with(asset.id)
        owner=REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON);owner.app=app;owner._images={}
        first=owner._art(app.icon_render_state(dev(),size=80))
        self.assertEqual(first.tobytes(),model.preview(key).tobytes())
        self.assertIs(first,owner._art(app.icon_render_state(dev(),size=80)))
        model._catalog.search.assert_not_called();model._catalog.prepare.assert_not_called()
        model._catalog.load.return_value=None; model.load_custom_icons()
        self.assertEqual(len(app.icon_render_state(dev())),13)

    def test_custom_config_values_require_safe_asset_hashes(self):
        self.assertTrue(hb._valid_setting("custom_icons",{"mouse":"a"*64}))
        for value in ([], True, "foo", {"mouse":"../../x"}, {"mouse":True}, {1:"a"*64}, {"mouse":"A"*64}):
            with self.subTest(value=value):self.assertFalse(hb._valid_setting("custom_icons",value))

    def test_stale_apply_cannot_overwrite_newer_selection_and_assets_are_pruned(self):
        app, model, asset = self.setup_model(); key=dev().key
        old = model.begin_custom_selection(key)
        model.set_device(key, "pictogram", "speaker")
        self.assertFalse(model.apply_custom_icon(key, asset, revision=old))
        self.assertEqual(app.cfg["custom_icons"], {})
        model.apply_custom_icon(key, asset)
        newer=SimpleNamespace(id="b"*64, icon_id="lucide:speaker", glyph=Glyph())
        model.apply_custom_icon(key,newer)
        self.assertEqual(set(app._custom_assets), {newer.id})
        model.apply_custom_icon(key,None)
        self.assertEqual(app._custom_assets,{})

    def test_pin_failure_does_not_apply_and_readonly_startup_still_displays(self):
        from halo.iconify_catalog import CatalogError
        app, model, asset = self.setup_model(); key=dev().key
        model._catalog.pin.side_effect=CatalogError("Read only", "cache_error")
        with self.assertRaises(CatalogError): model.apply_custom_icon(key,asset)
        self.assertEqual(app.cfg["custom_icons"],{})
        app.cfg["custom_icons"]={key:asset.id};model._catalog.load.return_value=asset
        with self.assertLogs("halo_battery",level="WARNING"): model.load_custom_icons()
        self.assertIn(asset.id,app._custom_assets)

    def test_custom_mapping_does_not_mutate_global_defaults(self):
        app=make_app()
        self.assertIsNot(app._settings_map("custom_icons"),hb.DEFAULTS["custom_icons"])
        app._settings_map("custom_icons")["mouse"]="a"*64
        self.assertEqual(hb.DEFAULTS["custom_icons"],{})
