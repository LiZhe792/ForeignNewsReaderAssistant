"""Optional native window; run this file directly in PyCharm."""
from threading import Thread
from werkzeug.serving import make_server
from app import create_app


def main():
    try:
        import webview
    except ImportError:
        raise SystemExit("请先安装桌面窗口依赖：python -m pip install -r requirements-desktop.txt")
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        webview.create_window("Margin 知刊", f"http://127.0.0.1:{server.server_port}", width=1280, height=900, min_size=(390, 640))
        webview.start(private_mode=False)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
