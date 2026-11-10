"""Local HD2 texture authoring. No reads/writes to the installed game."""
from pathlib import Path
import base64, copy, gzip, hashlib, io, json, math, re, shutil, struct, subprocess, threading, zipfile
from collections import Counter
from compute_runtime import np
from PIL import Image
from coloring import recolor_linear, recolor_srgb
from archive_layout import validate_archive
from storage_paths import DATA_ROOT

APP=Path(__file__).resolve().parent
ENGINE=APP/'engine'
DATA=APP/'data'
LOCK=threading.Lock()
TEXTURE=0xcd4238c6a0c69e32
MATERIAL=0xeac0b497876adedf
BASE='9ba626afa44a3aa3.patch_0'
PLAN=json.loads((ENGINE/'plan.json').read_text(encoding='utf8'))
CAT=json.loads((DATA/'catalog.json').read_text(encoding='utf8'))
GROUPS={g['id']:g for g in CAT['groups']}

def sha(b):return hashlib.sha256(b).hexdigest()
def align(n,a):return (n+a-1)//a*a
def dump(p,data):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
def linear(a):a=np.asarray(a,dtype=float);return np.where(a<=.04045,a/12.92,((a+.055)/1.055)**2.4)
def color(s):
    if not isinstance(s,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',s):raise ValueError('颜色格式有误')
    return np.array([int(s[i:i+2],16) for i in (1,3,5)],float)/255

def hash_id(s):
    b=s.encode();mask=(1<<64)-1;m=0xc6a4a7935bd1e995;h=len(b)*m&mask
    for i in range(0,len(b)//8*8,8):
        k=int.from_bytes(b[i:i+8],'little');k=k*m&mask;k^=k>>47;k=k*m&mask;h^=k;h=h*m&mask
    tail=b[len(b)//8*8:]
    if tail:h^=int.from_bytes(tail,'little');h=h*m&mask
    h^=h>>47;h=h*m&mask;h^=h>>47
    return h

def image_bytes(url,max_pixels=20_000_000):
    if not isinstance(url,str) or not url.startswith('data:image/'):raise ValueError('请提供图像')
    raw=base64.b64decode(url.split(',',1)[1],validate=True)
    if len(raw)>20*1024*1024:raise ValueError('单张图片请控制在20MB以内')
    im=Image.open(io.BytesIO(raw));im.verify()
    im=Image.open(io.BytesIO(raw))
    if im.width*im.height>max_pixels:raise ValueError('图片分辨率过大')
    im.load()
    return raw,im

def import_image(payload):
    raw,im=image_bytes(payload['image'])
    ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}.get(im.format)
    if not ext:raise ValueError('请使用 PNG、JPG 或 WebP 图片')
    digest=sha(raw);out=DATA_ROOT/'imports'/digest
    out.mkdir(parents=True,exist_ok=True)
    p=out/('original'+ext)
    if not p.exists():
        p.write_bytes(raw)
        dump(out/'source.json',dict(sourceName=payload.get('name','导入图像'),sourcePath='浏览器文件选择器仅提供文件名；完整原始字节已保留',copy=str(p),sha256=digest,bytes=len(raw)))
    assert sha(p.read_bytes())==digest
    return dict(path=str(p),sha256=digest)

def state_ok(state):
    if not isinstance(state,dict) or state.get('format')!='HD2VehicleProject' or state.get('version')!=1:raise ValueError('不支持的工程格式')
    if state.get('vehicle') not in {u['id'] for u in CAT['vehicles']}:raise ValueError('未知载具')
    if len(state.get('layers',[]))>100:raise ValueError('每个工程最多100张贴花')
    for gid,v in state.get('groups',{}).items():
        if gid not in GROUPS or v.get('mode') not in ['original','solid','native']:raise ValueError('部件设置有误')
        color(v.get('color','#777777'))
    ids=set()
    for layer in state.get('layers',[]):
        if layer.get('group') not in GROUPS:raise ValueError('贴花部件有误')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}',str(layer.get('id',''))) or layer['id'] in ids:raise ValueError('贴花图层编号有误')
        ids.add(layer['id'])
        if layer.get('unit') not in GROUPS[layer['group']]['units']:raise ValueError('贴花所属模型有误')
        for key in ['center','normal']:
            value=layer.get(key)
            if not isinstance(value,list) or len(value)!=3 or any(not isinstance(x,(int,float)) or not math.isfinite(x) or abs(x)>100 for x in value):raise ValueError('贴花位置有误')
        for key,lo,hi in [('size',.08,2.5),('angle',-180,180),('opacity',0,1),('aspect',.001,1000)]:
            value=layer.get(key)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:raise ValueError('贴花参数有误')
        image_bytes(layer.get('image',''))
    return state

def allocate(kind,name):
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(name)).strip(' .')[:64] or '我的载具涂装'
    if name.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:name='MOD_'+name
    parent=DATA_ROOT/kind/name
    parent.mkdir(parents=True,exist_ok=True)
    for n in range(1,10000):
        p=parent/f'V{n}'
        try:p.mkdir();return p
        except FileExistsError:continue
    raise ValueError('版本数量超出范围')

