const models=new Map();
function parse(buffer){
 const v=new DataView(buffer);if(new TextDecoder().decode(new Uint8Array(buffer,0,8))!=='HD2SURF4')throw Error('表面索引格式无效');const [version,p,n,r,e,m,i,k]=Array.from({length:8},(_,j)=>v.getUint32(8+j*4,true));if(version!==1)throw Error('表面索引版本无效');let offset=40;const d={count:n,surfaceCount:r};
 for(const [name,count,Type] of [['parts',p+1,Uint32Array],['adjOffsets',n+1,Uint32Array],['adjacent',m,Uint32Array],['region',n,Uint32Array],['regionOffsets',r+1,Uint32Array],['regionTriangles',n,Uint32Array],['regionAdjOffsets',r+1,Uint32Array],['regionAdjacent',k,Uint32Array],['edgeOffsets',e+1,Uint32Array],['edgeFaces',i,Uint32Array],['edgeSigns',i,Int32Array],['edgePositions',e*6,Float32Array]]){d[name]=new Type(buffer,offset,count);offset+=count*4;}if(offset!==buffer.byteLength)throw Error('表面索引不完整');return d;
}
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
function idsFor(d,surfaces){return new Set(surfaces.flatMap(s=>s.triangles.map(t=>d.parts[s.part]+t)));}
function adjacent(d,t){return d.adjacent.subarray(d.adjOffsets[t],d.adjOffsets[t+1]);}
function regionIds(d,r){return d.regionTriangles.subarray(d.regionOffsets[r],d.regionOffsets[r+1]);}
function connected(d,ids){if(!ids.size)return false;const seen=new Set([ids.values().next().value]),queue=[...seen];for(let q=0;q<queue.length;q++)for(const a of adjacent(d,queue[q]))if(ids.has(a)&&!seen.has(a)){seen.add(a);queue.push(a);}return seen.size===ids.size;}
class Heap{
 constructor(){this.items=[];}
 push(node,cost){const item={node,cost};let i=this.items.length;this.items.push(item);while(i>0){const parent=(i-1)>>1;if(this.items[parent].cost<=cost)break;this.items[i]=this.items[parent];i=parent;}this.items[i]=item;}
 pop(){const first=this.items[0],last=this.items.pop();if(this.items.length){let i=0;while(i*2+1<this.items.length){let child=i*2+1;if(child+1<this.items.length&&this.items[child+1].cost<this.items[child].cost)child++;if(this.items[child].cost>=last.cost)break;this.items[i]=this.items[child];i=child;}this.items[i]=last;}return first;}
}
function connectPath(d,current,target){
 const distance=new Float64Array(d.count);distance.fill(Infinity);const previous=new Int32Array(d.count);previous.fill(-1);const heap=new Heap();
 for(const t of current){if([...adjacent(d,t)].some(a=>!current.has(a))){distance[t]=0;heap.push(t,0);}}
 let end=-1;
 while(heap.items.length){const {node:t,cost}=heap.pop();if(cost!==distance[t])continue;if(target.has(t)){end=t;break;}
  for(const a of adjacent(d,t)){const p=d.centers[t],q=d.centers[a],bend=1+Math.max(0,1-dot(d.normals[t],d.normals[a]))*.4,step=Math.hypot(p[0]-q[0],p[1]-q[1],p[2]-q[2])*bend+.00001,next=cost+step;if(next<distance[a]){distance[a]=next;previous[a]=t;heap.push(a,next);}}
 }
 if(end<0)return null;const path=[];for(let t=end;t>=0&&!current.has(t);t=previous[t])path.push(t);return path;
}
function surfacesFor(d,ids){const parts=new Map();for(const t of [...ids].sort((a,b)=>a-b)){let p=0;while(d.parts[p+1]<=t)p++;if(!parts.has(p))parts.set(p,[]);parts.get(p).push(t-d.parts[p]);}return[...parts].map(([part,triangles])=>({part,triangles}));}
function boundary(d,ids){const result=[];for(let edge=0;edge<d.edgeOffsets.length-1;edge++){
 const lo=d.edgeOffsets[edge],hi=d.edgeOffsets[edge+1];let draw=false;
 for(let a=lo;a<hi&&!draw;a++)if(ids.has(d.edgeFaces[a])){let neighbor=false;for(let b=lo;b<hi;b++)if(a!==b&&ids.has(d.edgeFaces[b])&&d.edgeSigns[a]!==d.edgeSigns[b]&&dot(d.normals[d.edgeFaces[a]],d.normals[d.edgeFaces[b]])>-.995){neighbor=true;break;}if(!neighbor)draw=true;}
 if(draw)result.push(...d.edgePositions.subarray(edge*6,edge*6+6));
 }return new Float32Array(result);}
