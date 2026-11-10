"""Choose vehicle families and keep their color resources private to those Units."""
import copy,hashlib,json,struct
from pathlib import Path
import builder as legacy
from coloring import recolor_srgb
from native_writer import parse
from unit_colors import effective,for_unit

def choose(state,requested,catalog):
    import mech_appearance
    vehicles={v['id']:v for v in catalog['vehicles']};roots=[v for v in catalog['vehicles'] if v['role']=='主体']
    scope=requested or dict(kind='vehicle',family=state.get('family',state['vehicle']))
    if scope.get('kind')=='vehicle':
        family=scope.get('family',state.get('family',state['vehicle']))
        if family not in vehicles or vehicles[family]['role']!='主体':raise ValueError('请选择一个载具类型')
        families=[family];label=vehicles[family]['name']
        if family in mech_appearance.VARIANTS:
            families=list(mech_appearance.VARIANTS);label='四型机甲（各型独立外观）'
    elif scope.get('kind')=='category':
        label=scope.get('category');families=[v['id'] for v in roots if v.get('categoryV3')==label]
        if not families:raise ValueError('载具大类别无效')
        label='全部'+label
    elif scope.get('kind')=='all':families=[v['id'] for v in roots];label='全部载具'
    else:raise ValueError('导出范围无效')
    units=[v for v in catalog['vehicles'] if v['id'] in families or any(p in families for p in v.get('parents',[]))]
    ids={v['id'] for v in units};group_ids={g for v in units for g in v['groups']};filtered=copy.deepcopy(state)
    filtered['layers']=[l for l in filtered['layers'] if l['unit'] in ids]
    filtered['surfaceColors']=[p for p in filtered.get('surfaceColors',[]) if p['unit'] in ids]
    for gid,setting in filtered['groups'].items():
        if gid not in group_ids:setting['mode']='original'
    filtered['exportScope']=dict(kind=scope['kind'],label=label,families=families,units=[v['id'] for v in units])
    if filtered['vehicle'] not in ids:filtered['vehicle']=families[0];filtered['family']=families[0]
    filtered['unitColors']={uid:values for uid,values in filtered.get('unitColors',{}).items() if uid in ids}
    changed={g for v in units for g in v['groups'] if effective(filtered,v['id'],g)['mode']!='original'}
    return filtered,dict(kind=scope['kind'],label=label,families=families,units=[dict(id=v['id'],name=v['name'],role=v['role']) for v in units],changed_groups=sorted(changed),layers=sum(not l.get('hidden') for l in filtered['layers']),surface_colors=sum(not p.get('hidden') for p in filtered['surfaceColors']))

def _shared_colors(state,scope,catalog,out,fixed=None):
    units={v['id'] for v in scope['units']};changed=set(scope['changed_groups']);groups={g['id']:g for g in catalog['groups']}
    material_ids={int(b['material'],16) for gid in changed for b in groups[gid]['bindings'] if b['unit'] in units}
    if fixed:material_ids.add(fixed['id'])
    sources={mid:legacy.protect_resource(out,'material',f'{mid:016x}')['main'] for mid in material_ids}
    textures={};resources=[];proof=dict(textures=[],materials=[],units=[])
    for t in legacy.PLAN['textures']:
        old=int(t['id'],16)
        if old in textures or t['group'] not in changed or state['groups'][t['group']]['mode']=='original' or not any(struct.pack('<Q',old) in m for m in sources.values()):continue
        resource,edit=legacy.native_color(t,state['groups'][t['group']],out)
        new=legacy.hash_id('content/mods/vehicle_color_texture/'+f'{old:016x}/'+hashlib.sha256(resource['main']+resource.get('gpu',b'')+resource.get('stream',b'')).hexdigest())
        assert new!=old;resource['id']=new;textures[old]=new;resources.append(resource);proof['textures'].append(dict(original=f'{old:016x}',private=f'{new:016x}',edit=edit))
    replacements={}
    for mid,original in sources.items():
        data=fixed['main'] if fixed and mid==fixed['id'] else original
        extra_fixed=False
        import stock_decals
        data,decal_changes=stock_decals.clean(data,state.get('removeStockDecals',True))
        if mid==0x0f3201437c248dfc and state['groups']['P15']['mode']!='original':
            setting=state['groups']['P15'];buffer=bytearray(data);nt=struct.unpack_from('<I',data,64)[0];ns=struct.unpack_from('<I',data,104)[0];defs=136+nt*12;base=defs+ns*20
            for i in range(ns):
                typ,count,usage,offset,stride=struct.unpack_from('<5I',data,defs+i*20)
                if usage==(legacy.hash_id('base_color')>>32):
                    color=legacy.color(setting['color']);old=struct.unpack_from('<3f',data,base+offset)
                    if setting['mode']=='native':color=recolor_srgb(old,color,legacy.color(groups['P15']['defaultColor']))
                    struct.pack_into('<3f',buffer,base+offset,*color);extra_fixed=True
            assert extra_fixed;data=bytes(buffer)
        for old,new in textures.items():data=data.replace(struct.pack('<Q',old),struct.pack('<Q',new))
        if data==original:continue
        new=legacy.hash_id('content/mods/vehicle_color_material/'+f'{mid:016x}/'+hashlib.sha256(data).hexdigest());assert new!=mid
        replacements[mid]=new;resources.append(dict(id=new,type=legacy.MATERIAL,main=data,gpu=b'',stream=b''));proof['materials'].append(dict(original=f'{mid:016x}',private=f'{new:016x}',fixed=bool((fixed and mid==fixed['id']) or extra_fixed),removed_stock_decal_parameters=decal_changes))
    return resources,replacements,proof

