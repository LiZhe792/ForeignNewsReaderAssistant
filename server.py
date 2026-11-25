"""Serve account-isolated learning to trusted devices over HTTPS."""
import argparse
import os
from app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--hostname", default="localhost")
    parser.add_argument("--port", type=int, default=5443)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    shared = args.host not in ("127.0.0.1", "::1", "localhost")
    if shared and not (args.tls_cert and args.tls_key):
        parser.error("共享服务必须指定可信 HTTPS 证书和私钥；请勿把个人学习库直接暴露为未加密服务。")
    if not 1 <= args.port <= 65535:
        parser.error("端口无效")
    app = create_app({"LEARNING_SHARED":shared,"WRITING_SHARED":shared,
                      "WRITING_SIGNUP_CODE":os.getenv("MARGIN_SIGNUP_CODE",""),
                      "TRUSTED_HOSTS":list({args.hostname,args.host,"localhost","127.0.0.1","[::1]"})})
    ssl = (args.tls_cert, args.tls_key) if args.tls_cert and args.tls_key else None
    app.run(host=args.host,port=args.port,ssl_context=ssl,debug=False,threaded=True)


if __name__ == "__main__":
    main()
