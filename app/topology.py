"""Editable surfaces over the untouched native triangles, including UV seams and T-junctions."""
from pathlib import Path
import collections,functools,math,struct
from compute_runtime import np
MAGIC=b'HD2SURF4';HEADER=struct.Struct('<8s8I')

def csr(rows):
    offsets=np.zeros(len(rows)+1,dtype='<u4');flat=[]
    for i,row in enumerate(rows):flat.extend(sorted(row));offsets[i+1]=len(flat)
    return offsets,np.asarray(flat,dtype='<u4')

def construct(parts,tolerance=2e-5,vertex_normals=None,disabled_parts=None,part_keys=None):
    offsets=np.cumsum([0]+[len(p)//3 for p in parts],dtype=np.uint32)
    points=np.concatenate(parts).astype(float);triangles=points.reshape(-1,3,3);n=len(triangles)
    keys,inverse=np.unique(np.rint(points/tolerance).astype(np.int64),axis=0,return_inverse=True)
    vertex=np.zeros((len(keys),3));np.add.at(vertex,inverse,points);vertex/=np.bincount(inverse)[:,None]
    welded=inverse.reshape(-1,3);cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]);normal=cross/np.maximum(np.linalg.norm(cross,axis=1,keepdims=True),1e-20);center=triangles.mean(1)
    flipped=np.zeros(n,dtype=bool)
    if vertex_normals is not None:
        shading=np.concatenate(vertex_normals).reshape(-1,3,3).mean(1);flipped=np.einsum('ij,ij->i',normal,shading)<0;normal[flipped]*=-1
    disabled=np.repeat(disabled_parts,np.diff(offsets)).astype(bool) if disabled_parts is not None else np.zeros(n,dtype=bool)
    edges=collections.defaultdict(list)
    for t,ids in enumerate(welded):
        for j in range(3):
            a,b=int(ids[j]),int(ids[(j+1)%3])
            if a!=b:edges[min(a,b),max(a,b)].append((t,(1 if a<b else -1)*(-1 if flipped[t] else 1)))
    segments=[];lines=collections.defaultdict(list)
    for (a,b),incidence in edges.items():
        p,q=vertex[a],vertex[b]
        if len(incidence)>1:segments.append((p,q,incidence));continue
        direction=q-p;axis=int(np.argmax(np.abs(direction)));other=[k for k in range(3) if k!=axis]
        slope=direction[other]/direction[axis];intercept=p[other]-slope*p[axis];key=(axis,*np.rint(np.r_[slope,intercept]*1000).astype(int).tolist())
        lo,hi=sorted([float(p[axis]),float(q[axis])]);sign=1 if q[axis]>p[axis] else -1
        lines[key].append((lo,hi,p,q,[(t,s*sign) for t,s in incidence]))
    split_contacts=0
    for group in lines.values():
        clusters=[]
        for item in group:
            p,q=item[2:4];found=False
            for cluster in clusters:
                a,b=cluster[0][2:4];direction=(b-a)/np.linalg.norm(b-a)
                if max(np.linalg.norm(np.cross(p-a,direction)),np.linalg.norm(np.cross(q-a,direction)))<=tolerance*1.5:cluster.append(item);found=True;break
            if not found:clusters.append([item])
        for cluster in clusters:
            if len(cluster)==1:
                lo,hi,p,q,inc=cluster[0];axis=int(np.argmax(np.abs(q-p)))
                segments.append((p if p[axis]<q[axis] else q,q if p[axis]<q[axis] else p,inc));continue
            axis=int(np.argmax(np.abs(cluster[0][3]-cluster[0][2])));events=sorted({v for item in cluster for v in item[:2]})
            for lo,hi in zip(events,events[1:]):
                if hi-lo<tolerance*.25:continue
                mid=(lo+hi)/2;active=[item for item in cluster if item[0]<mid<item[1]]
                if not active:continue
                a,b=active[0][2:4];p=a+(b-a)*(lo-a[axis])/(b[axis]-a[axis]);q=a+(b-a)*(hi-a[axis])/(b[axis]-a[axis]);inc=[v for item in active for v in item[4]]
                if len(active)>1:split_contacts+=1
                segments.append((p,q,inc))
    adjacent=[set() for _ in range(n)];edge_faces=[];edge_signs=[];edge_offsets=[0];edge_positions=[]
    for p,q,incidence in segments:
        incidence=list(dict.fromkeys(incidence));edge_positions.extend([*p,*q]);edge_faces.extend(t for t,s in incidence);edge_signs.extend(s for t,s in incidence);edge_offsets.append(len(edge_faces))
        for i,(a,sa) in enumerate(incidence):
            for b,sb in incidence[i+1:]:
                if a!=b and not disabled[a] and not disabled[b] and sa!=sb and normal[a]@normal[b]>-.995:adjacent[a].add(b);adjacent[b].add(a)
    face_parts=np.repeat(np.arange(len(parts)),np.diff(offsets));keys=part_keys or list(range(len(parts)))
    owner=np.array([keys[int(p)] for p in face_parts]);active=(np.linalg.norm(cross,axis=1)>1e-12)&~disabled
    regions=np.full(n,0xffffffff,dtype='<u4');region_triangles=[];plane_tolerance=max(tolerance*12,.001);cos=math.cos(math.radians(6))
    inactive={}
    for start in range(n):
        if regions[start]!=0xffffffff:continue
        if not active[start]:
            rid=inactive.get(int(face_parts[start]))
            if rid is None:rid=len(region_triangles);inactive[int(face_parts[start])]=rid;region_triangles.append(set())
            regions[start]=rid;region_triangles[rid].add(start);continue
        rid=len(region_triangles);regions[start]=rid;queue=[start];seed=normal[start];origin=center[start]
        for t in queue:
            for candidate in adjacent[t]:
                if regions[candidate]!=0xffffffff or not active[candidate] or owner[candidate]!=owner[start]:continue
                if normal[candidate]@seed>=cos and np.max(np.abs((triangles[candidate]-origin)@seed))<=plane_tolerance:regions[candidate]=rid;queue.append(candidate)
        region_triangles.append(set(queue))
    # Equal planes split by UV seams or separate islands are one editing face.
    # Keep the exact native triangles underneath, including curvature and joints.
    buckets={};merged=[];coplanar_joins=0
    for row in region_triangles:
        start=min(row);seed=normal[start];origin=center[start];key=(str(owner[start]),*np.rint(seed*10000).astype(int),int(round(float(origin@seed)/.0002)))
        target=buckets.get(key) if active[start] else None
        if target is not None:
            anchor=min(merged[target]);ref=normal[anchor]
            if seed@ref>.999999 and np.max(np.abs((triangles[list(row)]-center[anchor])@ref))<.0003:merged[target].update(row);coplanar_joins+=1;continue
        if active[start]:buckets[key]=len(merged)
        merged.append(row)
    region_triangles=merged
    for rid,row in enumerate(region_triangles):regions[list(row)]=rid
    r_adj=[set() for _ in region_triangles]
    for a,row in enumerate(adjacent):
        for b in row:
            if regions[a]!=regions[b]:r_adj[int(regions[a])].add(int(regions[b]))
    adj_o,adj=csr(adjacent);reg_o,reg_t=csr(region_triangles);radj_o,radj=csr(r_adj)
    arrays=dict(parts=np.asarray(offsets,dtype='<u4'),adjOffsets=adj_o,adjacent=adj,region=regions,regionOffsets=reg_o,regionTriangles=reg_t,regionAdjOffsets=radj_o,regionAdjacent=radj,edgeOffsets=np.asarray(edge_offsets,dtype='<u4'),edgeFaces=np.asarray(edge_faces,dtype='<u4'),edgeSigns=np.asarray(edge_signs,dtype='<i4'),edgePositions=np.asarray(edge_positions,dtype='<f4'))
    info=dict(parts=len(parts),triangles=n,surfaces=len(region_triangles),editable_surfaces=sum(bool(active[min(row)]) for row in region_triangles),inactive_triangles=int((~active).sum()),coplanar_islands_merged=coplanar_joins,edges=len(segments),contacts=len(adj),edgeIncidences=len(edge_faces),regionContacts=len(radj),tJunctionSegments=split_contacts,largestSurface=max(map(len,region_triangles)),weldTolerance=tolerance,planeTolerance=plane_tolerance,mirrored_faces_aligned=int(flipped.sum()))
    return arrays,info

