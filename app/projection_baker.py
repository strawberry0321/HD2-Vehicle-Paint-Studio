"""Continuous decal coordinates and first-surface projection, independent of UV islands."""
import functools,hashlib,io,json,math,os,uuid
from pathlib import Path
from compute_runtime import np
from PIL import Image,ImageDraw
GRID=768
def parameters(layer):
 p=layer['projection'];t=layer['placement'];angle=-t['angle']*math.pi/180;c,s=math.cos(angle),math.sin(angle);width=p['width']*t['scale'];height=p['height']*t['scale'];corners=np.array(p.get('corners',[[0,0],[1,0],[1,1],[0,1]]),float);bbox=np.r_[corners.min(0),corners.max(0)]
 if p['type']=='cylinder':
  span=abs(c)*width*(bbox[2]-bbox[0])+abs(s)*height*(bbox[3]-bbox[1]);limit=2*math.pi*p['radius']*.995
  if span>limit:width*=limit/span;height*=limit/span
 rows=[];out=[]
 for (x,y),(u,v) in zip(corners,[[0,0],[1,0],[1,1],[0,1]]):rows.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);out.extend([u,v])
 h=np.r_[np.linalg.solve(rows,out),1].reshape(3,3);f=layer['frame'];basis=np.array([f['u'],f['v'],f['normal']]).T
 lock=p.get('surface',{}) if p['type']!='cylinder' else {};enabled=lock.get('mode')=='locked';side_normal=np.array(lock.get('normal',f['normal']))@basis;side_origin=(np.array(lock.get('origin',f['origin']))-np.array(f['origin']))@basis
 return dict(type=p['type'],origin=np.array(f['origin']),basis=basis,width=width,height=height,c=c,s=s,cx=t['x']*p['width'],cy=t['y']*p['height'],depth=p['depth'],radius=p.get('radius',1),distance=p.get('distance',0),bbox=bbox,H=h,cutoff=p.get('cutoff',.015),side_normal=side_normal,side_origin=side_origin,side_cos=math.cos(math.radians(lock.get('angle',55))) if enabled else -2,side_band=lock.get('band',1.25),crop=np.array(p.get('crop',[0,0,1,1]),float))
def uv_depth(local,p):
 x=local[...,0];y=local[...,1];z=local[...,2]
 if p['type']!='cylinder' and p['distance']:
  scale=p['distance']/np.maximum(.00001,p['distance']-z);x=x*scale;y=y*scale
 x=x-p['cx'];y=y-p['cy']
 if p['type']=='cylinder':
  span=2*math.pi*p['radius'];x=(p['radius']*np.arctan2(local[...,0],local[...,2])-p['cx']+span/2)%span-span/2;z=np.linalg.norm(local[...,[0,2]],axis=-1)-p['radius']
 return np.stack(((p['c']*x-p['s']*y)/p['width']+.5,(p['s']*x+p['c']*y)/p['height']+.5,z),axis=-1)
def front_depth(uv,p,plane):
 x=(uv[...,0]-.5)*p['width'];y=(uv[...,1]-.5)*p['height'];a=p['c']*x+p['s']*y+p['cx'];b=-p['s']*x+p['c']*y+p['cy']
 if p['type']=='cylinder':
  th=a/p['radius'];denom=plane[0]*np.sin(th)+plane[2]*np.cos(th);r=np.divide(-(plane[1]*b+plane[3]),denom,out=np.full_like(b,-1e30),where=np.abs(denom)>1e-12);return np.where(r>0,r-p['radius'],-1e30)
 if p['distance']:
  den=plane[0]*a+plane[1]*b-plane[2]*p['distance'];t=np.divide(-(plane[2]*p['distance']+plane[3]),den,out=np.full_like(b,-1e30),where=np.abs(den)>1e-12);return np.where(t>0,p['distance']*(1-t),-1e30)
 return -(plane[0]*a+plane[1]*b+plane[3])/plane[2] if abs(plane[2])>1e-12 else np.full_like(b,-1e30)
