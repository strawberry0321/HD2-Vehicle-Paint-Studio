import * as T from 'three';
export function prepareRecolor(material){
  const uniforms={enabled:{value:0},target:{value:new T.Color()},referenceLuma:{value:1}};
  material.userData.recolor=uniforms;
  material.onBeforeCompile=shader=>{
    shader.uniforms.recolorEnabled=uniforms.enabled;
    shader.uniforms.recolorTarget=uniforms.target;
    shader.uniforms.recolorReference=uniforms.referenceLuma;
    shader.fragmentShader='uniform float recolorEnabled;\nuniform vec3 recolorTarget;\nuniform float recolorReference;\n'+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`#include <map_fragment>
      if(recolorEnabled>0.5){
        float sourceLuma=dot(max(diffuseColor.rgb,vec3(0.0)),vec3(0.2126,0.7152,0.0722));
        float shade=clamp(0.45+0.55*pow(sourceLuma/max(0.01,recolorReference),0.55),0.45,1.6);
        diffuseColor.rgb=clamp(recolorTarget*shade,0.0,1.0);
      }`);
  };
  material.customProgramCacheKey=()=> 'native-luminance-v4-lift';
}
export function setRecolor(material,setting,reference){
  const u=material.userData.recolor;
  u.enabled.value=setting.mode==='native'&&reference?1:0;
  if(u.enabled.value){
    u.target.value.set(setting.color);
    const c=new T.Color(reference);
    u.referenceLuma.value=c.r*.2126+c.g*.7152+c.b*.0722;
  }
}
