"""Reuse verified encoded textures when their rendering inputs are unchanged."""
from pathlib import Path
import functools, hashlib, json, os, re, struct, threading, uuid
from dataclasses import dataclass
from concurrent.futures import Future,ThreadPoolExecutor
from storage_paths import CACHE_ROOT

APP=Path(__file__).resolve().parent
CACHE=CACHE_ROOT/'textures'
LIMIT=2*1024*1024*1024
_prune_lock=threading.Lock()

@dataclass(frozen=True)
class PreparedTexture:
    key: str
    tid: int

@functools.lru_cache(maxsize=512)
def _digest(path,size,mtime):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def digest(path):
    p=Path(path);st=p.stat();return _digest(str(p),st.st_size,st.st_mtime_ns)

def layer_inputs(layer):
    return {k:v for k,v in layer.items() if k not in ['id','name','surfaces','_order','_kind']}

class ExportCache:
    def __init__(self,out,engine):
        self.out=Path(out);self.engine=Path(engine);self.records=[];self.pending={};self.pending_lock=threading.Lock()
        self.image_sources={};self.encoder_buffers={};self.encoder_futures=[];self.encoder_pool=None;self.encoder_error=None;self.encoded_completed=0;self.waiting_for_encoding=False
        self.encoder_total=0;self.abort_event=threading.Event()
        import export_progress
        export_progress.on_finish(self.close)
        self.implementation={name:digest(APP/name) for name in [
            'atlas_baker.py','projection_baker.py','uv_reuse.py','coloring.py',
            'face_builder.py','texture_cache.py','builder.py','appearance_variants.py','compute_runtime.py']}
        self.implementation['texconv.exe']=digest(self.engine/'texconv.exe')

    def key(self,surface,channel,fmt,setting):
        import face_builder as fb
        part=fb.model(surface['unit'])[0]['parts'][surface['part']]
        model=APP/'data/models'/surface['unit'][2:]
        sources={'model':digest(model.with_suffix('.bin')),'descriptor':digest(model.with_suffix('.json')),
                 'source_texture':digest(APP/'data'/part[channel]),
                 'texture_header':digest(self.engine/'Raw/texture/6c1dac34c1e17de4.texture.main')}
        data={k:surface.get(k) for k in ['unit','part','triangles','atlas','preserveSourceDetail']}
        if channel=='color':
            data.update(layers=[layer_inputs(l) for l in surface['layers']],
                        baseColors=surface.get('baseColors',[]),setting=setting)
            for layer in surface['layers']:
                sources[layer['asset']]=digest(fb.library.path_for(layer['asset']))
        code=self.implementation
        if surface.get('atlas',{}).get('kind')=='independent-mech':code=dict(code,mech_appearance=digest(APP/'mech_appearance.py'))
        payload=dict(version=2,resolution=surface['resolution'] if channel=='color' else min(2048,surface['resolution']),channel=channel,format=fmt,surface=data,sources=sources,code=code)
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

    def image_key(self,channel,fmt,inputs):
        payload=dict(version=1,kind='whole-material',channel=channel,format=fmt,inputs=inputs,
                     code=self.implementation,material_surfaces=digest(APP/'material_surfaces.py'))
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

    def prepare_texture(self,surface,channel,fmt,setting,tid,bake):
        key=self.key(surface,channel,fmt,setting)
        size=surface['resolution'] if channel=='color' else min(2048,surface['resolution'])
        return self.prepare_image(key,tid,fmt,bake,(size,size),part=surface['part'],channel=channel)

    def prepare_image(self,key,tid,fmt,bake,size,*,part=-1,channel='color'):
        import export_progress
        export_progress.check_cancelled()
        self._check_encoder()
        if self.encoder_error:raise self.encoder_error
        with self.pending_lock:
            duplicate=key in self.pending
            if duplicate:future=self.pending[key]
            else:future=Future();self.pending[key]=future
        if not duplicate:
            try:
                folder=CACHE/key;dds=folder/'texture.dds';meta=folder/'meta.json';hit=False
                try:
                    info=json.loads(meta.read_text(encoding='utf8'));raw=dds.read_bytes()
                    assert info['key']==key and hashlib.sha256(raw).hexdigest()==info['sha256']
                    assert raw[:4]==b'DDS ' and len(raw)==info['bytes'] and len(raw)>148
                    assert struct.unpack_from('<II',raw,12)==(size[1],size[0])
                    assert struct.unpack_from('<I',raw,128)[0]=={'BC7_UNORM_SRGB':99,'BC5_UNORM':83,'BC7_UNORM':98}[fmt]
                    os.utime(dds,None);hit=True
                except (OSError,ValueError,KeyError,AssertionError,struct.error):
                    raw=None;image=bake();export_progress.check_cancelled();self._check_encoder();assert image.size==size
                    signature=hashlib.sha256(image.tobytes()+str((image.size,fmt)).encode()).hexdigest()
                    with self.pending_lock:
                        image_of=self.image_sources.get(signature)
                        if image_of is None:self.image_sources[signature]=key
                    directory=self.out/'textures/pending';directory.mkdir(parents=True,exist_ok=True)
                    if image_of is None:image.save(directory/(key+'.png'),compress_level=1)
                entry=dict(key=key,fmt=fmt,raw=raw,hit=hit,size=size,image_of=None if hit else image_of);future.set_result(entry)
                if not hit and image_of is None:self._queue_encoder(entry)
            except BaseException as error:
                if not future.done():future.set_exception(error)
                raise
        entry=future.result()
        with self.pending_lock:self.records.append(dict(part=part,channel=channel,cache_hit=entry['hit'] or duplicate,key=key,deduplicated=duplicate,image_reused=entry['image_of'] is not None))
        export_progress.texture_done()
        return PreparedTexture(key,tid)

    def _queue_encoder(self,entry):
        with self.pending_lock:
            if self.encoder_error:raise self.encoder_error
            if self.encoder_pool is None:self.encoder_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='HD2Encoder')
            buffer=self.encoder_buffers.setdefault(entry['fmt'],[]);buffer.append(entry)
            if len(buffer)>=8:
                batch=buffer[:];buffer.clear();self.encoder_futures.append(self.encoder_pool.submit(self._encode_batch,entry['fmt'],batch))

    def _encode_batch(self,fmt,batch):
        import export_progress
        encoded=self.out/'textures/encoded';encoded.mkdir(parents=True,exist_ok=True)
        try:
            export_progress.check_cancelled()
            args=[str(self.engine/'texconv.exe'),'-nologo','-y','-sepalpha','-dx10','-f',fmt,'-m','0','-o',str(encoded)]
            if fmt.endswith('_SRGB'):args+=['-srgbi']
            result=export_progress.run(args+[str(self.out/'textures/pending'/(e['key']+'.png')) for e in batch],cancel_check=self._check_encoder)
            if result.returncode:
                details=(result.stdout+result.stderr).decode('utf8','replace')[-600:]
                raise ValueError('批量纹理编码失败（0x'+format(result.returncode&0xffffffff,'08X')+'）：'+details)
            for entry in batch:
                raw=(encoded/(entry['key']+'.dds')).read_bytes();size=entry['size']
                assert raw[:4]==b'DDS ' and struct.unpack_from('<II',raw,12)==(size[1],size[0])
                assert struct.unpack_from('<I',raw,128)[0]=={'BC7_UNORM_SRGB':99,'BC5_UNORM':83,'BC7_UNORM':98}[fmt]
                entry['raw']=raw;self._store(entry)
            with self.pending_lock:
                self.encoded_completed+=len(batch)
                if self.waiting_for_encoding:export_progress.update('正在完成贴图压缩…',phase='encoding',completed=self.encoded_completed,total=self.encoder_total)
        except BaseException as error:
            self.encoder_error=error;raise

    def _check_encoder(self):
        if self.abort_event.is_set():raise RuntimeError('导出已停止')

    def close(self,error=''):
        if error:self.abort_event.set()
        if self.encoder_pool:self.encoder_pool.shutdown(wait=True,cancel_futures=True)

    def _store(self,entry):
        raw=entry['raw'];folder=CACHE/entry['key'];folder.mkdir(parents=True,exist_ok=True)
        token=uuid.uuid4().hex;pending=folder/(token+'.dds');pending.write_bytes(raw);pending.replace(folder/'texture.dds')
        info=dict(key=entry['key'],sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),format=entry['fmt'])
        pending=folder/(token+'.json');pending.write_text(json.dumps(info),encoding='utf8');pending.replace(folder/'meta.json')

    def flush(self):
        import export_progress
        entries=[f.result() for f in self.pending.values()]
        self.encoder_total=sum(not e['hit'] and e['image_of'] is None for e in entries)
        with self.pending_lock:
            self.waiting_for_encoding=True
            if self.encoder_total:export_progress.update('正在完成贴图压缩…',phase='encoding',completed=self.encoded_completed,total=self.encoder_total)
            if self.encoder_pool:
                for fmt,buffer in self.encoder_buffers.items():
                    if buffer:self.encoder_futures.append(self.encoder_pool.submit(self._encode_batch,fmt,buffer[:]));buffer.clear()
        try:
            for future in self.encoder_futures:future.result()
        finally:
            if self.encoder_pool:self.encoder_pool.shutdown(wait=True,cancel_futures=True)
        for entry in entries:
            if entry['image_of'] is not None:
                entry['raw']=self.pending[entry['image_of']].result()['raw'];self._store(entry)
        for record in self.records:
            if record['key'] in self.pending:record['dds_sha256']=hashlib.sha256(self.pending[record['key']].result()['raw']).hexdigest()

    def resolve(self,resource):
        if not isinstance(resource,PreparedTexture):return resource
        import face_builder as fb
        return fb.texture_from_dds(self.pending[resource.key].result()['raw'],resource.tid)

    def texture(self,surface,channel,fmt,setting,tid,bake,encode):
        import face_builder as fb
        key=self.key(surface,channel,fmt,setting);folder=CACHE/key;dds=folder/'texture.dds';meta=folder/'meta.json'
        hit=False
        try:
            info=json.loads(meta.read_text(encoding='utf8'));raw=dds.read_bytes()
            assert info['key']==key and hashlib.sha256(raw).hexdigest()==info['sha256']
            assert raw[:4]==b'DDS ' and len(raw)==info['bytes'] and len(raw)>148
            size=surface['resolution'] if channel=='color' else min(2048,surface['resolution'])
            assert struct.unpack_from('<II',raw,12)==(size,size)
            assert struct.unpack_from('<I',raw,128)[0]=={'BC7_UNORM_SRGB':99,'BC5_UNORM':83,'BC7_UNORM':98}[fmt]
            resource=fb.texture_from_dds(raw,tid);hit=True
            os.utime(dds,None)
        except (OSError,ValueError,KeyError,AssertionError,struct.error):
            image=bake();resource=encode(image)
            encoded=self.out/'textures/encoded'/f'{tid:016x}.dds'
            raw=encoded.read_bytes();folder.mkdir(parents=True,exist_ok=True)
            # Files are generated cache data, never source images/projects.
            # Atomic replacement also recovers interrupted/invalid entries.
            token=uuid.uuid4().hex;pending=folder/(token+'.dds');pending.write_bytes(raw);pending.replace(dds)
            info=dict(key=key,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),format=fmt)
            pending=folder/(token+'.json');pending.write_text(json.dumps(info),encoding='utf8');pending.replace(meta)
            # Entries in the active project stay available until the export
            # ends. The caller prunes the shared cache once per export.
        self.records.append(dict(part=surface['part'],channel=channel,cache_hit=hit,key=key,dds_sha256=info['sha256']))
        import export_progress
        export_progress.texture_done()
        return resource

    def prune(self):
        files=sorted(CACHE.glob('*/texture.dds'),key=lambda p:p.stat().st_mtime,reverse=True)
        total=0;root=CACHE.resolve()
        for p in files:
            total+=p.stat().st_size
            if total<=LIMIT:continue
            folder=p.parent.resolve()
            assert folder.parent==root and re.fullmatch('[0-9a-f]{64}',folder.name)
            p.unlink();meta=folder/'meta.json'
            if meta.exists():meta.unlink()
            if not any(folder.iterdir()):folder.rmdir()

    def report(self):
        return dict(hits=sum(r['cache_hit'] for r in self.records),
                    misses=sum(not r['cache_hit'] for r in self.records),
                    pixel_identical_images_reused=sum(r.get('image_reused',False) for r in self.records),
                    native_encoder_images=self.encoder_total,native_encoder_calls=len(self.encoder_futures),
                    all_hits_hash_checked=True,records=sorted(self.records,key=lambda r:(r['part'],r['channel'])))