def cache_key(layer):
 from face_builder import VEHICLES
 data={k:layer[k] for k in ['unit','projection','frame','placement']};vehicle=VEHICLES.get(data['unit'],{})
 # This cache contains visibility/geometry only. The four mech appearances
 # have byte-identical positions and normals; artwork and paint stay in each
 # appearance's separate render state and encoded texture cache.
 if vehicle.get('mechAppearance') and vehicle.get('nativeUnit')=='0x79e4b3d2da5e45e3':data['unit']=vehicle['nativeUnit']
 return json.dumps(data,sort_keys=True)
@functools.lru_cache(maxsize=24)
def build(key):
 from texture_cache import digest
 from storage_paths import CACHE_ROOT
 from face_builder import APP
 import export_progress
 export_progress.check_cancelled();layer=json.loads(key);stem=APP/'data/models'/layer['unit'][2:]
 signature=hashlib.sha256(json.dumps(dict(key=key,code=digest(Path(__file__)),descriptor=digest(stem.with_suffix('.json')),model=digest(stem.with_suffix('.bin')),catalog=digest(APP/'data/catalog.json')),sort_keys=True).encode()).hexdigest()
 folder=CACHE_ROOT/'projections';file=folder/(signature+'.npz');meta=folder/(signature+'.json')
 try:
  info=json.loads(meta.read_text(encoding='utf8'));raw=file.read_bytes();assert info['sha256']==hashlib.sha256(raw).hexdigest()
  with np.load(io.BytesIO(raw),allow_pickle=False) as stored:
   grid=stored['grid'];faces=stored['faces'];surfaces=json.loads(stored['surfaces'].tobytes().decode('utf8'))
  assert grid.shape==(GRID,GRID) and grid.dtype==np.int32 and grid.min()>=0 and grid.max()<=len(faces)
  assert not len(faces) or faces.shape[1:]==(3,3)
  os.utime(file,None)
  return dict(parameters=parameters(layer),grid=grid,faces=faces,surfaces=surfaces)
 except (OSError,ValueError,KeyError,AssertionError):pass
 result=_build(key);folder.mkdir(parents=True,exist_ok=True);token=uuid.uuid4().hex;pending=folder/(token+'.npz')
 with pending.open('xb') as target:np.savez_compressed(target,grid=result['grid'],faces=result['faces'],surfaces=np.frombuffer(json.dumps(result['surfaces'],separators=(',',':')).encode(),np.uint8))
 raw=pending.read_bytes();pending.replace(file);pending=folder/(token+'.json');pending.write_text(json.dumps(dict(sha256=hashlib.sha256(raw).hexdigest())),encoding='utf8');pending.replace(meta)
 return result

