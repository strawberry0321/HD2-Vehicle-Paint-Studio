(()=>{
 const themes={light:{name:'浅灰',background:'#e6e4e0',floor:'#dedcd7',grid:'#bfc0bf',lines:'#cecdca'},gray:{name:'中灰',background:'#777c84',floor:'#777c84',grid:'#626872',lines:'#858b94'},dark:{name:'深灰',background:'#343941',floor:'#343941',grid:'#626b78',lines:'#414953'},black:{name:'黑色',background:'#171b22',floor:'#171b22',grid:'#454f5e',lines:'#29313d'}};
 const label=document.createElement('label');label.className='background-choice';label.innerHTML='<span>背景</span><select id="workbenchBackground" aria-label="工作台背景色">'+Object.entries(themes).map(([id,t])=>'<option value="'+id+'">'+t.name+'</option>').join('')+'</select>';
 document.querySelector('.view-toolbar').lastElementChild.prepend(label);
 const control=label.querySelector('select');window.backgroundV9={value:'light',themes,set(value){this.value=themes[value]?value:'light';control.value=this.value;this.onChange?.(themes[this.value]);}};
 control.onchange=()=>window.backgroundV9.set(control.value);
})();
