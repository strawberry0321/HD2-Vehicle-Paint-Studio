"""Four logical mech appearances, one immutable geometry and native swap slots."""
from pathlib import Path
import copy,hashlib,json,math,struct
from compute_runtime import np
from PIL import Image,ImageDraw
import builder as legacy
from unit_colors import effective

APP=Path(__file__).resolve().parent
BODY='0x79e4b3d2da5e45e3'
VARIANTS={
    BODY:dict(name='爱国者机甲',material='23a41cbe0203470f'),
    '0xc2d449ecf7facab1':dict(name='解放者机甲',material='43cdcd71bb55e04a'),
    '0x7b2326f6fd9c8069':dict(name='伐木者机甲',material='d02d6e1e8ea98d4f'),
    '0x35dbf54f016f3624':dict(name='突破机甲',material='9dbda1aeb7dfa9ab'),
}

def migrate(state):
    if state.get('mechMaterialRevision',1)>=2:return state
    # Geometry, selections and projection frames keep their original numbering.
    # Only old UV-reuse bounds need moving from a repeated tile to the atlas.
    import face_builder as fb
    for layer in state.get('layers',[]):
        if layer['unit'] not in VARIANTS or not layer.get('uvReuse'):continue
        parts={s['part'] for s in layer['surfaces']}
        if len(parts)!=1:raise ValueError('旧机甲工程的复用图层跨多个贴图区，请改用投射图层后导出')
        part=fb.model(layer['unit'])[0]['parts'][next(iter(parts))]
        transform=part.get('legacyUvTransform')
        if not transform:continue
        reuse=layer['uvReuse'];offset=np.array(transform['offset']);scale=np.array(transform['scale']);bounds=np.array(reuse['bounds']).reshape(2,2)
        reuse['bounds']=(bounds*scale+offset).ravel().tolist()
        for axis,field in enumerate(['worldU','worldV']):
            if field in reuse:reuse[field]=(np.array(reuse[field])/scale[axis]).tolist()
    state['mechMaterialRevision']=2
    return state

def active(state):
    units=state.get('exportScope',{}).get('units',[])
    return any(uid in VARIANTS and (
        any(effective(state,uid,g)['mode']!='original' for g in ['P01','P02']) or
        any(l['unit']==uid and not l.get('hidden') and l.get('opacity',1)>0 for l in state.get('layers',[])+state.get('surfaceColors',[])))
        for uid in units)

def _destinations(uid,part,size,triangles):
    import face_builder as fb
    _,_,uv=fb.part_arrays(uid,part)
    return {t:np.c_[uv[t*3:t*3+3,0]*size-.5,(1-uv[t*3:t*3+3,1])*size-.5] for t in triangles}

def _flat_layer(image,surface,layer,size,destinations,source):
    import atlas_baker,face_builder as fb
    _,positions,_=fb.part_arrays(surface['unit'],surface['part']);f=layer['frame'];p=layer['placement'];asset=fb.library.describe(layer['asset']);pw,ph=atlas_baker.image_size(layer,asset);bounds=f['bounds'];cx=(bounds[0]+bounds[2])/2+p['x']*(bounds[2]-bounds[0]);cy=(bounds[1]+bounds[3])/2+p['y']*(bounds[3]-bounds[1]);angle=-p['angle']*math.pi/180;ca,sa=math.cos(angle),math.sin(angle)
    for t in layer['triangles']:
        dst=destinations[t];box=atlas_baker.box_for(dst,size)
        if box[2]<=box[0] or box[3]<=box[1]:continue
        rel=positions[t*3:t*3+3]-np.array(f['origin']);x=rel@np.array(f['u'])-cx;y=rel@np.array(f['v'])-cy;sample=np.c_[((ca*x-sa*y)/pw+.5)*source.width,(.5-(sa*x+ca*y)/ph)*source.height]
        try:coeff=np.linalg.solve(np.c_[dst-np.array(box[:2]),np.ones(3)],sample).T
        except np.linalg.LinAlgError:continue
        patch=source.transform((box[2]-box[0],box[3]-box[1]),Image.Transform.AFFINE,tuple(coeff.ravel()),Image.Resampling.BICUBIC);mask=Image.new('L',patch.size);ImageDraw.Draw(mask).polygon([tuple(x-np.array(box[:2])) for x in dst],fill=255)
        alpha=np.asarray(patch.getchannel('A'),np.float32)*np.asarray(mask,np.float32)/255*layer['opacity'];patch.putalpha(Image.fromarray(np.rint(alpha).astype('uint8')));image.paste(Image.alpha_composite(image.crop(box),patch),box[:2])
    return image

