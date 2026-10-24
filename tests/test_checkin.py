import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('checkin', Path(__file__).parents[1] / 'checkin.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class Mock(BaseHTTPRequestHandler):
    received = []
    def log_message(self, *args):
        pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        Mock.received.append(dict(self.headers))
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/ok')
            self.end_headers()
            return
        self.send_response(401 if self.path == '/unauthorized' else 200)
        self.end_headers()
        self.wfile.write(b'{"success":true,"data":{"signed":true}}')

class CheckinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Mock)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.DATA = Path(self.tmp.name)
        app.CONFIG = {'sites': [], 'bark_url': ''}
        app.STATE = {'success': {}, 'counts': {}, 'logs': [], 'attempts': {}}
        Mock.received.clear()
    def tearDown(self):
        self.tmp.cleanup()
    def site(self, **kwargs):
        return app.validate_site(dict(name='测试站点', url=self.base+'/ok', method='POST', auth_type='bearer', secret='test-token', times=['08:30','20:30'], headers={}, body_type='json', body='{}', daily_limit=2, **kwargs))
    def test_quota_persists_and_no_credential_leak(self):
        s = self.site()
        app.CONFIG['sites'] = [s]
        app.write_json(app.DATA/'config.json', app.CONFIG)
        self.assertEqual(app.run_site(s['id'])['result'], 'success')
        self.assertEqual(app.run_site(s['id'])['result'], 'success')
        self.assertEqual(app.run_site(s['id'])['result'], 'skipped')
        self.assertEqual(len(Mock.received), 2)
        self.assertEqual(Mock.received[0]['Authorization'], 'Bearer test-token')
        self.assertNotIn('test-token', json.dumps(app.public_config()))
        self.assertNotIn('test-token', (app.DATA/'state.json').read_text())
        app.load()
        self.assertEqual(app.run_site(s['id'])['result'], 'skipped')
        self.assertEqual((app.DATA/'config.json').stat().st_mode & 0o777, 0o600)
    def test_auth_types_and_redirect(self):
        for kind, key, secret in [('cookie', 'Cookie', 'a=b'), ('api_key', 'X-API-Key', 'abc')]:
            s = self.site()
            s.update(auth_type=kind, secret=secret)
            self.assertEqual(app.execute(s)[0], 'success')
            self.assertEqual({k.lower(): v for k, v in Mock.received[-1].items()}[key.lower()], secret)
        s['url'] = self.base+'/redirect'
        self.assertEqual(app.execute(s)[0], 'failed')
        self.assertEqual(len(Mock.received), 3)
        s['url'] = self.base+'/unauthorized'
        self.assertEqual(app.execute(s)[0], 'failed')
    def test_classification(self):
        s = self.site()
        for text in ['{}','{"code":200}', '{"success":false,"msg":"成功"}']:
            self.assertNotEqual(app.classify(s, 200, text), 'success')
        s.update(success_path='data.signed', success_value='true')
        self.assertEqual(app.classify(s, 200, '{"data":{"signed":true}}'), 'success')
        self.assertEqual(app.classify(s, 200, '{"data":{"signed":1}}'), 'unknown')
        self.assertEqual(app.classify(s, 401, '{"data":{"signed":true}}'), 'failed')
    def test_schedule_runs_once_per_slot_and_skips_paused(self):
        from datetime import datetime
        s = self.site()
        paused = self.site()
        paused['enabled'] = False
        app.CONFIG['sites'] = [s, paused]
        with patch.object(app, 'run_site', return_value={'result': 'failed'}) as run:
            app.tick(datetime(2026, 10, 24, 8, 29))
            self.assertEqual(run.call_count, 0)
            app.tick(datetime(2026, 10, 24, 8, 30))
            app.tick(datetime(2026, 10, 24, 8, 30))
            self.assertEqual(run.call_count, 1)
            app.tick(datetime(2026, 10, 24, 20, 30))
            self.assertEqual(run.call_count, 2)
            app.tick(datetime(2026, 10, 25, 8, 30))
            self.assertEqual(run.call_count, 3)

    def test_api_auth_and_csrf(self):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with patch.object(app, 'ADMIN_TOKEN', 'unit-test-admin-token'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(base+'/api/config')
                self.assertEqual(error.exception.code, 401)
                headers = {'Authorization': 'Bearer unit-test-admin-token', 'Content-Type': 'application/json'}
                with urlopen(Request(base+'/api/config', headers=headers)) as response:
                    self.assertIn('csrf', json.load(response))
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/settings', data=b'{}', headers=headers))
                self.assertEqual(error.exception.code, 403)
                headers['X-CSRF-Token'] = app.CSRF
                with urlopen(Request(base+'/api/settings', data=b'{}', headers=headers)) as response:
                    self.assertTrue(json.load(response)['ok'])
        finally:
            server.shutdown()
            server.server_close()

    def test_validation(self):
        s = self.site()
        for field, value in [('times',['24:01']), ('url','file:///tmp/x'), ('daily_limit',0), ('headers',{'X-Test':'a\nb'})]:
            with self.assertRaises(ValueError):
                app.validate_site({**s, field:value})

if __name__ == '__main__':
    unittest.main()
