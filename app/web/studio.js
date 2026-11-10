import * as T from 'three';
import {projectModel,projectionOverlay,updateProjectionAppearance,anchorFrame,quadWorld,parameters,validCorners} from '/projection.js';
import {hitNormal,sideLock,followHit,moveOffset,updatePose,ensureAttachment,projectionCenter} from '/surface-follow.js';
import {visiblePoint} from '/projection-math.js';
import {reuseOverlay,reuseAppearance,reuseUV,reuseFrame,reuseQuad,reuseWorldUV} from '/uv-reuse.js';
import {OrbitControls} from '/vendor/OrbitControls.js';
import {selectSurface,frameFor,surfaceGeometry,overlayMaterial} from '/surfaces.js';
import {prepareRecolor,setRecolor} from '/recolor.js';
import {surfaceHighlight,sharedSurfaces} from '/selection-visuals.js';
import {prepareTopology,editSelection,describeSelection,surfacesOf,selectionGeometry,selectionFrame,overlaps,migrateState,splitSurfaces} from '/topology.js';
import {colorOverlay,selectionKey,coveredBy,subtractSurfaces} from '/surface-colors.js';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
$('#busy .busy-card').insertAdjacentHTML('beforeend','<progress id="exportProgress" hidden></progress><p id="exportDetail" hidden></p><p id="exportBackup" hidden></p><button id="stopExport" hidden>取消导出并返回编辑</button>');
const catalog=await fetch('/data/catalog.json').then(r=>r.json()),groups=Object.fromEntries(catalog.groups.map(g=>[g.id,g])),vehicles=Object.fromEntries(catalog.vehicles.map(v=>[v.id,v]));
let state={format:'HD2VehicleProject',version:8,physicalRevision:2,mechMaterialRevision:2,unitColors:{},surfaceColors:[],name:'我的载具涂装',vehicle:'0xcc21c7ffd3ebefb9',family:'0xcc21c7ffd3ebefb9',coverFixed:true,removeStockDecals:true,groups:Object.fromEntries(catalog.groups.map(g=>[g.id,{mode:'original',color:g.defaultColor}])),layers:[]};
let initialized=false;let editMode='decal',placementAnchor=null,placingDecal=false,reanchoring=false,shapeMode=false,workspaceId=null,restoringLayout=false;
let model=null,selectedGroup='P24',selection=null,selectedAsset=null,activeLayer=null,tool='orbit',category='全部',serial=0,history=[],future=[];
let assets=new Map(),models=new Map(),textures=new Map(),overlayMeshes=new Map(),highlight=null,dirty=true;const overlayRevision=new Map(),faceColorMeshes=new Map();let libraryRole='photo',importRole='photo',contextLayerId=null,colorStroke=null,paintPending=null,paintBusy=false;
const scene=new T.Scene();scene.background=new T.Color('#e6e4e0');scene.fog=new T.Fog('#e6e4e0',18,40);
// Metal needs reflected light as well as direct lamps, including on the underside.
const studioEnvironment=new T.CubeTexture(Array.from({length:6},()=>{const canvas=document.createElement('canvas');canvas.width=canvas.height=64;const cx=canvas.getContext('2d'),gradient=cx.createLinearGradient(0,0,0,64);gradient.addColorStop(0,'#dddeda');gradient.addColorStop(.5,'#b6bab9');gradient.addColorStop(1,'#a6adaf');cx.fillStyle=gradient;cx.fillRect(0,0,64,64);return canvas;}));studioEnvironment.colorSpace=T.SRGBColorSpace;studioEnvironment.needsUpdate=true;scene.environment=studioEnvironment;scene.environmentIntensity=1;
const vp=$('#viewport'),renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;vp.prepend(renderer.domElement);
const camera=new T.PerspectiveCamera(36,1,.01,100),controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.09;controls.minDistance=.15;controls.maxDistance=30;controls.autoRotateSpeed=.6;controls.addEventListener('change',()=>dirty=true);
scene.add(new T.HemisphereLight('#fffaf4','#b6b3af',3.1));for(let [position,power] of [[[5,8,7],3.6],[[-6,4,-5],2.8]]){let light=new T.DirectionalLight('#fffaf5',power);light.position.set(...position);scene.add(light);}
const floor=new T.Mesh(new T.PlaneGeometry(200,200),new T.MeshStandardMaterial({color:'#dedcd7',roughness:1}));floor.rotation.x=-Math.PI/2;floor.position.y=-.025;scene.add(floor);const grid=new T.GridHelper(16,32,'#bfc0bf','#cecdca');grid.material.transparent=true;grid.material.opacity=.23;grid.position.y=-.018;scene.add(grid);
window.backgroundV9.onChange=theme=>{scene.background.set(theme.background);scene.fog.color.set(theme.background);floor.material.color.set(theme.floor);const colors=grid.geometry.attributes.color;for(let i=0;i<colors.count;i++){const c=new T.Color(i<8?theme.grid:theme.lines);colors.setXYZ(i,c.r,c.g,c.b);}colors.needsUpdate=true;dirty=true;if(initialized)layoutChanged();};window.backgroundV9.set('light');
let resizeFrame,lastWidth=0,lastHeight=0;new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{let r=vp.getBoundingClientRect(),w=Math.floor(r.width),h=Math.floor(r.height);if(w===lastWidth&&h===lastHeight)return;lastWidth=w;lastHeight=h;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();dirty=true;});}).observe(vp);
renderer.setAnimationLoop(()=>{updateDecalGizmo();controls.autoRotate=$('#autoRotate').checked;controls.update();if(dirty||controls.autoRotate){floor.visible=grid.visible=camera.position.y>=0;renderer.render(scene,camera);dirty=false;}});
let whiteCanvas=document.createElement('canvas');whiteCanvas.width=whiteCanvas.height=1;whiteCanvas.getContext('2d').fillStyle='white';whiteCanvas.getContext('2d').fillRect(0,0,1,1);const white=new T.CanvasTexture(whiteCanvas);white.colorSpace=T.SRGBColorSpace;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,4000);}
let recoveryTimer;
function recoveryData(){return{state,selection,activeLayer,selectedGroup,libraryRole,workspaceId,layout:captureLayout(),camera:{position:camera.position.toArray(),up:camera.up.toArray(),target:controls.target.toArray()}};}
function changed(){dirty=true;$('#projectStatus').textContent='工程有未保存的改动';clearTimeout(recoveryTimer);recoveryTimer=setTimeout(()=>autosaveWorkbench().catch(console.error),500);}
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/autosave',new Blob([JSON.stringify(recoveryData())],{type:'application/json'})));
function checkpoint(){history.push(JSON.stringify(state));if(history.length>40)history.shift();future=[];changed();historyButtons();}
function historyButtons(){$('#undo').disabled=!history.length;$('#redo').disabled=!future.length;}
async function restore(text){state=normalizeState(JSON.parse(text));activeLayer=null;selection=null;await chooseModel(state.vehicle,false);$('#projectName').value=state.name;await syncOverlays();historyButtons();changed();}
async function undo(){if(!history.length)return;future.push(JSON.stringify(state));await restore(history.pop());}
async function redo(){if(!future.length)return;history.push(JSON.stringify(state));await restore(future.pop());}
async function restoreSession(incoming,loadState=true){
 if((incoming.sourcePhysicalRevision??incoming.state?.physicalRevision??1)<2){incoming=structuredClone(incoming);incoming.selection=migratePhysicalRecord(incoming.selection);if(incoming.layout?.selection)incoming.layout.selection=migratePhysicalRecord(incoming.layout.selection);}

 if(loadState){await refreshLibrary();await restore(JSON.stringify(incoming.state));}
 if(incoming.selectedGroup&&vehicles[state.vehicle].groups.includes(incoming.selectedGroup))selectGroup(incoming.selectedGroup);
 if(incoming.libraryRole==='pattern'||incoming.libraryRole==='photo'){libraryRole=incoming.libraryRole;renderAssets();}
 if(incoming.selection?.unit===state.vehicle){const chosen=structuredClone(incoming.selection),details=await describeSelection(state.vehicle,model,surfacesOf(chosen));if(details.surfaces.length){selection=chosen;await highlightSelection(details);}}
 const current=state.layers.find(l=>l.id===incoming.activeLayer&&l.unit===state.vehicle);if(current){activeLayer=current.id;if(!selection){selection={unit:current.unit,surfaces:structuredClone(surfacesOf(current)),frame:structuredClone(current.frame),selectionVersion:current.selectionVersion};await highlightSelection();}}
 if(incoming.workspaceId)workspaceId=incoming.workspaceId;
 renderLayers();if(incoming.camera){camera.position.fromArray(incoming.camera.position);camera.up.fromArray(incoming.camera.up);controls.target.fromArray(incoming.camera.target);controls.update();dirty=true;}
 if(incoming.layout)await applyLayout(incoming.layout);
}
async function api(url,data){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});let v=await r.json();if(!r.ok){const error=Error(v.error||'操作失败');error.code=v.code;throw error;}return v;}
async function texture(url,srgb=false){let key=url+':'+srgb;if(!textures.has(key))textures.set(key,new T.TextureLoader().loadAsync(url).then(tex=>{tex.colorSpace=srgb?T.SRGBColorSpace:T.NoColorSpace;tex.anisotropy=Math.min(16,renderer.capabilities.getMaxAnisotropy());tex.wrapS=tex.wrapT=T.RepeatWrapping;return tex;}));return textures.get(key);}
async function loadModel(id){if(models.has(id)){const cached=models.get(id);models.delete(id);models.set(id,cached);return cached;}let promise=(async()=>{let v=vehicles[id],d=await fetch('/data/'+v.model).then(r=>r.json()),b=await fetch('/data/models/'+id.slice(2)+'.bin').then(r=>r.arrayBuffer()),root=new T.Group(),min=new T.Vector3(...d.bounds[0]),max=new T.Vector3(...d.bounds[1]),span=max.clone().sub(min),scale=5/Math.max(span.x,span.y,span.z),center=min.clone().add(max).multiplyScalar(.5);root.userData={id,isFRV:v.isFRV,height:span.y*scale,descriptor:d};
 for(let [index,p] of d.parts.entries()){let source=new Float32Array(b,p.offset,p.count*8),position=new Float32Array(p.count*3),normal=new Float32Array(p.count*3),uv=new Float32Array(p.count*2);for(let i=0;i<p.count;i++){position.set([(source[i*8]-center.x)*scale,(source[i*8+1]-min.y)*scale,(source[i*8+2]-center.z)*scale],i*3);normal.set(source.subarray(i*8+3,i*8+6),i*3);uv.set(source.subarray(i*8+6,i*8+8),i*2);}let g=new T.BufferGeometry();g.setAttribute('position',new T.BufferAttribute(position,3));g.setAttribute('normal',new T.BufferAttribute(normal,3));g.setAttribute('uv',new T.BufferAttribute(uv,2));g.computeBoundingSphere();let base=await texture('/data/'+p.color,true),n=await texture('/data/'+p.normal),mraKey='/data/'+p.mra+':packed';if(!textures.has(mraKey))textures.set(mraKey,(async()=>{let original=await texture('/data/'+p.mra),c=document.createElement('canvas');c.width=c.height=1024;let cx=c.getContext('2d');cx.drawImage(original.image,0,0);let a=cx.getImageData(0,0,1024,1024);for(let j=0;j<a.data.length;j+=4){let metal=a.data[j];a.data[j]=a.data[j+2];a.data[j+2]=metal;}cx.putImageData(a,0,0);let t=new T.CanvasTexture(c);t.wrapS=t.wrapT=T.RepeatWrapping;return t;})());let mra=await textures.get(mraKey),mat=new T.MeshStandardMaterial({map:base,normalMap:n,metalnessMap:mra,roughnessMap:mra,metalness:1,roughness:1,side:T.DoubleSide,transparent:(p.sourceOpacity??1)<.999,opacity:p.sourceOpacity??1,depthWrite:(p.sourceOpacity??1)>=.999}),mesh=new T.Mesh(g,mat);prepareRecolor(mat);mesh.name=p.name;mesh.userData={...p,part:index,baseTexture:base};root.add(mesh);}return root;})();models.set(id,promise);return promise;}
async function prunePreviewCache(){
 const request=serial;while(models.size>3){const entry=[...models].find(([id])=>id!==state.vehicle);if(!entry)break;models.delete(entry[0]);const old=await entry[1];if(old===model)continue;for(const m of old.children){m.geometry.dispose();m.material.dispose();}}
 const cached=await Promise.all([...models.values()]);if(request!==serial)return;const used=new Set([white]);for(const root of cached)for(const m of root.children)for(const tex of [m.userData.baseTexture,m.material.map,m.material.normalMap,m.material.roughnessMap,m.material.metalnessMap])if(tex)used.add(tex);for(const root of overlayMeshes.values())for(const m of root.children)if(m.material.map)used.add(m.material.map);
 if(textures.size>48)for(const [key,promise] of [...textures]){const tex=await promise;if(request!==serial)return;if(!used.has(tex)){textures.delete(key);tex.dispose();}}
}

const roots=catalog.vehicles.filter(v=>v.role==='主体').sort((a,b)=>(a.order??99)-(b.order??99));
function rootFor(id){let v=vehicles[id];return v.role==='主体'?id:(v.parents?.includes(state.family)?state.family:v.parents?.[0]||id);}
function renderVehicles(){let q=$('#searchVehicle').value.trim();let list=roots.filter(v=>(category==='全部'||v.categoryV3===category)&&(!q||v.name.includes(q)));$('#vehicleCount').textContent=list.length+' 辆';$('#vehicles').innerHTML=list.map(v=>`<div class="vehicle-entry ${v.id===state.family?'active':''}"><button class="vehicle-item ${v.id===state.family?'active':''}" data-vehicle="${v.id}"><span class="glyph">${v.categoryV3==='机甲'?'♙':v.categoryV3==='飞行载具'?'⌁':'▦'}</span><span><b>${esc(v.name)}</b><small>${esc(v.categoryV3)}</small></span></button></div>`).join('');let family=vehicles[state.family],options=catalog.vehicles.filter(v=>v.parents?.includes(state.family));$('#accessoryCount').textContent=options.length?'本体 + '+options.length+' 项':'本体';$('#accessories').innerHTML=[family,...options].filter(Boolean).map(v=>`<button data-component="${v.id}" class="${v.id===state.vehicle?'active':''}">${v.id===state.family?'载具本体':esc(v.name)}</button>`).join('');}
$('#categories').innerHTML=['全部','地面载具','机甲','飞行载具','任务设备'].map(c=>`<button data-cat="${c}" class="${c===category?'active':''}">${c}</button>`).join('');$('#categories').onclick=e=>{let b=e.target.closest('[data-cat]');if(!b)return;category=b.dataset.cat;$$('[data-cat]').forEach(x=>x.classList.toggle('active',x===b));renderVehicles();};$('#searchVehicle').oninput=renderVehicles;$('#vehicles').onclick=e=>{const exportButton=e.target.closest('[data-export-family]');if(exportButton){openExport({kind:'vehicle',family:exportButton.dataset.exportFamily});return;}let b=e.target.closest('[data-vehicle]');if(b){state.family=b.dataset.vehicle;chooseModel(b.dataset.vehicle);}};$('#accessories').onclick=e=>{let b=e.target.closest('[data-component]');if(b)chooseModel(b.dataset.component);};
function colorSetting(gid,uid=state.vehicle){return state.unitColors?.[uid]?.[gid]||state.groups[gid];}
function editableColor(gid=selectedGroup){state.unitColors??={};state.unitColors[state.vehicle]??={};return state.unitColors[state.vehicle][gid]??=structuredClone(colorSetting(gid));}
function effective(mesh){if(mesh.userData.group)return colorSetting(mesh.userData.group);const vehicle=vehicles[state.vehicle];if(state.coverFixed&&vehicle.fixedColorGroup&&mesh.userData.slot===vehicle.fixedColorSlot)return colorSetting(vehicle.fixedColorGroup);return{mode:'original',color:'#ffffff'};}

