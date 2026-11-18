"""Official API pooling/proxy/TLS contracts, with fake sockets and no network."""
import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import http.client
import ssl
import threading
import time
import unittest
from unittest import mock
import urllib.error

from halo import iconify_http as M

URL='https://api.iconify.design/tabler.json?icons=mouse'


class Response:
    def __init__(self,data=b'{}',*,status=200,mime='application/json',headers=None,will_close=False,length=True):
        self.data=data;self.position=0;self.status=status;self.will_close=will_close;self.closed=False
        self.headers=Message();self.headers['Content-Type']=mime
        if length:self.headers['Content-Length']=str(len(data))
        for key,value in (headers or {}).items():
            if key in self.headers:del self.headers[key]
            self.headers[key]=value
        self.amounts=[]

    def read(self,amount):
        self.amounts.append(amount);block=self.data[self.position:self.position+amount];self.position+=len(block);return block

    read1=read
    def close(self):self.closed=True


class Socket:
    def __init__(self,owner):self.owner=owner;self.timeouts=[];self.shutdowns=[]
    def settimeout(self,timeout):self.timeouts.append(timeout)
    def shutdown(self,how):
        self.shutdowns.append(how)
        if self.owner.gate:self.owner.gate.set()


class Connection:
    def __init__(self,host,port,timeout,context,plans=(),gate=None,tracker=None):
        self.host=host;self.port=port;self.timeout=timeout;self.context=context
        self.plans=deque(plans);self.gate=gate;self.tracker=tracker;self.closed=False;self.requests=[];self.tunnel=None
        self.sock=Socket(self)

    def set_tunnel(self,host,port,headers):self.tunnel=(host,port,headers)
    def request(self,method,path,headers):
        self.requests.append((method,path,headers))
        action=self.plans.popleft() if self.plans else Response()
        if isinstance(action,BaseException):raise action
        self.current=action
        if self.tracker:
            with self.tracker['lock']:
                self.tracker['active']+=1
                self.tracker['maximum']=max(self.tracker['maximum'],self.tracker['active'])
                if self.tracker['active']==4:self.tracker['four'].set()
        try:
            if self.gate:self.gate.wait(5)
        finally:
            if self.tracker:
                with self.tracker['lock']:self.tracker['active']-=1
    def getresponse(self):return self.current
    def close(self):self.closed=True


class Factory:
    def __init__(self,plans=(),gate=None,tracker=None):
        self.plans=deque(plans);self.connections=[];self.lock=threading.Lock();self.gate=gate;self.tracker=tracker
    def __call__(self,host,port,timeout,context):
        with self.lock:
            connection=Connection(host,port,timeout,context,self.plans.popleft() if self.plans else (),self.gate,self.tracker)
            self.connections.append(connection);return connection


