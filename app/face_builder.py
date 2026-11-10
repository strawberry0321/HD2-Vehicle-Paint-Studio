from pathlib import Path
import base64, collections, copy, hashlib, io, json, math, os, re, shutil, struct, subprocess, zipfile
from compute_runtime import np
from PIL import Image,ImageDraw
import builder as legacy
import image_library as library
import export_scope
import topology
import atlas_baker
import geometry_migration
import zip_export
import face_material
import export_progress
import export_destination
import appearance_variants
import mech_appearance
from app_version import TITLE
from storage_paths import DATA_ROOT,CACHE_ROOT
from concurrent.futures import ThreadPoolExecutor
from coloring import recolor_srgb
from unit_colors import effective
from native_writer import build_unit,UNIT,u32
APP=Path(__file__).resolve().parent;ROOT=DATA_ROOT;DATA=APP/'data';ENGINE=APP/'engine';LOCK=legacy.LOCK
CAT=json.loads((DATA/'catalog.json').read_text(encoding='utf8'));VEHICLES={u['id']:u for u in CAT['vehicles']};GROUPS={g['id']:g for g in CAT['groups']};MODELS={}
library.ensure_presets()
def model(uid):
    if uid not in MODELS:
        p=DATA/'models'/f'{uid[2:]}.json';d=json.loads(p.read_text(encoding='utf8'));b=p.with_suffix('.bin').read_bytes();MODELS[uid]=(d,b)
    return MODELS[uid]
def part_arrays(uid,part):
    d,b=model(uid);p=d['parts'][part];a=np.frombuffer(b,dtype='<f4',count=p['count']*8,offset=p['offset']).reshape(-1,8);minimum=np.array(d['bounds'][0]);maximum=np.array(d['bounds'][1]);center=(minimum+maximum)/2;scale=5/np.max(maximum-minimum);positions=(a[:,:3]-np.array([center[0],minimum[1],center[2]]))*scale
    return p,positions,a[:,6:8]
def migrate_state(s):
    s=copy.deepcopy(s)
    for group in CAT['groups']:s.setdefault('groups',{}).setdefault(group['id'],dict(mode='original',color=group['defaultColor']))
    if s.get('version')==2:
        for layer in s.get('layers',[]):
            layer['surfaces']=[dict(part=layer.pop('part'),triangles=layer.pop('triangles'))];layer['placement']['x']/=2;layer['placement']['y']/=2;layer['selectionVersion']=3
        s['version']=3
    if s.get('version')==3:
        s['surfaceColors']=[]
        for layer in s.get('layers',[]):
            asset=library.describe(layer['asset']);layer['role']='photo' if asset['kind']=='image' else 'pattern'
            if layer['role']=='photo' and layer.get('tint'):layer['legacyTinted']=True
        s['version']=4
    if s.get('version')==4:s['version']=5
    if s.get('version')==5:
        s['unitColors']=s.get('unitColors',{});s['colorLinks']=s.get('colorLinks',{});s['version']=6
    if s.get('version')==6:
        s.setdefault('unitColors',{})
        for vehicle in CAT['vehicles']:
            values=s['unitColors'].setdefault(vehicle['id'],{})
            for gid in vehicle['groups']:values.setdefault(gid,copy.deepcopy(s['groups'].get(gid,dict(mode='original',color=GROUPS[gid]['defaultColor']))))
        s.pop('colorLinks',None);s['version']=7
    if s.get('version')==7:s['version']=8
    for layer in s.get('layers',[]):
        if layer.get('projection') and 'groups' not in layer['projection']:
            layer['projection'].update(scopeKind='group',groups=[VEHICLES[layer['unit']]['groups'][0]])
    return mech_appearance.migrate(geometry_migration.migrate(s,CAT))
def geometry_ok(record):
    if record['unit'] not in VEHICLES:raise ValueError('选区模型无效')
    descriptor,_=model(record['unit']);seen=set();surfaces=record['surfaces']
    if not surfaces or len(surfaces)>len(descriptor['parts']):raise ValueError('选中区域无效')
    for surface in surfaces:
        part=surface['part'];triangles=surface['triangles']
        if not isinstance(part,int) or not 0<=part<len(descriptor['parts']) or part in seen:raise ValueError('选中部件无效')
        if record.get('selectionVersion',0)>=4 and descriptor['parts'][part].get('nonRenderable'):raise ValueError('不可编辑辅助面')
        seen.add(part);limit=descriptor['parts'][part]['count']//3
        if not triangles or len(triangles)>limit or len(set(triangles))!=len(triangles) or any(not isinstance(t,int) or not 0<=t<limit for t in triangles):raise ValueError('选面数据有误')
    for key,n in [('origin',3),('u',3),('v',3),('normal',3),('bounds',4)]:
        values=record['frame'][key]
        if len(values)!=n or any(not math.isfinite(float(x)) or abs(x)>100 for x in values):raise ValueError('表面坐标无效')
