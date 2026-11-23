"""Serve the account writing workspace without exposing the local reading library."""

import argparse
import ipaddress
import os
import ssl
from pathlib import Path

from flask import Flask, redirect, url_for

from newsreader.writing_workspace import default_database, install

ROOT = Path(__file__).resolve().parent


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        WRITING_DATABASE=os.getenv("MARGIN_WRITING_DATABASE") or
        default_database(ROOT / "instance" / "margin.sqlite3"),
        WRITING_ONLY=True,
        WRITING_SHARED=True,
        WRITING_SIGNUP_CODE=os.getenv("MARGIN_WRITING_SIGNUP_CODE", ""),
        MAX_CONTENT_LENGTH=512 * 1024,
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
    )
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False
    install(app)

    @app.get("/")
    def index():
        return redirect(url_for("workspace.writing_page"))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IP address to bind")
    parser.add_argument("--port", type=int, default=5443)
    parser.add_argument("--hostname", action="append", default=[], help="Allowed device-facing DNS name or IP")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError:
        parser.error("--host must be an IP address")
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be provided together")
    if not address.is_loopback and (not args.tls_cert or not args.hostname):
        parser.error("Cross-device access requires --hostname, --tls-cert and --tls-key")
    context = None
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
    hostnames = ["localhost", "127.0.0.1", "[::1]", *args.hostname]
    application = create_app({"TRUSTED_HOSTS": hostnames, "WRITING_SHARED": bool(context)})
    application.run(host=args.host, port=args.port, ssl_context=context, threaded=True, debug=False)


if __name__ == "__main__":
    main()
