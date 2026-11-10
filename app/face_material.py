"""Bind baked PBR channels using the vehicle material template."""
import struct
from pathlib import Path
import builder

PARENT=0x7712ee7808e0ec50
USAGES={'color':0xac652e43,'normal':0xcaed6cd6,'mra':0x756f6fa6}

def offsets(raw):
    if struct.unpack_from('<Q',raw,24)[0]!=PARENT:raise ValueError('贴图材质模板的原生 shader 不匹配')
    count=struct.unpack_from('<I',raw,64)[0]
    usages=struct.unpack_from('<'+'I'*count,raw,136)
    if set(usages)!=set(USAGES.values()) or count!=3:raise ValueError('贴图材质必须包含主色、法线和 MRA 三个通道')
    return {channel:136+count*4+usages.index(usage)*8 for channel,usage in USAGES.items()}

def template(out,engine):
    source=Path(engine)/'templates/vehicle_pbr.material.main'
    raw=builder.protect_file(out,source,Path('material_templates/vehicle_pbr.material.main')).read_bytes()
    offsets(raw)
    return raw

def bind(raw,textures):
    if set(textures)!=set(USAGES):raise ValueError('贴图材质通道不完整')
    result=bytearray(raw)
    for channel,offset in offsets(raw).items():struct.pack_into('<Q',result,offset,textures[channel])
    return bytes(result)

def texture_ids(raw):
    count=struct.unpack_from('<I',raw,64)[0]
    usages=struct.unpack_from('<'+'I'*count,raw,136)
    return {channel:struct.unpack_from('<Q',raw,136+count*4+usages.index(usage)*8)[0] for channel,usage in USAGES.items()}
