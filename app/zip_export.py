"""Keep finished export directories ZIP-only; put authoring records inside ZIP."""
from pathlib import Path
import hashlib,io,json,shutil,uuid,zipfile
from PIL import Image
from storage_paths import DATA_ROOT
import export_destination
import export_progress

def checked_write(archive,path,name,compress_type=None):
    export_progress.check_cancelled()
    info=zipfile.ZipInfo.from_file(path,name);info.compress_type=archive.compression if compress_type is None else compress_type;info._compresslevel=archive.compresslevel
    with Path(path).open('rb') as source,archive.open(info,'w') as target:
        while block:=source.read(4*1024*1024):
            export_progress.check_cancelled();target.write(block)

def workspace(root):
    folder=Path(root)/'tmp/export_work'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    return folder

def finish(work,archive,parts,engine,stats):
    work=Path(work);archive=Path(archive);engine=Path(engine)
    source=[]
    for p in (work/'source/Raw').rglob('*'):
        export_progress.check_cancelled()
        if not p.is_file():continue
        origin=engine/'Raw'/p.relative_to(work/'source/Raw');digest=hashlib.sha256(p.read_bytes()).hexdigest()
        assert hashlib.sha256(origin.read_bytes()).hexdigest()==digest
        source.append(dict(source=str(origin),copy=p.relative_to(work).as_posix(),sha256=digest))
    for journal in ['image_manifest.json','dependency_manifest.json']:
        manifest=work/'source'/journal
        if not manifest.exists():continue
        for record in json.loads(manifest.read_text(encoding='utf8')):
            export_progress.check_cancelled()
            original=Path(record['source']);copied=work/record['copy']
            assert copied.resolve().is_relative_to((work/'source').resolve())
            assert hashlib.sha256(original.read_bytes()).hexdigest()==record['sha256']
            assert hashlib.sha256(copied.read_bytes()).hexdigest()==record['sha256']
            source.append(dict(source=str(original),copy=Path(record['copy']).as_posix(),sha256=record['sha256']))
    (work/'source/manifest.json').write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf8')
    preview=Image.open(work/'package/preview.png');preview.load();assert preview.format=='PNG'
    audit=dict(status='passed',preview_in_zip=True,png_decoded=True,patch_hashes_unchanged=True,source_hashes_rechecked=len(source),game_directory_written=False,zip_only_output=True,authoring_records_inside_zip=True,authoring_bundle='project.hd2project',no_top_level_directories=True,**stats)
    (work/'reports/package_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    # HD2Arsenal treats top-level directories as optional components and then
    # ignores root patches. Keep authoring records in one ordinary root file.
    bundle=work/'project.hd2project'
    with zipfile.ZipFile(bundle,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        checked_write(z,work/'project.json','__project/project.json')
        for name in ['source','reports']:
            for p in sorted((work/name).rglob('*')):
                if p.is_file():checked_write(z,p,'__project/'+p.relative_to(work).as_posix())
    pending=archive.parent/('.'+archive.name+'.'+uuid.uuid4().hex+'.pending');owned=False
    try:
        with pending.open('xb') as stream:
            owned=True
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
                for p in sorted((work/'package').iterdir()):
                    assert p.is_file(), 'MOD root must not contain option folders'
                    checked_write(z,p,p.name)
                checked_write(z,bundle,bundle.name,compress_type=zipfile.ZIP_STORED)
        with zipfile.ZipFile(pending) as z:
            assert z.testzip() is None
            assert all('/' not in name and '\\' not in name for name in z.namelist())
            preview=Image.open(io.BytesIO(z.read('preview.png')));preview.load();assert preview.format=='PNG'
            for name,data in parts.items():assert hashlib.sha256(z.read(name)).digest()==hashlib.sha256(data).digest()
            with zipfile.ZipFile(io.BytesIO(z.read(bundle.name))) as project:
                assert project.testzip() is None
                assert 'HD2VehicleProject' in project.read('__project/project.json').decode('utf8')
        export_progress.check_cancelled();export_destination.publish(pending,archive)
    except OSError as error:
        raise export_destination.DestinationError('压缩包无法保存，请检查文件夹路径和写入权限：'+str(error)) from error
    finally:
        if owned and pending.exists():pending.unlink()
    root=DATA_ROOT;resolved=work.resolve()
    assert resolved.is_relative_to(root/'tmp/export_work') and resolved.name!= 'export_work'
    shutil.rmtree(resolved)
    assert archive.is_file()
    return archive
