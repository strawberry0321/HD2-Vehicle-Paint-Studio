"""Explicit UV-area copies. Every result is a new local layer over private atlas UVs."""
import base64,copy,io,math,collections
from compute_runtime import np
from PIL import Image,ImageDraw,ImageFilter
EPS=1e-9;GRID=32

def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def area(p):return abs(sum(p[i][0]*p[(i+1)%len(p)][1]-p[(i+1)%len(p)][0]*p[i][1] for i in range(len(p))))*.5
def intersection(a,b):
 p=list(a);sign=1 if cross(*b)>=0 else -1
 for i in range(3):
  if not p:break
  c,d=b[i],b[(i+1)%3];q=[]
  for j,s in enumerate(p):
   e=p[(j+1)%len(p)];ds=cross(c,d,s)*sign;de=cross(c,d,e)*sign
   if ds>=-EPS:q.append(s)
   if (ds>EPS and de<-EPS) or (ds<-EPS and de>EPS):q.append(s+(e-s)*(ds/(ds-de)))
  p=q
 return area(p) if len(p)>=3 else 0
def variants(uv):
 uv=uv-np.floor(uv.min(0)+1e-7);lo=uv.min(0);hi=uv.max(0)
 for x in range(math.ceil(-hi[0]+EPS),math.floor(1-lo[0]-EPS)+1):
  for y in range(math.ceil(-hi[1]+EPS),math.floor(1-lo[1]-EPS)+1):
   p=uv+[x,y]
   if area(p)>EPS:yield p
def cells(box):
 for x in range(max(0,int(math.floor(box[0]*GRID))),min(GRID-1,int(math.floor(box[2]*GRID)))+1):
  for y in range(max(0,int(math.floor(box[1]*GRID))),min(GRID-1,int(math.floor(box[3]*GRID)))+1):yield x*GRID+y
def box(uv):return np.r_[uv.min(0),uv.max(0)]
def same_surface(a,b):
 n=np.cross(a[1]-a[0],a[2]-a[0]);length=np.linalg.norm(n)
 if length<1e-12:return False
 n/=length
 if np.max(np.abs((b-a[0])@n))>2e-5:return False
 u=a[1]-a[0];u/=np.linalg.norm(u);v=np.cross(n,u);aa=np.c_[(a-a[0])@u,(a-a[0])@v];bb=np.c_[(b-a[0])@u,(b-a[0])@v]
 return intersection(aa,bb)>max(1e-10,min(area(aa),area(bb))*.02)

