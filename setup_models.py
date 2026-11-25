"""Optional, explicit model download; normal app startup never downloads models."""
import argparse
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--speech',action='store_true',help='下载 faster-whisper tiny.en 英语词级识别模型')
    parser.add_argument('--semantic',action='store_true',help='下载英文语义搜索模型')
    parser.add_argument('--directory',default=str(Path(os.getenv('MARGIN_DATA_DIR',Path(__file__).parent/'instance'))/'whisper-tiny.en'))
    args=parser.parse_args()
    if not args.speech and not args.semantic:parser.error('请选择 --speech 或 --semantic')
    if args.speech:
        from huggingface_hub import snapshot_download
        path=snapshot_download('Systran/faster-whisper-tiny.en',local_dir=args.directory,
                               allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.*'],max_workers=2)
        from faster_whisper import WhisperModel
        WhisperModel(path,device='cpu',compute_type='int8',local_files_only=True)
        print('Speech model loaded:',path)
    if args.semantic:
        from newsreader.search import embed
        print('Semantic model dimension:',len(embed(['A reader learns from an article.'])[0]))


if __name__=='__main__':main()