class HTTPTests(unittest.TestCase):
    def create(self,plans=(),**kwargs):
        factory=kwargs.pop('connection_factory',Factory(plans))
        fallback=kwargs.pop('fallback',mock.Mock(return_value=b'{}'))
        options=dict(connection_factory=factory,proxies={},proxy_bypass=lambda host:False,
                     context_factory=lambda:ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        options.update(kwargs)
        transport=M.IconifyHTTP(fallback,**options);self.addCleanup(transport.close)
        return transport,factory,fallback

    def test_reuses_only_fully_read_connections_and_verified_tls(self):
        first=Response(b'{"first":1}');second=Response(b'{"second":2}')
        transport,factory,_=self.create([[first,second]])
        self.assertEqual(transport(URL),b'{"first":1}');self.assertEqual(transport.fetch(URL),b'{"second":2}')
        self.assertEqual(len(factory.connections),1);connection=factory.connections[0]
        self.assertEqual((connection.host,connection.port),(M.HOST,443))
        self.assertEqual(connection.context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(connection.context.check_hostname)
        self.assertTrue(first.closed and second.closed);self.assertTrue(all(0<value<=8 for value in connection.sock.timeouts))
        self.assertEqual(len(connection.requests),2)
        self.assertTrue(all(req[0]=='GET' for req in connection.requests))
        self.assertEqual(transport._in_use,0);self.assertEqual(len(transport._idle),1)

    def test_default_context_factory_stays_verified_and_insecure_context_rejected(self):
        real=ssl.create_default_context
        with mock.patch.object(M.ssl,'create_default_context',wraps=real) as context:
            transport=M.IconifyHTTP(lambda url:b'{}',proxies={},proxy_bypass=lambda host:False)
            transport.close();context.assert_called_once()
        insecure=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);insecure.check_hostname=False;insecure.verify_mode=ssl.CERT_NONE
        with self.assertRaises(ValueError):self.create(context_factory=lambda:insecure)

    def test_http_proxy_connect_basic_auth_is_not_sent_to_origin(self):
        transport,factory,_=self.create(proxies={'https':'http://alice:secret%20p%40ss@proxy.invalid:8080'})
        transport(URL);connection=factory.connections[0]
        expected='Basic '+base64.b64encode(b'alice:secret p@ss').decode('ascii')
        self.assertEqual((connection.host,connection.port),('proxy.invalid',8080))
        self.assertEqual(connection.tunnel,(M.HOST,443,{'Proxy-Authorization':expected}))
        self.assertNotIn('Proxy-Authorization',connection.requests[0][2])
        self.assertNotIn('secret',repr(transport))

    def test_getproxies_and_proxy_bypass_are_respected(self):
        with mock.patch.object(M.urllib.request,'getproxies',return_value={'https':'http://proxy.invalid:3128'}) as proxies,\
             mock.patch.object(M.urllib.request,'proxy_bypass',return_value=True) as bypass:
            factory=Factory();transport=M.IconifyHTTP(lambda url:b'{}',connection_factory=factory)
            self.addCleanup(transport.close);transport(URL)
            self.assertEqual(factory.connections[0].host,M.HOST)
            proxies.assert_called_once();bypass.assert_called_once_with(M.HOST)

    def test_complex_proxy_falls_back_and_keeps_url_validation(self):
        for proxy in ('https://proxy.invalid:443','socks5://proxy.invalid:1080','http://proxy.invalid:bad'):
            with self.subTest(proxy=proxy):
                transport,factory,fallback=self.create(proxies={'https':proxy})
                self.assertEqual(transport(URL),b'{}');fallback.assert_called_once_with(URL)
                self.assertEqual(factory.connections,[])
                with self.assertRaises(ValueError):transport('http://api.iconify.design/anything')
                self.assertEqual(fallback.call_count,1)

    def test_strict_urls_reject_other_hosts_ports_redirect_targets_and_controls(self):
        transport,factory,fallback=self.create()
        for url in ('http://api.iconify.design/a','https://evil.invalid/a','https://api.iconify.design.evil/a',
                    'https://api.iconify.design:443/a','https://user@api.iconify.design/a',
                    'https://api.iconify.design/a#secret','https://api.iconify.design/a\r\nheader: x',None):
            with self.subTest(url=url),self.assertRaises(ValueError):transport(url)
        self.assertEqual(factory.connections,[]);fallback.assert_not_called()

    def test_redirect_and_429_never_retry_and_preserve_retry_headers(self):
        for status in (301,302,404,429):
            response=Response(status=status,headers={'Retry-After':'17','Location':'https://evil.invalid/'})
            transport,factory,fallback=self.create([[response]])
            with self.subTest(status=status),self.assertRaises(urllib.error.HTTPError) as caught:transport(URL)
            self.assertEqual(caught.exception.code,status)
            self.assertEqual(caught.exception.headers.get('Retry-After'),'17')
            self.assertEqual(len(factory.connections),1);self.assertTrue(factory.connections[0].closed)
            self.assertTrue(response.closed);fallback.assert_not_called()

    def test_mime_size_length_and_truncated_responses_are_not_reused(self):
        responses=[Response(mime='text/html'),Response(headers={'Content-Length':str(M.MAX_RESPONSE+1)}),
                   Response(headers={'Content-Length':'bad'}),Response(b'x'*(M.MAX_RESPONSE+1),length=False),
                   Response(b'{}',headers={'Content-Length':'20'})]
        for response in responses:
            with self.subTest(response=response):
                transport,factory,_=self.create([[response]])
                with self.assertRaises(urllib.error.HTTPError):transport(URL)
                self.assertTrue(factory.connections[0].closed);self.assertFalse(transport._idle)
                self.assertTrue(all(amount<=65536 for amount in response.amounts))

    def test_server_close_discards_connection(self):
        transport,factory,_=self.create([[Response(will_close=True)],[Response()]])
        transport(URL);transport(URL)
        self.assertEqual(len(factory.connections),2);self.assertTrue(factory.connections[0].closed)

    def test_reused_stale_connections_retry_once_but_fresh_or_certificate_failures_do_not(self):
        for error in (http.client.RemoteDisconnected('stale'),BrokenPipeError('stale'),ConnectionResetError('stale'),http.client.CannotSendRequest('stale')):
            with self.subTest(error=type(error).__name__):
                transport,factory,_=self.create([[Response(),error],[Response(b'{"retried":true}')]])
                transport(URL);self.assertEqual(transport(URL),b'{"retried":true}')
                self.assertEqual(len(factory.connections),2);self.assertTrue(factory.connections[0].closed)
        transport,factory,_=self.create([[http.client.RemoteDisconnected('fresh')]])
        with self.assertRaises(urllib.error.URLError):transport(URL)
        self.assertEqual(len(factory.connections),1)
        transport,factory,fallback=self.create([[Response(),ssl.SSLCertVerificationError('bad cert')]])
        transport(URL)
        with self.assertRaises(ssl.SSLCertVerificationError):transport(URL)
        self.assertEqual(len(factory.connections),1);fallback.assert_not_called()
        transport,factory,_=self.create([[Response(),BrokenPipeError('stale')],[BrokenPipeError('again')]])
        transport(URL)
        with self.assertRaises(urllib.error.URLError):transport(URL)
        self.assertEqual(len(factory.connections),2)

    def test_max_four_connections_and_four_waiters_and_close_releases_everyone(self):
        gate=threading.Event();tracker=dict(lock=threading.Lock(),active=0,maximum=0,four=threading.Event())
        factory=Factory(gate=gate,tracker=tracker);transport,_,_=self.create(connection_factory=factory)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures=[pool.submit(transport,URL) for _ in range(8)]
            self.assertTrue(tracker['four'].wait(5))
            until=time.monotonic()+5
            while time.monotonic()<until:
                with transport._condition:
                    if transport._waiters==4:break
                threading.Event().wait(.01)
            with transport._condition:self.assertEqual(transport._waiters,4)
            with self.assertRaises(urllib.error.URLError):transport(URL)
            transport.close()
            for future in futures:
                with self.assertRaises(urllib.error.URLError):future.result(timeout=5)
        self.assertLessEqual(tracker['maximum'],4);self.assertEqual(len(factory.connections),4)
        self.assertTrue(all(connection.closed for connection in factory.connections))
        self.assertFalse(transport._all);self.assertFalse(transport._idle);self.assertEqual(transport._in_use,0)
        with self.assertRaises(urllib.error.URLError):transport(URL)

    def test_close_discards_late_fallback_result(self):
        entered=threading.Event();release=threading.Event()
        def fallback(url):entered.set();release.wait(5);return b'late'
        transport,_,_=self.create(fallback=fallback,proxies={'https':'https://proxy.invalid'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(transport,URL);self.assertTrue(entered.wait(5))
            transport.close();release.set()
            with self.assertRaises(urllib.error.URLError):future.result(timeout=5)

    def test_invalid_pool_sizes_and_deadline_do_not_start_requests(self):
        for count in (0,5,True,4.,None):
            with self.subTest(count=count),self.assertRaises(ValueError):self.create(max_connections=count)
        transport,factory,_=self.create()
        with mock.patch.object(M,'TIMEOUT',0),self.assertRaises(TimeoutError):transport(URL)
        self.assertEqual(factory.connections[0].requests,[])
        self.assertEqual(transport._in_use,0)


if __name__=='__main__':unittest.main()
