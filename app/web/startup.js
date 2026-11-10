window.__startupError=null;
(function(){
 function fail(message){
  if(window.__studioReady)return;
  window.__startupError=String(message||'界面资源加载失败').slice(0,800);
  let show=()=>{let p=document.getElementById('startupFallback');if(p){p.hidden=false;document.getElementById('startupMessage').textContent=window.__startupError;}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',show,{once:true});else show();
 }
 window.addEventListener('error',e=>{if(String(e.message||'').startsWith('ResizeObserver loop'))return;fail(e.message||'某个界面文件未能载入');},true);
 window.addEventListener('unhandledrejection',e=>fail(e.reason?.message||String(e.reason)));
 const timeout=setTimeout(()=>fail('工作台加载超时，请点击重新加载。'),45000);
 window.addEventListener('studio-ready',()=>{clearTimeout(timeout);let p=document.getElementById('startupFallback');if(p)p.hidden=true;});
})();