function describe(d,ids){return{surfaces:surfacesFor(d,ids),surfaceCount:new Set([...ids].map(t=>d.region[t])).size,triangleCount:ids.size,connected:connected(d,ids),boundary:boundary(d,ids)};}
function seedRegion(d,start){
 const region=d.region[start],owner=d.owners[start],normal=d.normals[start],seen=new Set([start]),queue=[start];
 for(let q=0;q<queue.length;q++)for(const t of adjacent(d,queue[q]))if(!seen.has(t)&&d.region[t]===region&&d.owners[t]===owner&&dot(d.normals[t],normal)>.95){seen.add(t);queue.push(t);}
 return seen;
}
function candidate(d,start,mode){
 if(mode==='triangle')return new Set([start]);
 let base=seedRegion(d,start);if(mode==='plane')return base;
 const key=d.owners[start];
 if(mode==='panel'){
  const origin=d.centers[start],normal=d.normals[start],visited=new Set(base),queue=[...base];let best=d.region[start],area=0;
  for(let q=0;q<queue.length;q++)for(const t of adjacent(d,queue[q]))if(!visited.has(t)&&d.owners[t]===key&&dot(d.normals[t],normal)>.9&&Math.abs(dot(d.centers[t].map((v,a)=>v-origin[a]),normal))<.065){visited.add(t);queue.push(t);}
  const sizes=new Map();for(const t of visited)sizes.set(d.region[t],(sizes.get(d.region[t])||0)+d.areas[t]);for(const [r,size] of sizes)if(size>area){best=r;area=size;}
   base=new Set([...base,...[...visited].filter(t=>d.region[t]===best)]);
 }
 const normal=d.normals[start],origin=d.centers[start];let min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
 for(const t of base)for(let k=0;k<3;k++)for(let a=0;a<3;a++){const value=d.positions[t*9+k*3+a];min[a]=Math.min(min[a],value);max[a]=Math.max(max[a],value);}
 let depth=Math.max(.015,Math.min(.18,Math.hypot(...max.map((v,a)=>v-min[a]))*.16));
 const axis=normal.map(Math.abs).indexOf(Math.max(...normal.map(Math.abs))),other=[0,1,2].filter(a=>a!==axis),margin=.003;
 const allowed=(t,from)=>{if(d.owners[t]!==key)return false;const n=d.normals[t],p=d.centers[t],distance=dot(p.map((v,a)=>v-origin[a]),normal);return mode==='curved'?dot(n,d.normals[from])>.82&&dot(n,normal)>-.35:dot(n,normal)>-.05&&distance>=-.065&&distance<=depth&&other.every(a=>p[a]>=min[a]-margin&&p[a]<=max[a]+margin);};
 const queue=[...base];for(let q=0;q<queue.length;q++)for(const t of adjacent(d,queue[q]))if(!base.has(t)&&allowed(t,queue[q])){base.add(t);queue.push(t);}return base;
}
self.onmessage=({data:m})=>{try{
 if(m.type==='register'){
  const d=parse(m.topology);d.positions=new Float32Array(d.count*9);let offset=0;for(const b of m.positions){const p=new Float32Array(b);d.positions.set(p,offset);offset+=p.length;}
  const shading=m.normals?new Float32Array(d.count*9):null;if(shading){let off=0;for(const raw of m.normals){const values=new Float32Array(raw);shading.set(values,off);off+=values.length;}}
  d.normals=[];d.centers=[];for(let t=0;t<d.count;t++){const p=d.positions.subarray(t*9,t*9+9),b=[p[3]-p[0],p[4]-p[1],p[5]-p[2]],c=[p[6]-p[0],p[7]-p[1],p[8]-p[2]],n=[b[1]*c[2]-b[2]*c[1],b[2]*c[0]-b[0]*c[2],b[0]*c[1]-b[1]*c[0]],length=Math.hypot(...n)||1;if(shading){const k=t*9,s=[shading[k]+shading[k+3]+shading[k+6],shading[k+1]+shading[k+4]+shading[k+7],shading[k+2]+shading[k+5]+shading[k+8]];if(dot(n,s)<0)for(let a=0;a<3;a++)n[a]*=-1;}d.normals.push(n.map(v=>v/length));d.centers.push([(p[0]+p[3]+p[6])/3,(p[1]+p[4]+p[7])/3,(p[2]+p[5]+p[8])/3]);}
  d.owners=[];d.areas=[];for(let part=0;part<d.parts.length-1;part++)for(let t=d.parts[part];t<d.parts[part+1];t++){d.owners[t]=m.partKeys?.[part]??part;const i=t*9,p=d.positions,a=[p[i+3]-p[i],p[i+4]-p[i+1],p[i+5]-p[i+2]],b=[p[i+6]-p[i],p[i+7]-p[i+1],p[i+8]-p[i+2]];d.areas[t]=Math.hypot(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])/2;}
  models.set(m.unit,d);postMessage({request:m.request,result:{triangles:d.count,surfaceCount:d.surfaceCount}});return;
 }
 const d=models.get(m.unit),current=idsFor(d,m.surfaces||[]);let selected=current,bridgedSurfaces=0;
 if(m.type==='partition'){const remaining=new Set(current),components=[];while(remaining.size){const first=remaining.values().next().value,queue=[first];remaining.delete(first);for(let i=0;i<queue.length;i++)for(const a of adjacent(d,queue[i]))if(remaining.delete(a))queue.push(a);components.push(surfacesFor(d,new Set(queue)));}postMessage({request:m.request,result:components});return;}
 if(m.type==='edit'){
  if(m.operation==='expand'){
   const regions=new Set([...current].map(t=>d.region[t]));for(const t of current)for(const a of adjacent(d,t))regions.add(d.region[a]);selected=new Set([...regions].flatMap(r=>[...regionIds(d,r)]));
  }else{
   const added=candidate(d,d.parts[m.part]+m.triangle,m.mode||'panel');
   selected=m.operation==='add'?new Set([...current,...added]):m.operation==='subtract'?new Set([...current].filter(t=>!added.has(t))):added;
   if(m.operation==='add'&&m.autoConnect&&current.size&&!connected(d,selected)){
    const path=connectPath(d,current,added);
    if(path){const regions=new Set(path.map(t=>d.region[t]));for(const r of regions){const members=regionIds(d,r);if([...members].some(t=>!selected.has(t)))bridgedSurfaces++;for(const t of members)selected.add(t);}}
   }
  }
  if(!m.allowDisconnected&&selected.size&&!connected(d,selected)){postMessage({request:m.request,result:{rejected:true,message:m.operation==='subtract'?'减选后会分成不相连的区域，请从边缘减选。':m.autoConnect?'这两处在模型中没有连接路径，可能属于分离的零件；请分别添加图层。':'只能追加与蓝色选区相连的表面，或启用自动连接。'}});return;}
 }
 const result=describe(d,selected);result.bridgedSurfaces=bridgedSurfaces;postMessage({request:m.request,result},[result.boundary.buffer]);
}catch(error){postMessage({request:m.request,error:String(error)});}};