def encode(arrays,info):
    fields=['parts','adjOffsets','adjacent','region','regionOffsets','regionTriangles','regionAdjOffsets','regionAdjacent','edgeOffsets','edgeFaces','edgeSigns','edgePositions']
    return HEADER.pack(MAGIC,1,info['parts'],info['triangles'],info['surfaces'],info['edges'],info['contacts'],info['edgeIncidences'],info['regionContacts'])+b''.join(arrays[name].tobytes() for name in fields)

@functools.lru_cache(maxsize=48)
def load(unit):
    blob=(Path(__file__).resolve().parent/'data/models'/f'{unit[2:]}.surfaces.bin').read_bytes();magic,version,p,n,r,e,m,i,k=HEADER.unpack_from(blob);assert magic==MAGIC and version==1;cursor=HEADER.size;result={}
    for name,count,typ in [('parts',p+1,'<u4'),('adjOffsets',n+1,'<u4'),('adjacent',m,'<u4'),('region',n,'<u4'),('regionOffsets',r+1,'<u4'),('regionTriangles',n,'<u4'),('regionAdjOffsets',r+1,'<u4'),('regionAdjacent',k,'<u4'),('edgeOffsets',e+1,'<u4'),('edgeFaces',i,'<u4'),('edgeSigns',i,'<i4'),('edgePositions',e*6,'<f4')]:result[name]=np.frombuffer(blob,dtype=typ,count=count,offset=cursor);cursor+=count*4
    assert cursor==len(blob);result['count']=n;result['surfaceCount']=r;return result

def face_ids(data,surfaces):return {int(data['parts'][s['part']])+t for s in surfaces for t in s['triangles']}
def connected(data,ids):
    ids=set(ids)
    if not ids:return False
    seen={next(iter(ids))};queue=list(seen)
    for t in queue:
        for a in data['adjacent'][data['adjOffsets'][t]:data['adjOffsets'][t+1]]:
            a=int(a)
            if a in ids and a not in seen:seen.add(a);queue.append(a)
    return len(seen)==len(ids)
