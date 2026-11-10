"""Keep one private shared geometry buffer for the selected vehicle cohort."""
import copy,hashlib,struct
from pathlib import Path
import builder
from native_geometry import parse_geometry,clone_geometry
from native_writer import UNIT

def coalesce(resources,out):
    typ=builder.hash_id('geometry_group');groups={};by_id={r['id']:r for r in resources if r['type']==typ};proof=[]
    for unit in resources:
        if unit['type']!=UNIT:continue
        current=struct.unpack_from('<Q',unit['main'],16)[0]
        if current not in by_id:continue
        source=Path(out)/'source/Raw/unit'/f"{unit['id']:016x}.unit.main"
        original=struct.unpack_from('<Q',source.read_bytes(),16)[0]
        if original:groups.setdefault(original,[]).append((unit,by_id[current]))
    removed=set();added=[]
    for original,cohort in groups.items():
        if len(cohort)<2:continue
        prefix=Path(out)/'source/Raw/geometry_group'/f'{original:016x}.geometry_group';main=Path(str(prefix)+'.main').read_bytes();original_gpu=Path(str(prefix)+'.gpu').read_bytes();base=parse_geometry(main,original_gpu);n=len(base['layouts']);layouts=list(base['layouts']);gpu=bytearray(original_gpu);uids=[]
        for unit,resource in sorted(cohort,key=lambda pair:pair[0]['id']):
            assert resource['gpu'][:len(original_gpu)]==original_gpu
            data=parse_geometry(resource['main'],resource['gpu']);meshes=copy.deepcopy(data['entries'][unit['id']]['meshes'])
            # Match the original prefix's alignment residue before appending a
            # clone's tail, so all relocated vertex/index addresses stay aligned.
            gpu.extend(bytes((len(original_gpu)-len(gpu))%64));delta=len(gpu)-len(original_gpu);start=len(layouts)
            for layout in data['layouts'][n:]:
                raw=bytearray(layout['raw'])
                for pos in [416,424]:struct.pack_into('<I',raw,pos,struct.unpack_from('<I',raw,pos)[0]+delta)
                layouts.append(dict(raw=bytes(raw)))
            gpu.extend(resource['gpu'][len(original_gpu):])
            for mesh in meshes:
                if mesh['layout']>=n:mesh['layout']=start+mesh['layout']-n
            current=parse_geometry(main,bytes(gpu));main=clone_geometry(current,unit['id'],layouts,meshes);removed.add(resource['id']);uids.append(unit['id'])
        digest=hashlib.sha256(main+gpu).hexdigest();new=builder.hash_id('content/mods/vehicle_cohort_geometry/'+f'{original:016x}/'+digest)
        assert new!=original
        for unit,_ in cohort:
            raw=bytearray(unit['main']);struct.pack_into('<Q',raw,16,new);unit['main']=bytes(raw)
        added.append(dict(id=new,type=typ,main=main,gpu=bytes(gpu),stream=b''))
        proof.append(dict(original=f'{original:016x}',private=f'{new:016x}',units=[f'{u:016x}' for u in uids],original_gpu_bytes=len(original_gpu),combined_gpu_bytes=len(gpu),discarded_duplicate_gpu_bytes=sum(len(r['gpu']) for _,r in cohort)-len(gpu),original_prefix_preserved=True))
    builder.dump(Path(out)/'reports/shared_geometry_batch.json',dict(status='passed',groups=proof))
    return [r for r in resources if not (r['type']==typ and r['id'] in removed)]+added