def color_bake(uid,state,size,baseline=None):
    import face_builder as fb,projection_baker,uv_reuse
    from coloring import recolor_srgb
    descriptor,_=fb.model(uid);baseline=Path(baseline) if baseline else APP/'data/textures/mech'/uid[2:]/'color.png'
    image=Image.open(baseline).convert('RGB').resize((size,size),Image.Resampling.LANCZOS)
    config=json.loads((APP/'data/mech_atlas.json').read_text(encoding='utf8'));scale=size/config['size'];slots={'546657cb':'P01','9685707c':'P02'}
    rectangles=[dict(slot=r['slot'],rect=r['rect']) for r in config['rects']]+[dict(slot=f"{r['slot']:08x}",rect=r['rect']) for r in config['degenerateCharts']]
    for row in rectangles:
        gid=slots[row['slot']];setting=effective(state,uid,gid)
        if setting['mode']=='original':continue
        x,y,w=row['rect'];box=(round(x*scale),round(y*scale),round((x+w)*scale),round((y+w)*scale))
        if setting['mode']=='solid':image.paste(setting['color'],box)
        else:
            part=next(p for p in descriptor['parts'] if p.get('group')==gid);reference=legacy.color(part.get('defaultColor',fb.GROUPS[gid]['defaultColor']));pixels=np.asarray(image.crop(box),np.float32)/255;pixels=recolor_srgb(pixels,legacy.color(setting['color']),reference);image.paste(Image.fromarray(np.rint(pixels*255).astype('uint8')),box[:2])
    image=image.convert('RGBA')
    for patch in state.get('surfaceColors',[]):
        if patch['unit']!=uid or patch.get('hidden'):continue
        for surface in patch['surfaces']:
            destinations=_destinations(uid,surface['part'],size,surface['triangles']);draw=ImageDraw.Draw(image)
            for dst in destinations.values():draw.polygon([tuple(x) for x in dst],fill=patch['color'])
    for layer in state['layers']:
        if layer['unit']!=uid or layer.get('hidden') or layer['opacity']<=0:continue
        source=Image.open(fb.library.path_for(layer['asset'])).convert('RGBA')
        if layer.get('tint'):
            tint=legacy.color(layer['color']);rgb=source.convert('RGB');rgb=Image.merge('RGB',[rgb.getchannel(i).point([round(x*tint[i]) for x in range(256)]) for i in range(3)]);rgb.putalpha(source.getchannel('A'));source=rgb
        for selected in layer['surfaces']:
            part=selected['part']
            if descriptor['parts'][part].get('group') not in ['P01','P02']:raise ValueError('机甲灯光使用原生功能材质，请将图案放在主体或机械部件上')
            record=copy.copy(layer);record['triangles']=selected['triangles'];surface=dict(unit=uid,part=part);destinations=_destinations(uid,part,size,record['triangles'])
            if layer.get('projection'):image=projection_baker.bake(image,surface,record,size,destinations,source)
            elif layer.get('uvReuse'):image=uv_reuse.bake(image,surface,record,size,destinations,source)
            else:image=_flat_layer(image,surface,record,size,destinations,source)
    return image.convert('RGB')

