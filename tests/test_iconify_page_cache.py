"""Search pages are session-only, bounded and discarded when Settings closes."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image

from halo import iconify_page_cache as M


def preview():
    output=io.BytesIO();Image.new('RGBA',(2,2),(47,136,255,255)).save(output,format='PNG')
    return base64.b64encode(output.getvalue()).decode('ascii')

_PNG=preview()


def key(number=0):
    return dict(registry='abc',previewVersion='small-v1',deviceKey='mouse:1',query='mouse',
                start=number*50,size=32,state=(75,False,True,'thin'))


def page(number=0,count=50):
    return dict(items=[dict(id='tabler:mouse-'+str(number)+'-'+str(i),name='Mouse '+str(i),
                           collection='Tabler',preview=_PNG) for i in range(count)],
                nextStart=50,hasMore=True,cached=False,offline=False,skipped=0)


class PageCacheTests(unittest.TestCase):
    def setUp(self):
        self.now=[1000.]
        self.cache=M.PageCache('unused-directory',clock=lambda:self.now[0])
        self.addCleanup(self.cache.close)

    def test_fifty_item_page_and_canonical_keys_return_defensive_copies(self):
        identity=key();original=page()
        self.cache.put_async(identity,original)
        original['items'][0]['name']='mutated caller'
        first=self.cache.get(identity)
        self.assertTrue(first['cached']);self.assertEqual(len(first['items']),50)
        self.assertEqual(first['items'][0]['name'],'Mouse 0')
        first['items'][0]['name']='mutated returned';first['items'].pop()
        reordered=dict(reversed(list(identity.items())));reordered['state']=list(reordered['state'])
        again=self.cache.get(reordered)
        self.assertEqual(again['items'][0]['name'],'Mouse 0');self.assertEqual(len(again['items']),50)
        self.assertFalse(original['cached'])

    def test_no_filesystem_access_or_writer_thread_even_on_cold_miss(self):
        class NeverUsePath:
            def __fspath__(self):raise AssertionError('filesystem path conversion')
        cache=M.PageCache(NeverUsePath())
        with mock.patch('builtins.open',side_effect=AssertionError('file open')),\
             mock.patch.object(Path,'open',side_effect=AssertionError('path read')),\
             mock.patch.object(Path,'mkdir',side_effect=AssertionError('mkdir')),\
             mock.patch.object(Path,'iterdir',side_effect=AssertionError('directory scan')),\
             mock.patch.object(threading,'Thread',side_effect=AssertionError('writer thread')):
            self.assertIsNone(cache.get(key()))
            cache.put(key(),page());self.assertIsNotNone(cache.get(key()))
            cache.put_async(key(1),page(1));self.assertIsNotNone(cache.get(key(1)))
            cache.close();self.assertEqual(cache.memory_bytes,0)

    def test_close_discards_every_page_and_rejects_late_writes(self):
        for number in range(4):self.cache.put_async(key(number),page(number))
        self.assertGreater(self.cache.memory_bytes,0)
        self.cache.close();self.cache.close()
        self.assertEqual(self.cache.memory_bytes,0);self.assertFalse(self.cache._memory)
        for number in range(4):self.assertIsNone(self.cache.get(key(number)))
        with mock.patch.object(M,'_json',side_effect=AssertionError('late encoding')):
            self.cache.put_async(key(),page());self.cache.put(key(),page())
        self.assertEqual(self.cache.memory_bytes,0)
        self.assertIsNone(M.PageCache('unused-directory').get(key()))

    def test_close_wins_against_already_running_validation(self):
        entered=threading.Event();release=threading.Event()
        original=M._valid_result
        def blocked(result):entered.set();release.wait(5);return original(result)
        with mock.patch.object(M,'_valid_result',side_effect=blocked):
            writer=threading.Thread(target=lambda:self.cache.put_async(key(),page()));writer.start()
            self.assertTrue(entered.wait(5))
            try:self.cache.close()
            finally:release.set();writer.join(5)
        self.assertFalse(writer.is_alive());self.assertEqual(self.cache.memory_bytes,0)
        self.assertIsNone(self.cache.get(key()))

    def test_close_leaves_selected_offline_assets_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);selected=root/'assets'/'selected'
            selected.mkdir(parents=True);asset=selected/'glyph.png';asset.write_bytes(b'verified selected offline asset')
            cache=M.PageCache(root/'preview-pages');cache.put(key(),page());cache.close()
            self.assertEqual(asset.read_bytes(),b'verified selected offline asset')
            self.assertFalse((root/'preview-pages').exists())

    def test_lru_count_and_bytes_allow_adjacent_pages_without_unbounded_growth(self):
        for number in range(13):self.cache.put_async(key(number),page(number))
        self.assertIsNone(self.cache.get(key(0)))
        self.assertIsNotNone(self.cache.get(key(1)))
        self.cache.put_async(key(13),page(13))
        self.assertIsNone(self.cache.get(key(2)))
        self.assertIsNotNone(self.cache.get(key(1)))
        self.assertLessEqual(len(self.cache._memory),12)
        self.assertLessEqual(self.cache.memory_bytes,48*1024*1024)
        one=len(M._json(page()))+len(M._key(key())[0])
        bounded=M.PageCache(None)
        with mock.patch.object(M,'RAM_BYTE_LIMIT',one*2+32):
            for number in range(3):bounded.put_async(key(number),page(number))
            self.assertIsNone(bounded.get(key(0)))
            self.assertIsNotNone(bounded.get(key(1)));self.assertIsNotNone(bounded.get(key(2)))
            self.assertLessEqual(bounded.memory_bytes,one*2+32)
        bounded.close()

    def test_ttl_still_expires_stale_pages_inside_a_long_lived_session(self):
        self.cache.put(key(),page());self.now[0]+=M.TTL_SECONDS
        self.assertIsNotNone(self.cache.get(key()))
        self.now[0]+=.01;self.assertIsNone(self.cache.get(key()))
        self.assertEqual(self.cache.memory_bytes,0)

    def test_offline_partial_and_error_results_are_not_cached(self):
        for flag in ('offline','partial','partialFailure','networkError'):
            candidate=page();candidate[flag]=True
            self.cache.put_async(key(),candidate);self.assertIsNone(self.cache.get(key()))
        candidate=page();candidate['errorCode']='offline'
        self.cache.put(key(),candidate);self.assertIsNone(self.cache.get(key()))
        candidate=page();candidate['skipped']=4
        self.cache.put_async(key(),candidate);self.assertEqual(self.cache.get(key())['skipped'],4)

    def test_schema_preview_and_encoded_page_limits(self):
        invalid=[page(count=51)]
        for name,value in (('nextStart',True),('hasMore',1),('cached',1),('offline','false'),('skipped',-1)):
            candidate=page();candidate[name]=value;invalid.append(candidate)
        for row in (None,dict(id='../path',preview=_PNG),dict(id=True,preview=_PNG),
                    dict(id='tabler:mouse',preview='not PNG'),dict(id='tabler:mouse',preview=_PNG+'!'),
                    dict(id='tabler:mouse',preview='A'*(M.MAX_PREVIEW_CHARS+4))):
            candidate=page();candidate['items']=[row];invalid.append(candidate)
        candidate=page();candidate['padding']='x'*M.MAX_PAGE_BYTES;invalid.append(candidate)
        for candidate in invalid:
            self.cache.put_async(key(),candidate);self.assertIsNone(self.cache.get(key()))
        empty=page(count=0);empty['hasMore']=False;empty['nextStart']=0
        self.cache.put_async(key(),empty);self.assertEqual(self.cache.get(key())['items'],[])

    def test_large_local_search_totals_and_optional_pagination_metadata(self):
        candidate=page();candidate.update(nextStart=270400,total=270423,pageCount=5409,pageSize=50,contextKey='mouse-context')
        self.cache.put_async(key(5407),candidate)
        self.assertEqual(self.cache.get(key(5407))['total'],270423)
        boundary=page();boundary['nextStart']=500000
        self.cache.put(key(),boundary);self.assertEqual(self.cache.get(key())['nextStart'],500000)
        for field,value in (('nextStart',500001),('total',True),('total',-1),('pageCount',False),
                            ('pageCount',-1),('pageSize',0),('pageSize',51),('pageSize',True),('contextKey',123)):
            item=page();item[field]=value
            identity=dict(invalid=field,value=str(value))
            self.cache.put_async(identity,item);self.assertIsNone(self.cache.get(identity))
        unknown=page();unknown.update(total=None,pageCount=None)
        self.cache.put_async(dict(unknown=True),unknown)
        self.assertIsNone(self.cache.get(dict(unknown=True))['total'])

    def test_invalid_keys_and_hot_reads_need_no_png_decoder(self):
        for identity in (None,[],{'bad':object()},{1:'number key'},{'bad':float('nan')},
                         {'huge':'x'*(M.MAX_KEY_BYTES+1)}):
            self.cache.put_async(identity,page());self.assertIsNone(self.cache.get(identity))
        self.cache.put_async(key(),page())
        with mock.patch.object(Image,'open',side_effect=AssertionError('PNG decode')),\
             mock.patch.object(base64,'b64decode',side_effect=AssertionError('base64 decode')):
            self.assertEqual(len(self.cache.get(key())['items']),50)

    def test_concurrent_page_readers_and_prefetch_writers_remain_consistent(self):
        def work(slot):
            for number in range(20):
                identity=dict(slot=slot)
                candidate=page(slot);candidate['generation']=number
                self.cache.put_async(identity,candidate)
                found=self.cache.get(identity)
                self.assertEqual(found['items'][0]['id'],'tabler:mouse-'+str(slot)+'-0')
            return slot
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(work,range(4))),[0,1,2,3])
        for slot in range(4):self.assertEqual(self.cache.get(dict(slot=slot))['generation'],19)
        self.cache.close();self.assertEqual(self.cache.memory_bytes,0)


if __name__=='__main__':unittest.main()