def _build(key):
 from face_builder import model,part_arrays
 layer=json.loads(key);p=parameters(layer);descriptor,buffer=model(layer['unit']);N=GRID;grid=np.zeros((N,N),np.int32);front=np.full((N,N),-1e30,np.float32);faces=[];surfaces=[];candidates=[];x0,y0,x1,y1=p['bbox']
 mask={s['part']:set(s['triangles']) for s in layer['projection'].get('mask',[])}
 for part,desc in enumerate(descriptor['parts']):
  group=desc.get('group') or ('P24' if __import__('face_builder').VEHICLES.get(layer['unit'],{}).get('isFRV') and desc.get('slot')=='m_lights' else None)
  if desc.get('nonRenderable') or desc.get('decalDisabledReason') or (layer['projection'].get('groups') is not None and group not in layer['projection']['groups']):continue
  _,positions,_=part_arrays(layer['unit'],part);positions=positions.astype(np.float32).astype(float);raw=((positions-p['origin'])@p['basis']).reshape(-1,3,3);projected=uv_depth(raw,p);normal_vertices=np.frombuffer(buffer,dtype='<f4',count=desc['count']*8,offset=desc['offset']).reshape(-1,8)[:,3:6];local_normals=(normal_vertices@p['basis']).reshape(-1,3,3)
  # Reject disjoint triangles in one array pass. The rasterizer and all
  # retained triangle calculations below remain byte-for-byte the old math.
  eligible=np.ones(len(raw),dtype=bool)
  if mask:
   eligible[:]=False
   selected=list(mask.get(part,()))
   if selected:eligible[np.asarray(selected,dtype=int)]=True
  if p['type']!='cylinder':
   lo_all=projected.min(1);hi_all=projected.max(1)
   eligible&=(hi_all[:,0]>=x0)&(lo_all[:,0]<=x1)&(hi_all[:,1]>=y0)&(lo_all[:,1]<=y1)
  for serial,index in enumerate(np.flatnonzero(eligible)):
   if serial%128==0:__import__('export_progress').check_cancelled()
   t=int(index)
   tri,uv=raw[t],projected[t]
   cross=np.cross(tri[1]-tri[0],tri[2]-tri[0]);length=np.linalg.norm(cross)
   if length<1e-14:continue
   normal=cross/length;plane=np.r_[normal,-normal@tri[0]];uv=uv.copy()
   if p['type']=='cylinder':
    span=2*math.pi*p['radius']
    for k in [1,2]:
     delta=(uv[k,0]-uv[0,0])*p['width']*p['c']+(uv[k,1]-uv[0,1])*p['height']*p['s']
     while delta>span/2:uv[k,0]-=span*p['c']/p['width'];uv[k,1]-=span*p['s']/p['height'];delta-=span
     while delta< -span/2:uv[k,0]+=span*p['c']/p['width'];uv[k,1]+=span*p['s']/p['height'];delta+=span
   lo=uv.min(0);hi=uv.max(0)
   if hi[0]<x0 or lo[0]>x1 or hi[1]<y0 or lo[1]>y1:continue
   facing=local_normals[t,:,2]
   if p['type']=='cylinder':facing=np.sum(local_normals[t,:][:,[0,2]]*tri[:,[0,2]],axis=1)/np.maximum(1e-8,np.linalg.norm(tri[:,[0,2]],axis=1))
   elif p['distance']:
    direction=-tri.copy();direction[:,2]+=p['distance'];facing=np.sum(local_normals[t]*direction,axis=1)/np.maximum(1e-8,np.linalg.norm(direction,axis=1))
   geometric=normal if np.dot(normal,local_normals[t].sum(0))>=0 else -normal
   side=p['side_cos']< -1 or (np.dot(geometric,p['side_normal'])>=p['side_cos']-1e-6 and np.any(np.abs((tri-p['side_origin'])@p['side_normal'])<=p['side_band']))
   if side and np.max(facing)>=p['cutoff'] and hi[2]>=-p['depth'] and lo[2]<=p['depth'] and (not mask or t in mask.get(part,set())):candidates.append((len(faces)+1,part,t,tri))
   face=len(faces)+1;faces.append(tri);q=(uv[:,:2]-[x0,y0])/[x1-x0,y1-y0]*N;a,b,c=q;den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
   if abs(den)<1e-10:continue
   l,bottom=np.clip(np.floor(q.min(0)),0,N-1).astype(int);r,top=np.clip(np.ceil(q.max(0)),0,N-1).astype(int);yy,xx=np.mgrid[bottom:top+1,l:r+1];px=xx+.5;py=yy+.5;w0=((b[1]-c[1])*(px-c[0])+(c[0]-b[0])*(py-c[1]))/den;w1=((c[1]-a[1])*(px-c[0])+(a[0]-c[0])*(py-c[1]))/den;inside=(w0>=-.000001)&(w1>=-.000001)&(w0+w1<=1.000001);sample=np.stack((x0+px/N*(x1-x0),y0+py/N*(y1-y0)),axis=-1);depth=front_depth(sample,p,plane);old=front[bottom:top+1,l:r+1];write=inside&(depth>old);old[write]=depth[write];grid[bottom:top+1,l:r+1][write]=face
 data=dict(parameters=p,grid=grid,faces=np.array(faces));visible=set(np.unique(grid));selected={}
 for face,part,t,tri in candidates:
  include=face in visible
  if not include:
   points=np.r_[tri.mean(0)[None],tri];uv=uv_depth(points,p);inside=(uv[:,0]>=x0)&(uv[:,0]<=x1)&(uv[:,1]>=y0)&(uv[:,1]<=y1)
   if np.any(inside):include=np.any(~hidden(points[inside],uv[inside,:2],data))
  if include:selected.setdefault(part,[]).append(t)
 data['surfaces']=[dict(part=k,triangles=v) for k,v in selected.items()];return data
