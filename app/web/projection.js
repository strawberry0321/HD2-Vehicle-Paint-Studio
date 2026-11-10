import * as T from 'three';
import {parameters,localPoint,validCorners} from './projection-math.js';
import {hitNormal} from './surface-follow.js';
export {parameters,validCorners};
const worker=new Worker('/projection-worker.js',{type:'module'}),pending=new Map(),registered=new Map();let serial=0;
const latestJobs=new Map(),projectionCache=new Map();let working=false;
worker.onmessage=({data})=>{const p=pending.get(data.request);if(!p)return;pending.delete(data.request);data.error?p.reject(Error(data.error)):p.resolve(data.result);};
function send(data,transfer=[]){const request=++serial;return new Promise((resolve,reject)=>{pending.set(request,{resolve,reject});worker.postMessage({...data,request},transfer);});}
async function runNext(){if(working||!latestJobs.size)return;working=true;const [id,job]=latestJobs.entries().next().value;latestJobs.delete(id);try{const result=await send({type:'project',unit:job.unit,layer:job.layer,resolution:job.resolution});projectionCache.set(job.key,result);if(projectionCache.size>8)projectionCache.delete(projectionCache.keys().next().value);job.resolve(result);}catch(error){job.reject(error);}finally{working=false;runNext();}}
export async function projectModel(model,layer,resolution=768){
 const unit=model.userData.id;
 if(!registered.has(unit)){const parts=model.children.map(m=>({disabled:!!m.userData.nonRenderable||!!m.userData.decalDisabledReason,group:m.userData.group||(model.userData.isFRV&&m.userData.slot==='m_lights'?'P24':null),positions:m.geometry.attributes.position.array.slice().buffer,normals:m.geometry.attributes.normal.array.slice().buffer}));registered.set(unit,send({type:'register',unit,parts},parts.flatMap(p=>[p.positions,p.normals])));}
 await registered.get(unit);
 // Build reusable geometry for the full depth envelope once. Depth and crop
 // then change only shader uniforms, at the next rendered frame.
 const work=structuredClone(layer);work.projection.depth=20;delete work.projection.crop;
 const key=JSON.stringify([unit,work.frame,work.projection,work.placement,resolution]),live=result=>result?{...result,parameters:parameters(layer)}:null;
 if(projectionCache.has(key))return live(projectionCache.get(key));
 return new Promise((resolve,reject)=>{const old=latestJobs.get(layer.id);if(old)old.resolve(null);latestJobs.set(layer.id,{unit,layer:work,resolution,key,resolve:r=>resolve(live(r)),reject});runNext();});
}
export function updateProjectionAppearance(root,layer){
 if(!root)return;const p=parameters(layer);root.userData.parameters=p;if(root.userData.result)root.userData.result.parameters=p;
 for(const mesh of root.children){const u=mesh.material.userData.decalUniforms;if(u){u.decalSize.value.z=p.depth;u.decalCrop.value.set(...p.crop);}mesh.material.opacity=layer.opacity;mesh.material.color.set(layer.tint?layer.color:'#ffffff');}
}

