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
    candidates = []
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global or address.is_multicast:
            raise FetchError("仅支持公开网站，不允许访问本机、内网或保留地址。")
        candidates.append((address.version, str(address)))
    # Prefer IPv4 so dual-stack hosts without an IPv6 route still connect.
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


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


def fetch_document(url, max_bytes=2_000_000, extra_headers=None, allow_not_modified=False):
    """Fetch a public document, returning status, body, headers and final URL.

    Conditional headers let feed subscriptions ask for 304 Not Modified, which
    is reported as status 304 with an empty body instead of an error.
    """
    deadline = time.monotonic() + 25
    for _ in range(5):
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
            request_headers = {
                "User-Agent": "MarginReader/1.0 (personal reading assistant)",
                "Accept": "text/html, application/xhtml+xml, application/rss+xml, application/atom+xml, "
                          "application/xml;q=0.9, text/plain;q=0.8, application/json;q=0.7",
                "Accept-Encoding": "identity",
            }
            for key, value in (extra_headers or {}).items():
                if value:
                    request_headers[key] = value
            connection.request("GET", path, headers=request_headers)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise FetchError("网站返回了无效跳转。")
                url = urljoin(url, location)
                continue
            header_map = {}
            try:
                header_map = {str(key).lower(): str(value) for key, value in response.getheaders()}
            except (TypeError, ValueError):
                header_map = {}
            if response.status == 304 and allow_not_modified:
                return 304, b"", header_map, "", url
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
            return 200, b"".join(chunks), header_map, response.getheader("Content-Type", ""), url
        except FetchError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise FetchError("获取网页失败，请检查网络，或使用粘贴导入。") from exc
        finally:
            if connection:
                connection.close()
    raise FetchError("网站跳转次数过多，请使用最终文章链接。")


def fetch_public(url, max_bytes=2_000_000, extra_headers=None):
    status, body, _headers, content_type, final_url = fetch_document(
        url, max_bytes=max_bytes, extra_headers=extra_headers
    )
    return body, content_type, final_url