def state_ok(s):
    s=mech_appearance.migrate(geometry_migration.migrate(s,CAT))
    if not isinstance(s.get('removeStockDecals',True),bool):raise ValueError('原版标志选项无效')
    if s.get('format')!='HD2VehicleProject' or s.get('version')!=8:raise ValueError('请通过“打开工程”导入旧版文件')
    if s.get('vehicle') not in VEHICLES:raise ValueError('载具无效')
    if len(s.get('layers',[]))>100 or len(s.get('surfaceColors',[]))>300:raise ValueError('图层或局部底色过多，请合并或删除不需要的内容')
    for gid,value in s['groups'].items():
        if gid not in GROUPS or value['mode'] not in ['original','native','solid']:raise ValueError('部件颜色无效')
        legacy.color(value['color'])
    for uid,settings in s.get('unitColors',{}).items():
        if uid not in VEHICLES:raise ValueError('独立配色模型无效')
        for gid,value in settings.items():
            if gid not in VEHICLES[uid]['groups'] or value['mode'] not in ['original','native','solid']:raise ValueError('独立配色部件无效')
            legacy.color(value['color'])
    ids=set()
    for record in s.get('layers',[])+s.get('surfaceColors',[]):
        if not re.fullmatch('[0-9a-f-]{36}',record['id']) or record['id'] in ids:raise ValueError('图层编号无效')
        ids.add(record['id']);geometry_ok(record);legacy.color(record['color'])
    for layer in s['layers']:
        library.path_for(layer['asset']);placement=layer['placement']
        if layer.get('role') not in ['photo','pattern']:raise ValueError('图片分区无效')
        if layer.get('uvReuse'):
            reuse=layer['uvReuse'];bounds=np.array(reuse.get('bounds'),float);crop=np.array(reuse.get('crop',[0,0,1,1]),float)
            if bounds.shape!=(4,) or not np.all(np.isfinite(bounds)) or np.any(bounds<0) or np.any(bounds>1) or np.any(bounds[2:]-bounds[:2]<1e-8):raise ValueError('复用贴图区范围无效')
            if crop.shape!=(4,) or not np.all(np.isfinite(crop)) or np.any(crop<0) or np.any(crop>1) or np.any(crop[2:]-crop[:2]<.005):raise ValueError('复用图片裁剪范围无效')
            if layer.get('projection'):raise ValueError('复用图片不能同时启用自由投射')
        if layer.get('projection'):
            import projection_baker
            pr=layer['projection']
            if 'groups' not in pr:pr.update(scopeKind='group',groups=[VEHICLES[layer['unit']]['groups'][0]])
            if pr.get('groups') is not None and (not pr['groups'] or any(g not in VEHICLES[layer['unit']]['groups'] for g in pr['groups'])):raise ValueError('贴花作用部件无效')
            if pr['type'] not in ['planar','cylinder']:raise ValueError('贴花投射类型无效')
            for k,lo,hi in [('width',.001,30),('height',.001,30),('depth',.00001,20),('radius',.001,10)]:
                if k=='radius' and pr['type']!='cylinder':continue
                if not math.isfinite(float(pr[k])) or not lo<=pr[k]<=hi:raise ValueError('贴花范围无效')
            crop=np.array(pr.get('crop',[0,0,1,1]),float)
            if crop.shape!=(4,) or not np.all(np.isfinite(crop)) or np.any(crop<0) or np.any(crop>1) or crop[2]-crop[0]<.005 or crop[3]-crop[1]<.005:raise ValueError('裁剪范围无效')
            corners=np.array(pr.get('corners',[[0,0],[1,0],[1,1],[0,1]]),float)
            if corners.shape!=(4,2) or not np.all(np.isfinite(corners)) or np.max(np.abs(corners))>5:raise ValueError('贴花形状无效')
            edges=np.roll(corners,-1,axis=0)-corners;turn=edges[:,0]*np.roll(edges,-1,axis=0)[:,1]-edges[:,1]*np.roll(edges,-1,axis=0)[:,0]
            if not np.all(turn>.002):raise ValueError('贴花形状不能折叠、交叉或反转')
            projection_baker.parameters(layer)
            distance=pr.get('distance',0)
            if not math.isfinite(float(distance)) or not 0<=distance<=100:raise ValueError('贴花投射距离无效')
            if not math.isfinite(float(pr.get('cutoff',.015))) or not 0<=pr.get('cutoff',.015)<=1:raise ValueError('贴花贴合角度无效')
            basis=np.array([layer['frame'][k] for k in ['u','v','normal']],float)
            if not np.allclose(basis@basis.T,np.eye(3),atol=.0001):raise ValueError('贴花坐标方向无效，请重新定位')
            lock=pr.get('surface')
            if lock:
                if lock.get('mode') not in ['locked','free']:raise ValueError('侧面锁定模式无效')
                for key in ['normal','origin']:
                    a=np.array(lock.get(key),float)
                    if a.shape!=(3,) or not np.all(np.isfinite(a)) or np.max(np.abs(a))>100:raise ValueError('侧面锁定坐标无效')
                if not np.isclose(np.linalg.norm(lock['normal']),1,atol=.0001) or not 5<=lock.get('angle',55)<=89 or not .001<=lock.get('band',1.25)<=20:raise ValueError('侧面锁定范围无效')

        for key,low,high in [('scale',.01,20),('x',-10,10),('y',-10,10),('angle',-3600,3600)]:
            if not math.isfinite(float(placement[key])) or not low<=placement[key]<=high:raise ValueError('图案数值超出允许范围')
        if placement['fit'] not in ['cover','contain'] or not 0<=layer['opacity']<=1:raise ValueError('图案参数无效')
    return s
