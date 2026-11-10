"""Read and clone a shared geometry_group without replacing its original ID."""
import struct
SIZES={0:4,1:8,2:12,3:16,4:4,21:4,22:8,23:12,24:16,25:1,26:2,27:3,28:4,29:4,30:4,32:2,33:4,34:6,35:8}
def u32(b,o):return struct.unpack_from('<I',b,o)[0]
def pad(b):b.extend(bytes((-len(b))%16))
def table(items):
    b=bytearray(struct.pack('<I',len(items))+bytes(len(items)*4))
    for i,item in enumerate(items):struct.pack_into('<I',b,4+i*4,len(b));b.extend(item)
    return b
def parse_geometry(main,gpu):
    n=u32(main,8);lo=u32(main,12);layouts=[];entries={}
    for i in range(u32(main,lo)):
        off=lo+u32(main,lo+4+i*4);raw=main[off:off+448];items=[];cursor=0
        for j in range(u32(raw,328)):
            typ,fmt,layer=struct.unpack_from('<III',raw,8+j*20);items.append(dict(type=typ,format=fmt,layer=layer,offset=cursor,index=j));cursor+=SIZES[fmt]
        index_type=u32(raw,396);n_indices=u32(raw,392);index_bytes=u32(raw,428)
        if index_type not in [0,1] or index_bytes!=n_indices*(2 if index_type==0 else 4):raise ValueError('共享几何索引位宽声明与数据长度不一致')
        layouts.append(dict(raw=raw,items=items,count=u32(raw,352),stride=u32(raw,356),vOff=u32(raw,416),iOff=u32(raw,424),nIndices=n_indices,iBytes=index_bytes,indexType=index_type))
    for i in range(n):
        uid=struct.unpack_from('<Q',main,16+i*16+8)[0];o=u32(main,16+n*16+i*4);cnt=u32(main,o);names=[u32(main,o+4+j*4) for j in range(cnt)];meshes=[]
        for j in range(cnt):
            off=o+u32(main,o+4+cnt*4+j*4);raw=main[off:off+48];ns=u32(raw,24);ng=u32(raw,40)
            meshes.append(dict(raw=raw,index=j,name=names[j],layout=u32(raw,0),slots=list(struct.unpack_from('<'+'I'*ns,main,off+u32(raw,28))),groups=[list(struct.unpack_from('<6I',main,off+u32(raw,44)+k*24)) for k in range(ng)]))
        entries[uid]=dict(index=i,offset=o,names=names,meshes=meshes)
    return dict(main=main,gpu=gpu,layouts=layouts,entries=entries)
def clone_geometry(data,uid,layouts,meshes):
    b=bytearray(data['main']);pad(b);struct.pack_into('<I',b,12,len(b));b.extend(table([l['raw'] for l in layouts]));pad(b)
    entry=data['entries'][uid];n=len(meshes);items=[]
    for mesh in meshes:
        raw=bytearray(mesh['raw']);struct.pack_into('<I',raw,0,mesh['layout']);struct.pack_into('<II',raw,24,len(mesh['slots']),48);raw.extend(struct.pack('<'+'I'*len(mesh['slots']),*mesh['slots']));struct.pack_into('<II',raw,40,len(mesh['groups']),len(raw))
        for group in mesh['groups']:raw.extend(struct.pack('<6I',*group))
        items.append(raw)
    mesh_table=bytearray(struct.pack('<I',n)+struct.pack('<'+'I'*n,*entry['names'])+bytes(n*4))
    for i,item in enumerate(items):struct.pack_into('<I',mesh_table,4+n*4+i*4,len(mesh_table));mesh_table.extend(item)
    struct.pack_into('<I',b,16+u32(b,8)*16+entry['index']*4,len(b));b.extend(mesh_table)
    return bytes(b)