def hidden(points,uv,data):
 p=data['parameters'];grid=data['grid'];N=grid.shape[0];bbox=p['bbox'];coord=(uv-bbox[:2])/(bbox[2:]-bbox[:2]);xy=np.floor(coord*N).astype(int);result=np.zeros(points.shape[0],bool)
 for dy in [-1,0,1]:
  for dx in [-1,0,1]:
   ids=grid[np.clip(xy[:,1]+dy,0,N-1),np.clip(xy[:,0]+dx,0,N-1)];take=np.flatnonzero((ids>0)&~result)
   if not len(take):continue
   tri=data['faces'][ids[take]-1];a,b,c=tri[:,0],tri[:,1],tri[:,2];e=b-a;f=c-a;n=np.cross(e,f);point=points[take].copy();constant=np.einsum('ij,ij->i',n,a)
   if p['type']=='cylinder':
    own=np.linalg.norm(point[:,[0,2]],axis=1);direction=np.stack((point[:,0]/np.maximum(own,1e-8),np.zeros(len(own)),point[:,2]/np.maximum(own,1e-8)),axis=1);den=np.einsum('ij,ij->i',n,direction);front=np.divide(constant-n[:,1]*point[:,1],den,out=np.full_like(den,-1e30),where=np.abs(den)>1e-7);point[:,0]=direction[:,0]*front;point[:,2]=direction[:,2]*front
   else:
    own=point[:,2].copy()
    if p['distance']:
     direction=point.copy();direction[:,2]-=p['distance'];den=np.einsum('ij,ij->i',n,direction);time=np.divide(constant-n[:,2]*p['distance'],den,out=np.full_like(den,-1e30),where=np.abs(den)>1e-7);point=direction*time[:,None];point[:,2]+=p['distance'];front=np.where(time>0,point[:,2],-1e30)
    else:
     den=n[:,2];front=np.divide(constant-n[:,0]*point[:,0]-n[:,1]*point[:,1],den,out=np.full_like(den,-1e30),where=np.abs(den)>1e-7);point[:,2]=front
   rel=point-a;ee=np.einsum('ij,ij->i',e,e);ef=np.einsum('ij,ij->i',e,f);ff=np.einsum('ij,ij->i',f,f);re=np.einsum('ij,ij->i',rel,e);rf=np.einsum('ij,ij->i',rel,f);det=ee*ff-ef*ef;u=np.divide(re*ff-rf*ef,det,out=np.zeros_like(det),where=np.abs(det)>1e-10);v=np.divide(rf*ee-re*ef,det,out=np.zeros_like(det),where=np.abs(det)>1e-10);result[take]|=(front>own+.00002)&(np.abs(det)>1e-10)&(u>=-.00002)&(v>=-.00002)&(u+v<=1.00002)
 return result
def premultiply_source(source):
 source=np.asarray(source,dtype=np.float32).copy();source[...,:3]*=source[...,3:]/255
 return source

def sample_image(source,uv,premultiplied=False):
 h,w=source.shape[:2];x=np.clip(uv[:,0]*w-.5,0,w-1);y=np.clip((1-uv[:,1])*h-.5,0,h-1);x0=np.floor(x).astype(int);y0=np.floor(y).astype(int);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1);fx=(x-x0)[:,None];fy=(y-y0)[:,None]
 samples=[source[y0,x0],source[y0,x1],source[y1,x0],source[y1,x1]]
 if not premultiplied:
  samples=[v.astype(float).copy() for v in samples]
  for value in samples:value[:,:3]*=value[:,3:]/255
 a,b,c,d=samples;result=(a*(1-fx)+b*fx)*(1-fy)+(c*(1-fx)+d*fx)*fy
 result[:,:3]=np.divide(result[:,:3]*255,result[:,3:],out=np.zeros_like(result[:,:3]),where=result[:,3:]>1e-8)
 return result
