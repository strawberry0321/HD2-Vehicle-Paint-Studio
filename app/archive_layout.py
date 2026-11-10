"""Validate the engine's type blocks, beyond a permissive per-file parse."""
import struct

def validate_archive(main,stream,gpu):
    def require(condition,message):
        if not condition:raise ValueError('补丁容器结构无效：'+message)
    require(len(main)>=72,'缺少头部')
    require(bytes(main[:4])==bytes.fromhex('110000f0'),'不支持的容器版本')
    nt,nf=struct.unpack_from('<II',main,4)
    require(0<nt<=1024 and 0<nf<=1000000,'资源计数无效')
    table_end=72+nt*32+nf*80
    require(table_end<=len(main),'文件表不完整')
    types=[struct.unpack_from('<IIQ4I',main,72+i*32) for i in range(nt)]
    files=[struct.unpack_from('<7Q6I',main,72+nt*32+i*80) for i in range(nf)]
    require(len({t[2] for t in types})==nt,'资源类型重复')
    require(sum(t[3] for t in types)==nf,'类型计数与文件数不一致')
    cursor=0;blocks=[]
    for t in types:
        require(t[3]>0,'存在空类型分组')
        for index in range(cursor,cursor+t[3]):
            require(files[index][1]==t[2],f'第 {index+1} 项未处于声明的资源类型分组')
        blocks.append(dict(type=f'0x{t[2]:016x}',start=cursor,count=t[3]));cursor+=t[3]
    require(len({r[:2] for r in files})==nf,'资源编号重复')
    previous=[table_end,0,0]
    for i,row in enumerate(files):
        require(row[12]==i+1,'资源顺序索引不连续')
        for a in row[10:12]:require(a>0 and a&(a-1)==0,'资源对齐无效')
        require(row[2]%row[10]==0 and row[4]%row[11]==0,'资源起始地址未对齐')
        for channel,buf in enumerate([main,stream,gpu]):
            off,size=row[2+channel],row[7+channel]
            require(off+size<=len(buf),'资源越过容器边界')
            require(off>=previous[channel],'资源内容发生重叠')
            previous[channel]=off+size
    return dict(status='passed',type_blocks_contiguous=True,resource_count=nf,type_count=nt,blocks=blocks,resource_indices_contiguous=True,resource_bounds_and_alignment_valid=True)
