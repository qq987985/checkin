import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from unittest.mock import patch
from test_checkin import app
from cookiecloud import CloudError, cookie_header, decrypt, fetch_cookies, validate_config

FIXTURE = json.loads((Path(__file__).parent/'fixtures/cookiecloud.json').read_text())

class CloudMock(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args):
        pass
    def do_GET(self):
        CloudMock.requests.append((self.path, dict(self.headers)))
        if self.path.startswith('/redirect/'):
            self.send_response(302)
            self.send_header('Location', '/cookiecloud/get/'+FIXTURE['uuid'])
            self.end_headers()
            return
        if self.path != '/cookiecloud/get/'+FIXTURE['uuid']:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'encrypted': FIXTURE['legacy'], 'crypto_type':'legacy'}).encode())
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        CloudMock.requests.append((self.path, dict(self.headers)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"success":true}')

class CookieCloudTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),CloudMock)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.config={'server_url':self.base+'/cookiecloud','uuid':FIXTURE['uuid'],'password':FIXTURE['password'],'crypto_type':'auto'}
        CloudMock.requests.clear()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
    def test_node_generated_crypto_vectors(self):
        for name, algorithm in [('legacy','legacy'),('fixed','aes-128-cbc-fixed')]:
            for mode in ('auto',algorithm):
                self.assertEqual(decrypt({'encrypted':FIXTURE[name]},FIXTURE['uuid'],FIXTURE['password'],mode),FIXTURE['data']['cookie_data'])
        with self.assertRaises(CloudError):
            decrypt({'encrypted':FIXTURE['legacy']},FIXTURE['uuid'],'wrong-password')
        with self.assertRaises(CloudError):
            decrypt({'encrypted':'invalid'},FIXTURE['uuid'],FIXTURE['password'])
    def test_matching_domain_path_secure_expiry(self):
        cookie=lambda name,**kw:dict(domain='.example.com',hostOnly=False,path='/',name=name,value='value',session=True,**kw)
        data={'example.com':[
            cookie('all'),cookie('expired',expirationDate=1),cookie('secure',secure=True),
            {**cookie('expired'),'session':False,'expirationDate':1},
            {**cookie('host'),'domain':'example.com','hostOnly':True},
            {**cookie('path'),'path':'/api'},
            {**cookie('other'),'domain':'.other.com'},
            {**cookie('bad'),'value':'x\r\nCookie: evil'},
        ]}
        # explicit session cookies may carry historical expiration metadata and remain session cookies.
        header=cookie_header(data,'https://sub.example.com/api/checkin',now=100)
        self.assertIn('all=value',header)
        self.assertIn('secure=value',header)
        self.assertTrue(header.startswith('path=value'))
        self.assertNotIn('host=',header)
        self.assertNotIn('other=',header)
        self.assertNotIn('bad=',header)
        plain=cookie_header(data,'http://sub.example.com/apix',now=100)
        self.assertNotIn('secure=',plain)
        self.assertNotIn('path=',plain)
        with self.assertRaises(CloudError):
            cookie_header(data,'https://evilexample.com/api',now=100)
        with self.assertRaises(CloudError):
            cookie_header({'example.com':[{**cookie('expired'),'session':False,'expirationDate':1}]},'https://example.com',now=100)
    def test_fetch_keeps_prefix_and_never_sends_password(self):
        self.assertEqual(fetch_cookies(self.config),FIXTURE['data']['cookie_data'])
        self.assertEqual(CloudMock.requests[0][0],'/cookiecloud/get/'+FIXTURE['uuid'])
        self.assertNotIn(FIXTURE['password'],json.dumps(CloudMock.requests))
        before=len(CloudMock.requests)
        with self.assertRaises(CloudError):
            fetch_cookies({**self.config,'server_url':self.base+'/redirect'})
        self.assertEqual(len(CloudMock.requests),before+1)
    def test_signin_fetches_fresh_cookie_and_fails_closed(self):
        site={'auth_type':'cookiecloud','url':self.base+'/signin','method':'POST','headers':{'Cookie':'stale-cookie'},'body_type':'none'}
        with patch.object(app,'CONFIG',{'cookiecloud':self.config}):
            self.assertEqual(app.execute(site)[0],'success')
            self.assertEqual({k.lower():v for k,v in CloudMock.requests[-1][1].items()}['cookie'],'session=fixture-cookie')
            app.execute(site)
            self.assertEqual(sum(path.startswith('/cookiecloud/get/') for path,_ in CloudMock.requests),2)
        before=len(CloudMock.requests)
        with patch.object(app,'CONFIG',{'cookiecloud':{**self.config,'password':'wrong-password'}}):
            with self.assertRaises(CloudError):
                app.execute(site)
        self.assertEqual(len(CloudMock.requests),before+1)  # only fetch, no sign-in request
    def test_settings_save_test_preserve_clear(self):
        import contextlib
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        api='http://127.0.0.1:'+str(server.server_port)+'/api/'
        try:
            with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
                for name,value in [('DATA',Path(temp)),('CONFIG',{'sites':[]}),('ADMIN_USERNAME',''),('ADMIN_TOKEN','')]:
                    stack.enter_context(patch.object(app,name,value))
                def post(path,value):
                    request=Request(api+path,data=json.dumps(value).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':app.CSRF})
                    with urlopen(request) as response:
                        return json.load(response)
                self.assertTrue(post('cookiecloud',self.config)['ok'])
                self.assertEqual(CloudMock.requests,[])
                result=post('cookiecloud/test',{})
                self.assertIn('解密成功',result['message'])
                self.assertNotIn('fixture-cookie',json.dumps(result))
                post('cookiecloud',{'uuid':None,'password':None,'crypto_type':'auto'})
                self.assertEqual(app.CONFIG['cookiecloud'],self.config)
                self.assertEqual(json.loads((Path(temp)/'config.json').read_text())['cookiecloud'],self.config)
                post('cookiecloud',{'clear':True})
                self.assertNotIn('cookiecloud',app.CONFIG)
        finally:
            server.shutdown()
            server.server_close()

    def test_public_settings_redact_and_validation(self):
        with patch.object(app,'CONFIG',{'sites':[],'cookiecloud':self.config}):
            result=app.public_config()
            self.assertTrue(result['cookiecloud']['has_password'])
            self.assertTrue(result['cookiecloud']['has_uuid'])
            self.assertNotIn(FIXTURE['password'],json.dumps(result))
            self.assertNotIn(FIXTURE['uuid'],json.dumps(result))
        self.assertEqual(validate_config({'password':None,'uuid':None,'server_url':self.config['server_url'],'crypto_type':'auto'},self.config),self.config)
        for value in ['file:///tmp/x','https://user:pass@example.com','https://example.com?password=x']:
            with self.assertRaises(ValueError):
                validate_config({**self.config,'server_url':value})
