"""Disable stock decal color AND decal normal only in private vehicle clones."""
import struct
import builder

PARENT=0x43865ef50d88395e
VALUES={'decal_alpha_sharpness':0.0,'decal_normal_intentity':0.0}

def clean(raw,enabled=True):
    if not enabled or struct.unpack_from('<Q',raw,24)[0]!=PARENT:return raw,[]
    result=bytearray(raw);nt=struct.unpack_from('<I',raw,64)[0];ns=struct.unpack_from('<I',raw,104)[0]
    defs=136+nt*12;base=defs+ns*20;wanted={builder.hash_id(k)>>32:(k,v) for k,v in VALUES.items()};changes=[]
    for i in range(ns):
        typ,count,usage,offset,stride=struct.unpack_from('<5I',raw,defs+i*20)
        if usage not in wanted:continue
        name,value=wanted[usage]
        if typ!=0:raise ValueError('原版标志参数类型不匹配')
        struct.pack_into('<f',result,base+offset,value);changes.append(name)
    if set(changes)!=set(VALUES):raise ValueError('原版标志通道不完整，未输出不完整的替换')
    return bytes(result),changes
