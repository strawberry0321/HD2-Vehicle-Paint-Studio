"""Project a vehicle design onto additional native appearance states."""
from pathlib import Path
import copy,json,uuid
from unit_colors import effective
REGISTRY=json.loads((Path(__file__).resolve().parent/'data/appearance_variants.json').read_text(encoding='utf8'))

def variants(state):
    import face_builder as fb
    owners=state.get('exportScope',{}).get('units',[])
    return [dict(row,owner=owner) for owner in owners for row in REGISTRY.get(owner,[])
            if not (fb.VEHICLES[owner].get('categoryV3')=='飞行载具' and row.get('lodLevel',0)>0)]

def remap(surfaces,descriptor):
    selected={s['part']:set(s['triangles']) for s in surfaces};result=[]
    for part,p in enumerate(descriptor['parts']):
        source=selected.get(p['sourcePart'])
        if source:
            triangles=[i for i,t in enumerate(p['sourceTriangles']) if t in source]
            if triangles:result.append(dict(part=part,triangles=triangles))
    return result

def expand(state):
    import face_builder as fb
    aliases=variants(state)
    if not aliases:return state,aliases
    result=copy.copy(state);result['layers']=list(state['layers']);result['surfaceColors']=list(state.get('surfaceColors',[]));result['unitColors']=copy.deepcopy(state.get('unitColors',{}))
    for row in aliases:
        uid,owner=row['id'],row['owner'];descriptor,_=fb.model(uid)
        result['unitColors'][uid]={gid:copy.deepcopy(effective(state,owner,gid)) for gid in fb.VEHICLES[owner]['groups']}
        for kind in ['layers','surfaceColors']:
            for original in state.get(kind,[]):
                if original['unit']!=owner or original.get('hidden'):continue
                record=copy.deepcopy(original);record['unit']=uid;record['id']=str(uuid.uuid5(uuid.NAMESPACE_URL,original['id']+'/'+uid));record['surfaces']=remap(original['surfaces'],descriptor)
                record['coverageTransfer']=row.get('kind') in ['lod','hangar']
                if kind=='layers' and record.get('projection'):
                    mask=original['projection'].get('mask')
                    if mask:
                        record['projection']['mask']=remap(mask,descriptor)
                        if not record['projection']['mask']:continue
                elif not record['surfaces']:continue
                result[kind].append(record)
    return result,aliases

def bind_colors(replacements,aliases):
    for row in aliases:
        target=row.get('nativeUnit',row['id'])
        if target!=row['owner'] and row['owner'] in replacements:
            replacements.setdefault(target,{}).update(replacements[row['owner']])