def bake(image,surface,layer,size,destinations,source):
 from face_builder import part_arrays,model
 transfer=bool(layer.get('coverageTransfer'));data=None if transfer else build(cache_key(layer));p=parameters(layer) if transfer else data['parameters'];_,positions,_=part_arrays(surface['unit'],surface['part']);desc,buffer=model(surface['unit']);part=desc['parts'][surface['part']];normals=np.frombuffer(buffer,dtype='<f4',count=part['count']*8,offset=part['offset']).reshape(-1,8)[:,3:6];source=premultiply_source(source)
 for t in layer['triangles']:
  __import__('export_progress').check_cancelled()
  dest=destinations[t];l,b=np.maximum(0,np.floor(dest.min(0))).astype(int);r,top=np.minimum(size,np.ceil(dest.max(0))+1).astype(int)
  if r<=l or top<=b:continue
  tri=positions[t*3:t*3+3];local=(tri-p['origin'])@p['basis'];normal=normals[t*3:t*3+3]@p['basis']
  try:coeff=np.linalg.solve(np.c_[dest,np.ones(3)],np.c_[local,normal])
  except np.linalg.LinAlgError:continue
  mask=Image.new('L',(r-l,top-b));ImageDraw.Draw(mask).polygon([tuple(v-[l,b]) for v in dest],fill=255);mask=np.asarray(mask)
  for bottom in range(b,top,96):
   end=min(top,bottom+96);yy,xx=np.mgrid[bottom:end,l:r];plane=np.c_[xx.ravel(),yy.ravel(),np.ones(xx.size)]@coeff;points=plane[:,:3];ns=plane[:,3:];ns/=np.maximum(1e-10,np.linalg.norm(ns,axis=1))[:,None];uvz=uv_depth(points,p);hom=np.c_[uvz[:,:2],np.ones(len(points))]@p['H'].T;uv=hom[:,:2]/np.where(np.abs(hom[:,2:])<1e-12,1e-12,hom[:,2:]);facing=ns[:,2]
   if p['type']=='cylinder':facing=np.sum(ns[:,[0,2]]*points[:,[0,2]],axis=1)/np.maximum(1e-8,np.linalg.norm(points[:,[0,2]],axis=1))
   elif p['distance']:
    direction=-points.copy();direction[:,2]+=p['distance'];facing=np.sum(ns*direction,axis=1)/np.maximum(1e-8,np.linalg.norm(direction,axis=1))
   visible=(np.abs(uvz[:,2])<=p['depth'])&((facing>=p['cutoff'])|transfer)&(uv[:,0]>=0)&(uv[:,0]<=1)&(uv[:,1]>=0)&(uv[:,1]<=1)&(mask[bottom-b:end-b].ravel()>0)
   if p['side_cos']> -1 and not transfer:visible&=(ns@p['side_normal']>=p['side_cos']-1e-6)&(np.abs((points-p['side_origin'])@p['side_normal'])<=p['side_band'])
   if p['type']!='cylinder' and p['distance']:visible&=points[:,2]<p['distance']-.00001
   ids=np.flatnonzero(visible)
   if not len(ids):continue
   if not transfer:visible[ids]&=~hidden(points[ids],uvz[ids,:2],data)
   ids=np.flatnonzero(visible)
   if not len(ids):continue
   rgba=np.zeros((xx.size,4),np.uint8);sample=sample_image(source,p['crop'][:2]+uv[ids]*(p['crop'][2:]-p['crop'][:2]),premultiplied=True);sample[:,3]*=layer['opacity'];rgba[ids]=np.rint(sample).clip(0,255).astype('uint8');patch=Image.fromarray(rgba.reshape(end-bottom,r-l,4));box=(l,bottom,r,end);image.paste(Image.alpha_composite(image.crop(box),patch),box[:2])
 return image