def import_image(payload):
    role=payload.get('role','photo')
    if role not in ['photo','pattern']:raise ValueError('导入分区无效')
    return library.import_image(payload['image'],payload.get('name','导入图片'),'image' if role=='photo' else 'pattern',role=='pattern',[role])
def open_project(payload):
    if payload.get('archive'):
        raw=base64.b64decode(payload['archive'].split(',',1)[-1],validate=True)
        if len(raw)>380*1024*1024:raise ValueError('工程备份过大')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entry=archive.getinfo('__project/project.json')
            if entry.file_size>380*1024*1024:raise ValueError('工程备份过大')
            payload=dict(state=json.loads(archive.read(entry)))
    s=payload['state']
    if s.get('format')!='HD2VehicleProject':raise ValueError('工程格式无效')
    if s.get('version')==1:
        for l in s.get('layers',[]):
            if l.get('image'):library.import_image(l['image'],l.get('name','旧工程图片'))
        s['version']=2;s['layers']=[];s['coverFixed']=True;s['family']=s['vehicle']
        return dict(state=migrate_state(s),message='旧工程配色已保留，图片已进入图片库；请重新选择精确表面。')
    for a in s.pop('embeddedAssets',[]):
        result=library.import_image(a['image'],a['name'],a.get('kind','image'),a.get('tintable',False),a.get('roles'))
        if result['id']!=a['id']:raise ValueError('工程图片哈希不一致')
    s=migrate_state(s);state_ok(s);return dict(state=s)
def save_state(out,state):
    portable=copy.deepcopy(state);portable['embeddedAssets']=[];manifest=[]
    for key in sorted({l['asset'] for l in state['layers']}):
        src=library.path_for(key);a=library.describe(key);target=out/'source/images'/key/src.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target);raw=target.read_bytes();assert hashlib.sha256(raw).hexdigest()==key
        portable['embeddedAssets'].append(dict(id=key,name=a['name'],kind=a['kind'],tintable=a['tintable'],roles=a['roles'],image='data:image/'+('jpeg' if src.suffix=='.jpg' else src.suffix[1:])+';base64,'+base64.b64encode(raw).decode()))
        manifest.append(dict(source=str(src),copy=str(target.relative_to(out)),sha256=key,originalName=a['name'],width=a['width'],height=a['height']))
    legacy.dump(out/'project.json',portable);legacy.dump(out/'source/image_manifest.json',manifest)
def save_project(payload):
    s=state_ok(payload['state']);out=legacy.allocate('projects',s.get('name','我的载具涂装'));save_state(out,s);_,im=legacy.image_bytes(payload['preview']);im.convert('RGB').save(out/'preview.png');return dict(path=str(out),version=out.name)
def setting_for(state,uid,part):
    if part.get('group'):return effective(state,uid,part['group'])
    vehicle=VEHICLES.get(uid,{})
    if state.get('coverFixed',True) and vehicle.get('fixedColorGroup') and part['slot']==vehicle.get('fixedColorSlot'):return effective(state,uid,vehicle['fixedColorGroup'])
    return dict(mode='original',color='#ffffff')
DAMAGE_SUFFIX='_destroyed'
LAST_REMAP=[]
_UNIT_GEOM={}
def _unit_parts(uid):
    if uid not in _UNIT_GEOM:
        descriptor,_=model(uid);items=[]
        for index,p in enumerate(descriptor['parts']):
            if p.get('nonRenderable') or p.get('count',0)//3<1:continue
            _,positions,_=part_arrays(uid,index);tris=positions.reshape(-1,3,3)
            cross=np.cross(tris[:,1]-tris[:,0],tris[:,2]-tris[:,0]);normals=cross/np.maximum(1e-12,np.linalg.norm(cross,axis=1)[:,None])
            items.append((index,tris,normals))
        _UNIT_GEOM[uid]=items
    return _UNIT_GEOM[uid]
