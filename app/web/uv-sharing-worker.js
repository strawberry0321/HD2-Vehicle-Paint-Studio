const units=new Map(),GRID=32,EPS=1e-9;
const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
function area(p){let n=0;for(let i=0;i<p.length;i++){let a=p[i],b=p[(i+1)%p.length];n+=a[0]*b[1]-b[0]*a[1];}return Math.abs(n)*.5;}
function overlap(a,b){
  let p=a.slice(),sign=cross(...b)>=0?1:-1;
  for(let i=0;i<3&&p.length;i++){
    const c=b[i],d=b[(i+1)%3],q=[];
    for(let j=0;j<p.length;j++){
      const s=p[j],e=p[(j+1)%p.length],ds=cross(c,d,s)*sign,de=cross(c,d,e)*sign;
      if(ds>=-EPS)q.push(s);
      if((ds>EPS&&de<-EPS)||(ds<-EPS&&de>EPS)){let t=ds/(ds-de);q.push([s[0]+t*(e[0]-s[0]),s[1]+t*(e[1]-s[1])]);}
    }p=q;
  }return p.length>=3?area(p):0;
}
function variants(uv,t){
  let p=Array.from({length:3},(_,i)=>[uv[t*6+i*2],uv[t*6+i*2+1]]),x=Math.floor(Math.min(...p.map(v=>v[0]))+1e-7),y=Math.floor(Math.min(...p.map(v=>v[1]))+1e-7);
  p=p.map(v=>[v[0]-x,v[1]-y]);const out=[];
  for(let dx=Math.ceil(-Math.max(...p.map(v=>v[0]))+EPS);dx<=Math.floor(1-Math.min(...p.map(v=>v[0]))-EPS);dx++)for(let dy=Math.ceil(-Math.max(...p.map(v=>v[1]))+EPS);dy<=Math.floor(1-Math.min(...p.map(v=>v[1]))-EPS);dy++){
    const tri=p.map(v=>[v[0]+dx,v[1]+dy]),xs=tri.map(v=>v[0]),ys=tri.map(v=>v[1]),box=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];
    if(box[2]>EPS&&box[0]<1-EPS&&box[3]>EPS&&box[1]<1-EPS)out.push({tri,box,area:area(tri)});
  }return out;
}
function cells(box){
  const result=[];for(let x=Math.max(0,Math.floor(box[0]*GRID));x<=Math.min(GRID-1,Math.floor(box[2]*GRID));x++)for(let y=Math.max(0,Math.floor(box[1]*GRID));y<=Math.min(GRID-1,Math.floor(box[3]*GRID));y++)result.push(x*GRID+y);return result;
}
function indexFor(data,texture){
  if(data.indices.has(texture))return data.indices.get(texture);
  const index=new Map();for(let part of data.parts)if(part.texture===texture&&!part.disabled)for(let t=0;t<part.uv.length/6;t++)if(part.valid[t])for(let v of variants(part.uv,t)){
    if(v.area<EPS)continue;let candidate={...v,part:part.part,triangle:t};
    for(let key of cells(v.box)){if(!index.has(key))index.set(key,[]);index.get(key).push(candidate);}
  }data.indices.set(texture,index);return index;
}
function physicalTriangle(part,t){return Array.from({length:3},(_,k)=>Array.from(part.positions.subarray(t*9+k*3,t*9+k*3+3)));}
function sameSurface(a,b){const sub=(a,b)=>a.map((v,i)=>v-b[i]),dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),cross3=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],e=sub(a[1],a[0]),f=sub(a[2],a[0]),n=cross3(e,f),length=Math.hypot(...n);if(length<1e-12)return false;for(let i=0;i<3;i++)n[i]/=length;if(b.some(p=>Math.abs(dot(sub(p,a[0]),n))>2e-5))return false;const u=e.map(v=>v/Math.hypot(...e)),v=cross3(n,u),project=p=>{const q=sub(p,a[0]);return[dot(q,u),dot(q,v)];},aa=a.map(project),bb=b.map(project);return overlap(aa,bb)>Math.max(1e-10,Math.min(area(aa),area(bb))*.02);}
self.onmessage=({data:m})=>{try{
  if(m.type==='register'){units.set(m.unit,{parts:m.parts.map(p=>{const pos=new Float32Array(p.positions),valid=new Uint8Array(pos.length/9);for(let t=0;t<valid.length;t++){const i=t*9,ax=pos[i+3]-pos[i],ay=pos[i+4]-pos[i+1],az=pos[i+5]-pos[i+2],bx=pos[i+6]-pos[i],by=pos[i+7]-pos[i+1],bz=pos[i+8]-pos[i+2];valid[t]=Math.hypot(ay*bz-az*by,az*bx-ax*bz,ax*by-ay*bx)>1e-12;}return{...p,positions:pos,uv:new Float32Array(p.uv),valid};}),indices:new Map()});return;}
  const data=units.get(m.unit),source=data.parts[m.part],index=indexFor(data,source.texture),selected=new Set(m.triangles),shared=new Map();const blue=(m.surfaces||[{part:m.part,triangles:m.triangles}]).flatMap(s=>s.triangles.filter(t=>data.parts[s.part].valid[t]).map(t=>physicalTriangle(data.parts[s.part],t)));
  for(let t of m.triangles)if(source.valid[t])for(let v of variants(source.uv,t)){
    const tested=new Set();for(let key of cells(v.box))for(let c of index.get(key)||[]){
      const id=c.part+':'+c.triangle;if(tested.has(c)||shared.get(c.part)?.has(c.triangle)||(c.part===m.part&&selected.has(c.triangle)))continue;tested.add(c);
      if(Math.min(v.box[2],c.box[2])-Math.max(v.box[0],c.box[0])<=EPS||Math.min(v.box[3],c.box[3])-Math.max(v.box[1],c.box[1])<=EPS)continue;
      if(overlap(v.tri,c.tri)>Math.max(EPS,Math.min(v.area,c.area)*.02)&&!blue.some(a=>sameSurface(a,physicalTriangle(data.parts[c.part],c.triangle)))){
        if(!shared.has(c.part))shared.set(c.part,new Set());shared.get(c.part).add(c.triangle);
      }
    }
  }
  postMessage({request:m.request,shared:[...shared].map(([part,ts])=>({part,triangles:[...ts].sort((a,b)=>a-b)}))});
}catch(error){postMessage({request:m.request,error:String(error)});}};
