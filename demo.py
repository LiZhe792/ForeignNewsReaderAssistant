"""Start an isolated demo without changing the real learning data."""
import argparse
import os
from datetime import datetime
from pathlib import Path
from app import create_app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',help='A new directory; existing directories are never overwritten')
    parser.add_argument('--port',type=int,default=5001)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('端口需为 1–65535')
    directory=Path(args.directory or Path(__file__).parent/'instance'/('demo-'+datetime.now().strftime('%Y%m%d-%H%M%S'))).resolve()
    if directory.exists():parser.error('演示目录已存在，请选择新目录。')
    directory.mkdir(parents=True)
    os.environ['MARGIN_DATA_DIR']=str(directory)
    app=create_app({'DATABASE':str(directory/'margin.sqlite3')})
    print('独立演示数据：',directory)
    app.run(host='127.0.0.1',port=args.port,debug=False,threaded=True)


if __name__=='__main__':main()