function applyColors(){
 if(!model)return;
 for(let mesh of model.children){
  const setting=effective(mesh),group=groups[mesh.userData.group],material=mesh.material;
  material.map=setting.mode==='solid'||(setting.mode==='native'&&!group)?white:mesh.userData.baseTexture;
  material.color.set(setting.mode==='solid'||(setting.mode==='native'&&!group)?setting.color:'#ffffff');
  setRecolor(material,setting,mesh.userData.defaultColor||group?.defaultColor);
 }
 dirty=true;
}
function groupLabel(id,uid=state.vehicle){return vehicles[uid]?.groupLabels?.[id]||groups[id]?.name||"载具部件";}
function renderParts(){let ids=vehicles[state.vehicle].groups;$('#partCount').textContent=ids.length+' 栏';$('#parts').innerHTML=ids.map(id=>{let g=groups[id],s=colorSetting(id);return `<button data-group="${id}" class="part-row ${id===selectedGroup?'active':''}"><span class="swatch" style="background:${s.mode==='original'?g.defaultColor:s.color}"></span><span class="part-text"><b>${esc(groupLabel(id))}</b><small>仅当前模型</small></span></button>`;}).join('');}
function selectGroup(id){if(!groups[id])return;selectedGroup=id;let g=groups[id],s=colorSetting(id);renderParts();$('#groupName').textContent=groupLabel(id);$('#partBadge').textContent=groupLabel(id);$('#sharedInfo').textContent=vehicles[state.vehicle].mechAppearance?'仅当前机甲 · 四型分别编辑，主体合并导出':'仅当前模型 · 配色与贴花独立保存、独立导出';$$('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===s.mode));$('#baseColor').value=s.color;$('#hexColor').value=s.color.toUpperCase();$('#fixedColorLabel').hidden=vehicles[state.vehicle].fixedColorGroup!==id;$('#coverFixed').checked=state.coverFixed;$('#removeStockDecals').checked=state.removeStockDecals!==false;$('#affectedList').textContent='各模型独立保存；原材质来源：'+g.affected.map(x=>x.name).join('、');isolate();if($('#projectionScope'))renderProjectionTargets();}
$('#parts').onclick=e=>{let b=e.target.closest('[data-group]');if(b){activeLayer=null;selection=null;placementAnchor=null;reanchoring=placingDecal=false;clearHighlight();selectGroup(b.dataset.group);$('#projectionScope').value='group';renderLayers();}};
async function chooseModel(id,record=true){let request=++serial;$('#loading').hidden=false;try{let next=await loadModel(id);if(request!==serial)return;if(record)checkpoint();state.vehicle=id;state.family=rootFor(id);if(model)scene.remove(model);model=next;scene.add(model);selection=null;activeLayer=null;placementAnchor=null;placingDecal=reanchoring=false;clearHighlight();for(const l of state.layers)if(l.unit===id)ensureAttachment(model,l);$('#vehicleName').textContent=vehicles[id].name;selectedGroup=vehicles[id].defaultGroup||vehicles[id].groups[0];renderVehicles();selectGroup(selectedGroup);applyColors();await syncOverlays();resetView();setTool();model.userData.surfaceStats=await prepareTopology(id,model);await prunePreviewCache();$('#modelStats').textContent=vehicles[id].mechAppearance?'当前机甲外观独立 · 四型主体合并导出':'连续投射贴花 · 跨零件与曲面';$('#emptyError').hidden=true;window.__studioReady=initialized;window.__startupError=null;if(initialized)window.dispatchEvent(new Event('studio-ready'));}catch(e){$('#emptyError').hidden=false;$('#emptyError').textContent='模型载入失败：'+e.message;console.error(e);}finally{if(request===serial)$('#loading').hidden=true;}}
function isolate(){if(model)for(let m of model.children)m.visible=!m.userData.nonRenderable&&(!$('#isolate').checked||(m.userData.group===selectedGroup||(state.coverFixed&&vehicles[state.vehicle].fixedColorGroup===selectedGroup&&m.userData.slot===vehicles[state.vehicle].fixedColorSlot)));for(const group of [...overlayMeshes.values(),...faceColorMeshes.values()])for(const m of group.children)m.visible=!!model.children[m.userData.part]?.visible;dirty=true;}$('#isolate').onchange=isolate;
function changeColor(hex,mode){if(!/^#[0-9a-f]{6}$/i.test(hex)||!selectedGroup)return;checkpoint();let s=editableColor();s.color=hex;s.mode=mode||(s.mode==='original'?'solid':s.mode);applyColors();selectGroup(selectedGroup);}
$$('[data-mode]').forEach(b=>b.onclick=()=>{checkpoint();editableColor().mode=b.dataset.mode;applyColors();selectGroup(selectedGroup);});$('#baseColor').oninput=e=>{editableColor().color=e.target.value;if(editableColor().mode==='original')editableColor().mode='solid';applyColors();$('#hexColor').value=e.target.value;changed();};$('#baseColor').onpointerdown=checkpoint;$('#baseColor').onchange=()=>selectGroup(selectedGroup);$('#hexColor').onchange=e=>changeColor(e.target.value);$('#palette').innerHTML=catalog.palettes.map(p=>`<button data-color="${p.color}" style="background:${p.color}" title="${p.name}"></button>`).join('');$('#palette').onclick=e=>{let b=e.target.closest('[data-color]');if(b)changeColor(b.dataset.color);};$('#coverFixed').onchange=e=>{checkpoint();state.coverFixed=e.target.checked;applyColors();};$('#restoreGroup').onclick=()=>{checkpoint();Object.assign(editableColor(),{mode:'original',color:vehicles[state.vehicle].defaultColors?.[selectedGroup]||groups[selectedGroup].defaultColor});applyColors();selectGroup(selectedGroup);};
function resetView(){camera.up.set(0,1,0);let h=model?.userData.height||4;controls.target.set(0,h*.48,0);camera.position.set(6.5,h*.55+3,8.2);controls.update();dirty=true;}
function view(which){camera.up.set(0,1,0);let h=model?.userData.height||4;controls.target.set(0,h*.5,0);camera.position.set(...{front:[0,h*.5,9],back:[0,h*.5,-9],side:[9,h*.5,0],top:[0,10,.001]}[which]);controls.update();dirty=true;}
$('#viewFront').onclick=()=>view('front');$('#viewBack').onclick=()=>view('back');$('#viewSide').onclick=()=>view('side');$('#viewTop').onclick=()=>view('top');$('#resetView').onclick=resetView;
function setTool(){
 tool='combined';controls.enableRotate=true;
 $('#viewHint').textContent='拖动旋转 · 单击选贴花 · 中心柄沿面移动 · 右键菜单';
}
function layerRole(l){if(l?.role&&l.role!=='legacy')return l.role;return assets.get(l?.asset)?.kind==='image'?'photo':'pattern';}
function remapPhysicalSurfaces(surfaces,uid){const moves=catalog.physicalRemaps?.[uid]||[];return(surfaces||[]).flatMap(surface=>{const move=moves.find(m=>m.fromPart===surface.part);if(!move)return[surface];const map=new Map(move.triangles.map((old,i)=>[old,i])),keep=surface.triangles.filter(t=>!map.has(t)),shift=surface.triangles.filter(t=>map.has(t)).map(t=>map.get(t));return[...(keep.length?[{part:surface.part,triangles:keep}]:[]),...(shift.length?[{part:move.part,triangles:shift}]:[])];});}
function migratePhysicalRecord(record){if(!record)return record;record=structuredClone(record);record.surfaces=remapPhysicalSurfaces(record.surfaces,record.unit);if(record.projection?.mask)record.projection.mask=remapPhysicalSurfaces(record.projection.mask,record.unit);return record;}
function normalizeState(input){const value=migrateState(input);value.removeStockDecals??=true;for(const g of catalog.groups)value.groups[g.id]??={mode:'original',color:g.defaultColor};value.unitColors??={};for(const v of catalog.vehicles){value.unitColors[v.id]??={};for(const gid of v.groups)value.unitColors[v.id][gid]??=structuredClone(value.groups[gid]?.mode!=='original'?value.groups[gid]:{mode:'original',color:v.defaultColors?.[gid]||groups[gid].defaultColor});}delete value.colorLinks;if((value.physicalRevision||1)<2){value.layers=value.layers.map(migratePhysicalRecord);value.surfaceColors=(value.surfaceColors||[]).map(migratePhysicalRecord);value.physicalRevision=2;}for(const l of value.layers){if(!l.role||l.role==='legacy'){l.role=assets.get(l.asset)?.kind==='image'?'photo':'pattern';if(l.role==='photo'&&l.tint)l.legacyTinted=true;}if(l.projection&&!('groups' in l.projection)){l.projection.scopeKind='group';l.projection.groups=[vehicles[l.unit].groups[0]];}}value.surfaceColors=value.surfaceColors||[];return value;}
let highlightSerial=0,sharedSelection=[],selectionAction='replace',showSharedUVV8=false;
function clearHighlight(){
 highlightSerial++;sharedSelection=[];
 if(highlight){scene.remove(highlight);highlight.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});highlight=null;}
 $('#selectionLabel').textContent='单击选择一整块表面';$('#selectionHud').hidden=true;$('#applyAsset').disabled=!selectedAsset||(editMode==='area'&&!selection);syncFaceColorControls();dirty=true;
}
function addHighlight(part,triangles,secondary=false){
 const source=model.children[part],g=surfaceGeometry(source,triangles,selection.frame,{fit:'cover',scale:1,x:0,y:0,angle:0},1);
 const visual=surfaceHighlight(g,secondary);visual.userData.sourcePart=part;visual.visible=source.visible;highlight.add(visual);
}
async function highlightSelection(details=null){
 if(editMode==='decal'){clearHighlight();return;}
 clearHighlight();if(!selection||selection.unit!==state.vehicle)return;
 const request=highlightSerial,chosen=selection,surfaces=surfacesOf(selection),mesh=model.children[surfaces[0].part];
 try{
  details=details||await describeSelection(state.vehicle,model,surfaces);if(request!==highlightSerial||chosen!==selection)return;
  highlight=new T.Group();const primary=surfaceHighlight(selectionGeometry(model,surfaces),false,details.boundary);highlight.add(primary);scene.add(highlight);
  if(areaImage()){primary.children[0].visible=false;$('#selectionLabel').textContent='当前图片作用区域';$('#selectionHud').hidden=true;$('#applyAsset').disabled=!selectedAsset;syncFaceColorControls();dirty=true;}const name=groupLabel(mesh.userData.group);$('#selectionLabel').textContent='已选 '+details.surfaceCount+' 块区域（允许分离）';
  $('#selectionHud').hidden=false;$('#selectionName').textContent=name;$('#selectionCount').textContent='蓝色为当前选区 · '+details.surfaceCount+' 块区域（允许分离）';$('#sharedSelectionInfo').textContent=showSharedUVV8?'正在查找相同贴图区…':'';$('#applyAsset').disabled=!selectedAsset;syncFaceColorControls();dirty=true;
  if(!showSharedUVV8){sharedSelection=[];return;}
  const shared=await Promise.all(surfaces.map(s=>sharedSurfaces(state.vehicle,model,s.part,s.triangles,surfaces)));if(request!==highlightSerial||selection!==chosen)return;
  const selected=new Map(surfaces.map(s=>[s.part,new Set(s.triangles)])),merged=new Map();
  for(const list of shared)for(const other of list){if(!merged.has(other.part))merged.set(other.part,new Set());for(const t of other.triangles)if(!selected.get(other.part)?.has(t))merged.get(other.part).add(t);}
  sharedSelection=[...merged].filter(([p,ts])=>ts.size).map(([part,ts])=>({part,triangles:[...ts]}));
  for(const other of sharedSelection)addHighlight(other.part,other.triangles,true);
  $('#sharedSelectionInfo').textContent=sharedSelection.length?'虚线仅作参考；右键可将当前外观复用到这些区域。':'未找到其他相同贴图区。';dirty=true;
 }catch(error){if(request===highlightSerial)toast('表面识别失败：'+error.message);console.error(error);}
}
const ray=new T.Raycaster(),mouse=new T.Vector2();let down=null,pickSerial=0,pendingImportSurface=null;
function hit(e){if(!model)return;const r=renderer.domElement.getBoundingClientRect();mouse.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(mouse,camera);return ray.intersectObjects(model.children.filter(m=>m.visible),false)[0];}
async function selectPatch(part,triangle,operation='replace',mode=$('#surfaceMode').value,normal=null){
 if(model.children[part]?.userData.decalDisabledReason){toast('此处保留原有透明功能材质，可用部件配色调整底色');return false;}
 colorStroke=null;const request=++pickSerial,unit=state.vehicle;$('#selectionLabel').textContent='正在识别相连表面…';
 const details=await editSelection(unit,model,{part,triangle,operation,mode,surfaces:surfacesOf(selection),autoConnect:$('#autoConnect')?.checked??true});details.surfaces=details.surfaces.filter(s=>!model.children[s.part].userData.decalDisabledReason);
 if(unit!==state.vehicle||request!==pickSerial)return false;
 if(details.rejected){toast(details.message);if(selection)$('#selectionLabel').textContent='选区保持不变 · 仅允许相连表面';return false;}
 if(!details.surfaces.length){selection=null;activeLayer=null;clearHighlight();renderLayers();return true;}
 let reference=operation!=='replace'&&selection?selection.frame:null;
 if(!reference){const p=model.children[part].geometry.attributes.position,a=new T.Vector3().fromBufferAttribute(p,triangle*3),b=new T.Vector3().fromBufferAttribute(p,triangle*3+1),c=new T.Vector3().fromBufferAttribute(p,triangle*3+2);reference={normal:normal||b.sub(a).cross(c.sub(a)).normalize().toArray()};}
 selection={unit,surfaces:details.surfaces,frame:selectionFrame(model,details.surfaces,reference),selectionVersion:5};activeLayer=null;
 await highlightSelection(details);if(details.bridgedSurfaces)toast('已自动补齐 '+details.bridgedSurfaces+' 块连接表面');renderLayers();return true;
}
async function selectAt(e){
 if(e.button===2&&editMode==='area')return !!selection;
 const h=hit(e);
 if(editMode==='decal'){
  if(e.button===0&&(placingDecal||reanchoring)){if(h)await setProjectionAnchor(h);return !!h;}
  const picked=h?pickDecal(h):null;activeLayer=picked?.id||null;selection=null;clearHighlight();
  if(picked?.projection?.groups?.length===1)selectGroup(picked.projection.groups[0]);
  if(e.button===2)placementAnchor=h?anchorFrame(model,h,camera,$('#projectionType').value,$('#cylinderAxis').value):null;if(picked)window.layoutV7.show('decal');
  renderLayers();layoutChanged();return !!picked;
 }
 if(!h)return false;
 if(e.button===0&&!e.altKey&&!e.shiftKey&&!e.ctrlKey&&!e.metaKey&&selectionAction==='replace'){const picked=pickDecal(h);if(areaImage(picked)){await selectAreaImage(picked);return true;}}
 if(e.button===2&&selection&&surfacesOf(selection).some(s=>s.part===h.object.userData.part&&s.triangles.includes(h.faceIndex)))return true;
 const group=h.object.userData.group||(vehicles[state.vehicle].isFRV?'P24':null);if(group)selectGroup(group);
 const operation=e.shiftKey?'add':e.ctrlKey||e.metaKey?'subtract':selectionAction;
 try{return await selectPatch(h.object.userData.part,h.faceIndex,operation,$('#surfaceMode').value,h.face.normal.toArray());}catch(error){toast('选面失败：'+error.message);return false;}
}
for(const button of $$('[data-select-action]'))button.onclick=()=>{selectionAction=button.dataset.selectAction;$$('[data-select-action]').forEach(b=>b.classList.toggle('active',b===button));};
$('#expandSelection').onclick=async()=>{if(!selection){toast('先选择一块表面');return;}const first=surfacesOf(selection)[0];await selectPatch(first.part,first.triangles[0],'expand');};
const menu=$('#surfaceMenu');
function hideMenu(){menu.hidden=true;$('#assetMenu').hidden=true;}
function showMenu(e){
 $('#contextTitle').textContent=selection?'已选表面 · '+groupLabel(model.children[surfacesOf(selection)[0].part].userData.group):'模型操作';
 menu.querySelectorAll('[data-needs-surface]').forEach(b=>b.disabled=!selection);
 $('#contextApply').disabled=!selectedAsset||(editMode==='area'&&!selection);const current=layer();contextLayerId=current&&(current.projection||selection&&overlaps(current,selection))?current.id:state.layers.filter(l=>l.unit===state.vehicle&&!l.hidden&&selection&&overlaps(l,selection)).at(-1)?.id;$('#contextDelete').disabled=!contextLayerId;$('#contextDelete').textContent=contextLayerId?'删除图层：'+state.layers.find(l=>l.id===contextLayerId).name:'删除图层';
 configureSurfaceMenuV7();menu.hidden=false;
 menu.style.left=Math.max(8,Math.min(e.clientX,innerWidth-menu.offsetWidth-8))+'px';
 menu.style.top=Math.max(8,Math.min(e.clientY,innerHeight-menu.offsetHeight-8))+'px';
}
renderer.domElement.addEventListener('contextmenu',e=>e.preventDefault());
renderer.domElement.addEventListener('pointerdown',e=>{hideMenu();if(e.button===0||e.button===2)down={button:e.button,x:e.clientX,y:e.clientY,moved:false};});
renderer.domElement.addEventListener('pointermove',e=>{if(down&&Math.hypot(e.clientX-down.x,e.clientY-down.y)>5)down.moved=true;});
renderer.domElement.addEventListener('pointercancel',()=>down=null);
renderer.domElement.addEventListener('pointerup',async e=>{
 if(!down||down.button!==e.button)return;const moved=down.moved||Math.hypot(e.clientX-down.x,e.clientY-down.y)>5;down=null;if(moved)return;
  if(e.button===2){if(editMode==='decal')await selectAt(e);showMenu(e);return;}await selectAt(e);
});
document.addEventListener('pointerdown',e=>{if(!menu.contains(e.target)&&!$('#assetMenu').contains(e.target))hideMenu();},true);
window.addEventListener('resize',hideMenu);
menu.onclick=e=>{
 const item=e.target.closest('[data-action]'),action=item?.dataset.action;if(!action||item.disabled||item.hidden)return;hideMenu();
 if(action==='shared-uv'){showSharedUVV8=!showSharedUVV8;highlightSelection().catch(e=>toast(e.message));layoutChanged();return;}if(action==='reuse-uv'){reuseSelectedV8().catch(e=>toast(e.message));return;}
 if(action==='import'){importRole='photo';libraryRole='photo';pendingImportSurface=editMode==='decal'&&placementAnchor?{unit:state.vehicle,projectionAnchor:structuredClone(placementAnchor)}:selection?structuredClone(selection):null;$('#imageFile').click();}
 if(action==='color'){window.layoutV7.show('color');$('#faceBaseDetails').open=true;$('#faceColor').scrollIntoView({block:'center'});$('#faceColor').focus();try{$('#faceColor').showPicker();}catch{$('#faceColor').click();}}if(action==='delete')deleteLayer(contextLayerId);
 if(['crop','replace','duplicate','reanchor'].includes(action)){if(action==='crop')openCropV7().catch(error=>toast(error.message));if(action==='replace')chooseReplacementV7();if(action==='duplicate')$('#duplicateLayer').click();if(action==='reanchor')$('#repositionDecal').click();return;}
 if(action==='apply'){if(editMode==='decal'&&placementAnchor)addProjectionAtAnchor().catch(e=>toast(e.message));else $('#applyAsset').click();}
 if(action==='library'){window.layoutV7.show('assets');$('#assets').scrollIntoView({block:'center'});toast(editMode==='decal'?'选择素材，再点击“放置一张贴花”':'选择素材，再添加到当前选区');}
 if(action==='face')$('#faceView').click();
 if(action==='clear')$('#clearSelection').click();
 if(action==='reset')resetView();if(action==='expand')$('#expandSelection').click();
};
$('#clearSelection').onclick=()=>{pickSerial++;selection=null;activeLayer=null;clearHighlight();hideMenu();renderLayers();};$('#faceView').onclick=()=>{if(!selection){toast('先选择一个表面');return;}let f=selection.frame,center=new T.Vector3(...f.origin),n=new T.Vector3(...f.normal),w=f.bounds[2]-f.bounds[0],h=f.bounds[3]-f.bounds[1],distance=Math.max(.25,Math.max(w,h)*.7/Math.tan(camera.fov*Math.PI/360));if(n.dot(camera.position.clone().sub(center))<0)n.negate();camera.up.set(...f.v);controls.target.copy(center);camera.position.copy(center).add(n.multiplyScalar(distance));controls.update();dirty=true;};
async function refreshLibrary(){const data=await fetch('/api/library').then(r=>r.json());assets=new Map(data.map(a=>[a.id,a]));renderAssets();}
function renderAssets(){
 $$('[data-library-role]').forEach(b=>b.classList.toggle('active',b.dataset.libraryRole===libraryRole));$('#assets').dataset.role=libraryRole;
 const list=[...assets.values()].filter(a=>(a.roles||(a.kind==='image'?['photo']:['pattern'])).includes(libraryRole)&&!(a.removedRoles||[]).includes(libraryRole));
 $('#assets').innerHTML=list.map(a=>`<button class="asset-card ${a.id===selectedAsset?'active':''}" data-asset="${a.id}" title="${esc(a.name)} · ${a.width} × ${a.height}"><img src="${libraryRole==='photo'?(a.photoThumbnail||a.thumbnail):a.thumbnail}" alt=""><span><b>${esc(a.name)}</b><small>${a.width} × ${a.height}</small></span></button>`).join('')||`<p class="library-empty">${libraryRole==='photo'?'照片与图片保留原色，导入后可重复使用。':'内置与自定义图案放在这里，可单独着色。'}</p>`;
 $('#replaceAsset').disabled=!layer()||!selectedAsset;$('#libraryHint').textContent=libraryRole==='photo'?'照片默认保持原色，与部件配色、图案颜色分开。':'可导入自己的 PNG、JPG 或 WebP 图案，按需开启着色。';$('#applyAsset').disabled=!selectedAsset||(editMode==='area'&&!selection);syncPatternControls();
}
$$('[data-library-role]').forEach(button=>button.onclick=()=>{libraryRole=button.dataset.libraryRole;selectedAsset=null;renderAssets();syncPatternControls();});
$('#assets').onclick=e=>{const button=e.target.closest('[data-asset]');if(!button)return;selectedAsset=button.dataset.asset;renderAssets();if(pendingAssetReplacement&&layer()){pendingAssetReplacement=false;$('#replaceAsset').click();window.layoutV7.show('decal');}$('#placementHint').textContent=selection?'可反复添加到当前选区；同一面支持多个图层。':'点击“放置一张贴花”，再在模型上点击一次。';syncPatternControls();};
$('#uploadImage').onclick=()=>{importRole='photo';libraryRole='photo';pendingImportSurface=null;$('#imageFile').click();};
$('#uploadPattern').onclick=()=>{importRole='pattern';libraryRole='pattern';pendingImportSurface=null;$('#imageFile').click();};
$('#imageFile').addEventListener('cancel',()=>pendingImportSurface=null);
$('#imageFile').onchange=async e=>{
 const file=e.target.files[0];if(!file)return;
 try{const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file);}),asset=await api('/api/import',{image:data,name:file.name,role:importRole});libraryRole=importRole;await refreshLibrary();selectedAsset=asset.id;$('#tintImage').checked=false;renderAssets();if(pendingImportSurface?.unit===state.vehicle&&pendingImportSurface.projectionAnchor){placementAnchor=pendingImportSurface.projectionAnchor;await addProjectionAtAnchor();}else if(pendingImportSurface?.unit===state.vehicle){selection=pendingImportSurface;await highlightSelection();await addCurrentAsset();}toast((importRole==='photo'?'原图':'自定义图案')+'已存入对应分区');}
 catch(error){toast(error.message);}finally{e.target.value='';pendingImportSurface=null;}
};
$('#addText').onclick=()=>$('#textDialog').showModal();$('#cancelText').onclick=()=>$('#textDialog').close();$('#confirmText').onclick=async()=>{let text=$('#textContent').value.trim();if(!text)return;let a=await api('/api/stamp',{text});await refreshLibrary();selectedAsset=a.id;libraryRole='pattern';renderAssets();$('#textDialog').close();};
function layer(){return state.layers.find(l=>l.id===activeLayer);}
function areaImage(l=layer()){return !!(l?.projection?.mask||l?.uvReuse);}
function areaSelection(l){const surfaces=structuredClone(l.projection?.mask||l.surfaces);return{unit:l.unit,surfaces,frame:selectionFrame(model,surfaces,l.frame),selectionVersion:5};}
async function selectAreaImage(l){activeLayer=l.id;const ids=l.projection?.groups||[...new Set((l.surfaces||[]).map(s=>partGroup(s.part)).filter(Boolean))];if(ids.length===1)selectGroup(ids[0]);selection=areaSelection(l);window.layoutV7.show('decal');await highlightSelection();renderLayers();layoutChanged();}

