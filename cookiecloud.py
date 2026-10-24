"""CookieCloud 密文下载、本机解密与按请求 URL 选择 Cookie。"""
import base64
import hashlib
import json
import re
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class CloudError(Exception):
    """只允许向页面/日志返回不含凭据的固定错误说明。"""


def validate_config(value, old=None):
    old = old or {}
    if not isinstance(value, dict):
        raise ValueError('CookieCloud 配置无效')
    result = {key: value.get(key) if value.get(key) is not None else old.get(key, '')
              for key in ('server_url', 'uuid', 'password', 'crypto_type')}
    result['server_url'] = str(result['server_url']).strip().rstrip('/')
    u = urlsplit(result['server_url'])
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('CookieCloud 地址无效')
    if not isinstance(result['uuid'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', result['uuid']):
        raise ValueError('CookieCloud UUID 无效')
    if not isinstance(result['password'], str) or not result['password']:
        raise ValueError('CookieCloud 密码不能为空')
    result['crypto_type'] = result['crypto_type'] or 'auto'
    if result['crypto_type'] not in ('auto', 'legacy', 'aes-128-cbc-fixed'):
        raise ValueError('CookieCloud 算法无效')
    return result


def decrypt(envelope, uuid, password, algorithm='auto'):
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
        raw = base64.b64decode(envelope['encrypted'], validate=True)
        if algorithm == 'auto':
            algorithm = envelope.get('crypto_type') or ('legacy' if raw.startswith(b'Salted__') else 'aes-128-cbc-fixed')
        seed = hashlib.md5((uuid + '-' + password).encode()).hexdigest()[:16].encode()
        if algorithm == 'legacy':
            if not raw.startswith(b'Salted__') or len(raw) < 32:
                raise ValueError()
            salt = raw[8:16]
            derived, previous = b'', b''
            while len(derived) < 48:
                previous = hashlib.md5(previous + seed + salt).digest()
                derived += previous
            key, iv, ciphertext = derived[:32], derived[32:48], raw[16:]
        elif algorithm == 'aes-128-cbc-fixed':
            key, iv, ciphertext = seed, bytes(16), raw
        else:
            raise ValueError()
        decoded = json.loads(unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext), 16).decode())
        if not isinstance(decoded, dict) or not isinstance(decoded.get('cookie_data'), dict):
            raise ValueError()
        return decoded['cookie_data']
    except ImportError:
        raise CloudError('缺少 CookieCloud 解密依赖，请按 README 安装 requirements.txt 或使用新版镜像') from None
    except Exception:
        raise CloudError('CookieCloud 解密失败，请检查 UUID、端对端密码和加密算法') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_cookies(config):
    try:
        config = validate_config(config)
    except (ValueError, TypeError):
        raise CloudError('请先在 CookieCloud 设置中保存服务器地址、UUID 和端对端密码') from None
    # 仅下载密文；密码和解密后的 Cookie 不发送到 CookieCloud 服务器。
    url = config['server_url'] + '/get/' + quote(config['uuid'], safe='')
    try:
        with build_opener(NoRedirect).open(Request(url, headers={'Accept': 'application/json'}), timeout=20) as response:
            body = response.read(10 * 1024 * 1024 + 1)
            if len(body) > 10 * 1024 * 1024:
                raise ValueError()
            envelope = json.loads(body)
    except Exception:
        raise CloudError('CookieCloud 获取失败，请检查服务器地址、路径、UUID、网络及插件是否已上传') from None
    return decrypt(envelope, config['uuid'], config['password'], config['crypto_type'])


def cookie_header(data, url, now=None):
    u = urlsplit(url)
    host, path = (u.hostname or '').lower().rstrip('.'), u.path or '/'
    now = time.time() if now is None else now
    matched, seen = [], set()
    for bucket, cookies in data.items():
        if not isinstance(cookies, list):
            continue
        for item in cookies:
            if not isinstance(item, dict):
                continue
            raw_domain = str(item.get('domain') or bucket).lower().rstrip('.')
            domain = raw_domain.lstrip('.')
            host_only = item.get('hostOnly', not raw_domain.startswith('.'))
            if host != domain and (host_only or not host.endswith('.' + domain)):
                continue
            if item.get('secure') and u.scheme != 'https':
                continue
            if item.get('partitionKey'):
                continue  # 分区 Cookie 需要额外的浏览器上下文，不能盲目附加。
            expiry = item.get('expirationDate', item.get('expires'))
            try:
                if not item.get('session') and expiry is not None and float(expiry) >= 0 and float(expiry) <= now:
                    continue
            except (TypeError, ValueError):
                continue
            cookie_path = item.get('path') or '/'
            if not isinstance(cookie_path, str) or not (path == cookie_path or path.startswith(cookie_path) and (cookie_path.endswith('/') or path[len(cookie_path):].startswith('/'))):
                continue
            name, value = item.get('name'), item.get('value')
            if not isinstance(name, str) or not isinstance(value, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(c in value for c in ('\r', '\n', ';')):
                continue
            identity = (domain, cookie_path, name, value)
            if identity not in seen:
                matched.append((cookie_path, name + '=' + value))
                seen.add(identity)
    if not matched:
        raise CloudError('CookieCloud 中没有适用于签到 URL 的有效 Cookie，请核对域名、路径、HTTPS 和过期时间，并在浏览器重新登录上传')
    return '; '.join(pair for _, pair in sorted(matched, key=lambda item: -len(item[0])))
