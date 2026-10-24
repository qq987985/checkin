"""Bark 推送：只返回通用结果，不回传含 Key 的地址或服务器原文。"""
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

class PushError(Exception):
    pass


def validate_bark(value):
    if not isinstance(value, str):
        raise ValueError('Bark 地址无效')
    value = value.strip()
    if not value:
        return ''
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or any(c in value for c in ('\r', '\n')):
        raise ValueError('Bark 地址应为完整 HTTPS URL')
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send_bark(url, title, message):
    url = validate_bark(url)
    if not url:
        raise ValueError('请先填写 Bark 推送地址')
    data = json.dumps({'title': title, 'body': message}, ensure_ascii=False).encode()
    request = Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            status = response.status
            result = json.loads(response.read(65536))
    except HTTPError as error:
        raise PushError(f'Bark 推送失败（HTTP {error.code}），请检查推送地址与 Key') from None
    except Exception:
        raise PushError('Bark 请求失败或响应无效，请检查网络、证书和推送地址') from None
    if not 200 <= status < 300 or not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 200:
        raise PushError('Bark 未确认推送成功，请检查地址与 Key，并核对 Bark 服务状态')
    return 'Bark 服务已确认接收，请检查手机通知；这不等于已确认手机送达。'
