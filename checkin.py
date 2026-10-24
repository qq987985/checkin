"""多站点签到服务：python checkin.py，默认 http://127.0.0.1:8505。"""
import hashlib
from http.cookies import SimpleCookie
from cookiecloud import CloudError, fetch_cookies, cookie_header, validate_config as validate_cloud
from notifications import PushError, send_bark, validate_bark
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
LOCK = threading.RLock()
RUN_LOCK = threading.Lock()
CONFIG = {}
STATE = {}
CSRF = secrets.token_urlsafe(32)
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')  # 兼容旧版本；启用账号后禁用此入口
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
PUBLIC_URL = os.environ.get('PUBLIC_URL', '').rstrip('/')
PASSWORD_SALT = secrets.token_bytes(32)
PASSWORD_HASH = hashlib.pbkdf2_hmac('sha256', ADMIN_PASSWORD.encode(), PASSWORD_SALT, 600000)
SESSIONS = {}
LOGIN_FAILURES = {}
SESSION_SECONDS = 12 * 60 * 60


def session_for(handler):
    try:
        cookie = SimpleCookie()
        cookie.load(handler.headers.get('Cookie', ''))
        value = cookie['checkin_session'].value if 'checkin_session' in cookie else ''
    except Exception:
        return '', None
    key = hashlib.sha256(value.encode()).hexdigest()
    with LOCK:
        entry = SESSIONS.get(key)
        if entry and entry['expires'] > time.time():
            return key, entry
        SESSIONS.pop(key, None)
    return key, None


def session_cookie(value, max_age):
    return f'checkin_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}' + ('; Secure' if COOKIE_SECURE else '')


def write_json(path, value):
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)

def load():
    global CONFIG, STATE
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    cp, sp = DATA / 'config.json', DATA / 'state.json'
    if cp.exists():
        CONFIG = json.loads(cp.read_text())
    else:
        sites = []
        url, cookie = os.getenv('CHECKIN_URL', ''), os.getenv('LINGXI_COOKIE', '')
        if url and cookie and '请在这里' not in url + cookie:
            sites.append(dict(id='lingxi', name='金山灵犀', enabled=True, url=url,
                              method='POST', auth_type='cookie', secret=cookie,
                              times=os.getenv('CHECKIN_TIMES', '08:30,16:30').split(','),
                              headers={}, body='', body_type='none', success_path='', success_value='',
                              success_keywords='成功,已签到,已签过', failure_keywords='未登录,失败,过期', auth_header='X-API-Key'))
        bark = os.getenv('BARK_URL', '')
        CONFIG = dict(sites=sites, bark_url=bark if '请在这里' not in bark else '')
        write_json(cp, CONFIG)
    STATE = json.loads(sp.read_text()) if sp.exists() else {'success': {}, 'logs': [], 'attempts': {}}
    legacy = DATA / 'last_success.txt'
    if legacy.exists() and any(s['id'] == 'lingxi' for s in CONFIG['sites']):
        STATE['success'].setdefault('lingxi', legacy.read_text().strip())
    STATE.setdefault('attempts', {})
    STATE.setdefault('counts', {})

