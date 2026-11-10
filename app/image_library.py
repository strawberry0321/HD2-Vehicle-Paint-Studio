from pathlib import Path
import base64,hashlib,io,json,re,shutil
from PIL import Image,ImageDraw,ImageFont
from storage_paths import DATA_ROOT
ROOT=DATA_ROOT
LIB=ROOT/'library';LIB.mkdir(parents=True,exist_ok=True)
def import_image(data,name,kind='image',tintable=False,roles=None):
    raw=base64.b64decode(data.split(',',1)[1],validate=True)
    if len(raw)>32*1024*1024:raise ValueError('图片请控制在32MB以内')
    im=Image.open(io.BytesIO(raw));im.verify();im=Image.open(io.BytesIO(raw))
    if im.width*im.height>80_000_000:raise ValueError('图片像素超过8000万')
    im.load();ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}.get(im.format)
    if not ext:raise ValueError('支持PNG、JPG、WebP图片')
    key=hashlib.sha256(raw).hexdigest();folder=LIB/key;folder.mkdir(exist_ok=True);original=folder/('original'+ext)
    if not original.exists():
        original.write_bytes(raw)
        thumb=im.convert('RGBA');thumb.thumbnail((192,192),Image.Resampling.LANCZOS)
        original_thumb=thumb.copy();original_thumb.save(folder/'original_thumb.png')
        if kind in ['preset','text']:
            shade=Image.new('RGBA',thumb.size,'#7e8794');shade.putalpha(thumb.getchannel('A'));thumb=shade
        thumb.save(folder/'thumb.png')
        meta=dict(id=key,name=str(name)[:100],kind=kind,tintable=tintable,width=im.width,height=im.height,bytes=len(raw),sha256=key,filename=original.name)
        (folder/'asset.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf8')
    meta=json.loads((folder/'asset.json').read_text(encoding='utf8'));existing=meta.get('roles',['photo'] if meta.get('kind')=='image' else ['pattern']);requested=roles or (['photo'] if kind=='image' else ['pattern']);meta['roles']=sorted(set(existing)|({r for r in requested if r in ['photo','pattern']}));meta['tintable']='pattern' in meta['roles'];meta['removedRoles']=[r for r in meta.get('removedRoles',[]) if r not in requested];(folder/'asset.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf8')
    if not (folder/'original_thumb.png').exists():
        original_thumb=im.convert('RGBA');original_thumb.thumbnail((192,192),Image.Resampling.LANCZOS);original_thumb.save(folder/'original_thumb.png')
    assert hashlib.sha256(original.read_bytes()).hexdigest()==key
    return describe(key)
def describe(key):
    meta=json.loads((LIB/key/'asset.json').read_text(encoding='utf8'))
    meta['roles']=meta.get('roles',['photo'] if meta['kind']=='image' else ['pattern']);meta.setdefault('removedRoles',[])
    return meta|dict(image='/library/'+key+'/original',thumbnail='/library/'+key+'/thumb',photoThumbnail='/library/'+key+'/original-thumb')
def path_for(key,thumbnail=False):
    if not re.fullmatch('[0-9a-f]{64}',str(key)):raise ValueError('图片编号无效')
    meta=describe(key)
    if thumbnail=='photo':return LIB/key/('original_thumb.png' if (LIB/key/'original_thumb.png').exists() else 'thumb.png')
    return LIB/key/('thumb.png' if thumbnail else meta['filename'])
def list_assets():return [describe(p.name) for p in LIB.iterdir() if p.is_dir() and (p/'asset.json').exists()]
def remove_asset(payload):
    key=payload.get('id');role=payload.get('role')
    if role not in ['photo','pattern']:raise ValueError('素材分区无效')
    path_for(key);meta=describe(key);meta['removedRoles']=sorted(set(meta.get('removedRoles',[]))|{role})
    for field in ['image','thumbnail','photoThumbnail']:meta.pop(field,None)
    (LIB/key/'asset.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf8')
    # Keep the immutable original for existing layers, saved projects and undo.
    return dict(ok=True,id=key,role=role,original_retained=True)
def from_image(im,name,kind='preset'):
    b=io.BytesIO();im.save(b,format='PNG');return import_image('data:image/png;base64,'+base64.b64encode(b.getvalue()).decode(),name,kind,True)
def stamp(kind,text=''):
    im=Image.new('RGBA',(1024,1024),(0,0,0,0));d=ImageDraw.Draw(im)
    if kind=='star':
        import math
        d.polygon([(512+math.cos(-math.pi/2+i*math.pi/5)*(460 if i%2==0 else 195),512+math.sin(-math.pi/2+i*math.pi/5)*(460 if i%2==0 else 195)) for i in range(10)],fill='white');name='星形'
    elif kind=='stripe':
        for i in range(-2,5):d.polygon([(i*300,1024),(i*300+130,1024),(i*300+640,0),(i*300+510,0)],fill='white')
        name='条纹'
    elif kind=='arrow':
        d.polygon([(70,390),(625,390),(625,150),(970,512),(625,875),(625,635),(70,635)],fill='white');name='箭头'
    else:
        text=text or '01';im=Image.new('RGBA',(max(1024,len(text)*170),360),(0,0,0,0));d=ImageDraw.Draw(im)
        font=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',270);box=d.textbbox((0,0),text,font=font);d.text(((im.width-(box[2]-box[0]))/2,-box[1]+35),text,font=font,fill='white');name='文字：'+text
    return from_image(im,name,'text' if kind=='text' else 'preset')
def ensure_presets():
    if not any(a['kind']=='preset' for a in list_assets()):
        for k in ['star','stripe','number','arrow']:stamp(k)
