import * as T from 'three';
const vec=a=>new T.Vector3(...a);
export function hitNormal(hit,geometric=false){
 const p=hit.object.geometry.attributes.position,n=hit.object.geometry.attributes.normal,i=hit.faceIndex*3;
 const a=new T.Vector3().fromBufferAttribute(p,i),b=new T.Vector3().fromBufferAttribute(p,i+1),c=new T.Vector3().fromBufferAttribute(p,i+2);
 const face=b.clone().sub(a).cross(c.clone().sub(a)).normalize(),average=new T.Vector3();
 for(let k=0;k<3;k++)average.add(new T.Vector3().fromBufferAttribute(n,i+k));
 if(face.dot(average)<0)face.negate();
 if(geometric)return face;
 const weights=T.Triangle.getBarycoord(hit.point,a,b,c,new T.Vector3());
 if(!weights)return average.normalize();
 const normal=new T.Vector3();for(let k=0;k<3;k++)normal.addScaledVector(new T.Vector3().fromBufferAttribute(n,i+k),weights.getComponent(k));
 return normal.lengthSq()>1e-10?normal.normalize():face;
}
export function sideLock(frame){return{mode:'locked',normal:[...frame.normal],origin:[...frame.origin],angle:55,band:1.25};}
export function acceptsHit(model,layer,hit){
 if(!hit||hit.object.userData.nonRenderable||hit.object.userData.decalDisabledReason)return false;
 const p=layer.projection,g=hit.object.userData.group||(model.userData.isFRV&&hit.object.userData.slot==='m_lights'?'P24':null);
 if(p.groups&&!p.groups.includes(g))return false;
 if(p.mask&&!p.mask.some(s=>s.part===hit.object.userData.part&&s.triangles.includes(hit.faceIndex)))return false;
 const lock=p.surface;
 if(!lock||lock.mode==='free'||p.type==='cylinder')return true;
 const ref=vec(lock.normal),limit=Math.cos((lock.angle??55)*Math.PI/180);
 return hitNormal(hit,true).dot(ref)>=limit-1e-6&&hitNormal(hit).dot(ref)>=limit-1e-6&&Math.abs(hit.point.clone().sub(vec(lock.origin)).dot(ref))<=(lock.band??1.25);
}
export function projectionCenter(layer){const p=layer.projection,f=layer.frame;return vec(f.origin).addScaledVector(vec(f.u),layer.placement.x*p.width).addScaledVector(vec(f.v),layer.placement.y*p.height);}
export function updatePose(layer){
 const p=layer.projection;
 if(!p.surfaceFrame){p.surfaceFrame=structuredClone(layer.frame);p.surfaceFrame.origin=projectionCenter(layer).toArray();}
 const base=p.surfaceFrame,pose=p.pose||{pitch:0,yaw:0},u=vec(base.u),v=vec(base.v),n=vec(base.normal);
 const yaw=new T.Quaternion().setFromAxisAngle(v,(pose.yaw||0)*Math.PI/180);u.applyQuaternion(yaw);n.applyQuaternion(yaw);
 const pitch=new T.Quaternion().setFromAxisAngle(u,(pose.pitch||0)*Math.PI/180);v.applyQuaternion(pitch);n.applyQuaternion(pitch);
 const origin=vec(base.origin).addScaledVector(u,-layer.placement.x*p.width).addScaledVector(v,-layer.placement.y*p.height);
 layer.frame={...layer.frame,origin:origin.toArray(),u:u.normalize().toArray(),v:v.normalize().toArray(),normal:n.normalize().toArray()};
}
export function followHit(model,layer,hit){
 if(!acceptsHit(model,layer,hit))return false;
 const p=layer.projection,base=p.surfaceFrame||{...layer.frame,origin:projectionCenter(layer).toArray()},normal=hitNormal(hit),previous=vec(base.normal);
 const transport=new T.Quaternion().setFromUnitVectors(previous,normal),u=vec(base.u).applyQuaternion(transport).projectOnPlane(normal).normalize(),v=normal.clone().cross(u).normalize();
 p.surfaceFrame={...base,origin:hit.point.toArray(),normal:normal.toArray(),u:u.toArray(),v:v.toArray()};
 updatePose(layer);return true;
}
export function snapPoint(model,layer,point){
 const p=layer.projection,normal=vec(p.surface?.mode==='locked'?p.surface.normal:(p.surfaceFrame||layer.frame).normal),ray=new T.Raycaster(point.clone().addScaledVector(normal,8),normal.clone().negate(),0,16);
 return ray.intersectObjects(model.children.filter(m=>!m.userData.nonRenderable),false).filter(h=>acceptsHit(model,layer,h)).sort((a,b)=>a.point.distanceToSquared(point)-b.point.distanceToSquared(point))[0]||null;
}
export function moveOffset(model,layer,key,value){
 const p=layer.projection,old=layer.placement[key];
 if(!p.followSurface||p.type==='cylinder'){layer.placement[key]=value;if(p.surfaceFrame)p.surfaceFrame.origin=projectionCenter(layer).toArray();return true;}
 const direction=vec((p.surfaceFrame||layer.frame)[key==='x'?'u':'v']),point=projectionCenter(layer).addScaledVector(direction,(value-old)*p[key==='x'?'width':'height']),hit=snapPoint(model,layer,point);
 if(!hit)return false;
 layer.placement[key]=value;return followHit(model,layer,hit);
}
export function ensureAttachment(model,layer){
 const p=layer.projection;if(!p||p.surface)return;
 const center=projectionCenter(layer);let best=null,distance=Infinity;
 const selected=layer.surfaces?.length?layer.surfaces:model.children.map((m,part)=>({part,triangles:Array.from({length:m.geometry.attributes.position.count/3},(_,i)=>i)}));
 for(const s of selected){const mesh=model.children[s.part];if(!mesh||mesh.userData.nonRenderable||mesh.userData.decalDisabledReason)continue;const positions=mesh.geometry.attributes.position;for(const t of s.triangles){const point=new T.Vector3();for(let k=0;k<3;k++)point.add(new T.Vector3().fromBufferAttribute(positions,t*3+k));point.multiplyScalar(1/3);const d=point.distanceToSquared(center);if(d<distance){distance=d;best={object:mesh,faceIndex:t,point};}}}
 const frame=best?{normal:hitNormal(best,true).toArray(),origin:best.point.toArray()}:layer.frame;
 p.surface=sideLock(frame);p.followSurface=true;p.pose={pitch:0,yaw:0};
}
