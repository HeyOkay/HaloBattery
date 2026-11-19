"""Selected-cache migration races and ring-style settings without devices/network."""
from dataclasses import replace
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from test_hide_rename import HideRenameTestCase,dev,hb,make_app
from halo.settings_model import SettingsController
from halo import icons,svg_glyph,iconify_catalog

REAL_DEVICE_ICON=hb.DeviceIcon
REAL_SAVE_CONFIG=hb.save_config


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

    def legacy_catalog(self,app,model):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        model.context['data_dir']=temporary.name
        source=iconify_catalog.canonical_icon({'icons':{'mouse':{'body':'<path d="M0 0h10v10z"/>'}}},'mouse')
        entry={'name':'Test','author':{'name':'Artist'},'license':{'spdx':'MIT','title':'MIT'},
               'license_text':'Copyright Artist. Permission to use.','notices':[],
               'provenance':{},'icons':{'mouse':iconify_catalog._hash(source)}}
        registry={'schema':1,'collections':{'test':entry}}
        catalog=iconify_catalog.IconifyCatalog(temporary.name,registry=registry,
            fetch=lambda url:self.fail('startup must remain offline'))
        # A prior-format master has no evidence checksum in its configured ID.
        image=Image.new('L',(1024,1024));image.paste(255,(504,504,520,520))
        stream=io.BytesIO();image.save(stream,'PNG');png=stream.getvalue()
        version=svg_glyph.PREVIOUS_RENDERER_VERSION
        asset_id=iconify_catalog._hash([iconify_catalog.ASSET_REVISION,'test:mouse',entry['icons']['mouse'],version])
        metadata=dict(asset_id=asset_id,icon_id='test:mouse',source_hash=entry['icons']['mouse'],
            renderer_revision=version,collection=entry['name'],author=entry['author'],license=entry['license'],
            license_text=entry['license_text'],notices=[],provenance={},png_sha256=hashlib.sha256(png).hexdigest())
        folder=catalog.root/'assets'/asset_id;folder.mkdir(parents=True)
        (folder/'metadata.json').write_bytes(iconify_catalog._json_bytes(metadata))
        (folder/'glyph.png').write_bytes(png)
        (folder/'source.json').write_bytes(iconify_catalog._json_bytes(source))
        model._catalog=catalog;app.cfg['custom_icons']={dev().key:asset_id}
        return catalog,asset_id,folder,png,source

    def test_startup_seals_verified_legacy_evidence_and_preserves_original_pixels(self):
        app,model=self.setup_model();catalog,old_id,folder,png,source=self.legacy_catalog(app,model)
        model.load_custom_icons()
        selected=app.cfg['custom_icons'][dev().key]
        self.assertNotEqual(selected,old_id)
        self.assertEqual(set(app._custom_assets),{selected})
        self.assertEqual(app._custom_icon_errors,[])
        selected_folder=catalog.root/'assets'/selected
        self.assertEqual((selected_folder/'glyph.png').read_bytes(),png)
        self.assertEqual((selected_folder/'source.json').read_bytes(),iconify_catalog._json_bytes(source))
        changed=copy.deepcopy(catalog.registry);changed['collections']['test']['license_text']='Changed after update'
        restarted=iconify_catalog.IconifyCatalog(model.context['data_dir'],registry=changed,
            fetch=lambda url:self.fail('network'))
        self.assertIsNotNone(restarted.load(selected))
        self.assertIn(selected,restarted._pinned)

    def test_unverifiable_legacy_on_first_update_retains_binding_and_exposes_warning(self):
        app,model=self.setup_model();catalog,old_id,folder,png,source=self.legacy_catalog(app,model)
        catalog._collections['test']['license_text']='Changed before migration'
        with self.assertLogs('halo_battery',level='WARNING'):
            model.load_custom_icons()
        self.assertEqual(app.cfg['custom_icons'],{dev().key:old_id})
        self.assertEqual(app._custom_assets,{})
        self.assertEqual(app._custom_icon_errors,[old_id])
        self.assertEqual(model.snapshot()['custom_icon_errors'],[old_id])
        self.assertEqual((folder/'glyph.png').read_bytes(),png)
        model.apply_custom_icon(dev().key,None)
        self.assertEqual(model.snapshot()['custom_icon_errors'],[])

    def test_failed_legacy_sealing_keeps_valid_old_pixels_and_config(self):
        app,model=self.setup_model();catalog,old_id,folder,png,source=self.legacy_catalog(app,model)
        with patch.object(catalog,'_write_selection',side_effect=OSError('read only')):
            with self.assertLogs('halo_battery',level='WARNING'):
                model.load_custom_icons()
        self.assertEqual(app.cfg['custom_icons'],{dev().key:old_id})
        self.assertEqual(set(app._custom_assets),{old_id})
        self.assertEqual(app._custom_icon_errors,[])
        self.assertEqual((folder/'glyph.png').read_bytes(),png)

    def test_failed_config_migration_rolls_back_to_original_binding(self):
        app,model=self.setup_model();catalog,old_id,folder,png,source=self.legacy_catalog(app,model)
        model.context['save_config']=Mock(side_effect=OSError('read only config'))
        with self.assertLogs('halo_battery',level='WARNING'):
            model.load_custom_icons()
        self.assertEqual(app.cfg['custom_icons'],{dev().key:old_id})
        self.assertEqual(set(app._custom_assets),{old_id})
        self.assertTrue(folder.is_dir())

    def test_real_config_replace_failure_preserves_disk_binding_and_original_master(self):
        app,model=self.setup_model();catalog,old_id,folder,png,source=self.legacy_catalog(app,model)
        config_path=Path(model.context['data_dir'])/'config.json'
        model.context['save_config']=REAL_SAVE_CONFIG
        original_replace=hb.os.replace
        def fail_config_replace(source_path,target_path):
            if str(target_path)==str(config_path):
                raise OSError('read only configuration')
            return original_replace(source_path,target_path)
        with patch.object(hb,'CONFIG_PATH',str(config_path)):
            REAL_SAVE_CONFIG(app.cfg)
            original_config=config_path.read_bytes()
            with patch.object(hb.os,'replace',side_effect=fail_config_replace):
                with self.assertLogs('halo_battery',level='WARNING'):
                    model.load_custom_icons()
        self.assertEqual(config_path.read_bytes(),original_config)
        self.assertEqual(json.loads(config_path.read_text(encoding='utf-8'))['custom_icons'],{dev().key:old_id})
        self.assertEqual(app.cfg['custom_icons'],{dev().key:old_id})
        self.assertEqual(set(app._custom_assets),{old_id})
        self.assertEqual((folder/'glyph.png').read_bytes(),png)
        self.assertEqual((folder/'source.json').read_bytes(),iconify_catalog._json_bytes(source))
        self.assertIsNotNone(catalog.load(old_id))
        self.assertFalse(Path(str(config_path)+'.tmp').exists())

    def test_unavailable_registry_reports_all_saved_selections_without_resetting(self):
        app,model=self.setup_model();selected='a'*64
        app.cfg['custom_icons']={dev().key:selected}
        with patch.object(model,'icon_catalog',side_effect=ValueError('invalid registry')):
            with self.assertLogs('halo_battery',level='WARNING'):
                model.load_custom_icons()
        self.assertEqual(app.cfg['custom_icons'],{dev().key:selected})
        self.assertEqual(app._custom_icon_errors,[selected])

    def test_cache_upgrade_preserves_choice_and_replaces_only_pixels(self):
        app,model=self.setup_model();old,new=self.assets(app,model)
        self.assertTrue(model.adopt_custom_icon_upgrade(new))
        self.assertEqual(app.cfg['custom_icons'],{dev().key:new.id})
        self.assertEqual(app.pictogram(dev()),'mouse')
        self.assertEqual(set(app._custom_assets),{new.id})
        self.assertFalse(model.adopt_custom_icon_upgrade(new))

    def test_reapplying_repaired_same_id_clears_its_saved_icon_warning(self):
        app,model=self.setup_model();old,new=self.assets(app,model)
        app._custom_assets={};app._custom_icon_errors=[old.id]
        self.assertTrue(model.apply_custom_icon(dev().key,old))
        self.assertEqual(app.cfg['custom_icons'],{dev().key:old.id})
        self.assertEqual(set(app._custom_assets),{old.id})
        self.assertEqual(model.snapshot()['custom_icon_errors'],[])

    def test_repaired_selection_does_not_clear_another_failed_device_id(self):
        app,model=self.setup_model();old,new=self.assets(app,model)
        other_key='offline-other-device'
        app.cfg['custom_icons'][other_key]=new.id
        app._custom_assets={};app._custom_icon_errors=[old.id,new.id]
        self.assertTrue(model.apply_custom_icon(dev().key,old))
        self.assertEqual(app.cfg['custom_icons'],{dev().key:old.id,other_key:new.id})
        self.assertEqual(set(app._custom_assets),{old.id})
        self.assertEqual(model.snapshot()['custom_icon_errors'],[new.id])

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
        model._catalog.prepare.assert_called_once_with(old.icon_id,cancel=None,offline=True)
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

    def test_phone_identity_survives_tray_customization_and_unknown_charge(self):
        phone=hb.DeviceStatus('phone_link:11111111-1111-4111-8111-111111111111',
            'Linked tablet',46,False,True,'phone_link',kind='phone',charging_known=False)
        app=make_app({'low':50});app.hid_results=[phone];app.bt_cache=[];app.tray_size=20
        app.apply([phone]);model=SettingsController(app)
        self.assertIn(phone.key,app.icons)
        self.assertEqual(app.notes,[],'unknown charging must not invent a low-battery transition')
        self.assertEqual(hb.badge_for(phone),'phone')
        self.assertIn('phone',dict(hb.PICTOGRAM_CHOICES))
        model.set_device(phone.key,'pictogram','speaker')
        item=model.snapshot()['devices'][0]
        self.assertEqual((item['kind'],item['pictogram']),('phone','speaker'))

    def test_unavailable_phone_keeps_unknown_icon_without_old_battery_arc(self):
        phone=hb.DeviceStatus('phone_link:11111111-1111-4111-8111-111111111111',
            'Linked tablet',None,False,False,'phone_link',kind='phone',charging_known=False)
        app=make_app();app.hid_results=[phone];app.bt_cache=[];app.tray_size=20
        app.apply([phone]);model=SettingsController(app)
        item=model.snapshot()['devices'][0]
        self.assertIsNone(item['status'].level)
        self.assertFalse(item['status'].online)
        self.assertEqual(item['kind'],'phone')
        self.assertEqual(app.notes,[])
        self.assertNotIn(phone.key,app.history.devices)

    def test_last_reported_phone_keeps_ring_and_percent_without_online_or_charge_claim(self):
        from halo import i18n
        phone=hb.DeviceStatus('phone_link:11111111-1111-4111-8111-111111111111',
            'Linked phone',46,False,False,'phone_link',kind='phone',charging_known=False,
            ui_message='state.phone_link_last_reported',ui_params={'level':46})
        app=make_app();app.hid_results=[phone];app.bt_cache=[];app.tray_size=20
        app.apply([phone]);model=SettingsController(app)
        for language in i18n.LANGUAGES:
            self.assertEqual(hb.device_state(phone,language=language),
                i18n.translate('state.phone_link_last_reported',language=language,level=46))
        self.assertFalse(model.snapshot()['devices'][0]['status'].online)
        for style in ('thin','classic'):
            for light in (False,True):
                app.cfg.update(ring_style=style,percent_in_icon=False);app.light_taskbar=light
                expected=icons.render(46,False,True,light_taskbar=light,badge='phone',size=80,ring_style=style)
                self.assertEqual(model.preview(phone.key).tobytes(),expected.tobytes())
                app.cfg['percent_in_icon']=True
                self.assertEqual(app.icon_render_state(phone)[7],'46')
                self.assertFalse(app.icon_render_state(phone)[6], 'cached charging must not animate')
        for bad in (replace(phone,source='bluetooth'),replace(phone,level=True),
                    replace(phone,level=None),replace(phone,level=101)):
            self.assertFalse(app.icon_render_state(bad)[2], 'only a valid retained Phone Link level keeps its arc')

    def test_retained_phone_cannot_trigger_alerts_or_extend_history_use(self):
        cached=lambda level:hb.DeviceStatus('phone_link:11111111-1111-4111-8111-111111111111',
            'Linked phone',level,False,False,'phone_link',kind='phone',charging_known=False,
            ui_message='state.phone_link_last_reported',ui_params={'level':level})
        app=make_app({'low':50,'low_sound':True,'full_alert':True});app.bt_cache=[];app.tray_size=20
        with patch.object(hb,'play_low_sound') as sound:
            for level in (5,100):
                status=cached(level);app.hid_results=[status]
                app.full_state[status.key]='charging';app.apply([status])
                self.assertNotIn(status.key,app.full_state)
                self.assertEqual(app.notes,[])
                self.assertEqual(app.history.devices[status.key]['samples'],[])
                self.assertEqual(app.history.devices[status.key]['use'],0)
            sound.assert_not_called()
