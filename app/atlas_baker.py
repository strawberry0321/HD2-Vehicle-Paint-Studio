"""Bake nondegenerate islands, including the perpendicular sides of raised panels."""
from pathlib import Path
import math
from compute_runtime import np
from PIL import Image,ImageDraw
import topology
import builder as legacy
import image_library as library
from coloring import recolor_srgb
DATA=Path(__file__).resolve().parent/'data'

def arrays(surface):
    from face_builder import part_arrays
    return part_arrays(surface['unit'],surface['part'])

def frame(points):
    tri=points.reshape(-1,3,3);cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);normal=cross.sum(0)
    if np.linalg.norm(normal)<1e-12:normal=cross[np.argmax(np.linalg.norm(cross,axis=1))]
    normal/=max(1e-20,float(np.linalg.norm(normal)));up=np.array([0,0,-1] if abs(normal[1])>.9 else [0,1,0]);u=np.cross(up,normal);u/=max(1e-20,float(np.linalg.norm(u)));v=np.cross(normal,u);origin=points.mean(0);x=(points-origin)@u;y=(points-origin)@v
    return dict(origin=origin.tolist(),u=u.tolist(),v=v.tolist(),normal=normal.tolist(),bounds=[float(x.min()),float(y.min()),float(x.max()),float(y.max())])

def image_size(layer,asset):
    if layer.get('uvReuse'):
        p=layer['uvReuse'];b=p['bounds'];factor=layer['placement']['scale'];return max(1e-6,np.linalg.norm(p.get('worldU',[1,0,0]))*(b[2]-b[0])*factor),max(1e-6,np.linalg.norm(p.get('worldV',[0,1,0]))*(b[3]-b[1])*factor)
    if layer.get('projection'):
        import projection_baker
        p=projection_baker.parameters(layer);return p['width'],p['height']
    x0,y0,x1,y1=layer['frame']['bounds'];w=max(1e-7,x1-x0);h=max(1e-7,y1-y0);aspect=asset['width']/asset['height'];p=layer['placement'];pw=(max(w,h*aspect) if p['fit']=='cover' else min(w,h*aspect))*p['scale'];return pw,pw/aspect

def prepare(surface):
    if 'atlas' in surface:return surface['atlas']['size']
    _,positions,_=arrays(surface);data=topology.load(surface['unit']);offset=int(data['parts'][surface['part']]);groups={}
    for t in surface['triangles']:groups.setdefault(int(data['region'][offset+t]),[]).append(t)
    charts=[]
    for ts in groups.values():
        pts=positions[np.array([[t*3,t*3+1,t*3+2] for t in ts])].reshape(-1,3);f=frame(pts);w=max(1e-6,f['bounds'][2]-f['bounds'][0]);h=max(1e-6,f['bounds'][3]-f['bounds'][1]);charts.append(dict(triangles=ts,frame=f,width=w,height=h))
    density=32
    if surface.get('baseColors') or surface.get('preserveSourceDetail'):
        part,geometry,source_uv=arrays(surface);dimensions=Image.open(DATA/part['normal']).size;ratios=[]
        for t in surface['triangles']:
            pts=geometry[t*3:t*3+3];tex=source_uv[t*3:t*3+3]
            for k in range(3):
                length=float(np.linalg.norm(pts[k]-pts[(k+1)%3]))
                if length>1e-7:ratios.append(float(np.linalg.norm((tex[k]-tex[(k+1)%3])*dimensions))/length)
        if ratios:density=max(density,min(4096,float(np.percentile(ratios,70))))
    for layer in surface['layers']:
        asset=library.describe(layer['asset']);pw,ph=image_size(layer,asset);density=max(density,asset['width']/max(pw,1e-6),asset['height']/max(ph,1e-6))
    padding=32
    for trial in range(40):
        dimensions=[(max(4,math.ceil(c['width']*density/4)*4),max(4,math.ceil(c['height']*density/4)*4)) for c in charts]
        needed=max([max(w,h)+padding*2 for w,h in dimensions]+[math.sqrt(sum((w+padding*2)*(h+padding*2) for w,h in dimensions)/.72),512]);size=2**math.ceil(math.log2(needed))
        ok=False
        for size in [size,size*2]:
            if size>8192:continue
            x=y=row=0;rects={};ok=True
            for i in sorted(range(len(charts)),key=lambda i:dimensions[i][1],reverse=True):
                w,h=dimensions[i];outer_w=w+padding*2;outer_h=h+padding*2
                if outer_w>size or outer_h>size:ok=False;break
                if x+outer_w>size:x=0;y+=row;row=0
                if y+outer_h>size:ok=False;break
                rects[i]=[x+padding,y+padding,w,h];x+=outer_w;row=max(row,outer_h)
            if ok:break
        if ok and size<=8192:break
        density*=.7
    else:raise ValueError('选区过大，请缩小选区或分区域导出')
    uv_by_triangle={}
    for i,c in enumerate(charts):
        c['rect']=rects[i];f=c['frame'];origin=np.array(f['origin']);u=np.array(f['u']);v=np.array(f['v']);x0,y0,x1,y1=f['bounds'];rx,ry,rw,rh=c['rect']
        for t in c['triangles']:
            p=positions[t*3:t*3+3]-origin;x=(p@u-x0)/max(1e-8,x1-x0);y=(p@v-y0)/max(1e-8,y1-y0)
            # Bake and sample the same texel centres. Guard pixels carry edge
            # colours outward; shrinking UVs would crop/stretch the artwork.
            ux=(rx+.5+x*(rw-1))/size
            uy=1-(ry+.5+(1-y)*(rh-1))/size
            uv_by_triangle[t]=np.stack((ux,uy),axis=1).tolist()
    surface['atlas']=dict(size=size,padding=padding,charts=charts,pixels_per_world_unit=density);surface['native_uv']=[uv_by_triangle[t] for t in surface['triangles']]
    return size

