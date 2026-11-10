"""Copy native vertex records; split only painted triangles into private material slots.

Positions, packed normals, skin weights, bone indices and other UV channels are
copied byte for byte. Atlas UVs use Blender's bottom-left origin; native shader
sampling uses a top-left origin, so painted V is converted before writing UV0.
The visibility UDIM integer is preserved. Original physics is never emitted.
"""
from pathlib import Path
import struct, hashlib, json, math, copy
from compute_runtime import np
SIZES={0:4,1:8,2:12,3:16,4:4,21:4,22:8,23:12,24:16,25:1,26:2,27:3,28:4,29:4,30:4,32:2,33:4,34:6,35:8}
UNIT=0xe0a48d0be9a7453f
SHELL_OFFSET=0.0015
DAMAGE_SHELL_OFFSET=0.004
def u32(b,o):return struct.unpack_from('<I',b,o)[0]
def align(n,a=16):return (n+a-1)//a*a
def pad(b,a=16):b.extend(bytes(align(len(b),a)-len(b)))
def parse(main,gpu):
    lo=u32(main,92);mo=u32(main,100);ma=u32(main,112);sk=u32(main,88)
    layouts=[]
    if lo:
        for i in range(u32(main,lo)):
            off=lo+u32(main,lo+4+i*4);raw=main[off:off+448];items=[];cursor=0
            for j in range(u32(raw,328)):
                typ,fmt,layer=struct.unpack_from('<III',raw,8+j*20);items.append(dict(type=typ,format=fmt,layer=layer,offset=cursor,index=j));cursor+=SIZES[fmt]
            index_type=u32(raw,396);n_indices=u32(raw,392);index_bytes=u32(raw,428)
            if index_type not in [0,1] or index_bytes!=n_indices*(2 if index_type==0 else 4):raise ValueError('模型索引位宽声明与数据长度不一致')
            layouts.append(dict(raw=raw,items=items,count=u32(raw,352),stride=u32(raw,356),vOff=u32(raw,416),iOff=u32(raw,424),nIndices=n_indices,iBytes=index_bytes,indexType=index_type))
    mats={}
    if ma:
        n=u32(main,ma)
        for i in range(n):mats[u32(main,ma+4+i*4)]=struct.unpack_from('<Q',main,ma+4+n*4+i*8)[0]
    meshes=[];bones=[]
    if mo:
        n=u32(main,mo);bones=[u32(main,mo+4+n*4+i*4) for i in range(n)]
        for i in range(n):
            off=mo+u32(main,mo+4+i*4);h=main[off:off+128];nmat=u32(h,104);ng=u32(h,120);moff=u32(h,108);goff=u32(h,124);slots=list(struct.unpack_from('<'+'I'*nmat,main,off+moff));groups=[list(struct.unpack_from('<6I',main,off+goff+j*24)) for j in range(ng)]
            meshes.append(dict(raw=h,index=i,layout=struct.unpack_from('<i',h,60)[0],skeleton=struct.unpack_from('<i',h,56)[0],slots=slots,groups=groups))
    skeletons=[]
    if sk:
        for i in range(u32(main,sk)):
            off=sk+u32(main,sk+4+i*4);count,mat,idx,rem=struct.unpack_from('<4I',main,off);ro=off+rem;n=u32(main,ro);maps=[]
            for j in range(n):
                offset,num=struct.unpack_from('<II',main,ro+4+j*8);maps.append(main[ro+offset:ro+offset+num*4])
            skeletons.append(dict(count=count,matrices=main[off+mat:off+mat+count*64],indices=main[off+idx:off+idx+count*4],maps=maps))
    return dict(main=main,gpu=gpu,layouts=layouts,meshes=meshes,materials=mats,bones=bones,skeletons=skeletons,geometry=struct.unpack_from('<Q',main,16)[0])
def table(items):
    b=bytearray(struct.pack('<I',len(items))+bytes(len(items)*4))
    for i,data in enumerate(items):pad(b,4);struct.pack_into('<I',b,4+i*4,len(b));b.extend(data)
    return b