async function addCurrentAsset(){
 if(!selectedAsset){toast('先从素材库选择一张图片');return;}
 if(editMode==='decal'){
  placingDecal=true;reanchoring=false;syncProjectionControls();toast('放置模式：在目标部件点击一次；Esc 取消');return;
 }
 if(!selection){toast('先选择区域，或切换自由贴花直接放置');return;}
 const f=structuredClone(selection.frame),cx=(f.bounds[0]+f.bounds[2])/2,cy=(f.bounds[1]+f.bounds[3])/2;f.origin=f.origin.map((v,i)=>v+cx*f.u[i]+cy*f.v[i]);const w=f.bounds[2]-f.bounds[0],h=f.bounds[3]-f.bounds[1];f.bounds=[-w/2,-h/2,w/2,h/2];placementAnchor={frame:f,radius:.08};return addProjectionAtAnchor(structuredClone(selection.surfaces));
}
$('#applyAsset').onclick=()=>addCurrentAsset().catch(error=>toast(error.message));
function disposeOverlay(group){scene.remove(group);group.userData.ownedTextures?.forEach(t=>t.dispose());group.traverse(m=>{m.geometry?.dispose();m.material?.dispose();});}
function updateOverlayOrders(){const local=state.layers.filter(l=>l.unit===state.vehicle);local.forEach((l,index)=>{const group=overlayMeshes.get(l.id);if(group){group.renderOrder=0;group.children.forEach(m=>m.renderOrder=10+index);}});dirty=true;}
const overlayTasks=new Set();
function updateOverlay(...args){const task=renderOverlay(...args);overlayTasks.add(task);task.then(()=>overlayTasks.delete(task),()=>overlayTasks.delete(task));return task;}
async function renderOverlay(l,prepared=null){
 if(!state.layers.includes(l))return;
 const revision=(overlayRevision.get(l.id)||0)+1;overlayRevision.set(l.id,revision);const old=overlayMeshes.get(l.id);
 if(l.hidden||l.unit!==state.vehicle){if(old){disposeOverlay(old);overlayMeshes.delete(l.id);}dirty=true;return;}
 const asset=assets.get(l.asset);if(!asset)return;const target=model,tex=await texture(asset.image,true);let projection=null;
 if(l.projection)projection=prepared||await projectModel(target,structuredClone(l));
 if(overlayRevision.get(l.id)!==revision||!state.layers.includes(l)||l.hidden||l.unit!==state.vehicle||model!==target)return;
 tex.wrapS=tex.wrapT=T.ClampToEdgeWrapping;let root;
 if(l.uvReuse){root=reuseOverlay(target,l,tex);}
 else if(projection){projection.parameters=parameters(l);if(!projection.surfaces.length)toast('贴花框未覆盖当前侧面，请移动回表面或重新定位');l.surfaces=projection.surfaces;root=projectionOverlay(target,l,projection,tex);}
 else{root=new T.Group();root.userData.layer=l.id;for(const selected of surfacesOf(l)){const g=surfaceGeometry(model.children[selected.part],selected.triangles,l.frame,l.placement,asset.width/asset.height),m=overlayMaterial(tex,l.tint?l.color:'#ffffff',l.opacity),mesh=new T.Mesh(g,m);mesh.userData={part:selected.part,layer:l.id};mesh.visible=model.children[selected.part].visible;root.add(mesh);}}
 if(old)disposeOverlay(old);scene.add(root);overlayMeshes.set(l.id,root);updateOverlayOrders();dirty=true;
}
const queuedOverlays=new Map();let overlayFrame=0;
function scheduleOverlay(layer){if(layer.uvReuse&&overlayMeshes.has(layer.id)){reuseAppearance(overlayMeshes.get(layer.id),layer);dirty=true;return;}queuedOverlays.set(layer.id,layer);if(!overlayFrame)overlayFrame=requestAnimationFrame(()=>{overlayFrame=0;for(const l of queuedOverlays.values())updateOverlay(l);queuedOverlays.clear();});}
async function syncOverlays(){syncFaceColors();for(const [id,group] of overlayMeshes){overlayRevision.set(id,(overlayRevision.get(id)||0)+1);disposeOverlay(group);}overlayMeshes.clear();for(const l of state.layers)if(l.unit===state.vehicle)await updateOverlay(l);renderLayers();}
function visibleLayers(){return state.layers.filter(l=>l.unit===state.vehicle&&($('#layerFilter').value!=='surface'||!selection||overlaps(l,selection)));}
function layerCoveredByUpper(l){const index=state.layers.indexOf(l);if(index<0)return false;const mine=new Map((l.surfaces||[]).map(s=>[s.part,new Set(s.triangles||[])]));return state.layers.slice(index+1).some(u=>{if(u.unit!==l.unit||u.hidden||(u.opacity??1)<.99||(u.role||'')!=='photo')return false;for(const s of u.surfaces||[]){const a=mine.get(s.part);if(!a||!a.size)continue;let hit=0;for(const t of s.triangles||[])if(a.has(t))hit++;if(hit>=a.size*.9)return true;}return false;});}
function renderLayers(){
 const all=state.layers.filter(l=>l.unit===state.vehicle),visible=visibleLayers(),list=visible.slice().reverse();$('#layersCount').textContent=list.length+' 张';$('#layersContext').textContent=vehicles[state.vehicle].name+' · '+($('#layerFilter').value==='surface'&&selection?'选中表面':'当前模型');
 $('#layers').innerHTML=list.map((l,index)=>{const a=assets.get(l.asset),surfaces=surfacesOf(l);return `<div class="layer-item ${l.id===activeLayer?'active':''}" data-layer="${l.id}"><div class="layer-main"><input class="layer-check" type="checkbox" ${l.hidden?'':'checked'} data-visible="${l.id}" aria-label="显示图层"><img src="${(layerRole(l)==='photo'?a?.photoThumbnail:a?.thumbnail)||''}" alt=""><div class="layer-title"><b>${esc(l.name)}</b>${(l.surfaces||[]).some(s=>/_destroyed/.test(model?.children?.[s.part]?.name||''))?'<em class="damage-warn" title="该图层画在损坏状态部件上；完整车体在游戏中可能把它遮住。若成品看不到，请改选完整部件，或把上层不透明图层移走">损坏状态部件</em>':''}${layerCoveredByUpper(l)?'<em class="cover-warn" title="该图层被上方不透明照片覆盖；点 ⇈ 置顶">被上层覆盖</em>':''}<small>${l.projection?(l.projection.type==='cylinder'?'圆杆环绕':'连续投射')+' · 跨 '+surfaces.length+' 个部件区域':surfaces.length>1?'跨 '+surfaces.length+' 个材质区域':'旧版区域贴花'}${selection&&overlaps(l,selection)?' · 与选面重叠':''}</small></div></div><div class="layer-order"><span>第 ${list.length-index} 层</span><button data-order="top" title="置顶" ${index===0?'disabled':''}>⇈</button><button data-order="up" title="上移一层" ${index===0?'disabled':''}>↑</button><button data-order="down" title="下移一层" ${index===list.length-1?'disabled':''}>↓</button><button data-order="bottom" title="置底" ${index===list.length-1?'disabled':''}>⇊</button></div></div>`;}).join('')||'<p class="empty-layers">此模型的当前范围还没有图层。可在同一选区连续添加多张图片。</p>';
 const l=layer();$('#activeLayerLabel').textContent=l?l.name:'未选择图层';$('#deleteLayer').disabled=$('#duplicateLayer').disabled=!l;
 if(l&&layerRole(l)==='pattern'){$('#decalColor').value=l.color;$('#tintImage').checked=l.tint;}syncPatternControls();syncPlacement();syncProjectionControls();syncInspectorV7();
}
function reorderLayer(id,direction){const list=visibleLayers(),from=list.findIndex(l=>l.id===id);if(from<0)return;const to=direction==='top'?list.length-1:direction==='bottom'?0:Math.max(0,Math.min(list.length-1,from+(direction==='up'?1:-1)));if(to===from)return;checkpoint();const indices=state.layers.map((l,i)=>list.includes(l)?i:-1).filter(i=>i>=0),[moved]=list.splice(from,1);list.splice(to,0,moved);indices.forEach((index,i)=>state.layers[index]=list[i]);updateOverlayOrders();renderLayers();changed();}
$('#layerFilter').onchange=renderLayers;
$('#layers').onclick=async e=>{
 const row=e.target.closest('[data-layer]');if(!row)return;const l=state.layers.find(l=>l.id===row.dataset.layer);if(!l||l.unit!==state.vehicle)return;
 const order=e.target.closest('[data-order]');if(order){reorderLayer(l.id,order.dataset.order);return;}
 const checkbox=e.target.closest('[data-visible]');if(checkbox){checkpoint();l.hidden=!checkbox.checked;await updateOverlay(l);changed();return;}
 if(areaImage(l)){await selectAreaImage(l);return;}activeLayer=l.id;window.layoutV7.show('decal');if(l.projection){if(l.projection.groups?.length===1)selectGroup(l.projection.groups[0]);selection=null;clearHighlight();renderLayers();return;}selection={unit:l.unit,surfaces:structuredClone(surfacesOf(l)),frame:structuredClone(l.frame),selectionVersion:l.selectionVersion};await highlightSelection();renderLayers();
};
const placementFields=[['decalScale','scaleNumber','scale',100,.01,20],['decalX','xNumber','x',100,-10,10],['decalY','yNumber','y',100,-10,10],['decalAngle','angleNumber','angle',1,-3600,3600],['decalOpacity','opacityNumber','opacity',100,0,1]];
function fieldValue(l,key){return key==='opacity'?l.opacity:l.placement[key];}
function syncPlacement(){const l=layer();for(const [slider,number,key,mult] of placementFields){const a=$('#'+slider),b=$('#'+number);a.disabled=b.disabled=!l;if(l){const value=fieldValue(l,key)*mult;a.value=value;b.value=Number(value.toFixed(4));}}}
function setPlacement(key,value){const l=layer();if(!l)return;if(key==='opacity')l.opacity=value;else if(l.projection&&['x','y'].includes(key)){if(!moveOffset(model,l,key,value)){toast('已到当前侧面边缘；跨折角需主动允许');syncPlacement();return;}}else l.placement[key]=value;scheduleOverlay(l);changed();}
for(const [sliderId,numberId,key,mult,low,high] of placementFields){
 const slider=$('#'+sliderId),number=$('#'+numberId);
 slider.onpointerdown=()=>{if(layer())checkpoint();};slider.oninput=()=>{if(!layer())return;const value=Number(slider.value)/mult;number.value=Number((value*mult).toFixed(4));setPlacement(key,value);};
 let recorded=false;number.onfocus=()=>recorded=false;
 number.oninput=()=>{if(!layer()||number.value.trim()==='')return;const value=Number(number.value)/mult;if(!Number.isFinite(value)||value<low||value>high)return;if(!recorded){checkpoint();recorded=true;}slider.value=value*mult;setPlacement(key,value);};
 number.onchange=()=>{const value=Number(number.value)/mult;if(number.value.trim()===''||!Number.isFinite(value)||value<low||value>high){toast('请输入 '+low*mult+' 到 '+high*mult+' 之间的数值');syncPlacement();}};
 number.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();number.blur();}};
}
function syncPatternControls(){const l=layer(),photo=l&&layerRole(l)==='photo';$('#patternColorPanel').hidden=photo||(!l&&libraryRole==='photo');$('#photoColorInfo').hidden=!photo;$('#restorePhoto').hidden=!(photo&&l.tint);if(l&&layerRole(l)==='pattern'){$('#decalColor').value=l.color;$('#tintImage').checked=l.tint;}}
$('#restorePhoto').onclick=()=>{const l=layer();if(!l||layerRole(l)!=='photo')return;checkpoint();l.tint=false;l.color='#ffffff';delete l.legacyTinted;updateOverlay(l);renderLayers();changed();};
$('#decalColor').onpointerdown=()=>{if(layer()&&layerRole(layer())==='pattern')checkpoint();};
$('#decalColor').oninput=e=>{const l=layer();if(!l||layerRole(l)!=='pattern')return;l.color=e.target.value;l.tint=true;$('#tintImage').checked=true;overlayMeshes.get(l.id)?.children.forEach(m=>m.material.color.set(l.color));changed();};
$('#tintImage').onchange=e=>{const l=layer();if(!l||layerRole(l)!=='pattern')return;checkpoint();l.tint=e.target.checked;l.color=$('#decalColor').value;updateOverlay(l);changed();};
for(const [id,fit] of [['fitContain','contain'],['fitCover','cover']])$('#'+id).onclick=()=>{const l=layer();if(!l)return;checkpoint();l.placement.fit=fit;l.placement.scale=1;if(l.projection){const a=assets.get(l.asset),ratio=a.width/a.height,p=l.projection,w=fit==='cover'?Math.max(p.width,p.height*ratio):Math.min(p.width,p.height*ratio);p.width=w;p.height=w/ratio;if(p.surfaceFrame)updatePose(l);syncProjectionControls();}updateOverlay(l);syncPlacement();};
$('#centerLayer').onclick=()=>{const l=layer();if(!l)return;checkpoint();if(l.projection){moveOffset(model,l,'x',0);moveOffset(model,l,'y',0);}else l.placement.x=l.placement.y=0;l.placement.angle=0;updateOverlay(l);syncPlacement();};
function deleteLayer(id=activeLayer){const l=state.layers.find(l=>l.id===id);if(!l||l.unit!==state.vehicle)return;checkpoint();overlayRevision.set(id,(overlayRevision.get(id)||0)+1);queuedOverlays.delete(id);const group=overlayMeshes.get(id);if(group){disposeOverlay(group);overlayMeshes.delete(id);}state.layers=state.layers.filter(x=>x.id!==id);if(activeLayer===id)activeLayer=null;updateOverlayOrders();renderLayers();changed();renderer.render(scene,camera);}
$('#deleteLayer').onclick=()=>deleteLayer();
$('#duplicateLayer').onclick=async()=>{const l=layer();if(!l)return;checkpoint();const copy=structuredClone(l);copy.id=crypto.randomUUID();copy.name+=' 副本';state.layers.push(copy);activeLayer=copy.id;await updateOverlay(copy);renderLayers();changed();};
function syncFaceColors(){for(const group of faceColorMeshes.values())disposeOverlay(group);faceColorMeshes.clear();state.surfaceColors.forEach((patch,index)=>{if(patch.unit!==state.vehicle||patch.hidden)return;const group=colorOverlay(model,patch,white);group.children.forEach(m=>m.renderOrder=1+index);scene.add(group);faceColorMeshes.set(patch.id,group);});dirty=true;syncFaceColorControls();}
function syncFaceColorControls(){if(!$('#faceColor'))return;const enabled=Boolean(selection?.surfaces?.length);$('#faceColor').disabled=$('#faceHex').disabled=$('#clearFaceColor').disabled=!enabled;const patch=enabled?state.surfaceColors.filter(p=>p.unit===state.vehicle&&coveredBy(selection,p)).at(-1):null;if(patch){$('#faceColor').value=patch.color;$('#faceHex').value=patch.color.toUpperCase();}$('#faceColorHint').textContent=enabled?'只修改蓝色选区；照片与图案始终叠在这层底色上。':'先选择一块表面，再设置局部底色。';}
async function paintFaceColor(hex){if(!/^#[0-9a-f]{6}$/i.test(hex)||!selection)return;paintPending={hex,selection:structuredClone(selection),key:selectionKey(selection)};if(paintBusy)return;paintBusy=true;
 try{while(paintPending){const item=paintPending;paintPending=null;if(item.selection.unit!==state.vehicle)continue;let patch=colorStroke?.key===item.key?state.surfaceColors.find(p=>p.id===colorStroke.id):null;
  if(!patch){const checked=await describeSelection(state.vehicle,model,item.selection.surfaces);if(!checked.surfaces.length){toast('选区为空，请重新选择');continue;}checkpoint();state.surfaceColors=state.surfaceColors.filter(p=>!coveredBy(p,item.selection));patch={id:crypto.randomUUID(),...item.selection,selectionVersion:5,color:item.hex,hidden:false};state.surfaceColors.push(patch);colorStroke={key:item.key,id:patch.id};}
  patch.color=item.hex;$('#faceHex').value=item.hex.toUpperCase();syncFaceColors();changed();
 }}finally{paintBusy=false;}}
$('#faceColor').onpointerdown=()=>colorStroke=null;$('#faceColor').oninput=e=>paintFaceColor(e.target.value);$('#faceHex').onchange=e=>{colorStroke=null;paintFaceColor(e.target.value);};
$('#clearFaceColor').onclick=async()=>{if(!selection)return;checkpoint();const result=[];for(const patch of state.surfaceColors){if(patch.unit!==state.vehicle||!overlaps(patch,selection)){result.push(patch);continue;}const remaining=subtractSurfaces(patch,selection);if(remaining.length){const components=await splitSurfaces(state.vehicle,model,remaining);components.forEach(surfaces=>result.push({...patch,id:crypto.randomUUID(),surfaces}));}}state.surfaceColors=result;colorStroke=null;syncFaceColors();changed();};
$('#undo').onclick=undo;$('#redo').onclick=redo;document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;if(e.ctrlKey&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}if(e.key==='Escape'){pendingAssetReplacement=false;$('#assetMenu').hidden=true;placingDecal=reanchoring=false;placementAnchor=null;if(!menu.hidden)hideMenu();else $('#clearSelection').click();syncProjectionControls();}if(e.key==='Delete'&&layer())$('#deleteLayer').click();});
function preview(includeSelection=false){if(highlight)highlight.visible=includeSelection;renderer.render(scene,camera);let data=renderer.domElement.toDataURL('image/png');if(highlight)highlight.visible=true;dirty=true;return data;}
$('#screenshot').onclick=()=>{let a=document.createElement('a');a.href=preview();a.download=state.name+'_3D预览.png';a.click();};
let activeExportToken=null;
function busy(text,on=true){$('#busyText').textContent=text;$('#busy').hidden=!on;if(!on){$('#exportProgress').hidden=true;$('#exportDetail').hidden=true;$('#exportBackup').hidden=true;$('#stopExport').hidden=true;}}
function result(title,message,path){$('#resultTitle').textContent=title;$('#resultText').textContent=message;$('#resultPath').textContent=path;$('#resultDialog').showModal();}
$('#projectName').onchange=e=>{checkpoint();state.name=e.target.value.trim()||'我的载具涂装';};$('#saveProject').onclick=async()=>{busy('正在保存工程与图片原稿…');try{state.name=$('#projectName').value.trim()||'我的载具涂装';await settleProjectionEdits();let r=await api('/api/save',{state,preview:preview()});$('#projectStatus').textContent='已保存 · '+r.version;result('工程已保存','图片、独立表面、位置和颜色均已保留。',r.path);}catch(e){toast(e.message);}finally{busy('',false);}};
$('#openProject').onclick=()=>$('#projectFile').click();$('#projectFile').onchange=async e=>{let f=e.target.files[0];if(!f)return;busy('正在打开工程…');try{const payload=/\.hd2project$/i.test(f.name)?{archive:await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(f);})}:{state:JSON.parse(await f.text())};const r=await api('/api/open',payload);checkpoint();state=normalizeState(r.state);await refreshLibrary();$('#projectName').value=state.name;await chooseModel(state.vehicle,false);toast(r.message||'工程已打开');}catch(e){toast(e.message);}finally{e.target.value='';busy('',false);}};
let exportRequest=null,exportPlanSerial=0,exportDialogSerial=0,exportPlanReady=false,exportSaving=false,exportDefaultDirectory='',exportFolderRequest=null;
const exportCategories=['地面载具','机甲','飞行载具','任务设备'];
function exportSaveError(message=''){$('#exportSaveError').textContent=message;$('#exportSaveError').hidden=!message;}
function exportFilenameError(value){
 let name=value.trim();if(!name)return '请填写压缩包名称';if(name.toLowerCase().endsWith('.zip'))name=name.slice(0,-4);
 if(!name||name==='.'||name==='..'||/[. ]$/.test(name))return '压缩包名称不能为空，也不能以句点或空格结尾';
 if(/[<>:"/\\|?*\x00-\x1f]/.test(name))return '压缩包名称不能包含 \\ / : * ? " < > | 等字符';
 if(/^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$/i.test(name.split('.')[0]))return '这个名称是系统保留名称，请换一个名称';
 return name.length>180?'压缩包名称过长，请缩短后再导出':'';
}
function updateExportSaveControls(){
 const name=$('#exportZipName').value.trim(),directory=$('#exportDirectory').value.trim();
 const filename=name?(name.toLowerCase().endsWith('.zip')?name.slice(0,-4)+'.zip':name+'.zip'):'（压缩包名称）.zip';
 $('#exportDestinationPreview').textContent=directory?directory.replace(/[\\/]$/,'')+(directory.includes('\\')?'\\':'/')+filename:'请选择保存文件夹';
 $('#confirmExport').disabled=!exportPlanReady||exportSaving||!!exportFolderRequest||!directory||!!exportFilenameError(name);
 $('#exportZipName').disabled=$('#exportDirectory').disabled=$('#exportScope').disabled=exportSaving;
 $('#browseExportDirectory').disabled=exportSaving||!!exportFolderRequest;$('#resetExportDirectory').disabled=exportSaving||!exportDefaultDirectory;
}
for(const id of ['exportZipName','exportDirectory'])$('#'+id).oninput=()=>{exportSaveError();updateExportSaveControls();};
$('#exportZipName').onblur=()=>{if($('#exportZipName').value.trim())exportSaveError(exportFilenameError($('#exportZipName').value));};
$('#resetExportDirectory').onclick=()=>{$('#exportDirectory').value=exportDefaultDirectory;exportSaveError();updateExportSaveControls();};
$('#browseExportDirectory').onclick=()=>{
 const host=window.chrome?.webview;
 if(!host?.postMessage){exportSaveError('可直接填写保存文件夹路径，或使用 EXE 启动后点击“浏览”。');$('#exportDirectory').focus();return;}
 exportFolderRequest=crypto.randomUUID();updateExportSaveControls();
 try{host.postMessage({type:'choose-export-directory',requestId:exportFolderRequest,directory:$('#exportDirectory').value});}
 catch(error){exportFolderRequest=null;exportSaveError(error.message);updateExportSaveControls();}
};
window.chrome?.webview?.addEventListener('message',event=>{
 const data=event.data;if(data?.type!=='export-directory-selected'||data.requestId!==exportFolderRequest)return;
 exportFolderRequest=null;
 if(data.error)exportSaveError(data.error);
 else if(!data.cancelled&&typeof data.directory==='string'){$('#exportDirectory').value=data.directory;exportSaveError();}
 updateExportSaveControls();
});
function selectedExportScope(){
 const value=$('#exportScope').value;
 return value==='vehicle'?{kind:'vehicle',family:exportRequest?.family||state.family}:value==='all'?{kind:'all'}:{kind:'category',category:value};
}
async function updateExportPlan(){
 const request=++exportPlanSerial;exportPlanReady=false;updateExportSaveControls();$('#exportContents').textContent='正在统计改动…';
 try{
  const scope=selectedExportScope(),plan=await api('/api/export-plan',{state,scope});if(request!==exportPlanSerial)return;
  $('#exportContents').innerHTML=plan.units.map(v=>`<li>${esc(v.name)}${v.role==='主体'?'':' <small>配套部件</small>'}</li>`).join('');
  $('#exportSummary').textContent=plan.has_changes?'范围内有 '+plan.changed_groups.length+' 组配色、'+plan.surface_colors+' 处局部底色、'+plan.layers+' 张可见图案；只打包这些载具及配套部件的改动。':'这个范围还没有改动，请先修改颜色或添加图片。';
  exportPlanReady=!!plan.has_changes;updateExportSaveControls();
 }catch(error){if(request===exportPlanSerial){$('#exportContents').textContent=error.message;exportPlanReady=false;updateExportSaveControls();}}
}
async function openExport(scope={kind:'vehicle',family:state.family}){
 const serial=++exportDialogSerial;exportRequest=scope;exportPlanReady=false;exportSaving=false;exportFolderRequest=null;exportDefaultDirectory='';
 $('#exportZipName').value='';$('#exportDirectory').value='';exportSaveError();updateExportSaveControls();
 if(scope.kind==='vehicle'&&scope.family!==state.family)await chooseModel(scope.family);
 const family=vehicles[scope.family||state.family];
 $('#exportScope').innerHTML=`<option value="vehicle">${esc(family.mechAppearance?'四型机甲（各型独立外观）':family.name)}及其部件</option>`+exportCategories.map(c=>`<option value="${c}">全部${c}</option>`).join('')+'<option value="all">全部载具类型</option>';
 $('#exportScope').value=scope.kind==='category'?scope.category:scope.kind==='all'?'all':'vehicle';
 $('#exportScope').hidden=!exportAdvancedV7;document.querySelector('label[for=exportScope]').hidden=!exportAdvancedV7;$('#exportDialog h2').textContent=exportAdvancedV7?'高级导出':'导出当前载具';$('#exportDialog').showModal();
 const settings=fetch('/api/export-settings').then(async response=>{if(!response.ok)throw Error('无法读取默认导出文件夹');const value=await response.json();if(serial!==exportDialogSerial)return;exportDefaultDirectory=value.defaultDirectory;$('#exportDirectory').value=exportDefaultDirectory;updateExportSaveControls();}).catch(error=>{if(serial===exportDialogSerial)exportSaveError(error.message);});
 await Promise.all([updateExportPlan(),settings]);
}
$('#exportMod').onclick=()=>{exportAdvancedV7=false;openExport();};$('#exportAdvanced').onclick=()=>{exportAdvancedV7=true;openExport();};$('#exportFamily').onclick=()=>openExport();
$('#exportCategory').onclick=()=>openExport({kind:'category',category:category==='全部'?vehicles[state.family].categoryV3:category});
$('#exportScope').onchange=updateExportPlan;$('#cancelExport').onclick=()=>{++exportDialogSerial;$('#exportDialog').close();};$('#exportDialog').addEventListener('cancel',()=>++exportDialogSerial);
$('#confirmExport').onclick=async()=>{
 if($('#confirmExport').disabled)return;
 const scope=selectedExportScope(),serial=exportDialogSerial;exportSaving=true;exportSaveError();updateExportSaveControls();
 const exportToken=crypto.randomUUID();let started=false,backupPath='';
 try{
  const destination=await api('/api/export-destination',{name:$('#exportZipName').value,directory:$('#exportDirectory').value});
  if(serial!==exportDialogSerial||!$('#exportDialog').open)return;
  $('#exportDialog').close();started=true;busy('正在保存导出前工程备份…');
  await autosaveWorkbench();
  const plan=await api('/api/export-plan',{state,scope}),included=new Set(plan.units.map(v=>v.id));
  if(!included.has(state.vehicle))await chooseModel(plan.families[0]);
  state.name=$('#projectName').value.trim()||'我的载具涂装';
  await settleProjectionEdits();
  activeExportToken=exportToken;$('#stopExport').hidden=false;$('#stopExport').disabled=false;
  const job=await api('/api/export',{state,scope,preview:preview(),exportToken,destination});backupPath=job.backupPath||'';
  $('#exportBackup').textContent='工程已另存备份，可取消后继续编辑。';$('#exportBackup').hidden=false;
  const r=job.job?await waitForExport(exportToken):job;
  const timing=Number.isFinite(r.seconds)?' · 耗时 '+r.seconds+' 秒':'';const reused=r.cacheHits?' · 复用 '+r.cacheHits+' 张未改动贴图':'';
  result('MOD 已导出',r.scope+' · 已写入 '+r.units+' 个有改动的本体/部件'+timing+reused+'。压缩包内含预览，可直接导入管理器。',r.zip);
 }catch(error){
  if(!started)exportSaveError(error.message);
  else if(error.code==='destination'||error.code==='exists'){$('#exportDialog').showModal();exportSaveError(error.message);}
  else if(error.code==='cancelled'){toast('已取消导出，当前制作内容和工程备份均已保留。');$('#projectStatus').textContent='导出已取消 · 制作内容已保留';}
  else{result('导出未完成',error.message,backupPath||'当前工作台已自动保存，可继续编辑后重试。');console.error(error);}
 }finally{activeExportToken=null;if(started)busy('',false);exportSaving=false;updateExportSaveControls();}
};
async function waitForExport(token){
 let failures=0;
 while(true){
  let p;
  try{const response=await fetch('/api/export-progress?exportToken='+encodeURIComponent(token));if(!response.ok)throw Error('无法读取导出状态');p=await response.json();failures=0;}
  catch(error){if(++failures>=8)throw Error('导出连接已中断；工程已备份，重新打开软件后可恢复。');await new Promise(r=>setTimeout(r,500));continue;}
  busy(p.message+' · 已用 '+Math.round(p.seconds||0)+' 秒');
  $('#exportDetail').hidden=false;$('#exportDetail').textContent=p.total?'本阶段 '+p.completed+' / '+p.total+' 项 · 本阶段已用 '+Math.round(p.stageSeconds||0)+' 秒':'本阶段已用 '+Math.round(p.stageSeconds||0)+' 秒';
  const bar=$('#exportProgress');bar.hidden=false;if(p.total){bar.max=p.total;bar.value=p.completed;}else bar.removeAttribute('value');
  if(p.result){if(p.result.ok)return p.result.result;const error=Error(p.result.error);error.code=p.result.code;throw error;}
  await new Promise(r=>setTimeout(r,350));
 }
}
$('#stopExport').onclick=async()=>{
 if(!activeExportToken)return;$('#stopExport').disabled=true;busy('正在取消导出，工程备份将保留…');
 try{await api('/api/export-cancel',{token:activeExportToken});}catch(error){toast(error.message);$('#stopExport').disabled=false;}
};
$('#helpBtn').onclick=()=>$('#helpDialog').showModal();$('#closeHelp').onclick=()=>$('#helpDialog').close();$('#closeResult').onclick=()=>$('#resultDialog').close();setInterval(()=>fetch('/api/health').catch(()=>{}),30000);
window.studio={getInteraction:()=>({placingDecal,reanchoring,activeLayer,editMode}),selectAt,pickDecal,getState:()=>JSON.parse(JSON.stringify(state)),getCacheStats:()=>({models:models.size,textures:textures.size,gpu:renderer.info.memory}),projectAt:async(part,triangle)=>{const mesh=model.children[part],p=mesh.geometry.attributes.position,point=new T.Vector3();for(let k=0;k<3;k++)point.add(new T.Vector3().fromBufferAttribute(p,triangle*3+k));point.multiplyScalar(1/3);placingDecal=true;return setProjectionAnchor({object:mesh,point,faceIndex:triangle});},setEditMode,requestClose,saveWorkbenchLayout,settleProjectionEdits,getSession:()=>structuredClone(recoveryData()),restoreSession,getModel:()=>model,renderer,camera,controls,selectVehicle:chooseModel,selectGroup,getSelection:()=>selection,getAssets:()=>[...assets.values()],getOverlays:()=>[...overlayMeshes.values()].flatMap(g=>g.children),getVisibleLayers:()=>visibleLayers().map(l=>l.id),getFaceColorOverlays:()=>[...faceColorMeshes.values()].flatMap(g=>g.children),paintFaceColor,deleteLayer,selectPatch,reorderLayer,getSharedSelection:()=>JSON.parse(JSON.stringify(sharedSelection)),getHighlight:()=>highlight,restore:async incoming=>{await refreshLibrary();await restore(JSON.stringify(incoming));},preview};
function setEditMode(mode){hideMenu();pendingAssetReplacement=false;placingDecal=reanchoring=false;editMode=mode==='area'?'area':'decal';document.body.dataset.editMode=editMode;$('#areaToolbar').hidden=editMode!=='area';$('#repositionDecal').hidden=editMode==='area';$$('[data-edit-mode]').forEach(b=>b.classList.toggle('active',b.dataset.editMode===editMode));if(editMode==='decal')clearHighlight();else if(selection)highlightSelection();renderAssets();syncProjectionControls();dirty=true;}
$$('[data-edit-mode]').forEach(b=>b.onclick=()=>{setEditMode(b.dataset.editMode);layoutChanged();});

function partGroup(part){const m=model.children[part];return m.userData.group||(vehicles[state.vehicle].isFRV&&m.userData.slot==='m_lights'?'P24':null);}
function targetSpec(mask=null){if(mask){const ids=[...new Set(mask.map(s=>partGroup(s.part)).filter(Boolean))];return{scopeKind:ids.length?'custom':'model',groups:ids.length?ids:null};}const kind=$('#projectionScope').value;if(kind==='model')return{scopeKind:'model',groups:null};if(kind==='custom'){const ids=$$('#projectionTargets input:checked').map(i=>i.value);return{scopeKind:'custom',groups:ids.length?ids:[selectedGroup]};}return{scopeKind:'group',groups:[selectedGroup]};}
function renderProjectionTargets(){if(!$('#projectionTargets'))return;const l=layer(),p=l?.projection,mode=p?.scopeKind||$('#projectionScope').value||'group';$('#projectionScope').value=mode;const selected=p?.groups|| (mode==='model'?vehicles[state.vehicle].groups:mode==='group'?[selectedGroup]:$$('#projectionTargets input:checked').map(i=>i.value));$('#projectionScope option[value=group]').textContent='当前部件：'+groupLabel(p?.scopeKind==='group'&&p.groups?.length===1?p.groups[0]:selectedGroup);$('#projectionTargets').innerHTML=vehicles[state.vehicle].groups.map(id=>`<label><input type="checkbox" value="${id}" ${selected.includes(id)?'checked':''}>${esc(groupLabel(id))}</label>`).join('');}
async function changeProjectionTargets(spec){const l=layer();if(l?.projection){checkpoint();Object.assign(l.projection,spec);await updateOverlay(l);renderLayers();changed();}else renderProjectionTargets();}
$('#projectionScope').onchange=()=>changeProjectionTargets(targetSpec());
$('#projectionTargets').onchange=e=>{const ids=$$('#projectionTargets input:checked').map(i=>i.value);if(!ids.length){e.target.checked=true;toast('至少保留一个作用部件');return;}$('#projectionScope').value='custom';changeProjectionTargets({scopeKind:'custom',groups:ids});};

function syncProjectionControls(){
 const l=layer(),p=l?.projection;renderProjectionTargets();$('#replaceAsset').disabled=!l||!selectedAsset;$('#convertDecal').hidden=!l||!!p||!!l?.uvReuse;for(const id of ['projectionWidth','projectionHeight','projectionDepth','projectionRadius'])$('#'+id).disabled=!p;
 if(!reanchoring)$('#projectionType').value=p?.type||$('#projectionType').value;
 const round=$('#projectionType').value==='cylinder';$('#cylinderAxisRow').hidden=$('#cylinderRadiusRow').hidden=!round;
 if(p){for(const [id,key] of [['projectionWidth','width'],['projectionHeight','height'],['projectionDepth','depth'],['projectionRadius','radius']])$('#'+id).value=Number(((p[key]||.08)*20).toFixed(4));}
 $('#followSurface').checked=p?.followSurface??true;$('#allowCrossSide').checked=p?.surface?.mode==='free';$('#allowCrossSide').disabled=round;
 for(const key of ['pitch','yaw']){const input=$('#projection'+(key==='pitch'?'Pitch':'Yaw'));input.disabled=!p||round;input.value=p?.pose?.[key]||0;}
 $('#shapeDecal').disabled=!p||round;$('#resetDecalShape').disabled=!p;$('#shapeDecal').classList.toggle('active',shapeMode);
 if($('#projectionDepthRange'))$('#projectionDepthRange').value=p?p.depth*20:$('#projectionDepth').value;$('#projectionStatus').textContent=p?(round?'按弧长环绕圆杆；此模式允许绕向其他侧面。':'中心柄沿真实表面移动；俯仰、转向和面内旋转可分别调整。'):'选择素材，再点击“放置一张贴花”。';
 $('#applyAsset').textContent=editMode==='decal'?(placingDecal?'放置中 · Esc 取消':'放置一张贴花'):'投射到选中区域';
 $('#decalModeHint').textContent=reanchoring?'重新定位：在目标部件点击一次；Esc 取消。':placingDecal?'放置模式：点击目标部件，放置一张后自动退出。':'单击选中已有贴花 · 拖动查看 · 中心柄沿面移动';
 if(editMode==='area')$('#decalModeHint').textContent=areaImage()?'拖动中心柄移动图片 · Alt + 单击选择图片下方的面':'单击选择表面，再从素材库添加图片';renderer.domElement.style.cursor=placingDecal||reanchoring?'crosshair':'';
}
async function setProjectionAnchor(h){
 if(!placingDecal&&!reanchoring)return false;
 if(h.object.userData.decalDisabledReason){toast('透明功能件保留原材质');return false;}
 const type=$('#projectionType').value,current=layer(),spec=reanchoring&&current?.projection?current.projection:targetSpec();
 const group=partGroup(h.object.userData.part);if(spec.groups&&!spec.groups.includes(group)){toast('请点击当前作用部件，或先选择其他部件');return false;}
 placementAnchor=anchorFrame(model,h,camera,type,$('#cylinderAxis').value);
 if(reanchoring&&current){checkpoint();delete current.uvReuse;current.frame=structuredClone(placementAnchor.frame);current.projection={...(current.projection||targetSpec()),type,radius:placementAnchor.radius,distance:0,depth:type==='cylinder'?placementAnchor.radius*.8:Number($('#projectionDepth').value)/20,surface:sideLock(placementAnchor.frame),surfaceFrame:structuredClone(placementAnchor.frame),followSurface:$('#followSurface').checked,pose:{pitch:0,yaw:0}};if($('#allowCrossSide').checked)current.projection.surface.mode='free';if(!current.projection.width){const a=assets.get(current.asset);current.projection.width=1;current.projection.height=1/(a.width/a.height);}current.placement.x=current.placement.y=0;delete current.projection.mask;reanchoring=placingDecal=false;await updateOverlay(current);renderLayers();changed();return true;}
 placingDecal=false;syncProjectionControls();const placed=await addProjectionAtAnchor();if(!placed){placingDecal=true;syncProjectionControls();}return placed;
}
async function addProjectionAtAnchor(mask=null){
 if(!placementAnchor||!selectedAsset)return;
 const asset=assets.get(selectedAsset),role=libraryRole,type=mask?'planar':$('#projectionType').value,box=placementAnchor.frame.bounds,areaWidth=Math.max(.005,box[2]-box[0]),areaHeight=Math.max(.005,box[3]-box[1]),width=mask?Math.max(.005,Math.min(areaWidth,areaHeight*asset.width/asset.height)):Math.max(.005,Number($('#projectionWidth').value)/20),height=mask?width*asset.height/asset.width:$('#lockAspect').checked?width/(asset.width/asset.height):Math.max(.005,Number($('#projectionHeight').value)/20),tint=role==='pattern'&&(asset.kind==='preset'||asset.kind==='text'||$('#tintImage').checked),surface=sideLock(placementAnchor.frame);if(mask||$('#allowCrossSide').checked)surface.mode='free';
 const l={id:crypto.randomUUID(),name:asset.name,asset:asset.id,unit:state.vehicle,frame:structuredClone(placementAnchor.frame),surfaces:[],selectionVersion:5,projection:{...targetSpec(mask),type,width,height,depth:mask?20:type==='cylinder'?placementAnchor.radius*.8:Math.max(.0001,Number($('#projectionDepth').value)/20),radius:placementAnchor.radius,distance:placementAnchor.distance||0,corners:[[0,0],[1,0],[1,1],[0,1]],cutoff:.015,surface,surfaceFrame:structuredClone(placementAnchor.frame),followSurface:mask?false:$('#followSurface').checked,pose:{pitch:0,yaw:0},...(mask?{mask,areaAttached:true}:{})},role,tint,color:tint?$('#decalColor').value:'#ffffff',opacity:1,hidden:false,placement:{fit:'contain',scale:1,x:0,y:0,angle:0}};
 const revision=serial,prepared=await projectModel(model,l);if(revision!==serial)return;
 if(!prepared.surfaces.length){toast('当前位置没有可覆盖的载具，请重新点击定位');return false;}
 l.surfaces=prepared.surfaces;checkpoint();state.layers.push(l);activeLayer=l.id;selection=mask?areaSelection(l):null;placingDecal=reanchoring=false;placementAnchor=null;clearHighlight();await updateOverlay(l,prepared);if(mask)await highlightSelection();renderLayers();window.layoutV7.show('decal');changed();return true;
}
const alphaCache=new WeakMap();
function alphaAt(map,uv){if(!map?.image)return 1;const image=map.image;let data=alphaCache.get(image);if(!data){const c=document.createElement('canvas');c.width=image.width;c.height=image.height;const ctx=c.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0);data={pixels:ctx.getImageData(0,0,c.width,c.height).data,width:c.width,height:c.height};alphaCache.set(image,data);}const x=Math.max(0,Math.min(data.width-1,Math.floor(uv[0]*data.width))),y=Math.max(0,Math.min(data.height-1,Math.floor((1-uv[1])*data.height)));return data.pixels[(y*data.width+x)*4+3]/255;}
function pickDecal(h){
 for(const l of state.layers.slice().reverse()){
  if(l.unit!==state.vehicle||l.hidden||l.opacity<.02||!l.surfaces.some(s=>s.part===h.object.userData.part&&s.triangles.includes(h.faceIndex)))continue;
  const overlay=overlayMeshes.get(l.id);if(!overlay)continue;const map=overlay.children[0]?.material.map;
  if(l.projection){if(hitNormal(h,true).dot(camera.position.clone().sub(h.point))<=0)continue;const result=overlay.userData.result;if(!result)continue;const uv=visiblePoint(h.point.toArray(),hitNormal(h).toArray(),parameters(l),result);if(uv&&alphaAt(map,uv)*l.opacity>.03)return l;}
  else{const hits=ray.intersectObjects(overlay.children,false);for(const pick of hits)if(pick.point.distanceTo(h.point)<.001&&pick.uv){const uv=l.uvReuse?reuseUV(pick.uv.toArray(),l):pick.uv.toArray();if(uv&&uv.every(x=>x>=0&&x<=1)&&alphaAt(pick.object.material.map,uv)*l.opacity>.03)return l;}}
 }return null;
}
$('#followSurface').onchange=e=>{const l=layer();if(!l?.projection)return;checkpoint();l.projection.followSurface=e.target.checked;changed();syncProjectionControls();};
$('#allowCrossSide').onchange=e=>{const l=layer();if(!l?.projection)return;checkpoint();l.projection.surface??=sideLock(l.frame);if(e.target.checked)l.projection.surface.mode='free';else l.projection.surface=sideLock(l.projection.surfaceFrame||l.frame);scheduleOverlay(l);changed();syncProjectionControls();};
for(const [id,key] of [['projectionPitch','pitch'],['projectionYaw','yaw']])$('#'+id).onchange=e=>{const l=layer(),value=Number(e.target.value);if(!l?.projection||!Number.isFinite(value)||Math.abs(value)>3600)return;checkpoint();l.projection.pose??={pitch:0,yaw:0};l.projection.pose[key]=value;updatePose(l);scheduleOverlay(l);changed();syncProjectionControls();};
$('#replaceAsset').onclick=async()=>{const l=layer(),a=assets.get(selectedAsset);if(!l||!a)return;checkpoint();l.asset=a.id;l.name=a.name;l.role=libraryRole;l.tint=libraryRole==='pattern'&&(a.kind==='preset'||a.kind==='text');l.color=l.tint?$('#decalColor').value:'#ffffff';if(l.projection)l.projection.crop=[0,0,1,1];if(l.uvReuse)l.uvReuse.crop=[0,0,1,1];if(l.projection&&$('#lockAspect').checked){l.projection.height=l.projection.width/(a.width/a.height);const shrink=Math.min(1,30/l.projection.width,30/l.projection.height);l.projection.width*=shrink;l.projection.height*=shrink;}await updateOverlay(l);renderLayers();changed();};
$('#repositionDecal').onclick=()=>{setEditMode('decal');reanchoring=!!layer();placingDecal=!reanchoring;syncProjectionControls();toast(reanchoring?'在模型上点击新的贴花位置':'选图后在模型上点击，放置贴花');};
$('#projectionType').onchange=()=>{if(layer()?.projection){reanchoring=true;toast('点击模型重新定位，以使用新的贴合方式');}syncProjectionControls();};$('#cylinderAxis').onchange=()=>{reanchoring=!!layer();if(layer())toast('点击圆杆重新拟合方向');};
for(const [id,key] of [['projectionWidth','width'],['projectionHeight','height'],['projectionDepth','depth'],['projectionRadius','radius']])$('#'+id).onchange=e=>{const l=layer();if(!l?.projection)return;let value=Number(e.target.value)/20;if(!Number.isFinite(value)||value<=0)return;value=Math.max(.001,Math.min(key==='depth'?20:key==='radius'?10:30,value));checkpoint();const ratio=l.projection.height/l.projection.width;if($('#lockAspect').checked&&key==='width')l.projection.height=value*ratio;if($('#lockAspect').checked&&key==='height')l.projection.width=value/ratio;l.projection[key]=value;const fit=Math.min(1,30/l.projection.width,30/l.projection.height);l.projection.width*=fit;l.projection.height*=fit;if(l.projection.surfaceFrame)updatePose(l);scheduleOverlay(l);syncProjectionControls();changed();};
$('#shapeDecal').onclick=()=>{shapeMode=!shapeMode;syncProjectionControls();};$('#resetDecalShape').onclick=()=>{const l=layer();if(!l?.projection)return;checkpoint();l.projection.corners=[[0,0],[1,0],[1,1],[0,1]];shapeMode=false;scheduleOverlay(l);syncProjectionControls();changed();};
$('#convertDecal').onclick=async()=>{const l=layer();if(!l||l.projection)return;checkpoint();const a=assets.get(l.asset),f=l.frame,w=f.bounds[2]-f.bounds[0],h=f.bounds[3]-f.bounds[1],aspect=a.width/a.height,width=(l.placement.fit==='cover'?Math.max(w,h*aspect):Math.min(w,h*aspect)),height=width/aspect;l.projection={...targetSpec(),type:'planar',width,height,depth:5,corners:[[0,0],[1,0],[1,1],[0,1]],cutoff:.015};l.placement.x*=w/width;l.placement.y*=h/height;l.selectionVersion=5;ensureAttachment(model,l);setEditMode('decal');selection=null;clearHighlight();await updateOverlay(l);renderLayers();changed();};
function updateDecalGizmo(){const box=$('#decalGizmo'),l=layer();if(!box)return;if(placingDecal||reanchoring||editMode!=='decal'&&!(editMode==='area'&&areaImage(l))||!l?.projection&&!l?.uvReuse||l.hidden||l.unit!==state.vehicle){box.hidden=true;return;}const rect=vp.getBoundingClientRect(),points=(l.uvReuse?reuseQuad(l):quadWorld(l)).map(p=>p.project(camera));if(points.some(p=>!Number.isFinite(p.x)||p.z>1)){box.hidden=true;return;}const xy=points.map(p=>[(p.x+1)*rect.width/2,(1-p.y)*rect.height/2]);box.hidden=false;box.querySelector('polygon').setAttribute('points',xy.map(p=>p.join(',')).join(' '));xy.forEach((p,i)=>{const handle=box.querySelector('[data-corner="'+i+'"]');handle.style.left=Math.max(15,Math.min(rect.width-15,p[0]))+'px';handle.style.top=Math.max(15,Math.min(rect.height-15,p[1]))+'px';});const center=xy.reduce((a,p)=>[a[0]+p[0]/4,a[1]+p[1]/4],[0,0]),top=[(xy[2][0]+xy[3][0])/2,(xy[2][1]+xy[3][1])/2],len=Math.hypot(top[0]-center[0],top[1]-center[1])||1,rotate=[top[0]+(top[0]-center[0])/len*30,top[1]+(top[1]-center[1])/len*30];for(const [type,p] of [['move',center],['rotate',rotate]]){const h=box.querySelector('[data-gizmo="'+type+'"]');h.style.left=Math.max(16,Math.min(rect.width-16,p[0]))+'px';h.style.top=Math.max(16,Math.min(rect.height-16,p[1]))+'px';}const line=box.querySelector('line');for(const [a,value] of [['x1',top[0]],['y1',top[1]],['x2',rotate[0]],['y2',rotate[1]]])line.setAttribute(a,value);updatePoseGizmoV7(l,rect);}
let gizmoDrag=null;
function gizmoPoint(e,l){const rect=renderer.domElement.getBoundingClientRect(),mouse=new T.Vector2((e.clientX-rect.left)/rect.width*2-1,1-(e.clientY-rect.top)/rect.height*2),ray=new T.Raycaster();ray.setFromCamera(mouse,camera);const normal=new T.Vector3(...l.frame.normal),origin=new T.Vector3(...l.frame.origin);if(l.projection?.type==='cylinder')origin.addScaledVector(normal,l.projection.radius);const plane=new T.Plane().setFromNormalAndCoplanarPoint(normal,origin),point=new T.Vector3();if(!ray.ray.intersectPlane(plane,point))return null;point.sub(new T.Vector3(...l.frame.origin));return{x:point.dot(new T.Vector3(...l.frame.u)),y:point.dot(new T.Vector3(...l.frame.v))};}
$('#decalGizmo').onpointerdown=e=>{const button=e.target.closest('[data-gizmo]'),l=layer();if(e.button!==0||!button||!l?.projection&&!l?.uvReuse)return;const point=button.dataset.gizmo==='pose'||button.dataset.gizmo==='move'&&l.projection?.followSurface&&l.projection.type==='planar'?{x:e.clientX,y:e.clientY}:gizmoPoint(e,l);if(!point)return;e.preventDefault();e.stopPropagation();checkpoint();controls.enabled=false;button.setPointerCapture(e.pointerId);gizmoDrag={id:l.id,kind:button.dataset.gizmo,corner:Number(button.dataset.corner),point,start:structuredClone(l),axis:button.dataset.axis,client:{x:e.clientX,y:e.clientY},button,pointer:e.pointerId};};
$('#decalGizmo').onpointermove=e=>{if(!gizmoDrag)return;const l=layer();if(!l||l.id!==gizmoDrag.id)return;if(l.uvReuse){dragReuseV8(e,l);return;}if(gizmoDrag.kind==='pose'){const change=((e.clientX-gizmoDrag.client.x)-(e.clientY-gizmoDrag.client.y))*.5*window.interactionV8.rotation;if(gizmoDrag.axis==='roll')l.placement.angle=gizmoDrag.start.placement.angle+change;else{l.projection.pose??={pitch:0,yaw:0};l.projection.pose[gizmoDrag.axis]=(gizmoDrag.start.projection.pose?.[gizmoDrag.axis]||0)+change;updatePose(l);}scheduleOverlay(l);syncPlacement();syncInspectorV7();changed();return;}if(gizmoDrag.kind==='move'&&l.projection.followSurface&&l.projection.type==='planar'){const speed=window.interactionV8.move,h=hit({clientX:gizmoDrag.client.x+(e.clientX-gizmoDrag.client.x)*speed,clientY:gizmoDrag.client.y+(e.clientY-gizmoDrag.client.y)*speed});if(h&&followHit(model,l,h)){scheduleOverlay(l);syncPlacement();syncInspectorV7();changed();}return;}const point=gizmoPoint(e,gizmoDrag.start);if(!point)return;const start=gizmoDrag.start,p=parameters(start),dx=point.x-p.cx,dy=point.y-p.cy;
 if(gizmoDrag.kind==='move'){
  const x=Math.max(-10,Math.min(10,start.placement.x+(point.x-gizmoDrag.point.x)*window.interactionV8.move/start.projection.width)),y=Math.max(-10,Math.min(10,start.placement.y+(point.y-gizmoDrag.point.y)*window.interactionV8.move/start.projection.height));
  if(l.projection.followSurface&&l.projection.type!=='cylinder'){const h=hit(e),old={x:l.placement.x,y:l.placement.y};l.placement.x=x;l.placement.y=y;if(!h||!followHit(model,l,h)){l.placement.x=old.x;l.placement.y=old.y;return;}}
  else{l.placement.x=x;l.placement.y=y;if(l.projection.surfaceFrame)l.projection.surfaceFrame.origin=projectionCenter(l).toArray();}
 }
 if(gizmoDrag.kind==='rotate'){const previous=Math.atan2(gizmoDrag.point.y-p.cy,gizmoDrag.point.x-p.cx);l.placement.angle=((start.placement.angle+(((Math.atan2(dy,dx)-previous+Math.PI*3)%(Math.PI*2))-Math.PI)*180/Math.PI*window.interactionV8.rotation+180)%360+360)%360-180;}
 if(gizmoDrag.kind==='corner'){
  if(shapeMode&&l.projection.type==='planar'){const corners=structuredClone(start.projection.corners),x=(p.c*dx-p.s*dy)/p.width+.5,y=(p.s*dx+p.c*dy)/p.height+.5;const oldX=gizmoDrag.point.x-p.cx,oldY=gizmoDrag.point.y-p.cy,oldU=(p.c*oldX-p.s*oldY)/p.width+.5,oldV=(p.s*oldX+p.c*oldY)/p.height+.5;corners[gizmoDrag.corner]=[corners[gizmoDrag.corner][0]+x-oldU,corners[gizmoDrag.corner][1]+y-oldV];if(validCorners(corners))l.projection.corners=corners;}
  else{const old=Math.hypot(gizmoDrag.point.x-p.cx,gizmoDrag.point.y-p.cy),ratio=Math.pow(Math.hypot(dx,dy)/Math.max(.0001,old),window.interactionV8.scale);l.placement.scale=Math.max(.01,Math.min(20,start.placement.scale*ratio));}
 }scheduleOverlay(l);syncPlacement();changed();dirty=true;};
function finishGizmo(){if(!gizmoDrag)return;try{gizmoDrag.button.releasePointerCapture(gizmoDrag.pointer);}catch{}gizmoDrag=null;controls.enabled=true;syncProjectionControls();}
$('#decalGizmo').onpointerup=finishGizmo;$('#decalGizmo').onpointercancel=finishGizmo;
$('#decalGizmo').oncontextmenu=e=>{e.preventDefault();const h=hit(e),l=layer();if(h)placementAnchor=anchorFrame(model,h,camera,$('#projectionType').value,$('#cylinderAxis').value);else if(l?.projection)placementAnchor={frame:structuredClone(l.frame),radius:l.projection.radius||.08,distance:l.projection.distance||0};showMenu(e);};
async function settleProjectionEdits(){
 if(overlayFrame){cancelAnimationFrame(overlayFrame);overlayFrame=0;}
 const waiting=[...queuedOverlays.values()];queuedOverlays.clear();for(const l of waiting)updateOverlay(l);
 let timer;try{await Promise.race([Promise.all([...overlayTasks]),new Promise((resolve,reject)=>{timer=setTimeout(()=>reject(Error('贴花预览计算超时；当前工作台已保留，请保存工程后重试。')),20000);})]);}finally{clearTimeout(timer);}
}
function captureLayout(){return{vehicle:state.vehicle,selectedGroup,activeLayer,selection,editMode,libraryRole,selectedAsset,category,search:$('#searchVehicle').value,camera:{position:camera.position.toArray(),up:camera.up.toArray(),target:controls.target.toArray()},details:Object.fromEntries($$('details[id]').map(e=>[e.id,e.open])),inspectorScroll:$('.inspector-scroll').scrollTop,vehicleScroll:$('#vehicles').scrollTop,layerFilter:$('#layerFilter').value,surfaceMode:$('#surfaceMode').value,shapeMode,projectionType:$('#projectionType').value,cylinderAxis:$('#cylinderAxis').value,inspectorTab:window.layoutV7.active,sensitivity:{move:window.interactionV8.move,rotation:window.interactionV8.rotation,scale:window.interactionV8.scale},showSharedUV:showSharedUVV8,background:window.backgroundV9.value};}
async function applyLayout(layout){restoringLayout=true;try{window.backgroundV9.set(layout.background);window.interactionV8.set(layout.sensitivity||{});showSharedUVV8=!!layout.showSharedUV;if(layout.vehicle&&vehicles[layout.vehicle]&&layout.vehicle!==state.vehicle)await chooseModel(layout.vehicle,false);if(layout.selectedGroup&&vehicles[state.vehicle].groups.includes(layout.selectedGroup))selectGroup(layout.selectedGroup);category=layout.category||'全部';$('#searchVehicle').value=layout.search||'';$$('[data-cat]').forEach(b=>b.classList.toggle('active',b.dataset.cat===category));renderVehicles();setEditMode(layout.editMode||'decal');libraryRole=layout.libraryRole||'photo';selectedAsset=assets.has(layout.selectedAsset)?layout.selectedAsset:null;$('#layerFilter').value=layout.layerFilter||'model';$('#surfaceMode').value=layout.surfaceMode||'panel';$('#projectionType').value=layout.projectionType||'planar';$('#cylinderAxis').value=layout.cylinderAxis||'Y';shapeMode=!!layout.shapeMode;for(const [id,open] of Object.entries(layout.details||{})){const element=document.getElementById(id);if(element?.tagName==='DETAILS')element.open=open;}if(layout.activeLayer&&state.layers.some(l=>l.id===layout.activeLayer&&l.unit===state.vehicle))activeLayer=layout.activeLayer;else activeLayer=null;if(layout.selection?.unit===state.vehicle){selection=structuredClone(layout.selection);await highlightSelection();}if(layout.camera){camera.position.fromArray(layout.camera.position);camera.up.fromArray(layout.camera.up);controls.target.fromArray(layout.camera.target);controls.update();}renderAssets();renderLayers();window.layoutV7.show(layout.inspectorTab||(activeLayer?'decal':'assets'));await new Promise(r=>requestAnimationFrame(r));$('.inspector-scroll').scrollTop=layout.inspectorScroll||0;$('#vehicles').scrollTop=layout.vehicleScroll||0;dirty=true;}finally{restoringLayout=false;}}
async function autosaveWorkbench(){if(!workspaceId||restoringLayout)return;return api('/api/autosave',recoveryData());}
function layoutChanged(){if(restoringLayout)return;clearTimeout(recoveryTimer);recoveryTimer=setTimeout(()=>autosaveWorkbench().catch(console.error),700);}
controls.addEventListener('end',layoutChanged);$$('details[id]').forEach(e=>e.addEventListener('toggle',layoutChanged));$('.inspector-scroll').addEventListener('scroll',layoutChanged);$('#vehicles').addEventListener('scroll',layoutChanged);
async function refreshWorkspaces(){const list=await fetch('/api/workspaces').then(r=>r.json());$('#workspaceSelect').innerHTML=list.items.map(w=>`<option value="${w.id}">${esc(w.name)}</option>`).join('');$('#workspaceSelect').value=workspaceId||list.active;}
async function saveWorkbenchLayout(){await settleProjectionEdits();const result=await api('/api/workspace/save-layout',recoveryData());workspaceId=result.workspaceId;if(window.chrome?.webview)window.chrome.webview.postMessage('studio-save-layout');$('#projectStatus').textContent='工作台布局与编辑内容已保存 · '+result.version;await refreshWorkspaces();return result;}
$('#saveLayout').onclick=async()=>{try{await saveWorkbenchLayout();toast('当前工作台布局已保存，下次启动自动恢复');}catch(error){toast(error.message);}};
$('#newWorkspace').onclick=()=>{$('#newWorkspaceDialog').showModal();};$('#cancelNewWorkspace').onclick=()=>$('#newWorkspaceDialog').close();
$('#createWorkspace').onclick=async()=>{busy('正在创建独立工作台…');try{await saveWorkbenchLayout();clearTimeout(recoveryTimer);const fresh={format:'HD2VehicleProject',version:8,physicalRevision:2,mechMaterialRevision:2,unitColors:{},name:$('#newWorkspaceName').value.trim()||'新的载具涂装',vehicle:state.vehicle,family:state.family,coverFixed:true,removeStockDecals:true,groups:Object.fromEntries(catalog.groups.map(g=>[g.id,{mode:'original',color:g.defaultColor}])),layers:[],surfaceColors:[]},session={state:fresh,layout:{vehicle:fresh.vehicle,editMode:'decal',libraryRole:'photo',details:{projectionPanel:true,layerStack:true},category:'全部'}};const created=await api('/api/workspace/new',{name:fresh.name,session});workspaceId=created.workspaceId;history=[];future=[];placementAnchor=null;await restoreSession(created);$('#newWorkspaceDialog').close();await refreshWorkspaces();toast('新工作台已创建，旧工作台已保留');}catch(error){toast(error.message);}finally{busy('',false);}};
$('#workspaceSelect').onchange=async e=>{const target=e.target.value;if(target===workspaceId)return;busy('正在切换工作台…');try{await saveWorkbenchLayout();clearTimeout(recoveryTimer);const incoming=await api('/api/workspace/open',{id:target});incoming.sourcePhysicalRevision=incoming.state.physicalRevision||1;const opened=await api('/api/open',{state:incoming.state});incoming.state=opened.state;workspaceId=incoming.workspaceId;history=[];future=[];await restoreSession(incoming);await refreshWorkspaces();}catch(error){toast(error.message);await refreshWorkspaces();}finally{busy('',false);}};
function requestClose(){if(!$('#busy').hidden){toast('正在完成当前操作，请稍候再关闭');return;}if(!$('#exitDialog').open){$('#exitError').textContent='';$('#exitDialog').showModal();}}
$('#cancelExit').onclick=()=>$('#exitDialog').close();
async function closeWorkbench(save){$('#exitError').textContent='';try{clearTimeout(recoveryTimer);await settleProjectionEdits();if(save)await saveWorkbenchLayout();else await autosaveWorkbench();window.__closeApproved=true;if(window.chrome?.webview)window.chrome.webview.postMessage('studio-close-approved');else $('#exitDialog').close();}catch(error){$('#exitError').textContent='保存未完成：'+error.message;}}
$('#saveAndExit').onclick=()=>closeWorkbench(true);$('#exitWithoutLayout').onclick=()=>closeWorkbench(false);
var pendingAssetReplacement=false,poseToolActive=false,exportAdvancedV7=false,assetMenuTargetV7=null,cropDraftV7=null,cropDragV7=null;
function syncInspectorV7(){
 const l=layer(),p=l?.projection,area=!!l&&(areaImage(l)||editMode==='area'&&!p),mode=p?(p.followSurface?(p.surface?.mode==='free'?'surface':'locked'):'manual'):$('#attachmentMode').value;
 $('#attachmentMode').value=mode;$('#currentLayerName').textContent=l?l.name:'单击模型上的贴花';const asset=assets.get(l?.asset),thumb=$('#currentAssetThumb');thumb.hidden=!asset;if(asset)thumb.src=asset.photoThumbnail||asset.thumbnail;
 $('#chooseReplacement').disabled=!l;$('#cropDecal').disabled=!l;$('#poseTool').disabled=!p;$('#poseTool').classList.toggle('active',poseToolActive);$('.manual-position').hidden=area||mode!=='manual';$('.surface-position').hidden=!area&&mode==='manual';document.body.dataset.layerPlacement=area?'area':'free';$('.pose-card .control-heading b').textContent=area?'面内旋转':'三轴旋转';$('#poseTool').hidden=area;$('.area-image-note').hidden=!area;if(highlight)highlight.traverse(m=>{if(m.isMesh&&m.material?.isMeshBasicMaterial)m.visible=!area;});$('#projectionTargets').hidden=$('#projectionScope').value!=='custom';
 for(const [id,key] of [['projectionPitch','pitch'],['projectionYaw','yaw']]){$('#'+id).min='-3600';$('#'+id).max='3600';$('#'+id).value=p?.pose?.[key]||0;}
 if(l?.projection){const center=projectionCenter(l);for(const [axis,key] of [['X','x'],['Y','y'],['Z','z']])$('#position'+axis).value=Number((center[key]*20).toFixed(4));}
}
function configureSurfaceMenuV7(){
 const decal=editMode==='decal',l=layer(),labels={import:decal?'导入图片…':'导入图片到选区…',apply:decal?'在此放置已选素材':'添加已选素材到选区',library:'打开素材库',delete:decal?'删除贴花':'删除选区上的图层',reset:'重置视角','shared-uv':showSharedUVV8?'隐藏相同贴图区':'显示相同贴图区','reuse-uv':'复用到相同贴图区<span>生成独立副本，之后分别编辑</span>'};
 for(const b of menu.querySelectorAll('[data-action]')){
  const action=b.dataset.action;b.dataset.originalLabel??=b.innerHTML;b.innerHTML=labels[action]||b.dataset.originalLabel;
  const allowed=decal?['import','apply','library','crop','replace','duplicate','reanchor','delete','reset']:['import','apply','library','color','delete','expand','face','clear','shared-uv','reuse-uv','reset'];
  b.hidden=!allowed.includes(action)||(decal&&['crop','replace','duplicate','reanchor','delete'].includes(action)&&!l);
  if(action==='apply')b.disabled=!selectedAsset||(decal?!placementAnchor:!selection);
  if(action==='delete')b.disabled=!contextLayerId;if(['shared-uv','reuse-uv'].includes(action))b.disabled=!selection;
 }
 $('#contextTitle').textContent=decal?(l?'贴花 · '+l.name:'自由贴花'):'区域编辑';menu.querySelectorAll('hr').forEach(e=>e.hidden=decal);
}
$('#assets').oncontextmenu=e=>{const card=e.target.closest('[data-asset]');if(!card)return;e.preventDefault();e.stopPropagation();hideMenu();assetMenuTargetV7={id:card.dataset.asset,role:libraryRole};const m=$('#assetMenu');m.hidden=false;m.style.left=Math.max(8,Math.min(e.clientX,innerWidth-m.offsetWidth-8))+'px';m.style.top=Math.max(8,Math.min(e.clientY,innerHeight-m.offsetHeight-8))+'px';};
document.addEventListener('pointerdown',e=>{if(!$('#assetMenu').contains(e.target))$('#assetMenu').hidden=true;},true);
$('#removeLibraryAsset').onclick=async()=>{const target=assetMenuTargetV7;if(!target)return;$('#assetMenu').hidden=true;try{await api('/api/library/remove',target);if(selectedAsset===target.id)selectedAsset=null;await refreshLibrary();toast('已从当前素材分区删除；已有贴花和原图仍保留');}catch(e){toast(e.message);}};
function chooseReplacementV7(){if(!layer())return;pendingAssetReplacement=true;libraryRole=layerRole(layer());renderAssets();window.layoutV7.show('assets');toast('选择一张素材，替换当前贴花');}
$('#chooseReplacement').onclick=chooseReplacementV7;
$('#attachmentMode').onchange=e=>{
 const l=layer(),mode=e.target.value;$('#followSurface').checked=mode!=='manual';$('#allowCrossSide').checked=mode!=='locked';if(!l?.projection){syncInspectorV7();return;}
 checkpoint();const p=l.projection,wasManual=!p.followSurface;if(mode==='manual'){p.followSurface=false;p.surface??=sideLock(l.frame);p.surface.mode='free';poseToolActive=true;}
 else{if(wasManual){p.surfaceFrame={...structuredClone(l.frame),origin:projectionCenter(l).toArray()};p.pose={pitch:0,yaw:0};updatePose(l);}p.followSurface=true;p.surface=sideLock(p.surfaceFrame||l.frame);p.surface.mode=mode==='locked'?'locked':'free';}
 scheduleOverlay(l);syncProjectionControls();syncInspectorV7();changed();
};
function liveDepthV7(value){const l=layer(),depth=Number(value)/20;if(!Number.isFinite(depth)||depth<.0005||depth>20)return;$('#projectionDepth').value=Number((depth*20).toFixed(4));$('#projectionDepthRange').value=depth*20;if(l?.projection){l.projection.depth=depth;updateProjectionAppearance(overlayMeshes.get(l.id),l);dirty=true;changed();}}
$('#projectionDepthRange').onpointerdown=()=>{if(layer())checkpoint();};$('#projectionDepthRange').oninput=e=>liveDepthV7(e.target.value);$('#projectionDepth').onfocus=()=>{if(layer())checkpoint();};$('#projectionDepth').oninput=e=>{if(e.target.value.trim())liveDepthV7(e.target.value);};$('#projectionDepth').onchange=e=>liveDepthV7(e.target.value);
$('#poseTool').onclick=()=>{poseToolActive=!poseToolActive;syncInspectorV7();};
for(const [axis,key] of [['X','x'],['Y','y'],['Z','z']])$('#position'+axis).onchange=e=>{const l=layer(),value=Number(e.target.value)/20;if(!l?.projection||l.projection.followSurface||!Number.isFinite(value)||Math.abs(value)>20)return;checkpoint();const center=projectionCenter(l);center[key]=value;l.projection.surfaceFrame??={...structuredClone(l.frame),origin:center.toArray()};l.projection.surfaceFrame.origin=center.toArray();updatePose(l);scheduleOverlay(l);syncInspectorV7();changed();};
function updatePoseGizmoV7(l,rect){
 const p=l.projection,show=!!p&&poseToolActive&&p.type==='planar'&&!areaImage(l);for(const b of $$('#decalGizmo [data-gizmo=pose]'))b.hidden=!show;const svg=$('#decalFrame');for(const n of svg.querySelectorAll('.pose-ring'))n.remove();if(!show)return;
 const center=projectionCenter(l),base=p.surfaceFrame||l.frame,u=new T.Vector3(...base.u),v=new T.Vector3(...base.v),n=new T.Vector3(...l.frame.normal),radius=camera.position.distanceTo(center)*Math.tan(camera.fov*Math.PI/360)*.15;
 const axes=[['pitch',v,n,'#bc8787'],['yaw',u,n,'#8da38c'],['roll',new T.Vector3(...l.frame.u),new T.Vector3(...l.frame.v),'#809fbc']];
 for(let index=0;index<axes.length;index++){const [axis,a,b,color]=axes[index],points=[];for(let i=0;i<=56;i++){const angle=i/56*Math.PI*2,point=center.clone().addScaledVector(a,Math.cos(angle)*radius).addScaledVector(b,Math.sin(angle)*radius).project(camera);points.push([(point.x+1)*rect.width/2,(1-point.y)*rect.height/2]);}const ring=document.createElementNS('http://www.w3.org/2000/svg','polyline');ring.setAttribute('points',points.map(p=>p.join(',')).join(' '));ring.setAttribute('fill','none');ring.setAttribute('stroke',color);ring.setAttribute('stroke-width','1.5');ring.classList.add('pose-ring');svg.append(ring);const point=points[[8,19,37][index]],handle=$('#decalGizmo [data-axis='+axis+']');handle.style.left=Math.max(16,Math.min(rect.width-16,point[0]))+'px';handle.style.top=Math.max(16,Math.min(rect.height-16,point[1]))+'px';}
}
function drawCropV7(){if(!cropDraftV7)return;const [l,b,r,t]=cropDraftV7,rect=$('#cropRect');rect.style.left=l*100+'%';rect.style.top=(1-t)*100+'%';rect.style.width=(r-l)*100+'%';rect.style.height=(t-b)*100+'%';}
async function openCropV7(){const l=layer();if(!l)return;if(!l.projection&&!l.uvReuse){const mask=structuredClone(l.surfaces),previousMode=editMode;await $('#convertDecal').onclick();l.projection.mask=mask;l.projection.areaAttached=true;l.projection.followSurface=false;setEditMode(previousMode);selection=areaSelection(l);await updateOverlay(l);await highlightSelection();renderLayers();}const asset=assets.get(l.asset);cropDraftV7=structuredClone(l.uvReuse?.crop||l.projection?.crop||[0,0,1,1]);const img=$('#cropImage');img.src=asset.image;await img.decode();const width=Math.min(600,innerWidth*.84-46,innerHeight*.54*img.naturalWidth/img.naturalHeight);$('#cropStage').style.width=width+'px';$('#cropDialog').showModal();drawCropV7();}
$('#cropDecal').onclick=()=>openCropV7().catch(e=>toast(e.message));$('#cancelCrop').onclick=()=>$('#cropDialog').close();$('#resetCrop').onclick=()=>{cropDraftV7=[0,0,1,1];drawCropV7();};
$('#cropRect').onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();const rect=$('#cropImage').getBoundingClientRect();cropDragV7={pointer:e.pointerId,target:e.target,corner:e.target.dataset.cropCorner===undefined?null:Number(e.target.dataset.cropCorner),x:(e.clientX-rect.x)/rect.width,y:1-(e.clientY-rect.y)/rect.height,start:[...cropDraftV7]};e.target.setPointerCapture(e.pointerId);};
$('#cropRect').onpointermove=e=>{const drag=cropDragV7;if(!drag)return;const rect=$('#cropImage').getBoundingClientRect(),x=Math.max(0,Math.min(1,(e.clientX-rect.x)/rect.width)),y=Math.max(0,Math.min(1,1-(e.clientY-rect.y)/rect.height)),c=[...drag.start],min=.006;if(drag.corner===null){const dx=Math.max(-c[0],Math.min(1-c[2],x-drag.x)),dy=Math.max(-c[1],Math.min(1-c[3],y-drag.y));c[0]+=dx;c[2]+=dx;c[1]+=dy;c[3]+=dy;}else{if([0,3].includes(drag.corner))c[0]=Math.min(c[2]-min,x);else c[2]=Math.max(c[0]+min,x);if([0,1].includes(drag.corner))c[1]=Math.min(c[3]-min,y);else c[3]=Math.max(c[1]+min,y);}cropDraftV7=c;drawCropV7();};
function endCropV7(){if(cropDragV7){try{cropDragV7.target.releasePointerCapture(cropDragV7.pointer);}catch{}cropDragV7=null;}}$('#cropRect').onpointerup=endCropV7;$('#cropRect').onpointercancel=endCropV7;
$('#applyCrop').onclick=()=>{const l=layer();if(!l||!l.projection&&!l.uvReuse||!cropDraftV7)return;checkpoint();if(l.uvReuse){l.uvReuse.crop=[...cropDraftV7];scheduleOverlay(l);renderLayers();changed();$('#cropDialog').close();return;}l.projection.crop=[...cropDraftV7];const a=assets.get(l.asset),p=l.projection;if($('#lockAspect').checked){p.height=p.width/(a.width*(p.crop[2]-p.crop[0])/(a.height*(p.crop[3]-p.crop[1])));const scale=Math.min(1,30/p.width,30/p.height);p.width*=scale;p.height*=scale;if(p.surfaceFrame)updatePose(l);}scheduleOverlay(l);renderLayers();changed();$('#cropDialog').close();};
window.studio.getInspectorState=()=>({tab:window.layoutV7.active,poseToolActive,pendingAssetReplacement});



