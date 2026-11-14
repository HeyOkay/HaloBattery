"""50-item pages are pre-rendered, pushed for native decode, and window-scoped."""
import base64,io,queue,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
import unittest
from PIL import Image,ImageDraw
from halo.iconify_host import IconCatalogJobs,validate_request
from halo.iconify_catalog import CatalogCancelled


class SmallGlyph:
    def render(self,size,fg,alpha=255,foreground_opacity=230/255):
        image=Image.new('RGBA',(size,size));ImageDraw.Draw(image).ellipse((size*.35,size*.35,size*.65,size*.65),fill=fg+(round(alpha*foreground_opacity),));return image


class FakeCatalog:
    def __init__(self,root):
        self.root=Path(root);self._registry_key='f'*64
        self.searches=[];self.preview_calls=[];self.batch_calls=[];self.cleared=[]
        self.warm_enter=threading.Event();self.warm_release=threading.Event();self.warm_done=threading.Event()
        self.block=False
    def search(self,query,start=0,limit=50,cancel=None):
        self.searches.append((query,start))
        return dict(items=[dict(id=f'test:{query}-{i}',name=str(i),collection='Test',author='Author',license='MIT') for i in range(start,min(start+limit,150))],
                    total=150,has_more=start+limit<150,cached=True,offline=False)
    def prefetch_sources(self,names,cancel=None):
        self.batch_calls.append(tuple(names))
        if names and names[0]=='test:mouse-50':
            self.warm_enter.set()
            if self.block:
                while not self.warm_release.wait(.01):
                    if cancel.is_set():raise CatalogCancelled('cancel','invalid_request')
    def preview_glyph(self,name,size,cancel=None,offline=False):
        if cancel.is_set():raise CatalogCancelled('cancel','invalid_request')
        self.preview_calls.append(name)
        if name=='test:mouse-99':self.warm_done.set()
        return SmallGlyph()
    def prepare(self,*args,**kw):raise AssertionError('Full selected master created during browse')
    def clear_browse_cache(self,keep):self.cleared.append(list(keep))


def req(id=1,start=0,query='mouse',size=20):
    return dict(type='catalog',protocol=1,id=id,action='search',deviceKey='device',query=query,start=start,size=size,iconId=None)


class PagedCatalogTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.catalog=FakeCatalog(temp.name);self.replies=queue.Queue();self.warmed=[]
        self.state=(42,False,True,20,False,'mouse',False,'',False,20,'default',False,False)
        self.model=SimpleNamespace(icon_catalog=lambda:self.catalog,catalog_render_state=lambda key:(self.state,20),custom_icon_ids=lambda:['selected'])
        self.jobs=self.make_jobs()
    def make_jobs(self,*,factory=None):
        jobs=IconCatalogJobs(self.model,self.replies.put,lambda:None,lambda:True,catalog_factory=factory)
        def close_jobs():
            self.catalog.warm_release.set();jobs.close()
            self.assertTrue(jobs._cleanup_done.wait(3))
        self.addCleanup(close_jobs);return jobs
    def reply(self,id):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=self.replies.get(timeout=max(.01,deadline-time.monotonic()))
            if result.get('prefetch'):
                self.warmed.append(result);continue
            self.assertEqual(result['id'],id);self.assertNotIn('errorCode',result);return result
        self.fail('No foreground response')
    def wait_until_warm(self):
        self.assertTrue(self.catalog.warm_done.wait(3))
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with self.jobs._pages_lock:
                if not self.jobs._flights:return
            time.sleep(.005)
        self.fail('Prefetch did not finish')
    def test_neighbor_push_has_all_bitmaps_and_does_not_repeat_work_on_click(self):
        self.jobs.submit(req());first=self.reply(1)
        self.assertEqual(len(first['items']),50);self.assertEqual(first['total'],150)
        self.assertEqual(first['pageCount'],3);self.assertEqual(first['nextStart'],50)
        self.wait_until_warm()
        pushed=self.replies.get(timeout=3)
        self.assertTrue(pushed['prefetch']);self.assertEqual(pushed['anchorId'],1)
        self.assertEqual(pushed['start'],50);self.assertEqual(len(pushed['items']),50)
        self.assertEqual(pushed['contextKey'],first['contextKey'])
        before=self.catalog.preview_calls.count('test:mouse-50')
        self.jobs.submit(req(2,50));result=self.reply(2)
        self.assertTrue(result['cached']);self.assertEqual(len(result['items']),50)
        self.assertEqual(self.catalog.searches.count(('mouse',50)),1)
        self.assertEqual(self.catalog.preview_calls.count('test:mouse-50'),before)
    def test_matching_inflight_prefetch_is_promoted_without_duplicate_download(self):
        self.catalog.block=True;self.jobs.submit(req());self.reply(1)
        self.assertTrue(self.catalog.warm_enter.wait(1))
        self.jobs.submit(req(2,50));self.catalog.warm_release.set()
        self.assertEqual(len(self.reply(2)['items']),50)
        self.assertEqual(self.catalog.searches.count(('mouse',50)),1)
        self.assertEqual(self.catalog.batch_calls.count(tuple(f'test:mouse-{i}' for i in range(50,100))),1)
    def test_new_query_does_not_wait_for_old_background_download(self):
        self.catalog.block=True;self.jobs.submit(req());self.reply(1)
        self.assertTrue(self.catalog.warm_enter.wait(1))
        self.jobs.submit(req(2,0,'keyboard'));result=self.reply(2)
        self.assertEqual(result['query'],'keyboard')
        self.assertFalse(self.catalog.warm_release.is_set())
    def test_theme_and_dpi_change_never_reuse_wrong_ring_png(self):
        self.jobs.submit(req());first=self.reply(1);self.wait_until_warm()
        self.state=self.state[:4]+(True,)+self.state[5:]
        self.jobs.submit(req(2,50,size=24));result=self.reply(2)
        self.assertNotEqual(result['contextKey'],first['contextKey'])
        image=Image.open(io.BytesIO(base64.b64decode(result['items'][0]['preview'])))
        self.assertEqual(image.size,(24,24));self.assertEqual(image.getpixel((12,12))[:3],(0,0,0))
    def test_page_three_is_available_beyond_old_128_result_boundary(self):
        self.jobs.submit(req(1,100));last=self.reply(1)
        self.assertEqual(len(last['items']),50);self.assertFalse(last['hasMore'])
        self.assertEqual(last['items'][-1]['id'],'test:mouse-149')
    def test_close_cancels_prefetch_and_releases_session_cache(self):
        self.jobs.close();self.assertTrue(self.jobs._cleanup_done.wait(3))
        self.jobs=self.make_jobs(factory=lambda:self.catalog)
        self.catalog.block=True;self.jobs.submit(req());self.reply(1)
        self.assertTrue(self.catalog.warm_enter.wait(1));self.jobs.close();self.catalog.warm_release.set()
        self.assertTrue(self.jobs._cleanup_done.wait(3))
        self.assertEqual(self.catalog.cleared,[['selected']])
        self.assertEqual(self.jobs._pages.memory_bytes,0)
        self.assertIsNone(self.jobs._catalog_instance)
        self.assertFalse(self.jobs._thread.is_alive());self.assertFalse(self.jobs._warm_thread.is_alive())
    def test_late_old_window_cleanup_cannot_clear_new_window_cache(self):
        self.jobs.close();self.assertTrue(self.jobs._cleanup_done.wait(3))
        old=self.make_jobs(factory=lambda:self.catalog);self.catalog.block=True
        old.submit(req());self.reply(1);self.assertTrue(self.catalog.warm_enter.wait(1));old.close()
        newer=FakeCatalog(self.catalog.root);newjobs=self.make_jobs(factory=lambda:newer)
        newjobs.submit(req());self.reply(1)
        self.catalog.warm_release.set();self.assertTrue(old._cleanup_done.wait(3))
        self.assertEqual(self.catalog.cleared,[['selected']]);self.assertEqual(newer.cleared,[])
        self.assertGreater(newjobs._pages.memory_bytes,0)
    def test_cancel_delivered_with_reply_cannot_start_new_warm_job(self):
        sent=threading.Event()
        def send(message):
            self.replies.put(message)
            self.jobs.submit(dict(req(2),action='cancel'));sent.set()
        self.jobs.send=send
        self.jobs.submit(req());self.reply(1);self.assertTrue(sent.wait(1))
        self.assertFalse(self.catalog.warm_enter.wait(.1))
        self.assertEqual(self.catalog.searches,[('mouse',0)])
    def test_search_offsets_must_be_page_aligned_and_bounded(self):
        for start in (1,32,49,51,500050):
            with self.subTest(start=start),self.assertRaises(ValueError):validate_request(req(start=start))
        validate_request(req(start=500000))

    def test_inspecting_a_visible_tile_does_not_cancel_neighbor_preparation(self):
        self.catalog.block=True;self.jobs.submit(req());self.reply(1)
        self.assertTrue(self.catalog.warm_enter.wait(1))
        self.catalog.prepare=lambda *a,**kw:SimpleNamespace(id='a'*64,icon_id='test:mouse-0',glyph=SmallGlyph(),metadata={})
        self.model.custom_icon_in_use=lambda name:False
        self.jobs.submit(dict(req(2),action='details',iconId='test:mouse-0'))
        self.assertIn('detail',self.reply(2))
        self.assertFalse(self.jobs._warming[2].is_set())
        self.catalog.warm_release.set();self.wait_until_warm()
        self.assertEqual(self.catalog.searches.count(('mouse',50)),1)