def _ray_hit(uid,origin,direction):
    direction=np.asarray(direction,float);norm=float(np.linalg.norm(direction))
    if norm<1e-9:return None
    direction=direction/norm;best=None;best_t=np.inf
    for part,tris,normals in _unit_parts(uid):
        v0,v1,v2=tris[:,0],tris[:,1],tris[:,2];e1=v1-v0;e2=v2-v0
        pvec=np.cross(direction,e2);det=np.einsum('ij,ij->i',e1,pvec);ok=np.abs(det)>1e-12
        if not ok.any():continue
        inv=np.zeros_like(det);inv[ok]=1/det[ok];tvec=origin-v0
        u=np.einsum('ij,ij->i',tvec,pvec)*inv;qvec=np.cross(tvec,e1);v=(qvec@direction)*inv;t=np.einsum('ij,ij->i',e2,qvec)*inv
        hit=ok&(u>=-1e-6)&(u<=1+1e-6)&(v>=-1e-6)&(u+v<=1+1e-6)&(t>1e-4)
        if not hit.any():continue
        values=np.where(hit,t,np.inf);index=int(np.argmin(values))
        if values[index]<best_t:best_t=float(values[index]);best=(part,index)
    return best
def _visible_image_surfaces(uid,record):
    frame=record.get('frame') or {}
    if 'normal' not in frame:return None
    origin=np.array(frame['origin'],float);normal=np.array(frame['normal'],float)
    if np.linalg.norm(normal)<1e-9:return None
    normal/=np.linalg.norm(normal)
    u=np.array(frame['u'],float);v=np.array(frame['v'],float);x0,y0,x1,y1=[float(x) for x in frame['bounds']]
    try:
        asset=library.describe(record['asset']);pw,ph=atlas_baker.image_size(record,asset)
    except Exception:return None
    p=dict(record['placement']);cx=(x0+x1)/2+p['x']*(x1-x0);cy=(y0+y1)/2+p['y']*(y1-y0);angle=-p['angle']*math.pi/180
    ca,sa=math.cos(angle),math.sin(angle);result={};samples=[];steps=20
    for i in range(steps):
        for j in range(steps):
            lx=(i+.5)/steps*pw-pw/2;ly=(j+.5)/steps*ph-ph/2
            wx=cx+ca*lx-sa*ly;wy=cy+sa*lx+ca*ly;world=origin+u*wx+v*wy
            found=None
            for direction in (normal,-normal):
                hit=_ray_hit(uid,world+direction*2.0,-direction)
                if hit is None:continue
                hp,ht=hit;result.setdefault(hp,set()).add(int(ht));found=(hp,int(ht));break
            samples.append((lx,ly,found,i,j))
    if not samples:return None
    if not result:return None
    return {part:sorted(t) for part,t in result.items()}
def _visible_color_surfaces(uid,record):
    frame=record.get('frame') or {}
    if 'normal' not in frame:return None
    normal=np.array(frame['normal'],float)
    if np.linalg.norm(normal)<1e-9:return None
    normal/=np.linalg.norm(normal)
    origin=np.array(frame['origin'],float);u=np.array(frame['u'],float);v=np.array(frame['v'],float)
    xs=[];ys=[];result={};hits=0
    for surface in record.get('surfaces',[]):
        part=surface.get('part')
        if part is None:continue
        try:_,positions,_=part_arrays(uid,part)
        except Exception:continue
        tris=positions.reshape(-1,3,3)
        for t in surface.get('triangles',[]):
            try:t=int(t)
            except Exception:continue
            if not 0<=t<len(tris):continue
            center=tris[t].mean(0);rel=center-origin;xs.append(float(rel@u));ys.append(float(rel@v))
            for direction in (normal,-normal):
                hit=_ray_hit(uid,center+direction*2.0,-direction)
                if hit is None:continue
                hp,ht=hit;result.setdefault(hp,set()).add(int(ht));hits+=1
    return {p:sorted(t) for p,t in result.items()} if hits else None
