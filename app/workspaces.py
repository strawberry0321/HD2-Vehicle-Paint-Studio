"""Separate editable workbenches and versioned user-confirmed layout snapshots."""
from pathlib import Path
import copy,json,os,re,uuid,datetime,hashlib
from storage_paths import DATA_ROOT
ROOT=DATA_ROOT;STORE=ROOT/'workspaces'
def read(p,default=None):
 try:return json.loads(p.read_text(encoding='utf8'))
 except (OSError,ValueError):return default
def write(p,data):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.new');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8');os.replace(tmp,p)
def directory(key):
 if not re.fullmatch('[0-9a-f-]{36}',str(key)):raise ValueError('工作台编号无效')
 return STORE/key
def active_id():return read(STORE/'index.json',{}).get('active')
def activate(key):directory(key);write(STORE/'index.json',dict(active=key))
def list_all():
 result=[]
 if STORE.exists():
  for folder in STORE.iterdir():
   if folder.is_dir():
    meta=read(folder/'meta.json')
    if meta:result.append(meta)
 return sorted(result,key=lambda d:d.get('updated',''),reverse=True)
def ensure(session):
 key=session.get('workspaceId')
 if key and (directory(key)/'meta.json').exists():return key
 # Retain the exact pre-workspace recovery file, even if an old or damaged
 # project cannot be opened. A new workbench must never erase that source.
 prior=ROOT/'tmp/recovery.json'
 if not active_id() and prior.exists():
  raw=prior.read_bytes();backup=STORE/'migrated_sources'/('session_'+hashlib.sha256(raw).hexdigest()+'.json');backup.parent.mkdir(parents=True,exist_ok=True)
  if not backup.exists():backup.write_bytes(raw)
 key=str(uuid.uuid4());now=datetime.datetime.now(datetime.timezone.utc).isoformat();write(directory(key)/'meta.json',dict(id=key,name=session.get('state',{}).get('name','我的工作台'),updated=now));session=copy.deepcopy(session);session['workspaceId']=key;write(directory(key)/'autosave.json',session);write(directory(key)/'saved_layout.json',session.get('layout',{}));activate(key);return key
def autosave(session):
 key=ensure(session);session=copy.deepcopy(session);session['workspaceId']=key;write(directory(key)/'autosave.json',session);meta=read(directory(key)/'meta.json');meta['name']=session['state'].get('name','我的工作台');meta['updated']=datetime.datetime.now(datetime.timezone.utc).isoformat();write(directory(key)/'meta.json',meta);write(ROOT/'tmp/recovery.json',session);return dict(ok=True,workspaceId=key)
def save_layout(session):
 result=autosave(session);key=result['workspaceId'];base=directory(key)/'layouts';version=1
 while (base/f'V{version}').exists():version+=1
 target=base/f'V{version}';target.mkdir(parents=True);write(target/'session.json',session);write(directory(key)/'saved_layout.json',session.get('layout',{}));return result|dict(version=f'V{version}',path=str(target))
def restore(key=None):
 key=key or active_id()
 if key:
  folder=directory(key);session=read(folder/'autosave.json')
  if session:
   session['workspaceId']=key;layout=read(folder/'saved_layout.json')
   if layout:session['layout']=layout
   return session
 return read(ROOT/'tmp/recovery.json')
def open_workspace(key):
 session=restore(key)
 if not session:raise ValueError('工作台不存在')
 activate(key);return session
def new_workspace(payload):
 session=copy.deepcopy(payload['session']);session.pop('workspaceId',None);session['state']['name']=str(payload.get('name','新工作台')).strip()[:64] or '新工作台';key=ensure(session);session['workspaceId']=key;autosave(session);return session
