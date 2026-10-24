import hashlib
import json
import threading
import time
import unittest
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from unittest.mock import patch
from test_checkin import app

class LoginTests(unittest.TestCase):
    def setUp(self):
        self.password = 'unit-test-password-123'
        self.patches = [patch.object(app, 'ADMIN_USERNAME', '管理员'), patch.object(app, 'PASSWORD_HASH', hashlib.pbkdf2_hmac('sha256', self.password.encode(), app.PASSWORD_SALT, 600000)), patch.object(app, 'COOKIE_SECURE', False), patch.object(app, 'PUBLIC_URL', 'https://checkin.example.com')]
        for p in self.patches:
            p.start()
        app.SESSIONS.clear()
        app.LOGIN_FAILURES.clear()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in reversed(self.patches):
            p.stop()
    def login(self, password=None, origin='https://checkin.example.com'):
        return self.client.open(Request(self.base+'/api/login', data=json.dumps({'username':'管理员','password':password or self.password}).encode(), headers={'Content-Type':'application/json','Origin':origin}))
    def test_login_csrf_logout_expiry(self):
        with self.assertRaises(HTTPError) as e:
            urlopen(self.base+'/api/config')
        self.assertEqual(e.exception.code, 401)
        with self.login() as r:
            cookie=r.headers['Set-Cookie']
            self.assertIn('HttpOnly',cookie)
            self.assertIn('SameSite=Strict',cookie)
            self.assertTrue(json.load(r)['ok'])
        with self.client.open(self.base+'/api/config') as r:
            csrf=json.load(r)['csrf']
        with self.assertRaises(HTTPError) as e:
            self.client.open(Request(self.base+'/api/logout',data=b'{}'))
        self.assertEqual(e.exception.code,403)
        with self.client.open(Request(self.base+'/api/logout', data=b'{}', headers={'X-CSRF-Token':csrf})) as r:
            self.assertIn('Max-Age=0',r.headers['Set-Cookie'])
        with self.assertRaises(HTTPError):
            self.client.open(self.base+'/api/config')
        self.login().close()
        for session in app.SESSIONS.values():
            session['expires']=time.time()-1
        with self.assertRaises(HTTPError):
            self.client.open(self.base+'/api/config')
        with patch.object(app,'COOKIE_SECURE',True):
            self.assertIn('; Secure',app.session_cookie('test',100))
    def test_origin_rate_limit_and_token_cannot_bypass(self):
        with self.assertRaises(HTTPError) as e:
            self.login(origin='https://attacker.example')
        self.assertEqual(e.exception.code,403)
        with patch.object(app,'ADMIN_TOKEN','old-token'):
            with self.assertRaises(HTTPError) as e:
                urlopen(Request(self.base+'/api/config',headers={'Authorization':'Bearer old-token'}))
            self.assertEqual(e.exception.code,401)
        for _ in range(5):
            with self.assertRaises(HTTPError) as e:
                self.login('wrong-password')
            self.assertEqual(e.exception.code,401)
        with self.assertRaises(HTTPError) as e:
            self.login()
        self.assertEqual(e.exception.code,429)
        app.LOGIN_FAILURES['127.0.0.1']['since']-=301
        self.login().close()
