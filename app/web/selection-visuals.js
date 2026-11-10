import * as T from 'three';

// WebGL ignores LineBasicMaterial.linewidth. Expand boundary edges in screen
// space so the outline remains readable at every zoom and display density.
function outline(geometry,color,width,dashed,boundary){
  const edges=boundary?new T.BufferGeometry().setAttribute('position',new T.BufferAttribute(boundary,3)):new T.EdgesGeometry(geometry,12),p=edges.attributes.position;
  const starts=[],ends=[],corners=[];
  for(let i=0;i<p.count;i+=2)for(const [along,side] of [[0,-1],[1,-1],[1,1],[0,-1],[1,1],[0,1]]){
    starts.push(p.getX(i),p.getY(i),p.getZ(i));
    ends.push(p.getX(i+1),p.getY(i+1),p.getZ(i+1));
    corners.push(along,side);
  }
  edges.dispose();
  const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute(starts,3));
  g.setAttribute('end',new T.Float32BufferAttribute(ends,3));g.setAttribute('corner',new T.Float32BufferAttribute(corners,2));
  const material=new T.ShaderMaterial({transparent:true,depthTest:false,depthWrite:false,toneMapped:false,
    uniforms:{lineColor:{value:new T.Color(color)},resolution:{value:new T.Vector2(1,1)},lineWidth:{value:width},dashed:{value:dashed?1:0}},
    vertexShader:`attribute vec3 end;attribute vec2 corner;uniform vec2 resolution;uniform float lineWidth;varying float along;
      void main(){vec4 a=projectionMatrix*modelViewMatrix*vec4(position,1.0);vec4 b=projectionMatrix*modelViewMatrix*vec4(end,1.0);
        vec2 d=(b.xy/b.w-a.xy/a.w)*resolution;float len=length(d);vec2 n=vec2(-d.y,d.x)/max(len,0.00001);
        vec4 clip=mix(a,b,corner.x);clip.xy+=n*corner.y*lineWidth/resolution*clip.w;gl_Position=clip;along=corner.x*len*0.5;}`,
    fragmentShader:`uniform vec3 lineColor;uniform float dashed;varying float along;
      void main(){if(dashed>0.5&&mod(along,13.0)>8.0)discard;gl_FragColor=vec4(lineColor,dashed>0.5?0.8:1.0);
        #include <colorspace_fragment>
      }`
  });
  const mesh=new T.Mesh(g,material);mesh.frustumCulled=false;mesh.renderOrder=10001;
  mesh.onBeforeRender=renderer=>renderer.getSize(material.uniforms.resolution.value);
  return mesh;
}

export function surfaceHighlight(geometry,secondary=false,boundary=null){
  const group=new T.Group();
  const fill=new T.Mesh(geometry,new T.MeshBasicMaterial({color:secondary?'#b4926a':'#779bc6',transparent:true,opacity:secondary?.12:.36,depthWrite:false,side:T.DoubleSide,polygonOffset:true,polygonOffsetFactor:-6,polygonOffsetUnits:-6,toneMapped:false}));
  fill.renderOrder=10000;group.add(fill);
  group.add(outline(geometry,secondary?'#9e7650':'#365f91',secondary?2.2:3.5,secondary,boundary));
  return group;
}

const worker=new Worker('/uv-sharing-worker.js'),pending=new Map(),registered=new Set();let requestId=0;
worker.onmessage=({data})=>{const p=pending.get(data.request);if(!p)return;pending.delete(data.request);data.error?p.reject(Error(data.error)):p.resolve(data.shared);};
export function sharedSurfaces(unit,model,part,triangles,surfaces=null){
  if(!registered.has(unit)){
    const parts=model.children.map(m=>({part:m.userData.part,texture:m.userData.color,disabled:!!m.userData.nonRenderable||!!m.userData.decalDisabledReason,uv:m.geometry.attributes.uv.array.slice().buffer,positions:m.geometry.attributes.position.array.slice().buffer}));
    worker.postMessage({type:'register',unit,parts},parts.flatMap(p=>[p.uv,p.positions]));registered.add(unit);
  }
  const request=++requestId;
  return new Promise((resolve,reject)=>{pending.set(request,{resolve,reject});worker.postMessage({type:'find',unit,request,part,triangles,surfaces});});
}