def lookup(surface,size):
    prepare(surface);return {t:np.stack((np.array(uv)[:,0]*size-.5,(1-np.array(uv)[:,1])*size-.5),axis=1) for t,uv in zip(surface['triangles'],surface['native_uv'])}

def box_for(points,size):
    return [max(0,int(math.floor(points[:,0].min()))),max(0,int(math.floor(points[:,1].min()))),min(size,int(math.ceil(points[:,0].max()))+1),min(size,int(math.ceil(points[:,1].max()))+1)]

def pad_islands(image,surface,size,destinations):
    result=image.copy();scale=size/surface['atlas']['size'];padding=surface['atlas']['padding']
    for chart in surface['atlas']['charts']:
        rx,ry,rw,rh=chart['rect'];box=[max(0,math.floor((rx-padding)*scale)),max(0,math.floor((ry-padding)*scale)),min(size,math.ceil((rx+rw+padding)*scale)),min(size,math.ceil((ry+rh+padding)*scale))];crop=result.crop(box).convert('RGB');mask=Image.new('L',crop.size);draw=ImageDraw.Draw(mask)
        for t in chart['triangles']:draw.polygon([tuple(p-np.array(box[:2])) for p in destinations[t]],fill=255)
        rgb=np.array(crop);valid=np.array(mask)>0
        # Propagate actual boundary colours through the entire guard band.
        # A short Gaussian only filled ~4 px of the former 24 px padding and
        # mixed unrelated colours. Work on the advancing perimeter, not on a
        # float64 RGBA copy of every large chart.
        inner=np.pad(valid,1,constant_values=False)
        boundary=valid&~(inner[1:-1,:-2]&inner[1:-1,2:]&inner[:-2,1:-1]&inner[2:,1:-1])
        frontier=np.flatnonzero(boundary);height,width=valid.shape
        known=valid.ravel();pixels=rgb.reshape(-1,3)
        offsets=np.array([[-1,0],[0,-1],[0,1],[1,0],[-1,-1],[-1,1],[1,-1],[1,1]])
        for _ in range(max(1,math.ceil(padding*scale)+2)):
            if not len(frontier):break
            yy=frontier[:,None]//width+offsets[:,0];xx=frontier[:,None]%width+offsets[:,1]
            inside=(yy>=0)&(yy<height)&(xx>=0)&(xx<width)
            target=(yy*width+xx)[inside];parents=np.broadcast_to(frontier[:,None],yy.shape)[inside]
            empty=~known[target];target=target[empty];parents=parents[empty]
            frontier,first=np.unique(target,return_index=True)
            pixels[frontier]=pixels[parents[first]];known[frontier]=True
        result.paste(Image.fromarray(rgb),box[:2])
    return result

def base_bake(surface,channel,size,setting,pad=True):
    prepare(surface);part,positions,uv=arrays(surface);destinations=lookup(surface,size)
    if channel=='color' and (setting['mode']=='solid' or (setting['mode']=='native' and not part.get('group'))):return Image.new('RGB',(size,size),setting['color'])
    source=Image.open(DATA/part[channel]).convert('RGB')
    if channel=='color' and setting['mode']=='native':
        from face_builder import GROUPS
        rgb=recolor_srgb(np.asarray(source,dtype=float)/255,legacy.color(setting['color']),legacy.color(GROUPS[part['group']]['defaultColor']));source=Image.fromarray(np.rint(rgb*255).astype('uint8'))
    src=np.array(source);sw,sh=source.size;tiled_images={};target=Image.new('RGB',(size,size),(128,128,255) if channel=='normal' else (30,170,255) if channel=='mra' else (75,75,75))
    for t,dst in destinations.items():
        box=box_for(dst,size)
        if box[2]<=box[0] or box[3]<=box[1]:continue
        tex=uv[t*3:t*3+3].astype(float);tex-=np.floor(tex.min(0));nx=max(1,int(math.ceil(float(tex[:,0].max()))));ny=max(1,int(math.ceil(float(tex[:,1].max()))))
        if nx>8 or ny>8:continue
        if (nx,ny) not in tiled_images:tiled_images[nx,ny]=Image.fromarray(np.tile(src,(ny+2,nx+2,1)))
        tiled=tiled_images[nx,ny];sample=np.stack(((tex[:,0]+1)*sw,(ny+1-tex[:,1])*sh),axis=1)
        try:coeff=np.linalg.solve(np.c_[dst,np.ones(3)],sample).T
        except np.linalg.LinAlgError:continue
        offset=np.array(box[:2]);affine=[coeff[0,0],coeff[0,1],coeff[0,2]+coeff[0,:2]@offset,coeff[1,0],coeff[1,1],coeff[1,2]+coeff[1,:2]@offset];patch=tiled.transform((box[2]-box[0],box[3]-box[1]),Image.Transform.AFFINE,affine,Image.Resampling.BICUBIC);mask=Image.new('L',patch.size);ImageDraw.Draw(mask).polygon([tuple(p-offset) for p in dst],fill=255);target.paste(patch,box[:2],mask)
    return pad_islands(target,surface,size,destinations) if pad else target

