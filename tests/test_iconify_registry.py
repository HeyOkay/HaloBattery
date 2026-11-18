"""Lazy full-hash registry indices and preserved offline selections."""
import copy,gzip,hashlib,io,json,tempfile
from pathlib import Path
import unittest
from PIL import Image,ImageDraw
from halo import iconify_catalog as c
from halo import svg_glyph


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'manifests').mkdir()
        self.index={'mouse':'a'*64}
        self.info=dict(name='Test',author={'name':'Artist'},license={'spdx':'MIT','title':'MIT','url':'https://example.com/LICENSE'})
        self.entry=dict(self.info,license_text='Copyright Artist. Permission to use.',icons_count=1,icons_manifest='test.json.gz')
        raw=gzip.compress(c._json_bytes(self.index),mtime=0)
        self.entry['icons_sha256']=hashlib.sha256(raw).hexdigest()
        (self.root/'manifests/test.json.gz').write_bytes(raw)
        self.registry={'schema':1,'collections':{'test':self.entry}}

    def make(self,registry=None):
        return c.IconifyCatalog(self.root/'data',registry=registry or self.registry,
            registry_dir=self.root,fetch=lambda url:self.fail('Unexpected network'))

    def test_indices_are_loaded_only_on_demand(self):
        service=self.make();self.assertEqual(service._indices,{})
        self.assertEqual(service.collections()[0]['total'],1)
        self.assertEqual(service._indices,{})
        entry=service._entry('test:mouse')[2]
        self.assertEqual(entry['icons'],self.index);self.assertEqual(list(service._indices),['test'])
        self.assertNotIn('icons',service.registry['collections']['test'])

    def test_corrupt_and_missing_manifests_fail_closed(self):
        for raw in (b'bad',b''):
            (self.root/'manifests/test.json.gz').write_bytes(raw)
            service=self.make()
            with self.assertRaises(c.CatalogError):service._entry('test:mouse')
        (self.root/'manifests/test.json.gz').unlink()
        service=self.make();self.assertEqual(service.collections()[0]['total'],1)
        with self.assertRaises(c.CatalogError):service._entry('test:mouse')

    def test_manifest_paths_counts_and_hashes_are_strict(self):
        for field,value in (('icons_manifest','../test.json.gz'),('icons_count',True),('icons_count',60001),('icons_sha256','no')):
            registry=copy.deepcopy(self.registry);registry['collections']['test'][field]=value
            with self.subTest(field=field),self.assertRaises(c.CatalogError):self.make(registry)
        registry=copy.deepcopy(self.registry);registry['collections']['test']['icons_count']=2
        with self.assertRaises(c.CatalogError):self.make(registry)._entry('test:mouse')

    def test_index_memory_is_bounded_by_recent_collections(self):
        registry=copy.deepcopy(self.registry)
        for i in range(10):
            prefix='set-'+str(i);entry=copy.deepcopy(self.entry);entry['icons_manifest']=prefix+'.json.gz'
            (self.root/'manifests'/entry['icons_manifest']).write_bytes((self.root/'manifests/test.json.gz').read_bytes())
            registry['collections'][prefix]=entry
        service=self.make(registry)
        for i in range(10):service._entry('set-'+str(i)+':mouse')
        self.assertEqual(len(service._indices),8);self.assertNotIn('set-0',service._indices)

    def test_actual_license_is_separate_from_api_metadata(self):
        entry=dict(self.entry,api_license=self.info['license'],license={'spdx':'CC0-1.0','title':'CC0'})
        self.assertTrue(c.IconifyCatalog._metadata_matches(entry,self.info))
        wrong=copy.deepcopy(self.info);wrong['license']['spdx']='GPL'
        self.assertFalse(c.IconifyCatalog._metadata_matches(entry,wrong))

    def test_v1_selected_png_migrates_without_network_or_source_loss(self):
        service=self.make();version=svg_glyph.LEGACY_RENDERER_VERSION
        image=Image.new('L',(1024,1024));ImageDraw.Draw(image).ellipse((112,112,911,911),fill=255)
        output=io.BytesIO();image.save(output,'PNG');png=output.getvalue()
        asset_id=c._hash([c.ASSET_REVISION,'test:mouse','a'*64,version])
        metadata=dict(asset_id=asset_id,icon_id='test:mouse',source_hash='a'*64,renderer_revision=version,
            collection='Test',author=self.entry['author'],license=self.entry['license'],license_text=self.entry['license_text'],
            notices=[],provenance={},png_sha256=hashlib.sha256(png).hexdigest())
        folder=service.root/'assets'/asset_id;folder.mkdir(parents=True)
        (folder/'metadata.json').write_bytes(c._json_bytes(metadata));(folder/'glyph.png').write_bytes(png)
        asset=service.load(asset_id);self.assertIsNotNone(asset)
        self.assertEqual(asset.id,asset_id);self.assertEqual(asset.metadata,metadata)
        self.assertNotEqual(asset.glyph.png_bytes,png)
        # Cached original source stays intact; v2 placement is an offline view.
        self.assertEqual((folder/'glyph.png').read_bytes(),png)
        self.assertIsNotNone(service.load(asset_id))
        self.assertIs(service.cached("test:mouse"),asset)
        self.assertTrue(service.needs_upgrade(asset))