def validate_site(s):
    s = dict(s)
    s['id'] = s.get('id') or uuid.uuid4().hex
    s['name'] = str(s.get('name', '')).strip()
    if not s['name'] or len(s['name']) > 80:
        raise ValueError('名称不能为空，最多 80 字')
    s['url'] = str(s.get('url', '')).strip()
    u = urlsplit(s['url'])
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('请填写有效的 HTTP/HTTPS 签到接口 URL')
    if s.get('method') not in ('GET', 'POST', 'PUT', 'PATCH'):
        raise ValueError('不支持的请求方法')
    if s.get('auth_type') not in ('none', 'cookie', 'cookiecloud', 'bearer', 'api_key'):
        raise ValueError('不支持的验证方式')
    if s.get('auth_type') not in ('none', 'cookiecloud') and not s.get('secret'):
        raise ValueError('请填写 Cookie 或 Token')
    s['times'] = sorted(set(t.strip() for t in s.get('times', [])))
    if not s['times'] or any(not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', t) for t in s['times']):
        raise ValueError('签到时间应为 HH:MM，可填写多个')
    headers = s.get('headers', {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k) or '\r' in v or '\n' in v for k, v in headers.items()):
        raise ValueError('请求头必须是 JSON 对象，值为无换行的字符串')
    if s.get('auth_type') == 'api_key' and not re.fullmatch(r'[A-Za-z0-9_-]+', s.get('auth_header', '')):
        raise ValueError('API Key 请求头名称无效')
    if '\r' in s.get('secret', '') or '\n' in s.get('secret', ''):
        raise ValueError('验证凭据不能包含换行')
    if s.get('body_type') not in ('none', 'json', 'raw'):
        raise ValueError('请求体类型无效')
    if s.get('body_type') == 'json':
        json.loads(s.get('body', '') or '{}')
    s['daily_limit'] = int(s.get('daily_limit', 1))
    if not 1 <= s['daily_limit'] <= 50:
        raise ValueError('每日次数应在 1 到 50 之间')
    s['enabled'] = bool(s.get('enabled', True))
    return s

def public_config():
    with LOCK:
        result = json.loads(json.dumps(CONFIG))
        for s in result['sites']:
            s['has_secret'] = bool(s.pop('secret', ''))
            s['has_headers'] = bool(s.pop('headers', {}))
            s['has_body'] = bool(s.pop('body', ''))
            # URLs may contain query credentials; management page is authenticated.
        cloud = result.get('cookiecloud', {})
        cloud['has_uuid'] = bool(cloud.pop('uuid', ''))
        cloud['has_password'] = bool(cloud.pop('password', ''))
        result['cookiecloud'] = cloud
        result['has_bark'] = bool(result.pop('bark_url', ''))
        return result

def classify(site, status, text):
    if status < 200 or status >= 300:
        return 'failed'
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    failures = [x.strip() for x in site.get('failure_keywords', '未登录,失败,过期').split(',') if x.strip()]
    if any(x in text for x in failures):
        return 'failed'
    if isinstance(obj, dict) and (obj.get('success') is False or ('error' in obj and obj['error'] not in (0, None, False))):
        return 'failed'
    path = site.get('success_path', '').strip()
    if path:
        try:
            value = obj
            for part in path.split('.'):
                value = value[int(part)] if isinstance(value, list) else value[part]
            expected = site.get('success_value', '')
            try:
                expected = json.loads(expected)
            except ValueError:
                pass
            return 'success' if type(value) is type(expected) and value == expected else 'unknown'
        except (KeyError, TypeError, IndexError, ValueError):
            return 'unknown'
    words = [x.strip() for x in site.get('success_keywords', '成功,已签到,已签过').split(',') if x.strip()]
    if any(x in text for x in words) or isinstance(obj, dict) and (obj.get('success') is True or ('error' in obj and type(obj['error']) is int and obj['error'] == 0)):
        return 'success'
    return 'unknown'

def execute(site):
    headers = {'User-Agent': 'Checkin/2.0', **site.get('headers', {})}
    auth = site.get('auth_type')
    key = {'cookie': 'Cookie', 'bearer': 'Authorization', 'api_key': site.get('auth_header', 'X-API-Key')}.get(auth)
    if key:
        headers = {k: v for k, v in headers.items() if k.lower() != key.lower()}
        headers[key] = ('Bearer ' if auth == 'bearer' else '') + site['secret']
    if auth == 'cookiecloud':
        with LOCK:
            cloud = dict(CONFIG.get('cookiecloud', {}))
        value = cookie_header(fetch_cookies(cloud), site['url'])
        headers = {k: v for k, v in headers.items() if k.lower() != 'cookie'}
        headers['Cookie'] = value
    body = None
    if site.get('body_type') != 'none':
        body = site.get('body', '').encode()
        if site.get('body_type') == 'json':
            headers.setdefault('Content-Type', 'application/json')
    # Do not follow redirects with credentials to a different host.
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    req = Request(site['url'], data=body, headers=headers, method=site['method'])
    try:
        with build_opener(NoRedirect).open(req, timeout=20) as response:
            status = response.status
            text = response.read(1024 * 1024).decode('utf-8', errors='replace')
    except HTTPError as e:
        status, text = e.code, e.read(1024 * 1024).decode('utf-8', errors='replace')
    return classify(site, status, text), status

def success_count(site_id, day):
    count = STATE.get('counts', {}).get(site_id, {})
    if count.get('day') == day:
        return count.get('count', 0)
    return 1 if STATE['success'].get(site_id) == day else 0


def run_site(site_id):
    if not RUN_LOCK.acquire(blocking=False):
        return {'result': 'busy', 'message': '有任务执行中，请稍后再试'}
    try:
        with LOCK:
            site = next((dict(s) for s in CONFIG['sites'] if s['id'] == site_id), None)
            if site is None:
                raise ValueError('站点不存在')
            today = datetime.now().strftime('%Y-%m-%d')
            if success_count(site_id, today) >= site.get('daily_limit', 1):
                return {'result': 'skipped', 'message': '今天已达到签到次数，已跳过'}
        try:
            result, status = execute(site)
            message = {'success': '签到成功', 'failed': '签到失败，请检查凭据和结果规则', 'unknown': '响应未匹配成功规则，请人工确认'}[result]
            message += f'（HTTP {status}）'
        except CloudError as error:
            result, status, message = 'failed', None, str(error)
        except Exception:
            result, status, message = 'failed', None, '请求异常，请检查接口地址、网络或证书'
        with LOCK:
            entry = dict(site_id=site_id, name=site['name'], time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), result=result, message=message)
            if result == 'success':
                STATE['counts'][site_id] = {'day': today, 'count': success_count(site_id, today) + 1}
                STATE['success'][site_id] = today
            STATE['logs'] = [entry] + STATE['logs'][:199]
            write_json(DATA / 'state.json', STATE)
            bark = CONFIG.get('bark_url', '')
        if bark:
            try:
                send_bark(bark, site['name'] + '签到', message)
            except (ValueError, PushError):
                pass
        return entry
    finally:
        RUN_LOCK.release()

