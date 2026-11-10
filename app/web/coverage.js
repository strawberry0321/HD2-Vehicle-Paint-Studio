import * as T from 'three';
import {OrbitControls} from '/vendor/OrbitControls.js';
const coverage=await fetch('/data/coverage.json').then(r=>r.json());
const scene=new T.Scene();scene.background=new T.Color('#e6e4e0');scene.add(new T.HemisphereLight(0xffffff,0x858b89,3));
for(const p of [[5,7,6],[-5,3,-5]]){const l=new T.DirectionalLight(0xfffaf2,3);l.position.set(...p);scene.add(l);}
const view=document.getElementById('view'),renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));view.append(renderer.domElement);
const camera=new T.PerspectiveCamera(36,1,.01,100),controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;let root=null,state=null,owner=null,serial=0,rows=[];
function fit(){if(!root)return;const box=new T.Box3().setFromObject(root),center=box.getCenter(new T.Vector3()),span=box.getSize(new T.Vector3()),size=Math.max(span.x,span.y,span.z);controls.target.copy(center);camera.position.copy(center).add(new T.Vector3(size*1.15,size*.6,size*1.6));controls.update();}
new ResizeObserver(()=>{renderer.setSize(view.clientWidth,view.clientHeight,false);camera.aspect=view.clientWidth/view.clientHeight;camera.updateProjectionMatrix();}).observe(view);
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera);});document.getElementById('reset').onclick=fit;
async function show(index){const request=++serial,row=rows[index];document.getElementById('status').textContent='正在载入…';try{
 const id=row.id,d=await fetch('/data/models/'+id.slice(2)+'.json').then(r=>r.json()),b=await fetch('/data/models/'+id.slice(2)+'.bin').then(r=>r.arrayBuffer());if(request!==serial)return;
 const next=new T.Group();let triangles=0;
 for(const p of d.parts){if(p.nonRenderable)continue;const values=new Float32Array(b,p.offset,p.count*8),positions=new Float32Array(p.count*3),normals=new Float32Array(p.count*3);const actual=p.displayPositionOffset!==undefined?new Float32Array(b,p.displayPositionOffset,p.count*3):null;const nativeNormals=actual&&p.nativeMapping?.nativeNormalOffset!==undefined?new Float32Array(b,p.nativeMapping.nativeNormalOffset,p.count*3):null;
  for(let i=0;i<p.count;i++){if(actual){positions.set([actual[i*3],actual[i*3+2],-actual[i*3+1]],i*3);if(nativeNormals)normals.set([nativeNormals[i*3],nativeNormals[i*3+2],-nativeNormals[i*3+1]],i*3);}else{positions.set(values.subarray(i*8,i*8+3),i*3);normals.set(values.subarray(i*8+3,i*8+6),i*3);}}
  const g=new T.BufferGeometry();g.setAttribute('position',new T.BufferAttribute(positions,3));g.setAttribute('normal',new T.BufferAttribute(normals,3));const setting=state?.unitColors?.[owner]?.[p.group]||state?.groups?.[p.group];const mat=new T.MeshStandardMaterial({color:setting&&setting.mode!=='original'?setting.color:(p.defaultColor||'#8b958a'),metalness:.08,roughness:.7,side:T.DoubleSide});next.add(new T.Mesh(g,mat));triangles+=p.count/3;
 }
 if(root){scene.remove(root);root.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});}root=next;scene.add(root);fit();document.getElementById('status').textContent=triangles.toLocaleString()+' 个三角面 · 结构 / 配色';window.__coverageReady=true;
 }catch(e){document.getElementById('status').textContent=e.message;window.__coverageError=e.message;}}
document.getElementById('state').onchange=e=>show(Number(e.target.value));
window.addEventListener('message',e=>{if(e.origin!==location.origin||e.data?.type!=='vehicle-coverage')return;state=e.data.state;owner=state.vehicle;const info=coverage[owner];rows=[{id:owner,name:'工作台模型 · LOD 0'},...(info?.states||[])];const select=document.getElementById('state');select.replaceChildren(...rows.map((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.name;return o;}));show(0);});
window.parent.postMessage({type:'vehicle-coverage-ready'},location.origin);
