"""Independent export process; the editor keeps accepting saves and cancellation."""
from pathlib import Path
import json,os,sys,threading,time
sys.dont_write_bytecode=True

def write(path,value):
    pending=path.with_suffix('.new')
    with pending.open('w',encoding='utf8') as f:
        json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(pending,path)

def parent_watch():
    parent=int(os.environ.get('HD2_EXPORT_PARENT','0'))
    if not parent:return
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        handle=kernel.OpenProcess(0x00100000,False,parent)
        if not handle:Path(os.environ['HD2_EXPORT_CANCEL']).touch();return
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.WaitForSingleObject(handle,0xffffffff);kernel.CloseHandle(handle)
        Path(os.environ['HD2_EXPORT_CANCEL']).touch()
    else:
        while os.getppid()==parent:time.sleep(.5)
        Path(os.environ['HD2_EXPORT_CANCEL']).touch()

def main():
    folder=Path(sys.argv[1]);threading.Thread(target=parent_watch,daemon=True).start()
    import face_builder as builder
    data=json.loads((folder/'input.json').read_text(encoding='utf8'))
    try:result=dict(ok=True,result=builder.export_mod(data))
    except Exception as error:
        result=dict(ok=False,error=str(error),code=getattr(error,'code',None))
    write(folder/'result.json',result)

if __name__=='__main__':main()
