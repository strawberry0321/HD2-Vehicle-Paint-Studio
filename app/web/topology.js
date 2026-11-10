import * as T from 'three';
const worker=new Worker('/topology-worker.js'),waiting=new Map(),registered=new Map();let serial=0;
worker.onmessage=({data})=>{const p=waiting.get(data.request);if(!p)return;waiting.delete(data.request);data.error?p.reject(Error(data.error)):p.resolve(data.result);};
function send(message,transfer=[]){const request=++serial;return new Promise((resolve,reject)=>{waiting.set(request,{resolve,reject});worker.postMessage({...message,request},transfer);});}
export function prepareTopology(unit,model){
 if(!registered.has(unit))registered.set(unit,(async()=>{
  const response=await fetch('/data/models/'+unit.slice(2)+'.surfaces.bin');if(!response.ok)throw Error('缺少表面索引');const topology=await response.arrayBuffer();
  const positions=model.children.map(m=>m.geometry.attributes.position.array.slice().buffer),normals=model.children.map(m=>m.geometry.attributes.normal.array.slice().buffer);
   const partKeys=model.children.map((m,i)=>[m.userData.nativeUnit||unit,m.userData.nativeMapping?.mesh??i,m.userData.group||'',i].join('|'));
  return send({type:'register',unit,topology,positions,normals,partKeys},[topology,...positions,...normals]);
 })());return registered.get(unit);
}
export async function editSelection(unit,model,options){await prepareTopology(unit,model);return send({type:'edit',unit,...options,autoConnect:false,allowDisconnected:true});}
export async function describeSelection(unit,model,surfaces){await prepareTopology(unit,model);return send({type:'describe',unit,surfaces});}
export async function splitSurfaces(unit,model,surfaces){await prepareTopology(unit,model);return send({type:'partition',unit,surfaces});}
export function surfacesOf(value){if(value?.surfaces)return value.surfaces;if(value?.part!==undefined)return[{part:value.part,triangles:value.triangles}];return[];}
export function selectionGeometry(model,surfaces){
 const positions=[],normals=[];for(const s of surfaces){const p=model.children[s.part].geometry.attributes.position,n=model.children[s.part].geometry.attributes.normal;for(const t of s.triangles)for(let k=0;k<3;k++){const i=t*3+k;positions.push(p.getX(i),p.getY(i),p.getZ(i));normals.push(n.getX(i),n.getY(i),n.getZ(i));}}
 const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute(positions,3));g.setAttribute('normal',new T.Float32BufferAttribute(normals,3));return g;
}
export function selectionFrame(model,surfaces,reference){
 const g=selectionGeometry(model,surfaces),p=g.attributes.position,center=new T.Vector3(),a=new T.Vector3(),normal=reference?new T.Vector3(...reference.normal):new T.Vector3(0,0,1);
 if(reference?.normal?.isVector3)normal.copy(reference.normal);
 for(let i=0;i<p.count;i++)center.add(a.fromBufferAttribute(p,i));center.multiplyScalar(1/p.count);normal.normalize();
 const up=Math.abs(normal.y)>.9?new T.Vector3(0,0,-1):new T.Vector3(0,1,0),u=reference?.u?new T.Vector3(...reference.u):up.clone().cross(normal).normalize(),v=reference?.v?new T.Vector3(...reference.v):normal.clone().cross(u).normalize();
 let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;for(let i=0;i<p.count;i++){a.fromBufferAttribute(p,i).sub(center);const x=a.dot(u),y=a.dot(v);x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);}g.dispose();
 return{origin:center.toArray(),normal:normal.toArray(),u:u.toArray(),v:v.toArray(),bounds:[x0,y0,x1,y1]};
}
export function overlaps(a,b){if(a.unit!==b.unit)return false;const other=new Map(surfacesOf(b).map(s=>[s.part,new Set(s.triangles)]));return surfacesOf(a).some(s=>s.triangles.some(t=>other.get(s.part)?.has(t)));}
export function migrateState(input){
 const state=structuredClone(input);
 if(state.version===2){for(const l of state.layers){l.surfaces=[{part:l.part,triangles:l.triangles}];delete l.part;delete l.triangles;l.placement.x/=2;l.placement.y/=2;l.selectionVersion=3;}state.version=3;}
 if(state.version===3){state.surfaceColors=[];for(const layer of state.layers){layer.role=layer.role||'legacy';}state.version=4;}
 if(state.version===4)state.version=5;
 if(state.version===5){state.unitColors=state.unitColors||{};state.colorLinks=state.colorLinks||{};state.version=6;}
 if(state.version===6){state.version=7;delete state.colorLinks;}
 if(state.version===7)state.version=8;
 return state;
}
