"""Complete paint materials and bake shared props in their original UVs.

No geometry_group resource is emitted. The source Unit keeps its rig, LODs,
draw groups and shared-geometry identity; only its material bindings change.
"""
from pathlib import Path
import collections,copy,hashlib,json,struct
from PIL import Image,ImageDraw
from compute_runtime import np
import builder as legacy
import face_material
from unit_colors import effective

APP=Path(__file__).resolve().parent

def native_owner(uid,part,descriptor):
    return part.get('nativeUnit',descriptor.get('nativeUnit',uid))

def is_shared(out,uid):
    source=legacy.protect_resource(out,'unit',uid[2:])
    return bool(struct.unpack_from('<Q',source['main'],16)[0])

def _paint(image,surface,state,size):
    import face_builder as fb,mech_appearance,projection_baker,uv_reuse
    p,positions,uv=fb.part_arrays(surface['unit'],surface['part'])
    # Native UV0 samples the top-origin image. Workbench V is inverted.
    destinations={t:np.c_[uv[t*3:t*3+3,0]*size-.5,(1-uv[t*3:t*3+3,1])*size-.5] for t in surface['triangles']}
    draw=ImageDraw.Draw(image)
    for color in surface.get('baseColors',[]):
        for t in color['triangles']:draw.polygon([tuple(x) for x in destinations[t]],fill=color['color'])
    for layer in surface['layers']:
        source=Image.open(fb.library.path_for(layer['asset'])).convert('RGBA')
        if layer.get('tint'):
            tint=legacy.color(layer['color']);rgb=source.convert('RGB');rgb=Image.merge('RGB',[rgb.getchannel(i).point([round(v*tint[i]) for v in range(256)]) for i in range(3)]);rgb.putalpha(source.getchannel('A'));source=rgb
        if layer.get('projection'):image=projection_baker.bake(image,surface,layer,size,destinations,source)
        elif layer.get('uvReuse'):image=uv_reuse.bake(image,surface,layer,size,destinations,source)
        else:image=mech_appearance._flat_layer(image,surface,layer,size,destinations,source)
    return image

def _channel_image(source,job,channel,size,state):
    import face_builder as fb
    from coloring import recolor_srgb
    part=job['descriptor'];setting=job['setting'];records=job['surfaces'];image=Image.open(source).convert('RGB')
    if channel=='color':
        if setting['mode']=='solid':image=Image.new('RGB',(size,size),setting['color'])
        else:
            image=image.resize((size,size),Image.Resampling.LANCZOS)
            if setting['mode']=='native':
                reference=part.get('defaultColor',fb.GROUPS[part['group']]['defaultColor']);pixels=recolor_srgb(np.asarray(image,np.float32)/255,legacy.color(setting['color']),legacy.color(reference));image=Image.fromarray(np.rint(pixels*255).astype('uint8'))
        image=image.convert('RGBA')
        for surface in sorted(records,key=lambda s:fb.model(s['unit'])[0].get('lodLevel',0),reverse=True):image=_paint(image,surface,state,size)
        return image.convert('RGB')
    image.thumbnail((1024,1024),Image.Resampling.LANCZOS)
    return image