function dataTexture(data,width,channels=4){const pixels=data.length/channels,height=Math.max(1,Math.ceil(pixels/width)),padded=new Float32Array(width*height*channels);padded.set(data);const tex=new T.DataTexture(padded,width,height,channels===1?T.RedFormat:T.RGBAFormat,T.FloatType);tex.minFilter=tex.magFilter=T.NearestFilter;tex.needsUpdate=true;return tex;}
const helpers=`
varying vec3 decalLocal;varying vec3 decalSurfaceNormal;
uniform sampler2D decalDepthMap;uniform sampler2D decalFaceData;uniform vec2 decalFaceSize;uniform float decalGridSize;
uniform vec4 decalCrop;uniform vec4 decalBox;uniform vec4 decalSize;uniform vec4 decalMove;uniform mat3 decalShape;uniform float decalCylinder;uniform float decalRadius;uniform float decalCutoff;uniform float decalDistance;
uniform vec3 decalSideNormal;uniform vec3 decalSideOrigin;uniform float decalSideCos;uniform float decalSideBand;
vec3 readFace(float index){return texture2D(decalFaceData,vec2(mod(index,decalFaceSize.x)+0.5,floor(index/decalFaceSize.x)+0.5)/decalFaceSize).xyz;}
bool decalHidden(float face){if(face<0.5)return false;float index=(face-1.0)*3.0;vec3 a=readFace(index),b=readFace(index+1.0),c=readFace(index+2.0),e=b-a,f=c-a,n=cross(e,f);vec3 point;float front,own;
 if(decalCylinder>0.5){float rho=length(decalLocal.xz);vec3 direction=vec3(decalLocal.x/max(rho,0.000001),0.0,decalLocal.z/max(rho,0.000001));float denom=dot(n,direction);if(abs(denom)<0.0000001)return false;front=(dot(n,a)-n.y*decalLocal.y)/denom;if(front<=0.0)return false;point=vec3(direction.x*front,decalLocal.y,direction.z*front);own=rho;}
 else{own=decalLocal.z;if(decalDistance>0.0){vec3 direction=decalLocal-vec3(0.0,0.0,decalDistance);float denom=dot(n,direction);if(abs(denom)<0.0000001)return false;float t=(dot(n,a)-n.z*decalDistance)/denom;if(t<=0.0)return false;point=vec3(direction.xy*t,decalDistance+direction.z*t);front=point.z;}else{if(abs(n.z)<0.0000001)return false;front=(dot(n,a)-n.x*decalLocal.x-n.y*decalLocal.y)/n.z;point=vec3(decalLocal.xy,front);}}
 if(front<=own+0.00002)return false;vec3 r=point-a;float ee=dot(e,e),ef=dot(e,f),ff=dot(f,f),re=dot(r,e),rf=dot(r,f),det=ee*ff-ef*ef;if(abs(det)<0.0000000001)return false;float u=(re*ff-rf*ef)/det,v=(rf*ee-re*ef)/det;return u>=-0.00002&&v>=-0.00002&&u+v<=1.00002;
}
vec2 decalUV(){vec2 planar=decalLocal.xy;if(decalCylinder<0.5&&decalDistance>0.0){if(decalLocal.z>=decalDistance-0.00001)discard;planar*=decalDistance/(decalDistance-decalLocal.z);}float x=planar.x-decalMove.z,y=planar.y-decalMove.w,z=decalLocal.z;float facing=normalize(decalSurfaceNormal).z;if(decalCylinder<0.5&&decalDistance>0.0)facing=dot(normalize(decalSurfaceNormal),normalize(vec3(-decalLocal.xy,decalDistance-decalLocal.z)));
 if(decalCylinder>0.5){float rho=length(decalLocal.xz);x=decalRadius*atan(decalLocal.x,decalLocal.z)-decalMove.z;float span=6.28318530718*decalRadius;x=mod(x+span*0.5,span)-span*0.5;z=rho-decalRadius;facing=dot(normalize(decalSurfaceNormal),vec3(decalLocal.x/max(rho,0.000001),0.0,decalLocal.z/max(rho,0.000001)));}
 if(abs(z)>decalSize.z||facing<decalCutoff)discard;
 if(decalSideCos> -1.0&&(dot(normalize(decalSurfaceNormal),decalSideNormal)<decalSideCos||abs(dot(decalLocal-decalSideOrigin,decalSideNormal))>decalSideBand))discard;
 vec2 uv=vec2((decalMove.x*x-decalMove.y*y)/decalSize.x+0.5,(decalMove.y*x+decalMove.x*y)/decalSize.y+0.5);vec3 warped=decalShape*vec3(uv,1.0);vec2 imageUv=warped.xy/warped.z;if(any(lessThan(imageUv,vec2(0.0)))||any(greaterThan(imageUv,vec2(1.0))))discard;
 vec2 grid=(uv-decalBox.xy)/(decalBox.zw-decalBox.xy);float center=texture2D(decalDepthMap,grid).r;if(decalHidden(center))discard;
 for(int j=-1;j<=1;j++)for(int i=-1;i<=1;i++){if(i==0&&j==0)continue;float face=texture2D(decalDepthMap,grid+vec2(float(i),float(j))/decalGridSize).r;if(face!=center&&decalHidden(face))discard;}
 return mix(decalCrop.xy,decalCrop.zw,imageUv);
}`;
export function projectionOverlay(model,layer,result,texture){
 const p=result.parameters,depth=dataTexture(result.grid,result.resolution,1),faces=dataTexture(result.faces,2048),root=new T.Group();root.userData={layer:layer.id,result,parameters:p,ownedTextures:[depth,faces]};
 for(const selected of result.surfaces){const source=model.children[selected.part],g=new T.BufferGeometry(),positions=[],normals=[],local=[],localNormals=[],sp=source.geometry.attributes.position,sn=source.geometry.attributes.normal;
  for(const t of selected.triangles)for(let k=0;k<3;k++){const a=new T.Vector3().fromBufferAttribute(sp,t*3),b=new T.Vector3().fromBufferAttribute(sp,t*3+1),c=new T.Vector3().fromBufferAttribute(sp,t*3+2),mean=new T.Vector3().fromBufferAttribute(sn,t*3).add(new T.Vector3().fromBufferAttribute(sn,t*3+1)).add(new T.Vector3().fromBufferAttribute(sn,t*3+2)),flip=b.sub(a).cross(c.sub(a)).dot(mean)<0,i=t*3+(flip?[0,2,1][k]:k),position=[sp.getX(i),sp.getY(i),sp.getZ(i)],normal=[sn.getX(i),sn.getY(i),sn.getZ(i)];positions.push(...position);normals.push(...normal);local.push(...localPoint(position,p));localNormals.push(normal.reduce((s,v,j)=>s+v*p.frame.u[j],0),normal.reduce((s,v,j)=>s+v*p.frame.v[j],0),normal.reduce((s,v,j)=>s+v*p.frame.normal[j],0));}
  g.setAttribute('position',new T.Float32BufferAttribute(positions,3));g.setAttribute('normal',new T.Float32BufferAttribute(normals,3));g.setAttribute('decalPosition',new T.Float32BufferAttribute(local,3));g.setAttribute('decalNormal',new T.Float32BufferAttribute(localNormals,3));g.setAttribute('uv',new T.Float32BufferAttribute(new Float32Array(positions.length/3*2),2));
  const mat=new T.MeshBasicMaterial({map:texture,color:layer.tint?layer.color:'#ffffff',opacity:layer.opacity,transparent:true,side:T.FrontSide,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1,toneMapped:false});
  mat.onBeforeCompile=shader=>{Object.assign(shader.uniforms,{decalDepthMap:{value:depth},decalFaceData:{value:faces},decalFaceSize:{value:new T.Vector2(faces.image.width,faces.image.height)},decalGridSize:{value:result.resolution},decalBox:{value:new T.Vector4(...p.bbox)},decalSize:{value:new T.Vector4(p.width,p.height,root.userData.parameters.depth,0)},decalMove:{value:new T.Vector4(p.c,p.s,p.cx,p.cy)},decalShape:{value:new T.Matrix3().set(...p.H)},decalCylinder:{value:p.type==='cylinder'?1:0},decalRadius:{value:p.radius},decalCutoff:{value:p.cutoff},decalDistance:{value:p.distance}});Object.assign(shader.uniforms,{decalSideNormal:{value:new T.Vector3(...p.sideNormal)},decalSideOrigin:{value:new T.Vector3(...p.sideOrigin)},decalSideCos:{value:p.sideCos},decalSideBand:{value:p.sideBand},decalCrop:{value:new T.Vector4(...root.userData.parameters.crop)}});mat.userData.decalUniforms=shader.uniforms;shader.vertexShader='attribute vec3 decalPosition;attribute vec3 decalNormal;varying vec3 decalLocal;varying vec3 decalSurfaceNormal;\n'+shader.vertexShader;shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\ndecalLocal=decalPosition;decalSurfaceNormal=decalNormal;');shader.fragmentShader=helpers+shader.fragmentShader;shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>','diffuseColor *= texture2D(map, decalUV());');};mat.customProgramCacheKey=()=> 'projection-conform-v7';
  const mesh=new T.Mesh(g,mat);mesh.userData={part:selected.part,layer:layer.id};mesh.visible=source.visible;root.add(mesh);
 }return root;
}
export function anchorFrame(model,hit,camera,type='planar',axis='Y'){
 const origin=hit.point.clone(),normal=hitNormal(hit);let v=camera.up.clone().projectOnPlane(normal);if(v.lengthSq()<1e-8)v=new T.Vector3(...(Math.abs(normal.y)<.9?[0,1,0]:[0,0,1])).projectOnPlane(normal);v.normalize();let u=v.clone().cross(normal).normalize();v=normal.clone().cross(u).normalize();let radius=.08;
 if(type==='cylinder'){
  v=new T.Vector3(...{X:[1,0,0],Y:[0,1,0],Z:[0,0,1]}[axis]);normal.projectOnPlane(v);if(normal.lengthSq()<1e-8)normal.set(...(axis==='Z'?[1,0,0]:[0,0,1]));normal.normalize();u=v.clone().cross(normal).normalize();
  const source=hit.object,p=source.geometry.attributes.position,n=source.geometry.attributes.normal,rows=[];
  for(let i=0;i<p.count;i++){const point=new T.Vector3().fromBufferAttribute(p,i),rel=point.clone().sub(hit.point),norm=new T.Vector3().fromBufferAttribute(n,i);if(rel.length()>.45||Math.abs(norm.dot(v))>.35)continue;rows.push({x:rel.dot(u),z:rel.dot(normal),nx:norm.dot(u),nz:norm.dot(normal),d:rel.lengthSq()});}
  // Long rods often have no vertex near the clicked middle. Intersect their
  // triangle edges with an axial cross-section instead of relying on vertices.
  for(let t=0;t<p.count;t+=3){const positions=[0,1,2].map(k=>new T.Vector3().fromBufferAttribute(p,t+k)),along=positions.map(q=>q.clone().sub(hit.point).dot(v));if(Math.min(...along)>0||Math.max(...along)<0)continue;for(let k=0;k<3;k++){const j=(k+1)%3,den=along[k]-along[j];if(Math.abs(den)<1e-10||along[k]*along[j]>0)continue;const blend=along[k]/den,point=positions[k].clone().lerp(positions[j],blend),rel=point.sub(hit.point),norm=new T.Vector3().fromBufferAttribute(n,t+k).lerp(new T.Vector3().fromBufferAttribute(n,t+j),blend).normalize();if(rel.length()>.45||Math.abs(norm.dot(v))>.35)continue;rows.push({x:rel.dot(u),z:rel.dot(normal),nx:norm.dot(u),nz:norm.dot(normal),d:rel.lengthSq()});}}
  rows.sort((a,b)=>a.d-b.d);let aa=0,ab=0,bb=0,ax=0,bx=0;const sample=rows.slice(0,180);
  for(const q of sample){const a=-q.nz,b=q.nx,c=a*q.x+b*q.z;aa+=a*a;ab+=a*b;bb+=b*b;ax+=a*c;bx+=b*c;}
  const det=aa*bb-ab*ab;
  if(det>1e-5){const x=(ax*bb-bx*ab)/det,z=(bx*aa-ax*ab)/det,rs=sample.map(q=>Math.hypot(q.x-x,q.z-z)).sort((a,b)=>a-b),r=rs[Math.floor(rs.length/2)],error=rs.reduce((s,a)=>s+Math.abs(a-r),0)/rs.length;if(r>=.001&&r<.5&&error<r*.25){radius=r;origin.addScaledVector(u,x).addScaledVector(normal,z);}else origin.addScaledVector(normal,-radius);}
  else origin.addScaledVector(normal,-radius);
 }
 return {frame:{origin:origin.toArray(),u:u.toArray(),v:v.toArray(),normal:normal.toArray(),bounds:[-.5,-.5,.5,.5]},radius,distance:0};
}
export function quadWorld(layer){const p=parameters(layer),points=p.corners.map(([u,v])=>{const x=(u-.5)*p.width,y=(v-.5)*p.height,a=p.c*x+p.s*y+p.cx,b=-p.s*x+p.c*y+p.cy;if(p.type==='cylinder'){const theta=a/p.radius;return new T.Vector3(...p.frame.origin).addScaledVector(new T.Vector3(...p.frame.u),p.radius*Math.sin(theta)).addScaledVector(new T.Vector3(...p.frame.normal),p.radius*Math.cos(theta)).addScaledVector(new T.Vector3(...p.frame.v),b);}return new T.Vector3(...p.frame.origin).addScaledVector(new T.Vector3(...p.frame.u),a).addScaledVector(new T.Vector3(...p.frame.v),b);});return points;}