def skeleton_bytes(s):
    count=s['count'];b=bytearray(16);mat=16;b.extend(s['matrices']);idx=len(b);b.extend(s['indices']);ro=len(b);r=bytearray(struct.pack('<I',len(s['maps']))+bytes(len(s['maps'])*8))
    for i,indices in enumerate(s['maps']):struct.pack_into('<II',r,4+i*8,len(r),len(indices)//4);r.extend(indices)
    b.extend(r);struct.pack_into('<4I',b,0,count,mat,idx,ro);return b
def mesh_bytes(m):
    b=bytearray(m['raw']);struct.pack_into('<i',b,60,m['layout']);struct.pack_into('<i',b,56,m['skeleton']);struct.pack_into('<II',b,104,len(m['slots']),128);b.extend(struct.pack('<'+'I'*len(m['slots']),*m['slots']));go=len(b);struct.pack_into('<II',b,120,len(m['groups']),go)
    for g in m['groups']:b.extend(struct.pack('<6I',*g))
    return b
PERMS=[[0,1,2],[1,2,0],[2,0,1],[0,2,1],[2,1,0],[1,0,2]]
def build_unit(unit_id,descriptor,blob,surfaces,source_main,source_gpu,hash_id,shared=None):
    d=parse(source_main,source_gpu)
    original_gpu=source_gpu;original_layouts=d['layouts'];original_meshes=d['meshes'];dependencies=[];geometry=None
    if d['geometry']:
        from native_geometry import parse_geometry,clone_geometry
        if not shared:raise ValueError('缺少此载具的共享几何原稿')
        geometry=parse_geometry(shared['main'],shared['gpu']);entry=geometry['entries'][int(unit_id,16)]
        d['layouts']=copy.deepcopy(geometry['layouts']);d['meshes']=copy.deepcopy(entry['meshes']);source_gpu=shared['gpu']
        unit_meshes={u32(m['raw'],40):m for m in original_meshes}
        for m in d['meshes']:
            original=unit_meshes.get(m['name']);m['unit_mesh']=original['index'] if original else None;m['skeleton']=original['skeleton'] if original else -1
    main=bytearray(source_main);gpu=bytearray(source_gpu);jobs={};proof=[]
    minimum=np.array(descriptor['bounds'][0]);maximum=np.array(descriptor['bounds'][1]);scale=5/float(np.max(maximum-minimum));center=(maximum+minimum)/2
    for surface in surfaces:
        local_descriptor,local_blob=descriptor,blob
        if surface['unit']!=descriptor.get('id'):
            from face_builder import model
            local_descriptor,local_blob=model(surface['unit'])
        minimum=np.array(local_descriptor['bounds'][0]);maximum=np.array(local_descriptor['bounds'][1]);scale=5/float(np.max(maximum-minimum));center=(maximum+minimum)/2
        part=local_descriptor['parts'][surface['part']];mapping=part.get('nativeMapping')
        if not mapping:raise ValueError('此模型缺少已校验的原生面映射，请勿使用旧版模型数据')
        array=np.frombuffer(local_blob,dtype='<f4',count=part['count']*8,offset=part['offset']).reshape(-1,8)
        points=(array[:,:3]-np.array([center[0],minimum[1],center[2]]))*scale
        frame=surface['frame'];origin=np.array(frame['origin']);u=np.array(frame['u']);v=np.array(frame['v']);x0,y0,x1,y1=frame['bounds'];w=max(x1-x0,1e-6);h=max(y1-y0,1e-6)
        key=(mapping['mesh'],mapping['group']);triangles={};normals={};explicit=dict(zip(surface['triangles'],surface.get('native_uv',[])))
        native_to_preview=np.array(mapping['matrix'],float)[:3,:3]
        native_normals=np.frombuffer(local_blob,dtype='<f4',count=part['count']*3,offset=mapping['nativeNormalOffset']).reshape(-1,3) if 'nativeNormalOffset' in mapping else None
        for t in surface['triangles']:
            native=int(mapping['triangleMap'][t]);perm=PERMS[int(mapping['permutation'][t])];newuv=[]
            if t in explicit:newuv=explicit[t]
            else:
                for k in range(3):
                    p=points[t*3+k]-origin;newuv.append([min(.99999,max(.00001,(p@u-x0)/w)),min(.99999,max(.00001,(p@v-y0)/h))])
            ordered=[None]*3;ordered_normals=[None]*3
            for k,j in enumerate(perm):
                ordered[j]=newuv[k]
                # Preview normals live in a different coordinate basis from
                # the native vertex records. Transform them back before any
                # shell offset; comparing native geometry to a preview-space
                # frame normal can push a bonnet/roof shell inward.
                normal=native_normals[t*3+k] if native_normals is not None else array[t*3+k,3:6]@native_to_preview
                length=np.linalg.norm(normal)
                ordered_normals[j]=normal/max(1e-20,length)
            triangles[native]=ordered
            normals[native]=ordered_normals
        slot=(hash_id('vehicle_face_slot/'+surface['id'])>>32)&0xffffffff
        while slot in d['materials']:slot=(slot+1)&0xffffffff
        part_name=part.get('name') or '';shell=DAMAGE_SHELL_OFFSET if '_destroyed' in part_name else SHELL_OFFSET
        d['materials'][slot]=surface['material_id'];jobs.setdefault(mapping['mesh'],[]).append(dict(source_group=mapping['group'],triangles=triangles,normals=normals,slot=slot,surface=surface['id'],shell=shell))
    for mesh_index,work in jobs.items():
        m=d['meshes'][mesh_index];layout=d['layouts'][m['layout']];uv_item=next(i for i in layout['items'] if i['type']==4 and i['layer']==0);uv_offset=uv_item['offset'];old_uv_size=SIZES[uv_item['format']];extra=8-old_uv_size;stride=layout['stride']+extra
        pos_item=next((i for i in layout['items'] if i['type']==0 and i['layer']==0),None);pos_offset=pos_item['offset'] if pos_item else 0;can_offset=bool(pos_item and pos_item['format']==2)
        vertices=bytearray();indices=[];newgroups=[];newmaps=[];skeleton=d['skeletons'][m['skeleton']] if 0<=m['skeleton']<len(d['skeletons']) else None
        old_vertices=[];old_indices=[]
        for gi,g in enumerate(m['groups']):
            old_vertices.append(source_gpu[layout['vOff']+g[1]*layout['stride']:layout['vOff']+(g[1]+g[2])*layout['stride']])
            step=layout['iBytes']//layout['nIndices'];old_indices.append(np.frombuffer(source_gpu,dtype='<u2' if step==2 else '<u4',count=g[4],offset=layout['iOff']+g[3]*step).astype(int).reshape(-1,3))
        def record(raw,new_uv=None,offset=None):
            uv=struct.unpack_from('<ee' if uv_item['format']==33 else '<ff',raw,uv_offset)
            if new_uv is not None:
                cells=[math.floor(x) for x in uv];values=[]
                # atlas_baker emits Blender UVs, while the game's pixel shader
                # samples native UV0 directly. Preserve the native state cell,
                # but convert the atlas V fraction to the texture's top origin.
                fractions=(new_uv[0],1-new_uv[1])
                for fraction,cell in zip(fractions,cells):
                    value=np.float32(fraction+cell);upper=np.nextafter(np.float32(cell+1),np.float32(-np.inf));value=np.clip(value,np.float32(cell),upper);values.append(float(value))
                uv=tuple(values);assert [math.floor(x) for x in uv]==cells
            out=bytearray(raw[:uv_offset]+struct.pack('<ff',*uv)+raw[uv_offset+old_uv_size:])
            if offset is not None:
                if not can_offset:raise ValueError('模型顶点位置格式不支持贴花外壳偏移')
                x,y,z=struct.unpack_from('<3f',out,pos_offset);struct.pack_into('<3f',out,pos_offset,x+float(offset[0]),y+float(offset[1]),z+float(offset[2]))
            return bytes(out)
        # Smooth source vertex normals in the native coordinate basis.
        shell_normals={}
        if can_offset:
            for job in work:
                gi=job['source_group']
                for triangle in job['triangles']:
                    for vi,normal in zip(old_indices[gi][triangle],job['normals'][triangle]):
                        key=(gi,int(vi));shell_normals[key]=shell_normals.get(key,np.zeros(3))+normal
            for key,value in shell_normals.items():
                length=np.linalg.norm(value);shell_normals[key]=value/length if length>1e-12 else np.array([0,0,1.0])
        blocks={}
        for gi,g in enumerate(m['groups']):
            block=(g[1],g[2]);raw=old_vertices[gi]
            if block not in blocks:
                blocks[block]=len(vertices)//stride
                for vi in range(g[2]):vertices.extend(record(raw[vi*layout['stride']:(vi+1)*layout['stride']]))
            start=blocks[block]
            # Keep every original triangle and add an outward decal shell.
            keep=old_indices[gi];offset=len(indices)
            for tri in keep:indices.extend(start+int(x) for x in tri)
            newgroups.append([g[0],0,0,offset,len(keep)*3,g[5]])
            if skeleton:newmaps.append(skeleton['maps'][gi])
        for job in work:
            gi=job['source_group'];g=m['groups'][gi];raw=old_vertices[gi];start=len(vertices)//stride;index_start=len(indices);material_index=len(m['slots']);m['slots'].append(job['slot']);local=0
            for triangle,uvs in sorted(job['triangles'].items()):
                for k,vi in enumerate(old_indices[gi][triangle]):
                    r=raw[vi*layout['stride']:(vi+1)*layout['stride']]
                    shell=None
                    if can_offset:
                        normal=shell_normals.get((gi,int(vi)),np.array([0,0,1.0]));shell=normal*float(job.get('shell',SHELL_OFFSET))
                    out=record(r,uvs[k],shell)
                    if shell is None:
                        assert out[:uv_offset]==r[:uv_offset] and out[uv_offset+8:]==r[uv_offset+old_uv_size:]
                    else:
                        expected=bytearray(r[:uv_offset]);expected[pos_offset:pos_offset+12]=out[pos_offset:pos_offset+12]
                        assert bytes(expected)==out[:uv_offset] and out[uv_offset+8:]==r[uv_offset+old_uv_size:]
                    vertices.extend(out);indices.append(start+local);local+=1
            # Skinned draw groups address their own entry in the remap list.
            # Reusing the source group's ID aliases the original draw after
            # splitting; the copied remap is stored at the new group index.
            # Rigid meshes legitimately share group ID 0, so preserve those.
            render_group=len(newgroups) if skeleton else g[5]
            newgroups.append([material_index,0,0,index_start,local,render_group])
            if skeleton:newmaps.append(skeleton['maps'][gi])
            proof.append(dict(surface=job['surface'],mesh=mesh_index,source_group=gi,render_group=render_group,source_render_group=g[5],skinned=skeleton is not None,triangles=len(job['triangles']),original_faces_retained=True,shell_offset=float(job.get('shell',SHELL_OFFSET)) if can_offset else 0.0,shell_offset_applied=can_offset,shell_normals_in_native_basis=True,native_bytes_except_shell_offset_preserved=True,visibility_udim_preserved=True,native_v_converted_from_blender=True))
        # Use a shared vertex range and mesh-relative indices, as in the native
        # and finished reference meshes. This layout alone did not resolve the
        # V7 game result; atlas V conversion is a separate requirement.
        total_vertices=len(vertices)//stride
        for group in newgroups:group[1]=0;group[2]=total_vertices
        pad(gpu,64);vo=len(gpu);gpu.extend(vertices);pad(gpu,64);io=len(gpu);gpu.extend(np.array(indices,dtype='<u4').tobytes())
        raw=bytearray(layout['raw']);struct.pack_into('<I',raw,8+uv_item['index']*20+4,1);struct.pack_into('<II',raw,352,len(vertices)//stride,stride);struct.pack_into('<II',raw,392,len(indices),1);old_ps=u32(layout['raw'],420);new_ps=(len(vertices)//stride)*12 if old_ps==layout['count']*12 else len(vertices);struct.pack_into('<4I',raw,416,vo,new_ps,io,len(indices)*4)
        m['layout']=len(d['layouts']);d['layouts'].append(dict(raw=raw));m['groups']=newgroups
        if skeleton:
            m['skeleton']=len(d['skeletons']);d['skeletons'].append(dict(count=skeleton['count'],matrices=skeleton['matrices'],indices=skeleton['indices'],maps=newmaps))
    if geometry:
        from native_geometry import clone_geometry
        cloned=clone_geometry(geometry,int(unit_id,16),d['layouts'],d['meshes']);digest=hashlib.sha256(cloned+gpu).hexdigest();gid=hash_id('content/mods/vehicle_private_geometry/'+unit_id+'/'+digest)
        assert gid!=d['geometry'];struct.pack_into('<Q',main,16,gid)
        dependencies.append(dict(id=gid,type=hash_id('geometry_group'),main=cloned,gpu=bytes(gpu),stream=b''))
        for mi in jobs:
            m=d['meshes'][mi]
            if m['unit_mesh'] is not None:
                original=original_meshes[m['unit_mesh']];original['slots']=m['slots'];original['groups']=m['groups'];original['skeleton']=m['skeleton']
        d['layouts']=original_layouts;d['meshes']=original_meshes
    else:
        pad(main);struct.pack_into('<I',main,92,len(main));main.extend(table([l['raw'] for l in d['layouts']]))
    pad(main);offset=len(main);struct.pack_into('<I',main,100,offset);mesh_table=table([mesh_bytes(m) for m in d['meshes']]);n=len(d['meshes']);header=4+n*4;mesh_table[header:header]=struct.pack('<'+'I'*n,*d['bones'])
    for i in range(n):struct.pack_into('<I',mesh_table,4+i*4,u32(mesh_table,4+i*4)+n*4)
    main.extend(mesh_table)
    if d['skeletons']:pad(main);struct.pack_into('<I',main,88,len(main));main.extend(table([skeleton_bytes(s) for s in d['skeletons']]))
    pad(main);struct.pack_into('<I',main,112,len(main));items=list(d['materials'].items());main.extend(struct.pack('<I',len(items)));main.extend(struct.pack('<'+'I'*len(items),*(k for k,v in items)));main.extend(struct.pack('<'+'Q'*len(items),*(v for k,v in items)))
    # Reparse our result independently of the serializer's in-memory state.
    reread=parse(main,original_gpu if geometry else gpu);assert len(reread['meshes'])==len(d['meshes']);assert source_gpu==gpu[:len(source_gpu)]
    for p in proof:assert p['native_bytes_except_shell_offset_preserved']
    return dict(id=int(unit_id,16),type=UNIT,main=bytes(main),gpu=original_gpu if geometry else bytes(gpu),stream=b'',dependencies=dependencies),proof
