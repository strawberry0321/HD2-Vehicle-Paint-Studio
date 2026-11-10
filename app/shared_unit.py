"""V5-02 ship prop conversion: local native geometry with all records retained."""
from pathlib import Path
import copy
import hashlib
import struct
import sys

sys.dont_write_bytecode=True
from native_writer import parse, pad, table, mesh_bytes, u32
from native_geometry import parse_geometry


def flatten(uid, main, geometry, material_bindings):
    source=parse(main,b'')
    assert source['geometry'] and not source['layouts']
    original=bytes(main)
    entry=geometry['entries'][uid]
    by_name={m['name']:m for m in entry['meshes']}
    assert len(by_name)==len(entry['meshes'])
    # Bundled geometry may retain obsolete mesh entries. Materializing those
    # would add objects absent from this Unit's active mesh/LOD metadata.
    assert {u32(m['raw'],40) for m in source['meshes']} <= set(by_name)
    meshes=copy.deepcopy(source['meshes']);gpu=bytearray();layouts=[];proof=[]
    for mesh in meshes:
        name=u32(mesh['raw'],40)
        shared=by_name[name]
        layout=geometry['layouts'][shared['layout']]
        assert layout['nIndices']>0 and layout['iBytes']%layout['nIndices']==0
        stride=layout['stride'];step=layout['iBytes']//layout['nIndices']
        assert step in [2,4]
        vertices=bytearray();indices=bytearray();groups=[]
        for group in shared['groups']:
            mat,vo,nv,io,ni,render=group
            v=geometry['gpu'][layout['vOff']+vo*stride:layout['vOff']+(vo+nv)*stride]
            idx=geometry['gpu'][layout['iOff']+io*step:layout['iOff']+(io+ni)*step]
            assert len(v)==nv*stride and len(idx)==ni*step and ni%3==0
            decoded=struct.unpack('<'+('H' if step==2 else 'I')*ni,idx)
            assert not decoded or max(decoded)<nv
            groups.append([mat,len(vertices)//stride,nv,len(indices)//step,ni,render])
            vertices.extend(v);indices.extend(idx)
        pad(gpu,64);vo=len(gpu);gpu.extend(vertices)
        pad(gpu,64);io=len(gpu);gpu.extend(indices)
        raw=bytearray(layout['raw']);count=len(vertices)//stride
        struct.pack_into('<I',raw,352,count)
        struct.pack_into('<I',raw,392,len(indices)//step)
        positions=count*12 if u32(layout['raw'],420)==layout['count']*12 else len(vertices)
        struct.pack_into('<4I',raw,416,vo,positions,io,len(indices))
        mesh['layout']=len(layouts);mesh['slots']=shared['slots'];mesh['groups']=groups
        layouts.append(bytes(raw))
        proof.append(dict(mesh=mesh['index'],name=f'{name:08x}',source_layout=shared['layout'],local_layout=mesh['layout'],
                          vertex_records_sha256=hashlib.sha256(vertices).hexdigest(),index_records_sha256=hashlib.sha256(indices).hexdigest(),
                          vertex_records=len(vertices)//stride,triangles=len(indices)//step//3,source_groups=shared['groups'],local_groups=groups))
    result=bytearray(main)
    struct.pack_into('<Q',result,16,0)
    pad(result);struct.pack_into('<I',result,92,len(result));result.extend(table(layouts))
    pad(result);struct.pack_into('<I',result,100,len(result))
    mesh_table=table([mesh_bytes(m) for m in meshes]);n=len(meshes);at=4+n*4
    mesh_table[at:at]=struct.pack('<'+'I'*n,*source['bones'])
    for i in range(n):struct.pack_into('<I',mesh_table,4+i*4,u32(mesh_table,4+i*4)+n*4)
    result.extend(mesh_table)
    assert set(material_bindings)==set(source['materials'])
    pad(result);struct.pack_into('<I',result,112,len(result));items=list(material_bindings.items())
    result.extend(struct.pack('<I',len(items)))
    result.extend(struct.pack('<'+'I'*len(items),*(k for k,v in items)))
    result.extend(struct.pack('<'+'Q'*len(items),*(v for k,v in items)))
    reread=parse(result,gpu)
    assert not reread['geometry'] and len(reread['meshes'])==len(meshes)
    assert reread['skeletons']==source['skeletons'] and reread['bones']==source['bones']
    expected=bytearray(original)
    for offset,length in [(16,8),(92,4),(100,4),(112,4)]:
        expected[offset:offset+length]=result[offset:offset+length]
    assert result[:len(original)]==expected
    for m,record in zip(reread['meshes'],proof):
        layout=reread['layouts'][m['layout']];allv=bytearray();alli=bytearray()
        step=layout['iBytes']//layout['nIndices']
        for group in m['groups']:
            mat,vo,nv,io,ni,render=group
            allv.extend(gpu[layout['vOff']+vo*layout['stride']:layout['vOff']+(vo+nv)*layout['stride']])
            alli.extend(gpu[layout['iOff']+io*step:layout['iOff']+(io+ni)*step])
        assert hashlib.sha256(allv).hexdigest()==record['vertex_records_sha256']
        assert hashlib.sha256(alli).hexdigest()==record['index_records_sha256']
    return dict(id=uid,type=0xe0a48d0be9a7453f,main=bytes(result),gpu=bytes(gpu),stream=b''),dict(
        id=f'{uid:016x}',source_geometry_group=f'{source["geometry"]:016x}',native_geometry_group='0000000000000000',
        source_prefix_unchanged_except_geometry_and_table_pointers=True,skeleton_bones_lods_uv_and_vertex_bytes_preserved=True,
        unused_bundle_mesh_entries_omitted=len(entry['meshes'])-len(meshes),
        gpu_bytes=len(gpu),meshes=proof)