async function reuseSelectedV8(){
 if(!selection)return;const chosen=structuredClone(selection),name=layer()?.name||'选区底色';busy('正在生成独立贴图区副本…');try{const result=await api('/api/reuse-area',{state,selection:chosen});await refreshLibrary();checkpoint();let last;
 for(const [index,copy] of result.copies.entries()){const mapped=reuseFrame(model,copy.surfaces,copy.uvReuse.bounds),{frame,...world}=mapped,l={id:crypto.randomUUID(),name:name+' · 复用'+(index+1),asset:copy.asset,unit:state.vehicle,frame,surfaces:copy.surfaces,selectionVersion:5,uvReuse:{...copy.uvReuse,...world},role:'photo',tint:false,color:'#ffffff',opacity:1,hidden:false,placement:{fit:'contain',scale:1,x:0,y:0,angle:0}};state.layers.push(l);await updateOverlay(l);last=l;}
 if(last)await selectAreaImage(last);window.studio.lastUVReuse=result;changed();toast('已生成 '+result.copies.length+' 个独立副本，源图层保持原样');
 }finally{busy('',false);}
}
function reuseDragPointV8(e,l){const q=gizmoPoint(e,l);if(!q)return null;return reuseWorldUV(l,new T.Vector3(...l.frame.origin).addScaledVector(new T.Vector3(...l.frame.u),q.x).addScaledVector(new T.Vector3(...l.frame.v),q.y));}
function dragReuseV8(e,l){const start=gizmoDrag.start,a=reuseDragPointV8({clientX:gizmoDrag.client.x,clientY:gizmoDrag.client.y},start),b=reuseDragPointV8(e,start);if(!a||!b)return;const box=start.uvReuse.bounds,w=box[2]-box[0],h=box[3]-box[1],center=[(box[0]+box[2])/2+start.placement.x*w,(box[1]+box[3])/2+start.placement.y*h],pa=[(a[0]-center[0])/w,(a[1]-center[1])/h],pb=[(b[0]-center[0])/w,(b[1]-center[1])/h];
 if(gizmoDrag.kind==='move'){l.placement.x=Math.max(-10,Math.min(10,start.placement.x+(b[0]-a[0])/w*window.interactionV8.move));l.placement.y=Math.max(-10,Math.min(10,start.placement.y+(b[1]-a[1])/h*window.interactionV8.move));}
 if(gizmoDrag.kind==='rotate'){let delta=Math.atan2(pb[1],pb[0])-Math.atan2(pa[1],pa[0]);delta=((delta+Math.PI*3)%(Math.PI*2))-Math.PI;l.placement.angle=start.placement.angle+delta*180/Math.PI*window.interactionV8.rotation;}
 if(gizmoDrag.kind==='corner')l.placement.scale=Math.max(.01,Math.min(20,start.placement.scale*Math.pow(Math.hypot(...pb)/Math.max(.00001,Math.hypot(...pa)),window.interactionV8.scale)));
 scheduleOverlay(l);syncPlacement();changed();
}