def build(out,state,scope,appearances,shared_surfaces,cache=None):
    import face_builder as fb,export_progress
    from coloring import recolor_srgb
    jobs={};owners={v['id']:v for v in fb.CAT['vehicles']};shared_flags={}
    logical=[(u['id'],u['id']) for u in scope['units']]
    logical += [(r['id'],r['owner']) for r in appearances]
    for uid,owner in logical:
        vehicle=owners[owner]
        if vehicle.get('categoryV3') not in ['飞行载具','地面载具']:continue
        descriptor,_=fb.model(uid)
        for pi,part in enumerate(descriptor['parts']):
            gid=part.get('group')
            if not gid or part.get('nonRenderable') or part.get('decalDisabledReason') or part.get('partialColorSourceGroup') or part.get('sourceOpacity',1)<.999:continue
            setting=effective(state,uid,gid);native=native_owner(uid,part,descriptor)
            if native not in shared_flags:shared_flags[native]=is_shared(out,native)
            shared=shared_flags[native]
            if part.get('localColorOnly') and not shared and native==owner:continue
            records=[s for s in shared_surfaces if s['unit']==uid and s['part']==pi]
            if shared and records and not state.get('removeStockDecals',True):raise ValueError('机库图案需要完整材质替换，请启用“覆盖原版骷髅、编号与色条”后导出')
            touched=any(r['unit']==uid and not r.get('hidden') and any(s['part']==pi for s in r.get('surfaces',[])) for r in state.get('layers',[])+state.get('surfaceColors',[]))
            changed=setting['mode']!='original' or bool(records) or touched
            if not changed or not (state.get('removeStockDecals',True) or shared and records):continue
            original=legacy.protect_resource(out,'material',part['material'][2:])['main']
            parent=struct.unpack_from('<Q',original,24)[0]
            # Opaque paint shaders only: functional glass, lamps, shadow and
            # collision materials retain their native roles.
            if parent not in [0x43865ef50d88395e,0x6b9f33361b025760,0x2659a0b89d51d5a8,0x55f47c155e2a16fe]:continue
            key=(native,int(part['material'],16));job=jobs.get(key)
            if job is None:
                job=dict(native=native,owner=owner,uid=uid,part=pi,descriptor=part,setting=copy.deepcopy(setting),shared=shared,surfaces=[],original=original);jobs[key]=job
            elif job['setting']!=setting:raise ValueError('同一原生材质有冲突配色，请将对应颜色组设为一致后导出')
            job['surfaces'].extend(records)
    for s in shared_surfaces:
        descriptor,_=fb.model(s['unit']);part=descriptor['parts'][s['part']];key=(native_owner(s['unit'],part,descriptor),int(part['material'],16))
        if key not in jobs:raise ValueError('机库材质尚不支持原生 UV 烘焙，已保留工程：'+part.get('name','部件'))
    resources=[];mapping={};proof=[];template=face_material.template(out,fb.ENGINE);encoded={}
    export_progress.update('正在生成完整涂装与机库原生 UV 贴图…',total=len(jobs)*3,completed=0)
    for (native,old),job in sorted(jobs.items()):
        part=job['descriptor'];setting=job['setting'];records=job['surfaces'];size=2048 if records else 16 if setting['mode']=='solid' else 1024;ids={};hashes={}
        for channel,fmt in [('color','BC7_UNORM_SRGB'),('normal','BC5_UNORM'),('mra','BC7_UNORM')]:
            source=legacy.protect_file(out,APP/'data'/part[channel],Path('whole_material_sources')/part[channel])
            if cache:
                from texture_cache import digest as file_digest
                inputs=dict(source_sha256=file_digest(source),target_size=size if channel=='color' else 'source-thumbnail-1024')
                if channel=='color':
                    inputs.update(setting=setting,surfaces=records)
                    inputs['assets']={l['asset']:file_digest(fb.library.path_for(l['asset'])) for s in records for l in s['layers']}
                key=cache.image_key(channel,fmt,inputs);tid=legacy.hash_id('content/mods/complete_vehicle_material/'+channel+'/'+key)
                if tid not in encoded:
                    with Image.open(source) as header:w,h=header.size
                    scale=max(1,w/1024,h/1024)
                    dimensions=(size,size) if channel=='color' else (round(w/scale),round(h/scale))
                    bake=lambda source=source,job=job,channel=channel,size=size:_channel_image(source,job,channel,size,state)
                    encoded[tid]=cache.prepare_image(key,tid,fmt,bake,dimensions,part=job['part'],channel=channel);resources.append(encoded[tid])
                hashes[channel]=key
            else:
                image=_channel_image(source,job,channel,size,state)
                digest=hashlib.sha256(image.tobytes()+str(image.size).encode()+fmt.encode()).hexdigest();tid=legacy.hash_id('content/mods/complete_vehicle_material/'+channel+'/'+digest)
                if tid not in encoded:encoded[tid]=fb.new_texture(out,image,tid,fmt);resources.append(encoded[tid])
                hashes[channel]=digest;export_progress.texture_done()
            ids[channel]=tid
        raw=face_material.bind(template,ids);mid=legacy.hash_id('content/mods/complete_vehicle_material/'+f'{old:016x}/'+hashlib.sha256(raw).hexdigest());resources.append(dict(id=mid,type=legacy.MATERIAL,main=raw,gpu=b'',stream=b''));mapping.setdefault(native,{})[old]=mid
        proof.append(dict(unit=native,original=f'{old:016x}',private=f'{mid:016x}',replacement_kind='complete_surface_pbr',original_sha256=hashlib.sha256(job['original']).hexdigest(),source_parent=f'{struct.unpack_from("<Q",job["original"],24)[0]:016x}',original_marking_channels_removed=True,native_shared_geometry_retained=job['shared'],native_uv_surfaces=len(records),**({'texture_cache_keys':hashes} if cache else {'texture_inputs_sha256':hashes}),textures={c:f'{v:016x}' for c,v in ids.items()}))
    # Identical colors/materials across mounted props can share private assets.
    unique={}
    for r in resources:
        from texture_cache import PreparedTexture
        key=(r.tid,legacy.TEXTURE) if isinstance(r,PreparedTexture) else (r['id'],r['type'])
        if key in unique:assert unique[key]==r
        else:unique[key]=r
    legacy.dump(out/'reports/complete_materials.json',dict(status='passed_offline',materials=proof,geometry_group_resources_written=0,native_uv_bake=True))
    return list(unique.values()),mapping,proof
