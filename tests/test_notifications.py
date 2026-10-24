import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import MagicMock, patch
from test_checkin import app
from notifications import PushError, send_bark, validate_bark

SAVED='https://bark.example.com/saved-test-key'
PENDING='https://bark.example.com/pending-test-key'

class NotificationTests(unittest.TestCase):
    def response(self,body,status=200):
        response=MagicMock()
        response.status=status
        response.read.return_value=json.dumps(body).encode()
        response.__enter__.return_value=response
        return response
    def test_send_checks_json_success_and_payload(self):
        opener=MagicMock()
        opener.open.return_value=self.response({'code':200,'message':'success'})
        with patch('notifications.build_opener',return_value=opener):
            self.assertIn('确认接收',send_bark(SAVED,'测试','内容'))
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,SAVED)
        self.assertEqual(json.loads(request.data),{'title':'测试','body':'内容'})
        self.assertEqual(request.get_method(),'POST')
    def test_http200_failure_and_raw_error_are_not_success(self):
        for body in [{'code':400,'message':SAVED},{'ok':True},{'code':True},[]]:
            opener=MagicMock()
            opener.open.return_value=self.response(body)
            with patch('notifications.build_opener',return_value=opener):
                with self.assertRaises(PushError) as error:
                    send_bark(SAVED,'测试','内容')
                self.assertNotIn('saved-test-key',str(error.exception))
    def test_network_http_errors_are_sanitized(self):
        for failure in [OSError(SAVED),HTTPError(SAVED,403,'forbidden',{},io.BytesIO(SAVED.encode()))]:
            opener=MagicMock()
            opener.open.side_effect=failure
            with patch('notifications.build_opener',return_value=opener):
                with self.assertRaises(PushError) as error:
                    send_bark(SAVED,'测试','内容')
                self.assertNotIn('saved-test-key',str(error.exception))
    def test_validation(self):
        self.assertEqual(validate_bark(' '+SAVED+' '),SAVED)
        for url in ['http://bark.example.com/key','https://user:pass@example.com/key','file:///tmp/key']:
            with self.assertRaises(ValueError):
                validate_bark(url)
        with self.assertRaises(ValueError):
            send_bark('','测试','内容')

class NotificationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.stack=contextlib.ExitStack()
        for name,value in [('DATA',Path(self.temp.name)),('CONFIG',{'sites':[],'bark_url':SAVED}),('ADMIN_USERNAME',''),('ADMIN_TOKEN','notification-test-admin')]:
            self.stack.enter_context(patch.object(app,name,value))
        self.sender=self.stack.enter_context(patch.object(app,'send_bark',return_value='Bark 服务已确认接收'))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)+'/api/'
    def tearDown(self):
        self.server.shutdown();self.server.server_close()
        self.stack.close();self.temp.cleanup()
    def call(self,path,body=None,auth=True,csrf=True):
        headers={'Content-Type':'application/json'}
        if auth: headers['Authorization']='Bearer notification-test-admin'
        if csrf: headers['X-CSRF-Token']=app.CSRF
        request=Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        with urlopen(request) as response:
            return json.load(response)
    def test_saved_address_only_in_authenticated_settings(self):
        with self.assertRaises(HTTPError) as e:
            self.call('settings',auth=False)
        self.assertEqual(e.exception.code,401)
        self.assertEqual(self.call('settings')['bark_url'],SAVED)
        self.assertNotIn(SAVED,json.dumps(self.call('config')))
    def test_pending_saved_fallback_and_test_does_not_save(self):
        self.assertTrue(self.call('notifications/test',{'bark_url':PENDING})['ok'])
        self.assertEqual(self.sender.call_args.args[0],PENDING)
        self.assertEqual(self.call('settings')['bark_url'],SAVED)
        self.call('notifications/test',{'bark_url':None})
        self.assertEqual(self.sender.call_args.args[0],SAVED)
        self.assertFalse((Path(self.temp.name)/'config.json').exists())
    def test_save_reopen_clear_and_csrf(self):
        with self.assertRaises(HTTPError) as e:
            self.call('notifications/test',{'bark_url':PENDING},csrf=False)
        self.assertEqual(e.exception.code,403)
        self.sender.assert_not_called()
        self.call('settings',{'bark_url':PENDING})
        self.assertEqual(self.call('settings')['bark_url'],PENDING)
        self.call('settings',{})
        self.assertEqual(self.call('settings')['bark_url'],PENDING)
        self.call('settings',{'bark_url':''})
        self.assertEqual(self.call('settings')['bark_url'],'')
    def test_test_failure_propagates_without_persisting(self):
        self.sender.side_effect=PushError('Bark 请求失败')
        with self.assertRaises(HTTPError) as e:
            self.call('notifications/test',{'bark_url':PENDING})
        self.assertEqual(e.exception.code,400)
        self.assertNotIn('pending-test-key',e.exception.read().decode())
        self.assertEqual(self.call('settings')['bark_url'],SAVED)