window.interactionV8.onChange=()=>{controls.rotateSpeed=window.interactionV8.rotation;controls.panSpeed=window.interactionV8.move;controls.zoomSpeed=window.interactionV8.scale;layoutChanged();};window.interactionV8.set({});

setEditMode('decal');

await refreshLibrary();
let recovered=null;try{recovered=await fetch('/api/recovery').then(r=>r.json());if(recovered?.state){const opened=await api('/api/open',{state:recovered.state});state=normalizeState(opened.state);$('#projectName').value=state.name;}}catch(error){console.error(error);recovered=null;}
renderVehicles();historyButtons();await chooseModel(state.vehicle,false);
if(recovered){await restoreSession(recovered,false);$('#projectStatus').textContent='已恢复上次编辑内容';}

const ensured=await api('/api/workspace/ensure',recoveryData());workspaceId=ensured.workspaceId;await refreshWorkspaces();restoringLayout=false;initialized=true;window.__studioReady=true;window.dispatchEvent(new Event('studio-ready'));

$('#removeStockDecals').onchange=()=>{checkpoint();state.removeStockDecals=$('#removeStockDecals').checked;changed();};
const coverageDialog=$('#coverageDialog'),coverageFrame=$('#coverageFrame');
$('#inspectCoverage').onclick=()=>{coverageDialog.showModal();if(!coverageFrame.src)coverageFrame.src='/coverage.html';else coverageFrame.contentWindow.postMessage({type:'vehicle-coverage',state:structuredClone(state)},location.origin);};
$('#closeCoverage').onclick=()=>coverageDialog.close();
window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===coverageFrame.contentWindow&&e.data?.type==='vehicle-coverage-ready')coverageFrame.contentWindow.postMessage({type:'vehicle-coverage',state:structuredClone(state)},location.origin);});
