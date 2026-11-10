import * as T from 'three';
import {surfacesOf} from '/topology.js';
export function selectionKey(value){return value?value.unit+'|'+surfacesOf(value).map(s=>s.part+':'+s.triangles.slice().sort((a,b)=>a-b).join(',')).sort().join('|'):'';}
export function coveredBy(a,b){if(a.unit!==b.unit)return false;const sets=new Map(surfacesOf(b).map(s=>[s.part,new Set(s.triangles)]));return surfacesOf(a).every(s=>s.triangles.every(t=>sets.get(s.part)?.has(t)));}
export function subtractSurfaces(a,b){const sets=new Map(surfacesOf(b).map(s=>[s.part,new Set(s.triangles)]));return surfacesOf(a).map(s=>({part:s.part,triangles:s.triangles.filter(t=>!sets.get(s.part)?.has(t))})).filter(s=>s.triangles.length);}
export function colorOverlay(model,patch,white){
 const root=new T.Group();root.userData.patch=patch.id;
 for(const selected of surfacesOf(patch)){
  const source=model.children[selected.part],positions=[],normals=[],uv=[];
  for(const t of selected.triangles)for(let k=0;k<3;k++){const i=t*3+k,p=source.geometry.attributes.position,n=source.geometry.attributes.normal,u=source.geometry.attributes.uv;positions.push(p.getX(i),p.getY(i),p.getZ(i));normals.push(n.getX(i),n.getY(i),n.getZ(i));uv.push(u.getX(i),u.getY(i));}
  const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.Float32BufferAttribute(positions,3));geometry.setAttribute('normal',new T.Float32BufferAttribute(normals,3));geometry.setAttribute('uv',new T.Float32BufferAttribute(uv,2));
  const material=source.material.clone();material.map=white;material.color.set(patch.color);material.onBeforeCompile=()=>{};material.customProgramCacheKey=()=> 'local-base-color-v5';material.transparent=source.material.transparent;material.opacity=source.material.opacity;material.depthWrite=false;material.polygonOffset=true;material.polygonOffsetFactor=-2;material.polygonOffsetUnits=-2;
  const mesh=new T.Mesh(geometry,material);mesh.userData.part=selected.part;mesh.visible=source.visible;root.add(mesh);
 }return root;
}
