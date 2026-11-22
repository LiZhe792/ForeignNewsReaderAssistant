"""Small public-URL client with redirect, DNS, timeout and size boundaries.

Connect to a validated IP, retaining the original TLS hostname, so DNS rebinding
cannot turn an article import into a request to the local network.
"""

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import quote, urljoin, urlsplit


class FetchError(ValueError):
    pass


def public_address(host, port):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("无法解析该网站地址，请检查链接或使用粘贴导入。") from exc
    if not addresses:
        raise FetchError("网站地址不可用。")
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global or address.is_multicast:
            raise FetchError("仅支持公开网站，不允许访问本机、内网或保留地址。")
    return addresses[0][4][0]


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPS(PinnedHTTP):
    def connect(self):
        raw_socket = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def fetch_public(url, max_bytes=2_000_000):
    from flask import has_app_context
    if has_app_context():
        from .data_management import offline
        if offline():
            raise FetchError('当前为离线模式，网页导入和在线查询已暂停。可使用本地文章、离线词典或粘贴导入。')
    deadline = time.monotonic() + 25
    for _ in range(5):
        if has_app_context() and offline():
            raise FetchError('离线模式已暂停外部请求。')
        connection = None
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
                raise FetchError("请输入完整的 http:// 或 https:// 公开文章链接。")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if port not in (80, 443):
                raise FetchError("文章链接仅支持标准 HTTP / HTTPS 端口。")
            host = parsed.hostname.encode("idna").decode("ascii")
            address = public_address(host, port)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("网站响应超时，请改用粘贴导入。")
            connection_type = PinnedHTTPS if parsed.scheme == "https" else PinnedHTTP
            connection = connection_type(host, port, address, min(10, remaining))
            path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                path += "?" + quote(parsed.query, safe="=&%/:?@!$'()*+,;~-._")
            connection.request("GET", path, headers={
                "User-Agent": "MarginReader/1.0 (personal reading assistant)",
                "Accept": "text/html, application/json;q=0.9, text/plain;q=0.8",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise FetchError("网站返回了无效跳转。")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise FetchError(f"网站返回 HTTP {response.status}，可尝试粘贴正文。")
            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise FetchError("网页过大，请改用粘贴导入。")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("网站响应超时，请改用粘贴导入。")
                if connection.sock:
                    connection.sock.settimeout(min(10, remaining))
                chunk = response.read1(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("网页过大，请改用粘贴导入。")
            return b"".join(chunks), response.getheader("Content-Type", ""), url
        except FetchError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise FetchError("获取网页失败，请检查网络，或使用粘贴导入。") from exc
        finally:
            if connection:
                connection.close()
    raise FetchError("网站跳转次数过多，请使用最终文章链接。")