def merge_surfaces(state):
    global LAST_REMAP
    LAST_REMAP=[]
    batches=collections.defaultdict(list)
    material_records=[]
    for uid in state.get('exportScope',{}).get('units',[]):
        descriptor,_=model(uid)
        for part,p in enumerate(descriptor['parts']):
            source=p.get('partialColorSourceGroup')
            if (source and (effective(state,uid,source)['mode']!='original' or effective(state,uid,p['group'])['mode']!='original')) or (p.get('localColorOnly') and effective(state,uid,p['group'])['mode']!='original'):
                material_records.append(dict(unit=uid,surfaces=[dict(part=part,triangles=list(range(p['count']//3)))],frame=dict(origin=[0,0,0],normal=[0,0,1],u=[1,0,0],v=[0,1,0],bounds=[-2.5,-2.5,2.5,2.5])))
    for kind,records in [('material',material_records),('color',state.get('surfaceColors',[])),('image',state['layers'])]:
        for order,record in enumerate(records):
            if record.get('hidden') or (kind=='image' and record['opacity']<=0):continue
            for selected in record.get('surfaces',[]):
                part=selected['part']
                if model(record['unit'])[0]['parts'][part].get('nonRenderable'):raise ValueError('旧图层位于不可显示的辅助面，请隐藏后重新选面')
                triangles=sorted(set(int(t) for t in selected['triangles']))
                if not triangles:continue
                part_name=model(record['unit'])[0]['parts'][part].get('name') or ''
                if DAMAGE_SUFFIX in part_name:
                    LAST_REMAP.append(dict(warning='damaged_state_part',unit=record['unit'],part=part,part_name=part_name,kind=kind,index=order,name=record.get('name',record.get('color','')),message='该部件是损坏状态变体，完整车体上可能被原车身遮挡；完整车体不可见时请改选同一位置的完整部件。'))
                sliced=copy.copy(record);sliced.update(part=part,triangles=triangles,_order=order,_kind=kind);batches[(record['unit'],part)].append(sliced)
    result=[]
    for (uid,part),records in batches.items():
        triangles=sorted(set().union(*(set(r['triangles']) for r in records)))
        collapsed=[];seen={}
        for record in sorted(records,key=lambda r:r['_order']):
            key=(record['_kind'],record['_order'])
            if key in seen:
                seen[key]['triangles']=sorted(set(seen[key]['triangles'])|set(record['triangles']))
            else:
                kept=copy.copy(record);kept['triangles']=list(record['triangles']);seen[key]=kept;collapsed.append(kept)
        ordered=collapsed
        source_order=[r for r in ordered if r['_kind'] in ('color','image')]
        frame=copy.deepcopy(source_order[0]['frame'] if source_order else ordered[0]['frame'])
        sid=hashlib.sha256((uid+':'+str(part)+':'+','.join(map(str,triangles))).encode()).hexdigest()[:24]
        result.append(dict(id=sid,unit=uid,part=part,triangles=triangles,frame=frame,layers=[r for r in ordered if r['_kind']=='image'],baseColors=[r for r in ordered if r['_kind']=='color'],preserveSourceDetail=any(r['_kind']=='material' for r in ordered)))
    return result
base_bake=atlas_baker.base_bake
color_bake=atlas_baker.color_bake
resolution=atlas_baker.prepare
def new_texture(out,image,tid,fmt):
    directory=out/'textures';directory.mkdir(exist_ok=True);png=directory/f'{tid:016x}.png';image.save(png);encoded=directory/'encoded';encoded.mkdir(exist_ok=True)
    args=[str(ENGINE/'texconv.exe'),'-nologo','-y','-sepalpha','-dx10','-f',fmt,'-m','0','-o',str(encoded),str(png)]
    # Pillow's rendered RGB pixels are already sRGB. Declare that input space
    # when targeting an sRGB DDS; otherwise texconv applies another transfer
    # curve and brightens the decal/background relative to the workbench.
    if fmt.endswith('_SRGB'):args.insert(-1,'-srgbi')
    result=export_progress.run(args)
    if result.returncode:
        details=(result.stdout+result.stderr).decode('utf8','replace').strip()[-300:]
        raise ValueError('高精度贴图编码失败（0x'+format(result.returncode&0xffffffff,'08X')+'）'+('：'+details if details else '，请检查 texconv 运行环境；工程已保留。'))
    dds=(encoded/(png.stem+'.dds')).read_bytes();return texture_from_dds(dds,tid)
def texture_from_dds(dds,tid):
    assert dds[:4]==b'DDS '
    template=(ENGINE/'Raw/texture/6c1dac34c1e17de4.texture.main').read_bytes();main=bytearray(template);struct.pack_into('<II',main,4,0,0xffffffff);main[12:192]=bytes(180);main[192:340]=dds[:148]
    return dict(id=tid,type=legacy.TEXTURE,main=bytes(main),gpu=dds[148:],stream=b'')
def repair_fixed_color(out,state,uid=None):
    vehicle=VEHICLES.get(uid or state['vehicle'],{});gid=vehicle.get('fixedColorGroup','P24');mid=vehicle.get('fixedColorMaterial','c6130a81c56dff6e')
    if not state.get('coverFixed',True) or state['groups'][gid]['mode']=='original':return None
    parts=legacy.protect_resource(out,'material',mid);data=bytearray(parts['main']);nt=u32(data,64);ns=u32(data,104);defs=136+nt*12;base=defs+ns*20;color=legacy.color(state['groups'][gid]['color']);wanted={legacy.hash_id(k)>>32:k for k in ['use_uv_for_coloring','albedo_color','albedo_color_curavture','emissive_color','emissive_color_curavture']};changed=[]
    for i in range(ns):
        typ,count,usage,offset,stride=struct.unpack_from('<5I',data,defs+i*20);name=wanted.get(usage)
        if name=='use_uv_for_coloring':struct.pack_into('<f',data,base+offset,0);changed.append(name)
        elif name:struct.pack_into('<3f',data,base+offset,*color);changed.append(name)
    assert 'use_uv_for_coloring' in changed
    return dict(id=int(mid,16),type=legacy.MATERIAL,main=bytes(data),gpu=b'',stream=b'')
def export_plan(payload):
    state,scope=export_scope.choose(state_ok(payload['state']),payload.get('scope'),CAT)
    scope['has_changes']=bool(scope['changed_groups'] or scope['surface_colors'] or any(not l.get('hidden') and l['opacity']>0 for l in state['layers']))
    return scope
def export_mod(payload):
    export_progress.begin(str(payload.get('exportToken','')))
    try:
        result=_export_mod(payload);export_progress.finish();return result
    except Exception as error:
        export_progress.finish(str(error));raise
def _export_mod(payload):
    import time
    started=time.perf_counter()
    state,scope=export_scope.choose(state_ok(payload['state']),payload.get('scope'),CAT)
    target=export_destination.resolve(payload.get('destination'),create=True)
    out=zip_export.workspace(ROOT);save_state(out,state);_,preview=legacy.image_bytes(payload['preview']);preview.convert('RGB').save(out/'preview.png');proof=[]
    export_progress.update('已保留工程，正在展开模型状态…',phase='geometry',total=0,completed=0)
    render_state,appearances=appearance_variants.expand(state)
    import projection_baker
    export_progress.update('正在核对图案覆盖范围…')
    for layer in render_state['layers']:
        if layer.get('projection') and not layer.get('hidden') and not layer.get('coverageTransfer'):
            layer['surfaces']=projection_baker.build(projection_baker.cache_key(layer))['surfaces']
            if not layer['surfaces']:raise ValueError('贴花“'+layer['name']+'”未覆盖载具，请重新定位后导出')
    surfaces=merge_surfaces(render_state);changed=set(scope['changed_groups'])
    mech_active=mech_appearance.active(render_state)
    if mech_active:surfaces=[s for s in surfaces if s['unit'] not in mech_appearance.VARIANTS]
    if not surfaces and not changed and not mech_active:raise ValueError('请先修改颜色或添加图案')
    from texture_cache import ExportCache
    cache=ExportCache(out,ENGINE)
    import material_surfaces
    shared_surfaces=[];ordinary_surfaces=[];shared_flags={}
    # V5-02: the carrier uses the V2 shell/private-geometry route. Ship weapon
    # props bake their existing UVs and receive complete local mesh data.
    flight_ids={v['id'] for v in CAT['vehicles'] if v.get('categoryV3')=='飞行载具'}
    for surface in surfaces:
        descriptor,_=model(surface['unit']);part=descriptor['parts'][surface['part']];native=material_surfaces.native_owner(surface['unit'],part,descriptor)
        if native not in shared_flags:shared_flags[native]=material_surfaces.is_shared(out,native)
        native_uv=shared_flags[native] and native not in flight_ids
        (shared_surfaces if native_uv else ordinary_surfaces).append(surface)
    surfaces=ordinary_surfaces
    resources,replacements,bindings=export_scope.private_colors(state,scope,CAT,out)
    appearance_variants.bind_colors(replacements,appearances)
    complete,complete_mapping,complete_proof=material_surfaces.build(out,render_state,scope,appearances,shared_surfaces,cache=cache)
    resources.extend(complete);bindings['materials'].extend(complete_proof)
    for native,mapping in complete_mapping.items():replacements.setdefault(native,{}).update(mapping)
    edits=[t['edit'] for t in bindings['textures']]
    template=face_material.template(out,ENGINE) if surfaces else None
    export_progress.update('正在烘焙贴图（保留原分辨率）…',phase='baking',total=len(surfaces)*3+(12 if mech_active else 0),completed=0)
    if mech_active:resources.extend(mech_appearance.build(out,render_state,cache))
    futures=[]
    # Bake on bounded workers, then encode same-format inputs in native batches.
    # Collect in submission order so private resource bindings stay deterministic.
    with ThreadPoolExecutor(max_workers=min(4,max(2,(os.cpu_count() or 4)//2)),thread_name_prefix='HD2Texture') as pool:
        for s in surfaces:
            part=model(s['unit'])[0]['parts'][s['part']]
            if not part.get('nativeMapping'):raise ValueError('该载具的精确面映射尚未通过，已保留工程，未输出会串图的替代贴图')
            setting=setting_for(render_state,s['unit'],part);size=resolution(s)
            if model(s['unit'])[0].get('lodLevel',0)>0:size=min(size,1024)
            elif model(s['unit'])[0].get('kind')=='hangar':size=min(size,2048)
            elif VEHICLES.get(s['unit'],{}).get('categoryV3') in ['飞行载具','地面载具']:size=min(size,4096)
            s['resolution']=size;mid=legacy.hash_id('content/mods/vehicle_faces/'+state['name']+'/'+s['unit']+'/'+s['id']);s['material_id']=mid;texture_ids={}
            for channel,fmt in [('color','BC7_UNORM_SRGB'),('normal','BC5_UNORM'),('mra','BC7_UNORM')]:
                tid=legacy.hash_id('content/mods/vehicle_faces/'+state['name']+'/'+s['id']+'/'+channel)
                bake=lambda s=s,size=size,setting=setting,channel=channel:color_bake(s,size,setting) if channel=='color' else base_bake(s,channel,min(2048,size),setting)
                futures.append(pool.submit(cache.prepare_texture,s,channel,fmt,setting,tid,bake));texture_ids[channel]=tid
            resources.append(dict(id=mid,type=legacy.MATERIAL,main=face_material.bind(template,texture_ids),gpu=b'',stream=b''));s['material_shader']=f'{face_material.PARENT:016x}'
        try:resources.extend(future.result() for future in futures)
        except BaseException:
            cache.abort_event.set()
            for future in futures:future.cancel()
            raise
    cache.flush();resources=[cache.resolve(r) for r in resources]
    export_progress.update('正在生成载具补丁…')
    native_batches={};native_owners={}
    for s in surfaces:
        owner=s['unit'];descriptor=model(owner)[0];native=descriptor['parts'][s['part']].get('nativeUnit',descriptor.get('nativeUnit',owner))
        native_owners[native]=owner;native_batches.setdefault(native,[]).append(s)
    mounted=[]
    for entry in scope['units']:
        owner=entry['id'];descriptor,_=model(owner)
        for native in descriptor.get('mountedUnits',[]):
            mapping={int(p['material'],16):replacements.get(owner,{}).get(int(p['material'],16)) for p in descriptor['parts'] if p.get('nativeUnit')==native and not p.get('localColorOnly')}
            mapping={k:v for k,v in mapping.items() if v is not None}
            if mapping:replacements.setdefault(native,{}).update(mapping)
            mounted.append(dict(id=native,owner=owner))
    scope['mounted_units']=mounted
    scope['mounted_units'].extend(dict(id=a.get('nativeUnit',a['id']),owner=a['owner'],name=a['name'],kind=a.get('kind','appearance')) for a in appearances if a.get('nativeUnit',a['id'])!=a['owner'])
    scope['appearance_states']=[dict(id=a['id'],native_unit=a.get('nativeUnit',a['id']),owner=a['owner'],name=a['name'],kind=a.get('kind','appearance'),lod=a.get('lodLevel',0)) for a in appearances]
    painted_units=set(native_batches);unit_ids=painted_units|set(replacements)
    local_shared=[];shared_data={}
    for uid in sorted(unit_ids):
        export_progress.check_cancelled()
        parts=legacy.protect_resource(out,'unit',uid[2:])
        if uid in painted_units:
            d,b=model(native_owners[uid]);gid=struct.unpack_from('<Q',parts['main'],16)[0];shared=legacy.protect_resource(out,'geometry_group',f'{gid:016x}') if gid else None
            r,p=build_unit(uid,d,b,native_batches[uid],parts['main'],parts.get('gpu',b''),legacy.hash_id,shared);resources.extend(r.pop('dependencies'));proof += [dict(unit=uid,owner=native_owners[uid],**row) for row in p]
        else:r=dict(id=int(uid,16),type=UNIT,main=parts['main'],gpu=parts.get('gpu',b''),stream=parts.get('stream',b''))
        r['main'],unit_bindings=export_scope.bind_colors(r['main'],replacements.get(uid,{}))
        gid=struct.unpack_from('<Q',parts['main'],16)[0]
        if gid and uid not in painted_units:
            source=legacy.protect_resource(out,'geometry_group',f'{gid:016x}')
            if uid in flight_ids:
                # A recolor-only carrier still needs a complete private group
                # beside the patched Unit, matching the working V2 route.
                private=legacy.hash_id('content/mods/vehicle_private_geometry/'+uid+'/'+hashlib.sha256(source['main']+source['gpu']).hexdigest())
                raw=bytearray(r['main']);struct.pack_into('<Q',raw,16,private);r['main']=bytes(raw)
                resources.append(dict(id=private,type=legacy.hash_id('geometry_group'),main=source['main'],gpu=source['gpu'],stream=source.get('stream',b'')))
            else:
                from shared_unit import flatten
                from native_geometry import parse_geometry
                from native_writer import parse
                if gid not in shared_data:shared_data[gid]=parse_geometry(source['main'],source['gpu'])
                r,record=flatten(int(uid,16),parts['main'],shared_data[gid],parse(r['main'],b'')['materials']);local_shared.append(record)
        if unit_bindings:bindings['units'].append(dict(id=uid,materials=unit_bindings))
        if unit_bindings or uid in painted_units:resources.append(r)
    scope['written_units']=[f"0x{r['id']:016x}" for r in resources if r['type']==UNIT]
    allowed={u['id'] for u in scope['units']}|{u['id'] for u in mounted}
    if mech_active:allowed.add(mech_appearance.BODY)
    assert set(scope['written_units'])<=allowed,'Export wrote a Unit outside the selected vehicle family'
    legacy.dump(out/'reports/local_shared_units.json',local_shared)
    for resource in resources:
        if resource['type']==legacy.hash_id('geometry_group'):
            assert not (ENGINE/'Raw/geometry_group'/f'{resource["id"]:016x}.geometry_group.main').exists(),'共享几何必须使用私有编号'
    legacy.dump(out/'reports/resource_bindings.json',bindings);legacy.dump(out/'reports/export_scope.json',scope)
    parts=legacy.build_archive(out,resources);export_progress.update('正在独立回读校验补丁…',phase='verification',completed=0,total=0);check=export_progress.run([str(ENGINE/'verify_mod.exe'),str(out),str(CACHE_ROOT/'verified_dds')])
    if check.returncode:
        (out/'reports/verify_error.txt').write_bytes(check.stdout+check.stderr);raise ValueError('补丁回读检查失败，工程已保留：'+str(out))
    legacy.dump(out/'reports/precise_faces.json',[{k:v for k,v in s.items() if k!='layers'} for s in surfaces]);legacy.dump(out/'reports/vertex_preservation.json',proof);legacy.dump(out/'reports/native_color_edits.json',edits);legacy.dump(out/'reports/damaged_part_warnings.json',LAST_REMAP)
    legacy.dump(out/'reports/texture_cache.json',cache.report())
    legacy.dump(out/'reports/export_timings.json',export_progress.timings())
    cache.prune()
    package=out/'package';shutil.copy2(out/'preview.png',package/'preview.png')
    readme=TITLE+'''

导出范围：{label}及其配套部件的改动。
标准原生涂装为基准。用MOD管理器导入本ZIP。
颜色材质与贴图使用私有编号，只绑定导出范围内的载具，不覆盖其他载具共用的原始资源。
本包采用连续投射贴花，允许跨分离零件与间隙；凸起和曲面使用独立非退化贴图区域；同一物理部件的多个图层和局部底色按界面图层顺序合成为同一张图，不再拆成多个共面绘制组。
原车三角面保留，贴花是独立外壳：完整部件外移 1.5 mm，损坏状态部件外移 4 mm，避免完整车体共面时被车身盖住。
除 UV0 和贴花外壳的 1.5/4 mm 法线偏移外，原顶点位置、法线、骨骼权重与其他字段保持不变；碰撞资源未替换。
图集按像素中心对应，边缘颜色扩展到 32px 保护带；透明图案使用预乘透明度采样，投影尺寸只缩放一次。
图片原稿完整保留，输出采用高分辨率BC7及完整MIP。
preview.png是本工程3D预览，非游戏截图；新几何/材质路径仍需游戏内复核。
'''.replace('{label}',scope['label'])
    if mech_active:
        readme=readme.replace('颜色材质与贴图使用私有编号，只绑定导出范围内的载具，不覆盖其他载具共用的原始资源。','四型机甲各自使用独立的颜色与图案贴图，保留游戏按机甲类型切换材质的关系。四型共用主体几何，因此四型外观一起打包；修改一型不会将其图案复制到其他三型。其余载具与武器使用私有配色材质。')
        readme=readme.replace('原车三角面保留，贴花是独立外壳：','非机甲主体的原车三角面保留，贴花是独立外壳：')
        readme+='\n机甲主体不再使用共享贴花外壳，而是在各型完整材质中合成图案；骨骼、位置、原法线及可见性编号保留。\n'
    (package/'使用说明.txt').write_text(readme,encoding='utf-8-sig');legacy.dump(package/'mod_manifest.json',dict(name=state['name'],tool=TITLE,version=target.stem,archive=target.name,preview='preview.png',scope=scope,precise_surfaces=len(surfaces),max_image_resolution=max([s['resolution'] for s in surfaces],default=0),shell_offsets_mm=dict(intact=1.5,damaged=4.0),placement_scale_applied=True,atlas_padding=32,atlas_uv_inset=0,alpha_correct_sampling=True,verified_texture_cache=True,damaged_part_front_copy_removed=True,game_tested=False))
    export_progress.update('正在打包工程与预览…')
    zipfile_path=zip_export.finish(out,target,parts,ENGINE,dict(precise_surfaces=len(surfaces),native_vertex_proof=len(proof)))
    return dict(path=str(target.parent),zip=str(zipfile_path),version=target.stem,scope=scope['label'],units=len(scope['written_units']),surfaces=len(surfaces),seconds=round(time.perf_counter()-started,2),cacheHits=cache.report()['hits'],cacheMisses=cache.report()['misses'])
