"""Session-only final-PNG pages: no filesystem access, threads or persistence.

The root argument is retained for caller compatibility and is never used for
I/O. Immutable encoded pages are LRU-bounded; get returns a fresh nested copy.
Closing Settings discards every page and prevents late work repopulating it.
Selected offline glyph assets belong to a separate catalog store.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import json
import math
import re
import threading
import time

TTL_SECONDS = 24 * 3600
MAX_PAGE_ITEMS = 50
MAX_SEARCH_START = 500000
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_KEY_BYTES = 64 * 1024
MAX_PREVIEW_CHARS = 256 * 1024
RAM_PAGE_LIMIT = 12
RAM_BYTE_LIMIT = 48 * 1024 * 1024
_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,127}:[a-z0-9][a-z0-9-]{0,127}\Z')
_BASE64 = re.compile(r'[A-Za-z0-9+/]*={0,2}\Z')
_PNG_HEADER = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
_BAD_PAGE_FLAGS = ('offline','partial','partialFailure','networkError')


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def _key(key):
    if not isinstance(key,dict):
        return None
    def check(value,depth=0):
        if depth>24:
            raise ValueError('Page key is too deep')
        if isinstance(value,dict):
            for name,child in value.items():
                if not isinstance(name,str):
                    raise ValueError('Page key fields must be strings')
                check(child,depth+1)
        elif isinstance(value,(list,tuple)):
            for child in value:
                check(child,depth+1)
        elif value is not None and type(value) not in (str,int,float,bool):
            raise ValueError('Page key is not JSON')
    try:
        check(key)
        encoded=_json(key)
        if len(encoded)>MAX_KEY_BYTES:
            return None
        return encoded,hashlib.sha256(encoded).hexdigest()
    except (ValueError,TypeError,OverflowError,RecursionError,UnicodeError,RuntimeError):
        return None


def _valid_result(result):
    if not isinstance(result,dict):
        return False
    for flag in _BAD_PAGE_FLAGS:
        if flag in result and (type(result[flag]) is not bool or result[flag]):
            return False
    if result.get('errorCode'):
        return False
    if 'cached' in result and type(result['cached']) is not bool:
        return False
    items=result.get('items')
    if not isinstance(items,list) or len(items)>MAX_PAGE_ITEMS:
        return False
    if type(result.get('hasMore')) is not bool:
        return False
    if type(result.get('nextStart')) is not int or not 0<=result['nextStart']<=MAX_SEARCH_START:
        return False
    if 'skipped' in result and (type(result['skipped']) is not int or not 0<=result['skipped']<=10000):
        return False
    for name in ('total','pageCount'):
        value=result.get(name)
        if value is not None and (type(value) is not int or value<0):
            return False
    if 'pageSize' in result and (type(result['pageSize']) is not int or not 1<=result['pageSize']<=MAX_PAGE_ITEMS):
        return False
    if 'contextKey' in result and (not isinstance(result['contextKey'],str) or len(result['contextKey'])>MAX_KEY_BYTES):
        return False
    for row in items:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not _ID.fullmatch(row['id']):
            return False
        preview=row.get('preview')
        if (not isinstance(preview,str) or not 24<=len(preview)<=MAX_PREVIEW_CHARS or
                len(preview)%4 or not _BASE64.fullmatch(preview)):
            return False
        # Check only a fixed-size PNG header, never decompress or decode a full
        # image while serving a page. The producer already rendered these PNGs.
        try:
            if not base64.b64decode(preview[:24],validate=True).startswith(_PNG_HEADER):
                return False
        except (ValueError,TypeError):
            return False
    return True


class PageCache:
    def __init__(self,root,*,clock=time.time):
        self.root=root  # Compatibility only: do not inspect, create or read it.
        self._clock=clock
        self._lock=threading.RLock()
        self._memory=OrderedDict()
        self._memory_bytes=0
        self._closed=False

    @property
    def memory_bytes(self):
        with self._lock:
            return self._memory_bytes

    def _fresh(self,created):
        if type(created) not in (int,float) or not math.isfinite(created):
            return False
        return -300<=self._clock()-created<=TTL_SECONDS

    def get(self,key_dict):
        with self._lock:
            if self._closed:
                return None
        identity=_key(key_dict)
        if identity is None:
            return None
        key_bytes,token=identity
        with self._lock:
            if self._closed:
                return None
            saved=self._memory.get(token)
            if saved is None:
                return None
            if saved[0]!=key_bytes or not self._fresh(saved[2]):
                self._memory.pop(token)
                self._memory_bytes-=len(saved[0])+len(saved[1])
                return None
            self._memory.move_to_end(token)
            result=json.loads(saved[1])
            result['cached']=True
            return result

    def put(self,key_dict,result_dict):
        with self._lock:
            if self._closed:
                return
        identity=_key(key_dict)
        if identity is None:
            return
        key_bytes,token=identity
        try:
            result_bytes=_json(result_dict)
            if len(result_bytes)>MAX_PAGE_BYTES or not _valid_result(json.loads(result_bytes)):
                return
            created=self._clock()
            if not self._fresh(created):
                return
        except (ValueError,TypeError,OverflowError,RecursionError,UnicodeError,RuntimeError):
            return
        with self._lock:
            # An in-flight worker that started before close cannot retain pages.
            if self._closed:
                return
            old=self._memory.pop(token,None)
            if old is not None:
                self._memory_bytes-=len(old[0])+len(old[1])
            self._memory[token]=(key_bytes,result_bytes,created)
            self._memory_bytes+=len(key_bytes)+len(result_bytes)
            while len(self._memory)>RAM_PAGE_LIMIT or self._memory_bytes>RAM_BYTE_LIMIT:
                _,old=self._memory.popitem(last=False)
                self._memory_bytes-=len(old[0])+len(old[1])

    def put_async(self,key_dict,result_dict):
        """Compatibility entry point: RAM publication needs no writer or queue."""
        self.put(key_dict,result_dict)

    def close(self):
        """Discard the entire session cache and reject late worker publications."""
        with self._lock:
            self._closed=True
            self._memory.clear()
            self._memory_bytes=0
