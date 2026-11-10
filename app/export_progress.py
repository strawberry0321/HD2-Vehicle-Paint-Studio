"""Progress and cooperative cancellation, including independent export workers."""
from pathlib import Path
import json,os,subprocess,threading,time,uuid

_lock=threading.RLock()
_state=dict(active=False,token='',message='',completed=0,total=0,phase='',cancelled=False)
_started=0.0
_phase_started=0.0
_history=[]
_heartbeat=None
_cleanup=[]
_processes=set()

class ExportCancelled(Exception):
    code='cancelled'

def check_cancelled():
    path=os.environ.get('HD2_EXPORT_CANCEL')
    if path and Path(path).exists():
        with _lock:_state['cancelled']=True
        raise ExportCancelled('已取消导出，工程备份和已完成的贴图缓存均已保留。')

def _snapshot():
    return dict(_state,nativeProcesses=sorted(_processes),seconds=round(time.monotonic()-_started,1) if _started else 0,
                stageSeconds=round(time.monotonic()-_phase_started,1) if _phase_started else 0)

def _publish():
    path=os.environ.get('HD2_EXPORT_STATUS')
    if not path:return
    target=Path(path);pending=target.with_name(target.name+'.'+uuid.uuid4().hex+'.new')
    pending.write_text(json.dumps(_snapshot(),ensure_ascii=False),encoding='utf8');os.replace(pending,target)

def _pulse():
    while True:
        time.sleep(.5)
        with _lock:
            if not _state['active']:return
            _publish()

def begin(token):
    global _started,_phase_started,_history,_heartbeat,_cleanup
    with _lock:
        _started=_phase_started=time.monotonic();_history=[];_cleanup=[]
        _state.update(active=True,token=token,message='正在准备导出…',completed=0,total=0,error='',phase='prepare',cancelled=False);_publish()
        if os.environ.get('HD2_EXPORT_STATUS'):
            _heartbeat=threading.Thread(target=_pulse,daemon=True,name='ExportHeartbeat');_heartbeat.start()

def update(message,**values):
    global _phase_started
    check_cancelled()
    with _lock:
        if message!=_state['message']:
            _history.append(dict(message=_state['message'],seconds=round(time.monotonic()-_phase_started,3)));_phase_started=time.monotonic()
        _state.update(message=message,**values);_publish()

def texture_done():
    check_cancelled()
    with _lock:_state['completed']+=1;_publish()

def finish(error=''):
    for callback in list(_cleanup):callback(error)
    with _lock:
        _history.append(dict(message=_state['message'],seconds=round(time.monotonic()-_phase_started,3)))
        _state.update(active=False,error=error,message='导出已取消' if _state['cancelled'] else '导出未完成' if error else '导出完成');_publish()

def snapshot():
    with _lock:return _snapshot()

def timings():
    with _lock:return list(_history)+[dict(message=_state['message'],seconds=round(time.monotonic()-_phase_started,3))]

def on_finish(callback):
    with _lock:_cleanup.append(callback)

def run(args,cancel_check=None,**kwargs):
    """Keep native codecs/verifiers cancellable without blocking the workbench."""
    check_cancelled()
    if cancel_check:cancel_check()
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),**kwargs)
    with _lock:_processes.add(process.pid);_publish()
    try:
        while True:
            try:
                stdout,stderr=process.communicate(timeout=.15);check_cancelled()
                if cancel_check:cancel_check()
                return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
            except subprocess.TimeoutExpired:
                check_cancelled()
                if cancel_check:cancel_check()
    except BaseException:
        process.terminate()
        try:process.communicate(timeout=2)
        except subprocess.TimeoutExpired:process.kill();process.communicate()
        raise
    finally:
        with _lock:_processes.discard(process.pid);_publish()