def private_colors(state,scope,catalog,out,fixed=None):
    from face_builder import repair_fixed_color,VEHICLES
    import shutil
    resources={};replacements={};proof=dict(textures=[],materials=[],units=[])
    for unit in scope['units']:
        uid=unit['id'];local=for_unit(state,uid)
        import mech_appearance
        if uid in mech_appearance.VARIANTS:continue
        changed=[gid for gid in VEHICLES[uid]['groups'] if local['groups'][gid]['mode']!='original']
        if state.get('removeStockDecals',True):
            for record in state.get('layers',[])+state.get('surfaceColors',[]):
                if record['unit']!=uid or record.get('hidden'):continue
                from face_builder import model
                for selected in record.get('surfaces',[]):
                    gid=model(uid)[0]['parts'][selected['part']].get('group')
                    if gid and gid not in changed:changed.append(gid)
        if not changed:continue
        work=out/'color_work'/uid[2:];work.mkdir(parents=True,exist_ok=True)
        repair=repair_fixed_color(work,local,uid) if VEHICLES[uid].get('isFRV') or VEHICLES[uid].get('fixedColorGroup') else None
        generated,mapping,checks=_shared_colors(local,dict(units=[unit],changed_groups=changed),catalog,work,repair)
        if mapping:replacements[uid]=mapping
        for resource in generated:
            key=(resource['id'],resource['type'])
            if key in resources:assert resources[key]==resource,'Conflicting private resource ID'
            else:resources[key]=resource
        for kind in ['textures','materials']:proof[kind].extend(dict(unit=uid,**row) for row in checks[kind])
        for src in (work/'source').rglob('*'):
            if not src.is_file():continue
            if src.relative_to(work/'source').as_posix()=='dependency_manifest.json':continue
            dst=out/'source'/src.relative_to(work/'source');dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists():
                if dst.read_bytes()!=src.read_bytes():raise ValueError('来源备份内容冲突：'+str(src.relative_to(work/'source')))
            else:shutil.copy2(src,dst)
        journal=work/'source/dependency_manifest.json'
        if journal.exists():merge_dependency_journal(out,journal)
    return list(resources.values()),replacements,proof

def merge_dependency_journal(out,journal):
    """Union per-unit provenance records while still checking each raw copy."""
    out=Path(out)
    target=out/'source/dependency_manifest.json'
    records=json.loads(target.read_text(encoding='utf8')) if target.exists() else []
    index={row['copy'].replace('\\','/'):row for row in records}
    for row in json.loads(journal.read_text(encoding='utf8')):
        key=row['copy'].replace('\\','/');copied=(out/key).resolve()
        if not copied.is_relative_to((out/'source').resolve()):raise ValueError('来源清单路径超出备份目录')
        actual=hashlib.sha256(copied.read_bytes()).hexdigest()
        if actual!=row['sha256']:raise ValueError('来源副本校验失败：'+key)
        if key in index:
            if index[key]['sha256']!=row['sha256']:raise ValueError('来源清单内容冲突：'+key)
        else:
            index[key]=dict(row,copy=key)
    legacy.dump(target,[index[key] for key in sorted(index)])

def bind_colors(main,replacements):
    d=parse(main,b'');bindings={k:replacements.get(v,v) for k,v in d['materials'].items()}
    if bindings==d['materials']:return main,[]
    b=bytearray(main);b.extend(bytes((-len(b))%16));struct.pack_into('<I',b,112,len(b));items=list(bindings.items());b.extend(struct.pack('<I',len(items)));b.extend(struct.pack('<'+'I'*len(items),*(k for k,v in items)));b.extend(struct.pack('<'+'Q'*len(items),*(v for k,v in items)))
    return bytes(b),[dict(slot=f'{k:08x}',original=f"{d['materials'][k]:016x}",private=f'{v:016x}') for k,v in items if v!=d['materials'][k]]