def color_bake(surface,size,setting):
    image=base_bake(surface,'color',size,setting,pad=False).convert('RGBA');destinations=lookup(surface,size);_,positions,_=arrays(surface);scale=size/surface['atlas']['size']
    for color in surface.get('baseColors',[]):
        mask=Image.new('L',(size,size));draw=ImageDraw.Draw(mask)
        for t in color['triangles']:draw.polygon([tuple(p) for p in destinations[t]],fill=255)
        image.paste(Image.new('RGBA',(size,size),color['color']),mask=mask)
    for layer in surface['layers']:
        source=Image.open(library.path_for(layer['asset'])).convert('RGBA');asset=library.describe(layer['asset']);pw,ph=image_size(layer,asset);lf=layer['frame'];lw=lf['bounds'][2]-lf['bounds'][0];lh=lf['bounds'][3]-lf['bounds'][1];p=layer['placement'];cx=(lf['bounds'][0]+lf['bounds'][2])/2+p['x']*lw;cy=(lf['bounds'][1]+lf['bounds'][3])/2+p['y']*lh;angle=-p['angle']*math.pi/180;ca,sa=math.cos(angle),math.sin(angle);selected=set(layer['triangles'])
        if layer.get('tint'):
            tint=legacy.color(layer['color']);rgb=source.convert('RGB');rgb=Image.merge('RGB',[rgb.getchannel(i).point([round(x*tint[i]) for x in range(256)]) for i in range(3)]);rgb.putalpha(source.getchannel('A'));source=rgb
        if layer.get('projection'):
            import projection_baker
            image=projection_baker.bake(image,surface,layer,size,destinations,source);continue
        if layer.get('uvReuse'):
            import uv_reuse
            image=uv_reuse.bake(image,surface,layer,size,destinations,source);continue
        for chart in surface['atlas']['charts']:
            ts=[t for t in chart['triangles'] if t in selected]
            if not ts:continue
            rx,ry,rw,rh=chart['rect'];f=chart['frame'];fx0,fy0,fx1,fy1=f['bounds'];box=[math.floor(rx*scale),math.floor(ry*scale),min(size,math.ceil((rx+rw)*scale)),min(size,math.ceil((ry+rh)*scale))];w,h=box[2]-box[0],box[3]-box[1];pixels=np.array([[box[0],box[1]],[box[0]+w,box[1]],[box[0],box[1]+h]],float)
            x=fx0+(pixels[:,0]-(rx+.5)*scale)/max(1e-8,(rw-1)*scale)*(fx1-fx0);y=fy1-(pixels[:,1]-(ry+.5)*scale)/max(1e-8,(rh-1)*scale)*(fy1-fy0);world=np.array(f['origin'])+x[:,None]*np.array(f['u'])+y[:,None]*np.array(f['v']);rel=world-np.array(lf['origin']);lx=rel@np.array(lf['u'])-cx;ly=rel@np.array(lf['v'])-cy;sample=np.stack((((ca*lx-sa*ly)/pw+.5)*source.width,(.5-(sa*lx+ca*ly)/ph)*source.height),axis=1)
            # A perpendicular wall can project exactly onto u=0 or u=1. Match
            # GPU clamp-to-edge instead of treating that whole wall as outside.
            for axis,limit in [(0,source.width),(1,source.height)]:
                sample[np.isclose(sample[:,axis],0,atol=1e-6,rtol=0),axis]=1e-5;sample[np.isclose(sample[:,axis],limit,atol=1e-6,rtol=0),axis]=limit-1e-5
            local=pixels-np.array(box[:2]);coeff=np.linalg.solve(np.c_[local,np.ones(3)],sample).T;patch=source.transform((w,h),Image.Transform.AFFINE,tuple(coeff.ravel()),Image.Resampling.BICUBIC);mask=Image.new('L',(w,h));draw=ImageDraw.Draw(mask)
            for t in ts:draw.polygon([tuple(v-np.array(box[:2])) for v in destinations[t]],fill=255)
            alpha=np.array(patch.getchannel('A'),dtype=np.uint16)*np.array(mask,dtype=np.uint16)//255;patch.putalpha(Image.fromarray(np.rint(alpha*layer['opacity']).astype('uint8')));image.paste(Image.alpha_composite(image.crop(box),patch),box[:2])
    return pad_islands(image.convert('RGB'),surface,size,destinations)