def build(out,state,cache):
    """Retain native material IDs so the game's type-specific swaps still apply."""
    import face_builder as fb,face_material,export_progress
    resources=[];report=[];shared_images={};template=face_material.template(out,fb.ENGINE)
    for uid,row in VARIANTS.items():
        layers=[l for l in state['layers'] if l['unit']==uid and not l.get('hidden') and l['opacity']>0]
        colors=[p for p in state.get('surfaceColors',[]) if p['unit']==uid and not p.get('hidden')]
        settings={g:copy.deepcopy(effective(state,uid,g)) for g in ['P01','P02']}
        changed=bool(layers or colors or any(s['mode']!='original' for s in settings.values()));size=8192 if layers or colors else 4096
        ids={c:legacy.hash_id('content/mods/independent_mech/'+uid+'/'+c+'/'+state['name']) for c in ['color','normal','mra']}
        hashes={}
        for channel,fmt in [('color','BC7_UNORM_SRGB'),('normal','BC5_UNORM'),('mra','BC7_UNORM')]:
            baseline=fb.ENGINE/'mech_atlas'/uid[2:]/(channel+'.dds')
            if channel=='color' and changed:
                # Once the body is solid, its original type-specific paint is
                # completely covered. The mechanical baseline is genuinely
                # shared, so identical visible inputs can share encoded data.
                cache_uid=BODY if settings['P01']['mode']=='solid' else uid
                source=legacy.protect_file(out,APP/'data/textures/mech'/cache_uid[2:]/'color.png',Path('mech_atlas')/cache_uid[2:]/'color.png')
                canonical=lambda items:[dict(item,unit=cache_uid) for item in items]
                surface=dict(unit=cache_uid,part=0,triangles=[],atlas=dict(kind='independent-mech',size=size,selections=[dict(surfaces=l['surfaces']) for l in layers]),layers=canonical(layers),baseColors=canonical(colors),resolution=size)
                resource=cache.texture(surface,channel,fmt,settings,ids[channel],lambda:color_bake(uid,state,size,source),lambda im:fb.new_texture(out,im,ids[channel],fmt))
            else:
                protected=legacy.protect_file(out,baseline,Path('mech_atlas')/uid[2:]/(channel+'.dds'))
                raw=protected.read_bytes();resource=fb.texture_from_dds(raw,ids[channel]);export_progress.texture_done()
            digest=hashlib.sha256(resource['main']+resource.get('gpu',b'')).hexdigest();hashes[channel]=digest
            content_id=legacy.hash_id('content/mods/independent_mech/image/'+channel+'/'+digest);resource['id']=content_id;ids[channel]=content_id
            if content_id in shared_images:assert shared_images[content_id]==resource,'Conflicting content-addressed mech image'
            else:shared_images[content_id]=resource;resources.append(resource)
        original=legacy.protect_resource(out,'material',row['material'])['main']
        resources.append(dict(id=int(row['material'],16),type=legacy.MATERIAL,main=face_material.bind(template,ids),gpu=b'',stream=b''))
        report.append(dict(id=uid,name=row['name'],native_material=row['material'],changed=changed,texture_payload_hashes=hashes,original_material_sha256=hashlib.sha256(original).hexdigest(),native_type_swap_preserved=True))
    source=legacy.protect_resource(out,'unit',BODY[2:]);geometry=fb.ENGINE/'mech_atlas';main=legacy.protect_file(out,geometry/'body.unit.main',Path('mech_atlas/body.unit.main')).read_bytes();gpu=legacy.protect_file(out,geometry/'body.unit.gpu',Path('mech_atlas/body.unit.gpu')).read_bytes();assert gpu[:len(source['gpu'])]==source['gpu']
    resources.append(dict(id=int(BODY,16),type=fb.UNIT,main=main,gpu=gpu,stream=b''));legacy.dump(out/'reports/mech_appearances.json',dict(status='passed',shared_geometry=True,independent_materials=True,shared_overlay_slots_removed=True,appearances=report))
    return resources
