"""Full instance backup, including account writing, groups, recordings and caches.

Stop the application before taking a whole-instance snapshot. Restore always goes
to a new directory, so it cannot overwrite the current learning environment.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def checksum(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def create_backup(source, destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_dir():raise ValueError('数据目录不存在。')
    if destination.is_relative_to(source):raise ValueError('完整备份应保存在数据目录以外。')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise ValueError('目标文件已存在，请选择新文件名。')
    files={}
    staging=destination.with_suffix(destination.suffix+'.partial')
    if staging.exists():raise ValueError('存在未完成的同名备份，请选择新文件名。')
    try:
        with tempfile.TemporaryDirectory() as temp,zipfile.ZipFile(staging,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob('*')):
                if not path.is_file() or path.is_symlink() or path.name.endswith(('-wal','-shm')):continue
                relative=path.relative_to(source)
                if 'backups' in relative.parts or 'migration-backups' in relative.parts:continue
                saved=path
                with path.open('rb') as stream:database=stream.read(16)==b'SQLite format 3\x00'
                if database:
                    saved=Path(temp)/('snapshot-'+str(len(files))+'.sqlite3')
                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(saved)) as target:
                        original.backup(target)
                        if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('数据库完整性检查失败：'+str(relative))
                files[relative.as_posix()]={'sha256':checksum(saved),'size':saved.stat().st_size}
                archive.write(saved,'instance/'+relative.as_posix())
            archive.writestr('manifest.json',json.dumps({'format':'margin-instance-v1','created_at':datetime.now(timezone.utc).isoformat(),'files':files},ensure_ascii=False))
        os.replace(staging,destination)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    return {'path':str(destination),'files':len(files),'size':destination.stat().st_size}


def restore_backup(archive_path,destination=None):
    destination=Path(destination).resolve() if destination else None
    if destination and destination.exists():raise ValueError('恢复目录必须是尚不存在的新目录，当前数据不会被覆盖。')
    with zipfile.ZipFile(archive_path) as archive:
        entries=archive.infolist()
        if len(entries)>20000 or sum(e.file_size for e in entries)>2*1024**3:raise ValueError('备份超过2GB或文件过多。')
        if len({e.filename for e in entries})!=len(entries):raise ValueError('备份包含重复文件名。')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!='margin-instance-v1' or not isinstance(manifest.get('files'),dict):raise ValueError('备份格式无效。')
        expected={'manifest.json',*('instance/'+p for p in manifest['files'])}
        if set(e.filename for e in entries)!=expected:raise ValueError('备份文件清单不一致。')
        for name in manifest['files']:
            pure=PurePosixPath(name)
            if pure.is_absolute() or '..' in pure.parts or ':' in name or '\\' in name or not pure.parts:raise ValueError('备份路径无效。')
        if destination:destination.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent if destination else None) as temp:
            root=Path(temp)/'restored';root.mkdir()
            for name,meta in manifest['files'].items():
                target=root.joinpath(*PurePosixPath(name).parts)
                if not target.resolve().is_relative_to(root.resolve()):raise ValueError('备份路径越界。')
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open('instance/'+name) as src,target.open('xb') as out:
                    import shutil
                    shutil.copyfileobj(src,out,1024*1024)
                if target.stat().st_size!=meta['size'] or checksum(target)!=meta['sha256']:raise ValueError('备份校验失败：'+name)
            if destination:os.replace(root,destination)
    return {'files':len(manifest['files']),'created_at':manifest['created_at'],'restored_to':str(destination) if destination else None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    save=sub.add_parser('backup');save.add_argument('--source',default=str(Path(__file__).parent/'instance'));save.add_argument('--output',required=True)
    restore=sub.add_parser('restore');restore.add_argument('archive');restore.add_argument('--into',help='省略时只验证，不写入文件')
    args=parser.parse_args()
    result=create_backup(args.source,args.output) if args.command=='backup' else restore_backup(args.archive,args.into)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
