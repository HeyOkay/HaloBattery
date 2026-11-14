"""Selected-cache migration races and ring-style settings without devices/network."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from test_hide_rename import HideRenameTestCase,dev,hb,make_app
from halo.settings_model import SettingsController
from halo import icons,svg_glyph

REAL_DEVICE_ICON=hb.DeviceIcon


class PresentationSettingTests(HideRenameTestCase):
    def setup_model(self):
        app=make_app({'custom_icons':{}});app.hid_results=[dev()];app.bt_cache=[];app.tray_size=20
        app.apply([dev()]);model=SettingsController(app)
        return app,model

    def assets(self,app,model):
        old=SimpleNamespace(id='a'*64,icon_id='test:color',metadata={'source_hash':'c'*64,'renderer_revision':svg_glyph.PREVIOUS_RENDERER_VERSION},glyph=Mock())
        new=SimpleNamespace(id='b'*64,icon_id='test:color',metadata={'source_hash':'c'*64,'renderer_revision':svg_glyph.RENDERER_VERSION},glyph=Mock())
        app._custom_assets={old.id:old};app.cfg['custom_icons']={dev().key:old.id}
        model._catalog=Mock();model._catalog.needs_upgrade.side_effect=lambda a:a.metadata['renderer_revision']!=svg_glyph.RENDERER_VERSION
        return old,new

    def test_cache_upgrade_preserves_choice_and_replaces_only_pixels(self):
        app,model=self.setup_model();old,new=self.assets(app,model)
        self.assertTrue(model.adopt_custom_icon_upgrade(new))
        self.assertEqual(app.cfg['custom_icons'],{dev().key:new.id})
        self.assertEqual(app.pictogram(dev()),'mouse')
        self.assertEqual(set(app._custom_assets),{new.id})
        self.assertFalse(model.adopt_custom_icon_upgrade(new))

    def test_upgrade_cannot_replace_a_newer_user_selection(self):
        app,model=self.setup_model();old,new=self.assets(app,model)
        bindings={dev().key:(old.id,0)}
        model.begin_custom_selection(dev().key)
        self.assertFalse(model.adopt_custom_icon_upgrade(new,expected=bindings))
        self.assertEqual(app.cfg['custom_icons'][dev().key],old.id)
        model._catalog.pin.side_effect=lambda asset_id:app.cfg.update(custom_icons={})
        self.assertFalse(model.adopt_custom_icon_upgrade(new))
        self.assertEqual(app.cfg['custom_icons'],{})

    def test_offline_upgrade_retains_old_selection_and_start_is_once(self):
        app,model=self.setup_model();old,new=self.assets(app,model)
        model._catalog.prepare.return_value=old
        # Deterministic worker runs immediately, without a network/thread.
        with patch('halo.settings_model.threading.Thread',lambda target,**kw:SimpleNamespace(start=target)):
            model.schedule_custom_icon_upgrade();model.schedule_custom_icon_upgrade()
        model._catalog.prepare.assert_called_once_with(old.icon_id,cancel=None)
        self.assertEqual(app.cfg['custom_icons'][dev().key],old.id)

    def test_ring_style_is_validated_and_stored(self):
        app,model=self.setup_model()
        for bad in (None,True,1,[],{},'bad','Thin'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):model.set_global('ring_style',bad)
        model.set_global('ring_style','classic')
        state=app.icon_render_state(dev());self.assertEqual(state[-2:],(None,'classic'))
        self.assertEqual(len(state),15)
        owner=REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON);owner.app=app;owner._images={}
        actual=model.preview(dev().key);expected=owner._art(app.icon_render_state(dev(),size=80))
        self.assertEqual(actual.tobytes(),expected.tobytes())
        thin=icons.render(dev().level,False,True,light_taskbar=False,badge='mouse',size=80)
        self.assertNotEqual(actual.tobytes(),thin.tobytes())
        model.set_global('ring_style','thin')
        self.assertEqual(len(app.icon_render_state(dev())),13)

    def test_empty_placeholder_uses_selected_ring_immediately(self):
        app,model=self.setup_model();app.icons.clear();app.hid_results=[]
        app.placeholder=SimpleNamespace(icon=None)
        model.set_global('ring_style','classic')
        self.assertEqual(app.placeholder.icon.tobytes(),icons.render(None,False,False,light_taskbar=False,size=20,ring_style='classic').tobytes())
        self.assertEqual(model.preview().tobytes(),icons.render(None,False,False,light_taskbar=False,size=80,ring_style='classic').tobytes())

    def test_device_header_kind_uses_actual_status_not_tray_customization(self):
        app,model=self.setup_model();key=dev().key
        app.cfg.update(filled_icons=True,icons={key:'speaker'},controller_brands={key:'xbox'},controller_colors={key:True},custom_icons={key:'a'*64})
        app._custom_assets={'a'*64:SimpleNamespace(icon_id='test:custom-art')}
        item=model.snapshot()['devices'][0]
        self.assertEqual(item['kind'],'mouse')
        self.assertEqual(item['pictogram'],'speaker')
        app.icons[key].status=replace(dev(),kind='headset')
        self.assertEqual(model.snapshot()['devices'][0]['kind'],'headset')
        app.icons[key].status=replace(dev(),kind='gamepad')
        self.assertEqual(model.snapshot()['devices'][0]['kind'],'gamepad')
