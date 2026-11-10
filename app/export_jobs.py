"""Durable pre-export checkpoints and a cancellable, supervised export process."""
from pathlib import Path
import atexit,copy,json,os,re,subprocess,sys,threading,time,uuid,zipfile
from storage_paths import DATA_ROOT

APP=Path(__file__).resolve().parent
ROOT=DATA_ROOT/'tmp/export_jobs'
_lock=threading.RLock()
_jobs={}
_current=None

def read(path,default=None):
    try:return json.loads(path.read_text(encoding='utf8'))
    except (OSError,ValueError):return default

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);pending=path.with_name(path.name+'.'+uuid.uuid4().hex+'.new')
    with pending.open('w',encoding='utf8') as f:
        json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(pending,path)

class ExportBusy(ValueError):
    code='busy'

class ProcessTree:
    """Close/terminate only the worker process tree created by this job."""
    def __init__(self,process):
        self.process=process;self.handle=None
        if os.name!='nt':return
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',wintypes.DWORD),('MinWorking',ctypes.c_size_t),('MaxWorking',ctypes.c_size_t),('Active',wintypes.DWORD),('Affinity',ctypes.c_size_t),('Priority',wintypes.DWORD),('Scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):_fields_=[('x'+str(i),ctypes.c_uint64) for i in range(6)]
        class Extended(ctypes.Structure):
            _fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.CreateJobObjectW(None,None);info=Extended();info.Basic.Flags=0x2000
        if handle and kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) and kernel.AssignProcessToJobObject(handle,int(process._handle)):
            self.handle=handle;self.kernel=kernel
        elif handle:kernel.CloseHandle(handle)

    def stop(self):
        if self.process.poll() is not None:return
        if self.handle:self.kernel.TerminateJobObject(self.handle,130)
        elif os.name=='nt':
            subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else:self.process.terminate()

    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

def checkpoint(payload):
    import face_builder as builder
    result=builder.save_project(dict(state=copy.deepcopy(payload['state']),preview=payload['preview']))
    folder=Path(result['path']);archive=folder/'导出前自动备份.hd2project';pending=archive.with_suffix('.pending')
    with pending.open('xb') as target:
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            z.write(folder/'project.json','__project/project.json');z.write(folder/'preview.png','preview.png')
            for p in sorted((folder/'source').rglob('*')):
                if p.is_file():z.write(p,'__project/'+p.relative_to(folder).as_posix())
        target.flush();os.fsync(target.fileno())
    os.replace(pending,archive)
    return str(archive)

def start(payload):
    global _current
    token=str(payload.get('exportToken',''))
    if not re.fullmatch('[0-9a-zA-Z_-]{1,80}',token):raise ValueError('导出任务编号无效')
    with _lock:
        if _current and _jobs[_current]['process'].poll() is None:raise ExportBusy('已有导出任务正在运行，请先等待完成或取消。')
        backup=checkpoint(payload);folder=ROOT/uuid.uuid4().hex;folder.mkdir(parents=True)
        write(folder/'input.json',payload)
        info=dict(token=token,backupPath=backup,started=time.time(),active=True)
        write(folder/'job.json',info);write(folder/'progress.json',dict(token=token,active=True,message='工程已备份，正在准备导出…',phase='prepare',completed=0,total=0,seconds=0))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',HD2_EXPORT_PARENT=str(os.getpid()),HD2_EXPORT_STATUS=str(folder/'progress.json'),HD2_EXPORT_CANCEL=str(folder/'cancel'))
        python=Path(sys.executable).with_name('python.exe') if os.name=='nt' else Path(sys.executable)
        if not python.exists():python=Path(sys.executable)
        process=subprocess.Popen([str(python),str(APP/'export_worker.py'),str(folder)],env=env,cwd=APP,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        tree=ProcessTree(process);info['pid']=process.pid;info['process_tree_supervised']=bool(tree.handle);write(folder/'job.json',info)
        job=dict(folder=folder,process=process,tree=tree,info=info);_jobs[token]=job;_current=token
        threading.Thread(target=_watch,args=(job,),daemon=True,name='ExportSupervisor').start()
        return dict(job=folder.name,exportToken=token,backupPath=backup)

def _final(job):
    folder=job['folder'];result=read(folder/'result.json')
    if result is None:
        cancelled=(folder/'cancel').exists()
        result=dict(ok=False,error='已取消导出，工程备份和已完成的贴图缓存均已保留。' if cancelled else '导出进程已中断，工程备份已保留，可直接重试。',code='cancelled' if cancelled else 'interrupted')
        write(folder/'result.json',result)
    return result

def _watch(job):
    job['process'].wait()
    with _lock:
        _final(job);job['tree'].close()

def snapshot(token=None):
    with _lock:
        token=token or _current;job=_jobs.get(token)
        if not job:return dict(active=False,token=token or '',message='',completed=0,total=0,seconds=0)
        data=read(job['folder']/'progress.json',{})
        active=job['process'].poll() is None
        result=None if active else _final(job)
        return dict(data,token=token,active=active,backupPath=job['info']['backupPath'],result=result,seconds=round(time.time()-job['info']['started'],1))

def cancel(payload):
    token=str(payload.get('token',''))
    with _lock:
        job=_jobs.get(token)
        if not job:raise ValueError('导出任务不存在')
        if job['process'].poll() is None:
            (job['folder']/'cancel').touch()
            def stop_later():
                try:job['process'].wait(timeout=2)
                except subprocess.TimeoutExpired:
                    with _lock:job['tree'].stop()
            threading.Thread(target=stop_later,daemon=True,name='ExportCancellation').start()
        return dict(ok=True,backupPath=job['info']['backupPath'])

def previous():
    folders=sorted(ROOT.glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not folders:return None
    path=folders[0];info=read(path,{})
    result=read(path.parent/'result.json')
    if result and result.get('ok'):return None
    return dict(backupPath=info.get('backupPath'),message='上次导出未完成；已保留导出前工程，可重新打开后继续制作。')

def shutdown():
    with _lock:
        for job in _jobs.values():
            if job['process'].poll() is None:
                (job['folder']/'cancel').touch();job['tree'].stop()
            job['tree'].close()

atexit.register(shutdown)
