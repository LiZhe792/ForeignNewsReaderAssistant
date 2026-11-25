"""Run in PyCharm to inspect dependencies, data paths and a chosen local port."""
import argparse
import importlib.util
import json
import os
import shutil
import socket
from pathlib import Path


def inspect(port=5000):
    root=Path(os.getenv('MARGIN_DATA_DIR',Path(__file__).parent/'instance')).resolve()
    modules={'flask':'基础服务','bs4':'网页提取','charset_normalizer':'编码识别','tzdata':'时区',
             'edge_tts':'在线朗读','pymupdf':'PDF导入','rapidocr_onnxruntime':'OCR','fastembed':'语义搜索',
             'openpyxl':'Excel导出','reportlab':'PDF导出','genanki':'Anki','webview':'桌面窗口','faster_whisper':'本机语音识别'}
    dependencies={label:importlib.util.find_spec(module) is not None for module,label in modules.items()}
    with socket.socket() as sock:
        try:sock.bind(('127.0.0.1',port));available=True
        except OSError:available=False
    location=root if root.exists() else root.parent
    return {'data_directory':str(root),'database':os.getenv('MARGIN_DATABASE',str(root/'margin.sqlite3')),
            'free_disk_mb':shutil.disk_usage(location).free//1024**2,'port':port,'port_available':available,
            'dependencies':dependencies,
            'speech_model_available':(Path(os.getenv('MARGIN_WHISPER_MODEL',root/'whisper-tiny.en'))/'model.bin').is_file(),
            'note':'未显示密钥。模型是否能加载、在线服务能否连接需另行验证。'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=5000)
    print(json.dumps(inspect(parser.parse_args().port),ensure_ascii=False,indent=2))