def shared(unit,surfaces):
 import face_builder as fb
 selected={s['part']:set(s['triangles']) for s in surfaces};descriptor,_=fb.model(unit);indices={};sources=collections.defaultdict(list);world=[]
 for part,ids in selected.items():
  desc,positions,uv=fb.part_arrays(unit,part);texture=desc['color'];index=indices.setdefault(texture,collections.defaultdict(list))
  for t in sorted(ids):
   tri=positions[t*3:t*3+3]
   if np.linalg.norm(np.cross(tri[1]-tri[0],tri[2]-tri[0]))<1e-12:continue
   world.append(tri.copy())
   for v in variants(uv[t*3:t*3+3]):
    row=dict(part=part,t=t,uv=v,box=box(v),area=area(v));sources[texture].append(row)
    for key in cells(row['box']):index[key].append(row)
 targets=collections.defaultdict(lambda:collections.defaultdict(set));world=np.asarray(world);world_lo=world.min(1) if len(world) else np.empty((0,3));world_hi=world.max(1) if len(world) else np.empty((0,3))
 for part,desc in enumerate(descriptor['parts']):
  texture=desc['color']
  if texture not in indices or desc.get('nonRenderable') or desc.get('decalDisabledReason'):continue
  _,positions,uv=fb.part_arrays(unit,part)
  for t in range(desc['count']//3):
   if t in selected.get(part,set()):continue
   tri=positions[t*3:t*3+3]
   if np.linalg.norm(np.cross(tri[1]-tri[0],tri[2]-tri[0]))<1e-12:continue
   matched=False
   for v in variants(uv[t*3:t*3+3]):
    b=box(v);tested=set()
    for key in cells(b):
     for source in indices[texture].get(key,[]):
      identity=(source['part'],source['t'],tuple(source['box']))
      if identity in tested:continue
      tested.add(identity);a=source['box']
      if min(a[2],b[2])-max(a[0],b[0])<=EPS or min(a[3],b[3])-max(a[1],b[1])<=EPS:continue
      if intersection(source['uv'],v)>max(EPS,min(source['area'],area(v))*.02):matched=True;break
     if matched:break
    if matched:break
   if matched:
    candidates=np.flatnonzero(np.all(world_hi>=tri.min(0)-2e-5,axis=1)&np.all(world_lo<=tri.max(0)+2e-5,axis=1))
    if not any(same_surface(world[i],tri) for i in candidates):targets[texture][part].add(t)
 return sources,targets

def sample_uv(layer,raw):
 b=np.array(layer['uvReuse']['bounds']);p=layer['placement'];q=(np.mod(raw,1)-b[:2])/(b[2:]-b[:2])-.5-[p['x'],p['y']];a=-p['angle']*math.pi/180;c,s=math.cos(a),math.sin(a);uv=np.stack((c*q[:,0]-s*q[:,1],s*q[:,0]+c*q[:,1]),axis=1)/p['scale']+.5
 valid=np.all((uv>=0)&(uv<=1),axis=1);crop=np.array(layer['uvReuse'].get('crop',[0,0,1,1]));return crop[:2]+uv*(crop[2:]-crop[:2]),valid

def bake(image,surface,layer,size,destinations,source):
 import face_builder as fb
 import projection_baker as pb
 _,positions,rawuv=fb.part_arrays(surface['unit'],surface['part']);source=pb.premultiply_source(source)
 for t in layer['triangles']:
  dest=np.asarray(destinations[t]);lo=np.maximum(0,np.floor(dest.min(0))).astype(int);hi=np.minimum(size,np.ceil(dest.max(0))+1).astype(int);l,b=lo;r,top=hi
  if r<=l or top<=b:continue
  mask=Image.new('L',(r-l,top-b));ImageDraw.Draw(mask).polygon([tuple(v-lo) for v in dest],fill=255);mask=np.asarray(mask)
  if layer.get('uvReuse'):values=rawuv[t*3:t*3+3]
  else:
   f=layer['frame'];relative=positions[t*3:t*3+3]-np.array(f['origin']);values=np.c_[relative@f['u'],relative@f['v']]
  try:coeff=np.linalg.solve(np.c_[dest,np.ones(3)],values)
  except np.linalg.LinAlgError:continue
  for bottom in range(b,top,96):
   end=min(top,bottom+96);yy,xx=np.mgrid[bottom:end,l:r];points=np.c_[xx.ravel(),yy.ravel(),np.ones(xx.size)]@coeff
   if layer.get('uvReuse'):uv,valid=sample_uv(layer,points)
   else:
    f=layer['frame'];p=layer['placement'];w=f['bounds'][2]-f['bounds'][0];h=f['bounds'][3]-f['bounds'][1];aspect=source.shape[1]/source.shape[0];pw=(max(w,h*aspect) if p['fit']=='cover' else min(w,h*aspect))*p['scale'];ph=pw/aspect;center=np.array([(f['bounds'][0]+f['bounds'][2])/2+p['x']*w,(f['bounds'][1]+f['bounds'][3])/2+p['y']*h]);q=points-center;a=-p['angle']*math.pi/180;c,s=math.cos(a),math.sin(a);uv=np.c_[(c*q[:,0]-s*q[:,1])/pw+.5,(s*q[:,0]+c*q[:,1])/ph+.5];valid=np.all((uv>=0)&(uv<=1),axis=1)
   ids=np.flatnonzero(valid&(mask[bottom-b:end-b].ravel()>0))
   if not len(ids):continue
   rgba=np.zeros((xx.size,4),np.uint8);sample=pb.sample_image(source,uv[ids],premultiplied=True);sample[:,3]*=layer['opacity'];rgba[ids]=np.rint(sample).clip(0,255).astype('uint8');patch=Image.fromarray(rgba.reshape(end-bottom,r-l,4));bounds=(l,bottom,r,end);image.paste(Image.alpha_composite(image.crop(bounds),patch),bounds[:2])
 return image

def tinted(layer):
 import face_builder as fb
 im=Image.open(fb.library.path_for(layer['asset'])).convert('RGBA')
 if layer.get('tint'):
  a=np.array(im);a[:,:,:3]=np.rint(a[:,:,:3]*fb.legacy.color(layer['color'])).clip(0,255).astype('uint8');im=Image.fromarray(a)
 return im

def create(payload):
 import face_builder as fb
 import projection_baker as pb
 state=fb.state_ok(payload['state']);selection=payload['selection'];fb.geometry_ok(selection);unit=selection['unit']
 if unit!=state['vehicle']:raise ValueError('请在当前模型中选择需要复用的区域')
 sources,targets=shared(unit,selection['surfaces']);copies=[];proof=[]
 for texture,parts in targets.items():
  if not parts:continue
  rows=sources[texture];bounds=np.r_[np.maximum(0,np.min([r['uv'].min(0) for r in rows],axis=0)),np.minimum(1,np.max([r['uv'].max(0) for r in rows],axis=0))];span=bounds[2:]-bounds[:2];density=1024.
  layer_sets={l['id']:{s['part']:set(s['triangles']) for s in l['surfaces']} for l in state['layers'] if l['unit']==unit and not l.get('hidden') and l['opacity']>0};layers=[l for l in state['layers'] if l['id'] in layer_sets];images={l['id']:tinted(l) for l in layers}
  color_sets=[(patch,{s['part']:set(s['triangles']) for s in patch['surfaces']}) for patch in state.get('surfaceColors',[]) if patch['unit']==unit and not patch.get('hidden')]
  for row in rows:
   _,positions,uv=fb.part_arrays(unit,row['part']);t=row['t'];d=uv[t*3+1:t*3+3]-uv[t*3]
   if abs(np.linalg.det(d))<1e-12:continue
   jac=np.linalg.solve(d,positions[t*3+1:t*3+3]-positions[t*3]);world_per_uv=np.linalg.norm(jac,2)
   for layer in layers:
    if t not in layer_sets[layer['id']].get(row['part'],set()):continue
    asset=fb.library.describe(layer['asset'])
    if layer.get('projection'):p=pb.parameters(layer);density=max(density,world_per_uv*max(asset['width']/p['width'],asset['height']/p['height']))
    else:density=max(density,max(asset['width'],asset['height']))
  size=min(8192,max(64,2**math.ceil(math.log2(max(span)*density))));scratch=Image.new('RGBA',(size,size));pixels=np.zeros((size,size,4),np.uint8);occupied=np.zeros((size,size),bool);source_records=[]
  for row in rows:
   part,t=row['part'],row['t'];dest=(row['uv']-bounds[:2])/span*size-.5;dest[:,1]=size-1-dest[:,1];lo=np.maximum(0,np.floor(dest.min(0))).astype(int);hi=np.minimum(size,np.ceil(dest.max(0))+1).astype(int);l,b=lo;r,top=hi
   if r<=l or top<=b:continue
   bounds_px=(l,b,r,top);scratch.paste((0,0,0,0),bounds_px);geometry=Image.new('L',(r-l,top-b));ImageDraw.Draw(geometry).polygon([tuple(v-lo) for v in dest],fill=255)
   for patch,chosen in color_sets:
    if t in chosen.get(part,set()):scratch.paste(Image.new('RGBA',geometry.size,patch['color']),bounds_px,geometry)
   surface=dict(unit=unit,part=part)
   for layer in layers:
    if t not in layer_sets[layer['id']].get(part,set()):continue
    local=dict(layer,triangles=[t])
    if layer.get('projection'):pb.bake(scratch,surface,local,size,{t:dest},images[layer['id']])
    else:bake(scratch,surface,local,size,{t:dest},images[layer['id']])
   data=np.asarray(scratch.crop(bounds_px));mask=np.asarray(geometry)>0;old=pixels[b:top,l:r];seen=occupied[b:top,l:r];interior=np.asarray(geometry.filter(ImageFilter.MinFilter(3)))>0;conflict=seen&interior&(np.max(np.abs(data.astype(int)-old.astype(int)),axis=2)>7)
   if np.count_nonzero(conflict)>2:raise ValueError('蓝色选区内部有相互重叠但外观不同的贴图区，请缩小到一处后再复用')
   write=mask&~seen;old[write]=data[write];seen[mask]=True;source_records.append((part,t))
  if not np.any(pixels[:,:,3]):continue
  image=Image.fromarray(pixels);buffer=io.BytesIO();image.save(buffer,format='PNG');asset=fb.library.import_image('data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(),'贴图区复用副本.png','image',False,['photo'])
  style='appearance' if any(any(row['t'] in layer_sets[layer['id']].get(row['part'],set()) for row in rows) for layer in layers) else 'color'
  for part,triangles in sorted(parts.items()):copies.append(dict(asset=asset['id'],surfaces=[dict(part=part,triangles=sorted(triangles))],uvReuse=dict(version=1,bounds=bounds.tolist(),crop=[0,0,1,1],style=style),name='复用图片 · '+fb.model(unit)[0]['parts'][part]['name']))
  proof.append(dict(texture=texture,source_faces=len(set(source_records)),target_faces=sum(map(len,parts.values())),snapshot_size=size,overlap_conflicts_rejected=True))
 if not copies:raise ValueError('当前选区没有可复用的图片或局部底色，或不存在相同贴图区')
 if len(state['layers'])+len(copies)>100:raise ValueError('复用后图层超过100张，请先合并或删除部分图层')
 return dict(copies=copies,proof=proof,current_unit_only=True,source_layers_unchanged=True,independent_copies=True)