def tick(now=None):
    now = now or datetime.now()
    day, minute = now.strftime('%Y-%m-%d'), now.strftime('%H:%M')
    with LOCK:
        ids = [s['id'] for s in CONFIG['sites'] if s.get('enabled') and minute in s.get('times', []) and success_count(s['id'], day) < s.get('daily_limit', 1) and STATE['attempts'].get(s['id']) != day + ' ' + minute]
    for sid in ids:
        with LOCK:
            STATE['attempts'][sid] = day + ' ' + minute
            write_json(DATA / 'state.json', STATE)
        result = run_site(sid)
        if result['result'] == 'busy':
            with LOCK:
                STATE['attempts'].pop(sid, None)
                write_json(DATA / 'state.json', STATE)


def scheduler():
    while True:
        try:
            tick()
        except Exception:
            print('调度异常，请检查数据目录权限', flush=True)
        time.sleep(5)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, value, content_type='application/json; charset=utf-8', cookie=None):
        body = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if ADMIN_USERNAME:
            return session_for(self)[1] is not None
        if ADMIN_TOKEN:
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + ADMIN_TOKEN)
        # 本地免令牌模式拒绝非本机 Host，防止 DNS 重绑定读取配置。
        try:
            return urlsplit('http://' + self.headers.get('Host', '')).hostname in ('127.0.0.1', 'localhost', '::1')
        except ValueError:
            return False

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/', '/app.js', '/style.css'):
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8'}[name]
            return self.respond(200, (ROOT / 'static' / name).read_bytes(), mime)
        if path == '/api/auth':
            return self.respond(200, {'mode': 'password' if ADMIN_USERNAME else 'token' if ADMIN_TOKEN else 'local', 'authenticated': self.authorized()})
        if not self.authorized():
            return self.respond(401, {'error': '请先登录'})
        if path == '/api/config':
            session = session_for(self)[1] if ADMIN_USERNAME else None
            if ADMIN_USERNAME and session is None:
                return self.respond(401, {'error': '登录已过期，请重新登录'})
            return self.respond(200, {**public_config(), 'csrf': session['csrf'] if session else CSRF})
        if path == '/api/settings':
            with LOCK:
                return self.respond(200, {'bark_url': CONFIG.get('bark_url', '')})
        if path == '/api/state':
            with LOCK:
                return self.respond(200, {**STATE, 'today': datetime.now().strftime('%Y-%m-%d'), 'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'timezone': os.getenv('TZ', '系统时区')})
        self.respond(404, {'error': '未找到'})

    def login(self):
        if not ADMIN_USERNAME:
            return self.respond(400, {'error': '服务器尚未启用账号登录'})
        if PUBLIC_URL and self.headers.get('Origin') and self.headers['Origin'] != PUBLIC_URL:
            return self.respond(403, {'error': '访问域名不匹配，请使用配置的公网地址'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.respond(415, {'error': '请求格式无效'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            username, password = payload['username'], payload['password']
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            return self.respond(400, {'error': '请输入账号和密码'})
        now, peer = time.time(), self.client_address[0]
        # 不信任客户端可伪造的 X-Forwarded-For；单用户服务按直连地址限速。
        with LOCK:
            for ip in list(LOGIN_FAILURES):
                if now - LOGIN_FAILURES[ip]['since'] >= 300:
                    del LOGIN_FAILURES[ip]
            failure = LOGIN_FAILURES.setdefault(peer, {'since': now, 'count': 0})
            if failure['count'] >= 5:
                return self.respond(429, {'error': '登录失败次数过多，请在 5 分钟后重试'})
            if len(LOGIN_FAILURES) > 1024:
                return self.respond(429, {'error': '登录繁忙，请稍后再试'})
            failure['count'] += 1
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), PASSWORD_SALT, 600000)
        valid = hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode()) & hmac.compare_digest(candidate, PASSWORD_HASH)
        if not valid:
            return self.respond(401, {'error': '账号或密码错误'})
        sid = secrets.token_urlsafe(48)
        with LOCK:
            LOGIN_FAILURES.pop(peer, None)
            for key in list(SESSIONS):
                if SESSIONS[key]['expires'] <= now:
                    del SESSIONS[key]
            old_key, _ = session_for(self)
            SESSIONS.pop(old_key, None)
            if len(SESSIONS) >= 100:
                del SESSIONS[min(SESSIONS, key=lambda key: SESSIONS[key]['expires'])]
            SESSIONS[hashlib.sha256(sid.encode()).hexdigest()] = {'expires': now + SESSION_SECONDS, 'csrf': secrets.token_urlsafe(32)}
        return self.respond(200, {'ok': True}, cookie=session_cookie(sid, SESSION_SECONDS))

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/login':
            return self.login()
        if not self.authorized():
            return self.respond(401, {'error': '请先登录'})
        session = session_for(self)[1] if ADMIN_USERNAME else None
        if ADMIN_USERNAME and session is None:
            return self.respond(401, {'error': '登录已过期，请重新登录'})
        expected = session['csrf'] if session else CSRF
        if self.headers.get('X-CSRF-Token') != expected:
            return self.respond(403, {'error': '页面已失效，请刷新'})
        if path == '/api/logout':
            with LOCK:
                SESSIONS.pop(session_for(self)[0], None)
            return self.respond(200, {'ok': True}, cookie=session_cookie('', 0))
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 100000:
                raise ValueError('请求大小无效')
            payload = json.loads(self.rfile.read(size))
            path = urlsplit(self.path).path
            if path == '/api/site':
                with RUN_LOCK, LOCK:
                    old = next((s for s in CONFIG['sites'] if s['id'] == payload.get('id')), {})
                    for field, default in [('secret', ''), ('headers', {}), ('body', '')]:
                        if payload.get(field) is None or field not in payload:
                            payload[field] = old.get(field, default)
                    site = validate_site(payload)
                    if old and old != site:
                        # 保留当天计数；编辑配置不会意外重新签到。
                        pass
                    CONFIG['sites'] = [site if s['id'] == site['id'] else s for s in CONFIG['sites']]
                    if not old:
                        CONFIG['sites'].append(site)
                    write_json(DATA / 'config.json', CONFIG)
                return self.respond(200, {'ok': True})
            if path == '/api/delete':
                with RUN_LOCK, LOCK:
                    CONFIG['sites'] = [s for s in CONFIG['sites'] if s['id'] != payload['id']]
                    STATE['success'].pop(payload['id'], None)
                    STATE['counts'].pop(payload['id'], None)
                    STATE['attempts'].pop(payload['id'], None)
                    write_json(DATA / 'config.json', CONFIG)
                    write_json(DATA / 'state.json', STATE)
                return self.respond(200, {'ok': True})
            if path == '/api/run':
                return self.respond(200, run_site(payload['id']))
            if path in ('/api/cookiecloud', '/api/cookiecloud/test'):
                with LOCK:
                    cloud = validate_cloud(payload, CONFIG.get('cookiecloud', {})) if not payload.get('clear') else None
                if path.endswith('/test'):
                    try:
                        data = fetch_cookies(cloud or {})
                        domains = sum(isinstance(items, list) and bool(items) for items in data.values())
                        return self.respond(200, {'ok': True, 'message': f'连接及本机解密成功，共 {domains} 个域名分组；未发送签到请求。'})
                    except CloudError as error:
                        return self.respond(400, {'error': str(error)})
                with LOCK:
                    if cloud is None:
                        CONFIG.pop('cookiecloud', None)
                    else:
                        CONFIG['cookiecloud'] = cloud
                    write_json(DATA / 'config.json', CONFIG)
                return self.respond(200, {'ok': True})
            if path in ('/api/settings', '/api/notifications/test'):
                bark = payload.get('bark_url')
                if bark is not None:
                    bark = validate_bark(bark)
                if path == '/api/notifications/test':
                    with LOCK:
                        target = CONFIG.get('bark_url', '') if bark is None else bark
                    try:
                        message = send_bark(target, '签到 · 测试推送', '这是一条测试通知，通知通道已发起测试。时间：' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                        return self.respond(200, {'ok': True, 'message': message + ' 本次测试未保存设置。'})
                    except (ValueError, PushError) as error:
                        return self.respond(400, {'error': str(error)})
                with LOCK:
                    if bark is not None:
                        CONFIG['bark_url'] = bark
                    write_json(DATA / 'config.json', CONFIG)
                return self.respond(200, {'ok': True})
            self.respond(404, {'error': '未找到'})
        except (ValueError, KeyError, TypeError):
            if path.startswith('/api/cookiecloud'):
                return self.respond(400, {'error': '请检查 CookieCloud 服务器地址、UUID、端对端密码及加密算法；编辑已保存凭据时可留空保留'})
            self.respond(400, {'error': '配置无效，请检查 URL、验证凭据、时间和 JSON 格式'})

if __name__ == '__main__':
    host, port = os.getenv('HOST', '127.0.0.1'), int(os.getenv('PORT', '8505'))
    if bool(ADMIN_USERNAME) != bool(ADMIN_PASSWORD) or ADMIN_USERNAME and len(ADMIN_PASSWORD) < 8:
        raise SystemExit('请同时设置 ADMIN_USERNAME 和至少 8 位的 ADMIN_PASSWORD')
    if PUBLIC_URL and (urlsplit(PUBLIC_URL).scheme != 'https' or not urlsplit(PUBLIC_URL).hostname or urlsplit(PUBLIC_URL).path):
        raise SystemExit('PUBLIC_URL 应为不含路径的 HTTPS 公网域名，如 https://checkin.example.com')
    if host not in ('127.0.0.1', 'localhost', '::1') and not ADMIN_USERNAME and len(ADMIN_TOKEN) < 16:
        raise SystemExit('对外监听请设置 ADMIN_USERNAME / ADMIN_PASSWORD，或旧版 ADMIN_TOKEN')
    if hasattr(time, 'tzset'):
        time.tzset()
    load()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f'签到已启动：http://{host}:{port}', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
