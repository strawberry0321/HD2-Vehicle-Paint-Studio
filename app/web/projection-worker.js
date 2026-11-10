import {projectParts} from './projection-math.js';
const models=new Map();
onmessage=({data:m})=>{try{if(m.type==='register'){models.set(m.unit,m.parts.map(p=>({...p,positions:new Float32Array(p.positions),normals:p.normals?new Float32Array(p.normals):null})));postMessage({request:m.request,result:true});return;}const result=projectParts(models.get(m.unit),m.layer,m.resolution||768);postMessage({request:m.request,result},[result.grid.buffer,result.faces.buffer]);}catch(error){postMessage({request:m.request,error:error.message});}};