def save_sources(out,state):
    dump(out/'project.json',state)
    entries=[]
    for i,l in enumerate(state.get('layers',[])):
        raw,im=image_bytes(l['image']);ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}.get(im.format,'.img')
        p=out/'source/images'/f'{i+1:03d}_{sha(raw)[:12]}{ext}';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        entries.append(dict(source=l.get('originalName',l.get('name','导入图像')),copy=str(p.relative_to(out)),sha256=sha(raw),bytes=len(raw)))
    dump(out/'source/image_manifest.json',entries)
    return entries

def save_project(payload):
    state=state_ok(payload['state'])
    out=allocate('projects',state.get('name','我的载具涂装'))
    save_sources(out,state)
    raw,im=image_bytes(payload['preview']);im.convert('RGB').save(out/'preview.png')
    return dict(path=str(out),project=str(out/'project.json'),version=out.name)

def mips(dds):
    h,w=struct.unpack_from('<II',dds,12);count=struct.unpack_from('<I',dds,28)[0];fmt=struct.unpack_from('<I',dds,128)[0];off=148;out=[]
    for i in range(count):
        mw=max(1,w>>i);mh=max(1,h>>i);sz=mw*mh*8 if fmt==10 else max(1,(mw+3)//4)*max(1,(mh+3)//4)*(8 if fmt==71 else 16)
        out.append((off,sz,mw,mh));off+=sz
    return out,off,fmt

def protect_resource(out,typ,tid):
    files={}
    for suffix in ['main','stream','gpu']:
        src=ENGINE/'Raw'/typ/f'{tid}.{typ}.{suffix}'
        if not src.exists():continue
        p=out/'source/Raw'/typ/src.name;p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():shutil.copy2(src,p)
        b=src.read_bytes();assert sha(p.read_bytes())==sha(b)
        files[suffix]=b
    return files

def protect_file(out,src,relative):
    p=out/'source'/relative;p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists():shutil.copy2(src,p)
    assert sha(src.read_bytes())==sha(p.read_bytes())
    manifest=out/'source/dependency_manifest.json'
    records=json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else []
    key=p.relative_to(out).as_posix()
    if not any(record['copy']==key for record in records):
        records.append(dict(source=str(src),copy=key,sha256=sha(src.read_bytes())))
        dump(manifest,records)
    return p

def native_color(t,setting,out):
    tid=t['id'][2:];parts=protect_resource(out,'texture',tid)
    dds_source=protect_file(out,ENGINE/'DDS'/f'{tid}.dds',Path('DDS')/f'{tid}.dds')
    src=dds_source.read_bytes();ml,total,fmt=mips(src);src=src[:total];dst=bytearray(src)
    rgb=color(setting['color']);native=setting['mode']=='native';ref=np.maximum(color(GROUPS[t['group']]['defaultColor']),1/255)
    if t['usage']=='material_lut':
        a=np.frombuffer(dst,dtype='<f2',offset=148,count=t['width']*t['height']*4).reshape(t['height'],t['width'],4)
        if native:a[:,PLAN['lut_color_columns'],:3]=recolor_srgb(a[:,PLAN['lut_color_columns'],:3].astype(float),rgb,ref)
        else:a[:,PLAN['lut_color_columns'],:3]=rgb
        old=np.frombuffer(src,dtype='<u2',offset=148,count=t['width']*t['height']*4).reshape(t['height'],t['width'],4)
        new=np.frombuffer(dst,dtype='<u2',offset=148,count=t['width']*t['height']*4).reshape(t['height'],t['width'],4)
        mask=np.zeros(old.shape,bool);mask[:,PLAN['lut_color_columns'],:3]=True
        assert np.array_equal(old[~mask],new[~mask])
        assert dst[ml[0][0]+ml[0][1]:]==src[ml[0][0]+ml[0][1]:]
    else:
        # Proven constant-color path: leave BC3 alpha / BC1 opacity bit-identical.
        if not native:
            c=linear(rgb);q=(round(c[0]*31)<<11)|(round(c[1]*63)<<5)|round(c[2]*31)
            for off,sz,w,h in ml:
                bs=8 if fmt==71 else 16
                for pos in range(off,off+sz,bs):
                    cp=pos+8 if fmt==77 else pos;bits=0
                    if fmt==71:
                        a,b,idx=struct.unpack_from('<HHI',src,pos)
                        if a<=b:
                            for pixel in range(16):
                                if ((idx>>(pixel*2))&3)==3:bits|=3<<(pixel*2)
                    struct.pack_into('<HHI',dst,cp,q,q,bits)
        else:
            # Preserve every original numeric MIP and alpha block separately.
            for off,sz,w,h in ml:
                hdr=bytearray(src[:148]);struct.pack_into('<II',hdr,12,h,w);struct.pack_into('<I',hdr,28,1)
                im=Image.open(io.BytesIO(bytes(hdr)+src[off:off+sz])).convert('RGB')
                a=np.array(im).astype(float)/255
                a=recolor_linear(a,rgb,ref)
                bc=encode_bc1(a,src[off:off+sz] if fmt==71 else None)
                if fmt==71:dst[off:off+sz]=bc
                else:
                    for j in range(sz//16):dst[off+j*16+8:off+j*16+16]=bc[j*8:j*8+8]
    final=out/'textures'/tid/'final.dds';final.parent.mkdir(parents=True,exist_ok=True);final.write_bytes(dst)
    return native_texture_payload(int(tid,16),bytes(dst),parts),dict(id=t['id'],group=t['group'],mode=setting['mode'],native_main_preserved=True,noncolor_lut_fields_and_alpha_preserved=True,dds_sha256=sha(dst))

def encode_bc1(rgb,original=None):
    h,w=rgb.shape[:2];hp=max(4,align(h,4));wp=max(4,align(w,4));a=np.pad(rgb,((0,hp-h),(0,wp-w),(0,0)),mode='edge')
    b=a.reshape(hp//4,4,wp//4,4,3).transpose(0,2,1,3,4).reshape(-1,16,3)
    lum=b@np.array([.2126,.7152,.0722]);lo=b[np.arange(len(b)),lum.argmin(1)];hi=b[np.arange(len(b)),lum.argmax(1)]
    def pack(c):v=np.rint(c*np.array([31,63,31])).astype(np.uint16);return (v[:,0]<<11)|(v[:,1]<<5)|v[:,2]
    def unpack(c):return np.stack(((c>>11)/31,((c>>5)&63)/63,(c&31)/31),axis=1)
    x=pack(hi);y=pack(lo);c0=np.maximum(x,y);c1=np.minimum(x,y)
    transparent=np.zeros((len(b),16),bool)
    if original:
        dt=np.dtype([('a','<u2'),('b','<u2'),('i','<u4')]);old=np.frombuffer(original,dtype=dt)
        transparent=((old['i'][:,None]>>(np.arange(16)*2))&3)==3;transparent&=(old['a']<=old['b'])[:,None]
    has=transparent.any(1);c0[has],c1[has]=c1[has].copy(),c0[has].copy()
    p0=unpack(c0);p1=unpack(c1)
    p2=np.where((c0>c1)[:,None],(2*p0+p1)/3,(p0+p1)/2);p3=np.where((c0>c1)[:,None],(p0+2*p1)/3,0)
    palette=np.stack((p0,p1,p2,p3),axis=1);dist=((b[:,:,None,:]-palette[:,None,:,:])**2).sum(-1)
    dist[c0<=c1,:,3]=1e9;idx=dist.argmin(-1).astype(np.uint32);idx[transparent]=3
    bits=(idx<<np.arange(16,dtype=np.uint32)*2).sum(1).astype('<u4')
    arr=np.empty(len(b),dtype=[('a','<u2'),('b','<u2'),('i','<u4')]);arr['a']=c0;arr['b']=c1;arr['i']=bits
    return arr.tobytes()

def native_texture_payload(tid,dds,parts):
    body=dds[148:];oldgpu=parts.get('gpu',b'');oldstream=parts.get('stream',b'')
    assert parts['main'][192:340]==dds[:148]
    return dict(id=tid,type=TEXTURE,main=parts['main'],stream=body if oldstream else b'',gpu=body[-len(oldgpu):] if oldgpu else b'')

def baked_texture(out,image,template,tid,channel):
    rawparts=protect_resource(out,'texture',template)
    dds_source=protect_file(out,ENGINE/'DDS'/f'{template}.dds',Path('DDS')/f'{template}.dds')
    template_dds=dds_source.read_bytes();ml,total,fmt=mips(template_dds)
    png=out/'textures'/f'{tid:016x}.png';png.parent.mkdir(parents=True,exist_ok=True)
    im=image.convert('RGB').resize((1024,1024),Image.Resampling.LANCZOS);im.save(png)
    target=out/'textures/encoded';target.mkdir(exist_ok=True)
    args=[str(ENGINE/'texconv.exe'),'-nologo','-y','-dx10','-f',{71:'BC1_UNORM',77:'BC3_UNORM',83:'BC5_UNORM'}[fmt],'-m','11','-o',str(target)]
    if channel=='albedo':args+=['-srgbi']
    p=subprocess.run(args+[str(png)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:raise ValueError('纹理编码失败：'+p.stdout.decode('utf8','replace')[-500:])
    encoded=(target/(png.stem+'.dds')).read_bytes();e_mips,e_total,e_fmt=mips(encoded)
    assert e_fmt==fmt and e_total==total and len(encoded)==total
    # Reuse the exact native header and resident mip split for the new resource.
    dds=template_dds[:148]+encoded[148:];(out/'textures'/f'{tid:016x}.dds').write_bytes(dds)
    return native_texture_payload(tid,dds,rawparts)

def build_archive(out,payload):
    # The engine consumes contiguous blocks described by the type table.
    # Sorting by resource ID first interleaves those blocks; Filediver's
    # per-file parser accepts that order, but the game loader does not.
    payload.sort(key=lambda p:(p['type'],p['id']))
    assert len({(p['id'],p['type']) for p in payload})==len(payload)
    counts=Counter(p['type'] for p in payload);types=sorted(counts)
    start=72+32*len(types);pm=align(start+len(payload)*80,16);ps=pg=rm=rg=0;rows=[]
    for i,p in enumerate(payload):
        pm=align(pm,16);ps=align(ps,64);pg=align(pg,64)
        row=[p['id'],p['type'],pm,ps,pg,rm,rg,len(p['main']),len(p.get('stream',b'')),len(p.get('gpu',b'')),16,64,i+1]
        rows.append(row);pm+=row[7];ps+=row[8];pg+=row[9];rm+=align(row[7],256);rg+=align(row[9],256)
    if max(pg,rg)>=2**31:raise ValueError('本次资源超过单个补丁的安全范围，请按载具类别分别导出；工程已保留')
    main=bytearray(align(pm,16));stream=bytearray(ps);gpu=bytearray(pg)
    main[:72]=gzip.decompress((ENGINE/'boot_header.gz').read_bytes());struct.pack_into('<II',main,4,len(types),len(payload));struct.pack_into('<QQ',main,32,rm,rg)
    for i,typ in enumerate(types):
        td=bytearray(gzip.decompress((ENGINE/'texture_type.gz').read_bytes()));struct.pack_into('<Q',td,8,typ);struct.pack_into('<I',td,16,counts[typ]);main[72+32*i:104+32*i]=td
    for i,(p,r) in enumerate(zip(payload,rows)):
        struct.pack_into('<7Q6I',main,start+i*80,*r);main[r[2]:r[2]+r[7]]=p['main'];stream[r[3]:r[3]+r[8]]=p.get('stream',b'');gpu[r[4]:r[4]+r[9]]=p.get('gpu',b'')
    layout_report=validate_archive(main,stream,gpu)
    dump(out/'reports/archive_layout.json',layout_report)
    package=out/'package';package.mkdir()
    parts={BASE:bytes(main),BASE+'.stream':bytes(stream),BASE+'.gpu_resources':bytes(gpu)}
    for name,data in parts.items():(package/name).write_bytes(data)
    dump(out/'reports/archive_rows.json',rows)
    dump(out/'reports/patch_hashes_before_preview.json',{n:sha(b) for n,b in parts.items()})
    return parts

def export_mod(payload):
    verifier=ENGINE/'verify_mod.exe'
    if not verifier.is_file():raise ValueError('安装包缺少补丁校验器，请重新解压完整软件')
    state=state_ok(payload['state']);settings=state.get('groups',{})
    decal_groups={l['group'] for l in state.get('layers',[]) if not l.get('hidden') and l.get('opacity',1)>0}
    changed={g for g,s in settings.items() if s['mode']!='original'}|decal_groups
    if not changed:raise ValueError('请先修改部件颜色或添加贴花')
    raw,preview=image_bytes(payload['preview']);out=allocate('mods',state.get('name','我的载具涂装'))
    save_sources(out,state);preview.convert('RGB').save(out/'preview.png')
    resources=[];edits=[];new_mats=[]
    for t in PLAN['textures']:
        if t['group'] in changed and t['group'] not in decal_groups:
            res,edit=native_color(t,settings[t['group']],out);resources.append(res);edits.append(edit)
    materials={}
    for gid in decal_groups:
        for binding in GROUPS[gid]['bindings']:materials[binding['material']]=gid
    # Each original Material ID is rewritten exactly once; linked UI groups stay linked.
    supplied=payload.get('baked',{})
    if set(supplied)!=set(materials):raise ValueError('共享部件的烘焙贴图不完整，请重新导出')
    template=(ENGINE/'Raw/material/7f98d17ebc5f4757.material.main').read_bytes()
    for mid,gid in materials.items():
        protect_resource(out,'material',mid[2:]);m=bytearray(template);newids=[]
        for name in ['color','normal','mra']:
            protect_file(out,DATA/'textures'/mid[2:]/f'{name}.png',Path('textures')/mid[2:]/f'{name}.png')
        for channel,old in [('albedo','6c1dac34c1e17de4'),('normal','29b6d94cf227c6d7'),('mra','74c190e4092513d5')]:
            nid=hash_id('content/mods/vehicle_tool/'+state.get('name','custom')+'/'+mid+'/'+channel)
            if channel=='albedo':_,im=image_bytes(supplied[mid],2_000_000)
            else:
                p=out/'source/textures'/mid[2:]/f'{channel}.png'
                if not p.exists():raise ValueError('缺少部件烘焙材质：'+mid)
                im=Image.open(p)
            resources.append(baked_texture(out,im,old,nid,channel));newids.append(f'0x{nid:016x}')
            needle=struct.pack('<Q',int(old,16));assert m.count(needle)==1
            m=m.replace(needle,struct.pack('<Q',nid))
        resources.append(dict(id=int(mid,16),type=MATERIAL,main=bytes(m),stream=b'',gpu=b''))
        new_mats.append(dict(material=mid,group=gid,textures=newids,base_material='0x55f47c155e2a16fe',method='UV0 baked albedo + tangent normal + MRA; native generic PBR template'))
    parts=build_archive(out,resources);package=out/'package'
    check=subprocess.run([str(verifier),str(out)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if check.returncode:raise ValueError('补丁独立回读校验失败，已保留制作记录：'+str(out))
    shutil.copy2(out/'preview.png',package/'preview.png')
    dump(out/'reports/native_color_edits.json',edits);dump(out/'reports/baked_materials.json',new_mats)
    affected=[dict(group=g,name=GROUPS[g]['name'],affected=GROUPS[g]['affected'],externalCount=GROUPS[g]['externalCount'],externalNote=GROUPS[g]['externalNote']) for g in sorted(changed)]
    dump(out/'reports/affected_parts.json',affected)
    readme='载具替换工具 V2\n\n适用基准：标准原生涂装。部分游戏内置自定义涂装会冲突，请切回标准涂装。\n退出游戏后用 MOD 管理器导入本 ZIP；先停用其他载具纹理 MOD。\n不需要也不会由本软件写入游戏目录。\n\n'
    readme+='本包修改：'+ '、'.join(GROUPS[g]['name'] for g in sorted(changed))+'。\n共享的颜色/贴图资源会在引用它的部件中联动；完整范围见 affected_parts.json。\n'
    readme+='preview.png 是本工程的 3D 材质预览，不是游戏实测截图。\n'
    if new_mats:readme+='\n贴花使用原始 UV0 烘焙图案，并输出通用 PBR 材质。保持模型、骨骼与碰撞资源不变。UV 重叠处会同步出现图案。该贴花材质路径已做文件结构回读检查，尚需游戏内验证；动态污渍/损伤效果可能与原材质有差别。\n'
    else:readme+='\n颜色使用既有原生纹理路径；保留纹理头、Alpha、LUT 非颜色列与数值 MIP。新软件导出包尚未逐项实机复测。\n'
    (package/'使用说明.txt').write_text(readme,encoding='utf-8-sig')
    shutil.copy2(out/'reports/affected_parts.json',package/'affected_parts.json')
    dump(package/'mod_manifest.json',dict(name=state.get('name','我的载具涂装'),version=out.name,tool='载具替换工具 2.0.0',baseline='标准原生涂装',preview='preview.png',nativeColorGroups=sorted(changed-decal_groups),bakedDecalGroups=sorted(decal_groups),gameTested=False))
    zipname=out/(state.get('name','载具涂装').translate(str.maketrans({c:'_' for c in '<>:"/\\|?*'}))[:64]+'_'+out.name+'.zip')
    with zipfile.ZipFile(zipname,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in package.iterdir():z.write(p,p.name)
    with zipfile.ZipFile(zipname) as z:
        im=Image.open(io.BytesIO(z.read('preview.png')));im.load();assert im.format=='PNG'
        for n,b in parts.items():assert sha(z.read(n))==sha(b)==sha((package/n).read_bytes())
    sources=[]
    for p in (out/'source/Raw').rglob('*'):
        if p.is_file():
            rel=p.relative_to(out/'source/Raw');src=ENGINE/'Raw'/rel
            assert sha(src.read_bytes())==sha(p.read_bytes())
            sources.append(dict(source=str(src),copy=str(p.relative_to(out)),sha256=sha(p.read_bytes())))
    for directory,origin in [('DDS',ENGINE/'DDS'),('textures',DATA/'textures')]:
        for p in (out/'source'/directory).rglob('*'):
            if p.is_file():
                src=origin/p.relative_to(out/'source'/directory)
                assert sha(src.read_bytes())==sha(p.read_bytes())
                sources.append(dict(source=str(src),copy=str(p.relative_to(out)),sha256=sha(p.read_bytes())))
    dump(out/'source/manifest.json',sources)
    dump(out/'reports/package_audit.json',dict(status='passed',png_decoded=True,preview_in_zip=True,patch_hashes_unchanged_after_preview=True,protected_sources_rechecked=len(sources),textures=sum(p['type']==TEXTURE for p in resources),materials=len(new_mats),units=0,game_directory_written=False))
    return dict(path=str(out),zip=str(zipname),url='/download/'+str(zipname.relative_to(DATA_ROOT)).replace('\\','/'),version=out.name,materials=len(new_mats))
