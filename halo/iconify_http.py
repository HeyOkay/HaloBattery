"""Window-owned, bounded keep-alive transport for the official Iconify API.

Only GETs to the exact HTTPS API host are accepted. Four leased connections and
four waiting callers bound concurrency. HTTP proxies use CONNECT (Basic auth is
sent only to the proxy); complex proxy schemes retain the supplied urllib path.
No proxy URLs, credentials or response bodies are logged.
"""
from __future__ import annotations

import base64
from collections import deque
import http.client
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = 'api.iconify.design'
MAX_RESPONSE = 2 * 1024 * 1024
TIMEOUT = 8.0
MAX_WAITERS = 4
_STALE = (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError,
          http.client.CannotSendRequest)


class IconifyHTTP:
    def __init__(self,fallback,*,max_connections=4,connection_factory=None,
                 proxies=None,proxy_bypass=None,context_factory=None):
        if not callable(fallback):
            raise TypeError('Icon API fallback must be callable')
        if type(max_connections) is not int or not 1<=max_connections<=4:
            raise ValueError('Icon API pool size must be within 1..4')
        self._fallback=fallback
        self._factory=connection_factory or http.client.HTTPSConnection
        self._context=(context_factory or ssl.create_default_context)()
        if not self._context.check_hostname or self._context.verify_mode!=ssl.CERT_REQUIRED:
            raise ValueError('Icon API transport requires verified TLS')
        self._limit=max_connections
        self._condition=threading.Condition(threading.RLock())
        self._idle=deque()
        self._all=set()
        self._in_use=0
        self._waiters=0
        self._closed=False
        self._proxy_host=None
        self._proxy_port=None
        self._proxy_headers={}
        self._use_fallback=False
        configured=urllib.request.getproxies() if proxies is None else proxies
        bypass=proxy_bypass or urllib.request.proxy_bypass
        proxy=None if bypass(HOST) else configured.get('https',configured.get('all'))
        if proxy:
            try:
                parsed=urllib.parse.urlsplit(proxy if '://' in proxy else 'http://'+proxy)
                port=parsed.port or 80
                if (parsed.scheme!='http' or not parsed.hostname or not 1<=port<=65535 or
                        parsed.path not in ('','/') or parsed.query or parsed.fragment):
                    self._use_fallback=True
                else:
                    self._proxy_host,self._proxy_port=parsed.hostname,port
                    if parsed.username is not None:
                        user=urllib.parse.unquote(parsed.username)
                        password=urllib.parse.unquote(parsed.password or '')
                        token=base64.b64encode((user+':'+password).encode('utf-8')).decode('ascii')
                        self._proxy_headers={'Proxy-Authorization':'Basic '+token}
            except (TypeError,ValueError):
                self._use_fallback=True

    @staticmethod
    def _url(url):
        if not isinstance(url,str) or len(url)>32768 or any(ord(c)<=32 or ord(c)==127 for c in url):
            raise ValueError('Untrusted icon API URL')
        parsed=urllib.parse.urlsplit(url)
        if (parsed.scheme!='https' or parsed.netloc!=HOST or parsed.username or
                parsed.password or parsed.fragment):
            raise ValueError('Untrusted icon API URL')
        return (parsed.path or '/')+('?' + parsed.query if parsed.query else '')

    def _open(self):
        with self._condition:
            if self._closed:
                raise urllib.error.URLError('Icon API transport closed')

    @staticmethod
    def _close_connection(connection):
        # Interrupt a blocked read before HTTPConnection.close closes its buffered
        # response. Otherwise closing a response can wait for that read's lock.
        sock=getattr(connection,'sock',None)
        if sock is not None:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        try:connection.close()
        except OSError:pass

    def _new_connection(self):
        host=self._proxy_host or HOST
        port=self._proxy_port or 443
        connection=self._factory(host,port,timeout=TIMEOUT,context=self._context)
        try:
            if self._proxy_host:
                connection.set_tunnel(HOST,443,headers=dict(self._proxy_headers))
            with self._condition:
                if self._closed:
                    raise urllib.error.URLError('Icon API transport closed')
                self._all.add(connection)
            return connection
        except BaseException:
            self._close_connection(connection)
            raise

    def _acquire(self,deadline):
        with self._condition:
            while self._in_use>=self._limit:
                if self._closed:
                    raise urllib.error.URLError('Icon API transport closed')
                if self._waiters>=MAX_WAITERS:
                    raise urllib.error.URLError('Icon API connection queue full')
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise urllib.error.URLError('Icon API connection wait timed out')
                self._waiters+=1
                try:self._condition.wait(remaining)
                finally:self._waiters-=1
            if self._closed:
                raise urllib.error.URLError('Icon API transport closed')
            self._in_use+=1
            if self._use_fallback:
                return None,False
            if self._idle:
                return self._idle.popleft(),True
        try:return self._new_connection(),False
        except BaseException:
            with self._condition:
                self._in_use-=1;self._condition.notify_all()
            raise

    def _discard(self,connection):
        with self._condition:self._all.discard(connection)
        self._close_connection(connection)

    def _release(self,connection,reusable):
        close=None
        with self._condition:
            self._in_use-=1
            if connection is not None:
                if reusable and not self._closed:
                    self._idle.append(connection)
                else:
                    self._all.discard(connection);close=connection
            self._condition.notify_all()
        if close is not None:self._close_connection(close)

    def _timeout(self,connection,deadline):
        self._open()
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('Icon API request timed out')
        connection.timeout=min(TIMEOUT,remaining)
        sock=getattr(connection,'sock',None)
        if sock is not None:sock.settimeout(connection.timeout)

    @staticmethod
    def _response_error(url,response,message):
        error=urllib.error.HTTPError(url,response.status,message,response.headers,None)
        # Only status/headers are exposed; the response body is deliberately not
        # attached. Close HTTPError's empty placeholder stream immediately.
        error.close()
        return error

    def _perform(self,connection,path,url,deadline):
        response=None
        try:
            self._timeout(connection,deadline)
            connection.request('GET',path,headers={'Accept':'application/json','User-Agent':'HaloBattery/IconPicker'})
            self._timeout(connection,deadline)
            response=connection.getresponse()
            if response.status!=200:
                raise self._response_error(url,response,'Icon API HTTP '+str(response.status))
            if response.headers.get_content_type()!='application/json':
                raise self._response_error(url,response,'Unexpected icon API content type')
            length=response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length)>MAX_RESPONSE):
                raise self._response_error(url,response,'Oversized icon API response')
            chunks=[];total=0
            read=getattr(response,'read1',response.read)
            while True:
                self._timeout(connection,deadline)
                block=read(min(65536,MAX_RESPONSE+1-total))
                if not block:break
                total+=len(block)
                if total>MAX_RESPONSE:
                    raise self._response_error(url,response,'Oversized icon API response')
                chunks.append(block)
            if length is not None and total!=int(length):
                raise self._response_error(url,response,'Truncated icon API response')
            self._open()
            return b''.join(chunks),not response.will_close
        finally:
            if response is not None:response.close()

    def fetch(self,url):
        path=self._url(url)
        deadline=time.monotonic()+TIMEOUT
        connection,reused=self._acquire(deadline)
        reusable=False
        try:
            if self._use_fallback:
                self._open();payload=self._fallback(url);self._open()
                return payload
            for attempt in range(2):
                try:
                    payload,reusable=self._perform(connection,path,url,deadline)
                    return payload
                except _STALE as error:
                    self._open()
                    if attempt or not reused:
                        raise urllib.error.URLError('Icon API connection failed') from error
                    self._discard(connection)
                    connection=None
                    connection=self._new_connection()
                    reused=False
            raise urllib.error.URLError('Icon API connection failed')
        finally:self._release(connection,reusable)

    __call__=fetch

    def close(self):
        with self._condition:
            self._closed=True
            connections=list(self._all)
            self._idle.clear();self._all.clear()
            self._condition.notify_all()
        for connection in connections:self._close_connection(connection)
