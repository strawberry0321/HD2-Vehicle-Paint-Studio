"""Package the program only; user work, logs and build records are excluded."""
from pathlib import Path
import argparse,hashlib,json,zipfile

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('program',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    root=args.program.resolve();target=args.output or root.parent/'载具替换工具_V19_Windows_x64.zip'
    if target.exists():parser.error('Archive already exists; choose a new output')
    target.parent.mkdir(parents=True,exist_ok=True)
    allowed={'app','runtime','licenses','载具替换工具.exe','settings.json','使用说明.txt','LICENSE','AUTHORSHIP.md','THIRD_PARTY.md'}
    rows=[]
    with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for source in sorted(root.rglob('*')):
            relative=source.relative_to(root)
            if not source.is_file() or relative.parts[0] not in allowed:continue
            if any(p in ('__pycache__','diagnostics','reports','test_data') for p in relative.parts) or source.suffix.lower() in ('.pyc','.log','.pstats','.pending'):continue
            with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            archive.write(source,relative.as_posix());rows.append((relative.as_posix(),digest))
        archive.writestr('mod/','')
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name,digest in rows:
            with archive.open(name) as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==digest,name
        assert json.loads(archive.read('settings.json'))['dataDirectory']=='../载具涂装数据'
        assert 'B站：StarAiri' in archive.read('app/web/author-watermark.js').decode('utf8')
        assert '严禁更改' in archive.read('app/web/author-watermark.js').decode('utf8')
    with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    sums=target.parent/'SHA256SUMS.txt';sums.write_text(digest+'  '+target.name+'\n',encoding='utf8')
    print(json.dumps(dict(archive=str(target),sha256=digest,files_verified=len(rows),bytes=target.stat().st_size),ensure_ascii=True))

if __name__=='__main__':main()
