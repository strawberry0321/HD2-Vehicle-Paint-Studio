from pathlib import Path
import argparse, ctypes, hashlib, http.server, json, mimetypes, os, re, secrets, subprocess, threading, time, urllib.parse, urllib.request
import face_builder as builder
import workspaces
from app_version import VERSION
from storage_paths import DATA_ROOT
import export_destination
import export_jobs
APP=Path(__file__).resolve().parent
ROOT=DATA_ROOT
WEB=APP/'web'
HOST='127.0.0.1'
TOKEN=secrets.token_urlsafe(32)
LAST_ACTIVE=time.monotonic()
mimetypes.add_type('text/javascript','.js')

def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.new');temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8');os.replace(temp,path)

def local_request(url,path,post=False):
    import http.client
    u=urllib.parse.urlparse(url);connection=http.client.HTTPConnection(u.hostname,u.port,timeout=2)
    try:
        connection.request('POST' if post else 'GET',path+'?'+u.query,body=b'{}' if post else None,headers={'Content-Type':'application/json'})
        response=connection.getresponse();data=response.read()
        if response.status>=400:raise RuntimeError('Local request failed: '+str(response.status))
        return json.loads(data)
    finally:connection.close()

def existing_session():
    try:
        s=json.loads((ROOT/'tmp/session.json').read_text(encoding='utf8'))
        if urllib.parse.urlparse(s['url']).hostname!=HOST:return None
        result=local_request(s['url'],'/api/health')
        if result.get('version')==VERSION and not result.get('closing'):return s
    except Exception:pass
    return None

class Desktop:
    def __init__(self,server,args,url):self.server=server;self.args=args;self.url=url;self.process=None;self.lock=threading.Lock()
    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:return self.focus()
            host=APP/'desktop/VehicleStudioWindow.exe'
            if not host.is_file():raise RuntimeError('缺少桌面窗口组件，请完整解压软件包')
            args=[str(host),self.url]
            if self.args.capture:args.append('--capture')
            if self.args.desktop_debug_port:args.append('--debug-port='+str(self.args.desktop_debug_port))
            self.process=subprocess.Popen(args,cwd=host.parent,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            write_json(ROOT/'tmp/desktop_process.json',dict(pid=self.process.pid,server_pid=os.getpid()))
            threading.Thread(target=self._watch,args=(self.process,),daemon=True).start()
            return dict(opened=True,pid=self.process.pid)
    def _watch(self,process):
        process.wait()
        if self.process is process:self.server.closing=True;self.server.shutdown()
    def focus(self):
        if self.server.closing:raise RuntimeError('窗口正在关闭，请稍后重新打开')
        if not self.process or self.process.poll() is not None:return self.start()
        from ctypes import wintypes
        name='Local\\HD2VehicleStudio_'+hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:24].upper()
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenEventW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR];kernel.OpenEventW.restype=wintypes.HANDLE
        kernel.SetEvent.argtypes=[wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenEventW(2,False,name)
        if handle:kernel.SetEvent(handle);kernel.CloseHandle(handle)
        return dict(opened=True,reused=True,pid=self.process.pid)

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,fmt,*args):pass
    def send(self,status,data,content='application/json; charset=utf-8',cookie=False):
        self.send_response(status)
        for name,value in [('Content-Type',content),('Content-Length',str(len(data))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer')]:self.send_header(name,value)
        if cookie:self.send_header('Set-Cookie',f'{self.server.cookie_name}={TOKEN}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        try:self.wfile.write(data);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass
    def auth(self):
        global LAST_ACTIVE
        if self.headers.get('Host','')!=f'{HOST}:{self.server.server_port}':return False
        q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        cookies=dict(pair.strip().split('=',1) for pair in self.headers.get('Cookie','').split(';') if '=' in pair)
        ok=secrets.compare_digest(q.get('token',[''])[0],TOKEN) or secrets.compare_digest(cookies.get(self.server.cookie_name,''),TOKEN)
        if ok:LAST_ACTIVE=time.monotonic()
        return ok
    def do_GET(self):
        if not self.auth():self.send(403,b'Local session required','text/plain');return
        p=urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if p=='/':self.send(200,(WEB/'index.html').read_bytes(),'text/html; charset=utf-8',True);return
        if p=='/api/health':self.send(200,json.dumps(dict(ok=True,version=VERSION,pid=os.getpid(),desktop_pid=self.server.desktop.process.pid if self.server.desktop.process else None,closing=self.server.closing or bool(self.server.desktop.process and self.server.desktop.process.poll() is not None))).encode());return
        if p=='/api/workspaces':self.send(200,json.dumps(dict(items=workspaces.list_all(),active=workspaces.active_id()),ensure_ascii=False).encode());return
        if p=='/api/library':self.send(200,json.dumps(builder.library.list_assets(),ensure_ascii=False).encode());return
        if p=='/api/export-settings':self.send(200,json.dumps(dict(defaultDirectory=str(export_destination.DEFAULT_DIRECTORY)),ensure_ascii=False).encode());return
        if p=='/api/export-progress':
            q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self.send(200,json.dumps(export_jobs.snapshot(q.get('exportToken',[None])[0]),ensure_ascii=False).encode());return
        if p=='/api/export-last':self.send(200,json.dumps(export_jobs.previous(),ensure_ascii=False).encode());return
        if p=='/api/recovery':
            self.send(200,json.dumps(workspaces.restore(),ensure_ascii=False).encode());return
        if p.startswith('/library/'):
            pieces=p.split('/');variant=pieces[3] if len(pieces)>3 else 'original';f=builder.library.path_for(pieces[2],'photo' if variant=='original-thumb' else variant=='thumb');self.send(200,f.read_bytes(),mimetypes.guess_type(f)[0] or 'image/png');return
        if p=='/api/materials':
            result={}
            for f in (APP/'data/models').glob('*.json'):
                for part in json.loads(f.read_text(encoding='utf8'))['parts']:
                    if part.get('group'):result[part['material']]={k:part[k] for k in ['material','group','color','normal','mra']}
            self.send(200,json.dumps(result).encode());return
        if p.startswith('/download/'):
            f=(ROOT/p[len('/download/'):]).resolve()
            if not f.is_relative_to(ROOT/'mods') or f.suffix.lower()!='.zip' or not f.is_file():self.send(404,b'Not found','text/plain');return
            self.send(200,f.read_bytes(),'application/zip');return
        base=APP/'data' if p.startswith('/data/') else WEB
        f=(base/(p[6:] if p.startswith('/data/') else p[1:])).resolve()
        if not f.is_relative_to(base) or not f.is_file():self.send(404,b'Not found','text/plain');return
        self.send(200,f.read_bytes(),mimetypes.guess_type(f)[0] or 'application/octet-stream')
    def do_POST(self):
        if not self.auth():self.send(403,b'Forbidden','text/plain');return
        if self.headers.get('Origin') not in [None,f'http://{HOST}:{self.server.server_port}']:self.send(403,b'Forbidden origin','text/plain');return
        path=urllib.parse.urlparse(self.path).path
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=512*1024*1024:raise ValueError('工程过大，请减少贴花分辨率')
            data=json.loads(self.rfile.read(length))
            if path=='/api/focus':result=self.server.desktop.focus()
            elif path=='/api/quit':self.server.closing=True;result={'ok':True};threading.Thread(target=self.server.shutdown,daemon=True).start()
            elif path=='/api/export':result=export_jobs.start(data)
            elif path=='/api/export-cancel':result=export_jobs.cancel(data)
            else:
                with builder.LOCK:
                    if path=='/api/validate':builder.state_ok(data['state']);result={'ok':True}
                    elif path=='/api/import':result=builder.import_image(data)
                    elif path=='/api/library/remove':result=builder.library.remove_asset(data)
                    elif path=='/api/reuse-area':
                        import uv_reuse
                        result=uv_reuse.create(data)
                    elif path=='/api/stamp':result=builder.library.stamp('text',data.get('text','01'))
                    elif path=='/api/open':result=builder.open_project(data)
                    elif path=='/api/save':result=builder.save_project(data)
                    elif path=='/api/autosave':builder.state_ok(data['state']);result=workspaces.autosave(data)
                    elif path=='/api/workspace/ensure':result={'workspaceId':workspaces.ensure(data)}
                    elif path=='/api/workspace/save-layout':builder.state_ok(data['state']);result=workspaces.save_layout(data)
                    elif path=='/api/workspace/open':result=workspaces.open_workspace(data['id'])
                    elif path=='/api/workspace/new':builder.state_ok(data['session']['state']);result=workspaces.new_workspace(data)
                    elif path=='/api/export-plan':result=builder.export_plan(data)
                    elif path=='/api/export-destination':result=export_destination.inspect(data)
                    else:raise ValueError('未知操作')
            self.send(200,json.dumps(result,ensure_ascii=False).encode())
        except Exception as e:self.send(400,json.dumps({'error':str(e),'code':getattr(e,'code',None)},ensure_ascii=False).encode())

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=0);parser.add_argument('--capture',action='store_true');parser.add_argument('--desktop-debug-port',type=int,default=0);args=parser.parse_args()
    (ROOT/'tmp').mkdir(parents=True,exist_ok=True)
    existing=existing_session()
    if existing:
        if not args.no_browser:local_request(existing['url'],'/api/focus',True)
        return
    mutex=None
    if os.name=='nt':
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
        mutex=kernel.CreateMutexW(None,True,'Local\\HD2VehicleService_'+hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:24])
        if ctypes.get_last_error()==183:
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            for _ in range(30):
                existing=existing_session()
                if existing:
                    if not args.no_browser:local_request(existing['url'],'/api/focus',True)
                    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(mutex)
                    return
                if kernel.WaitForSingleObject(mutex,100) in [0,128]:break
            else:raise RuntimeError('工作台正在启动，请稍后重试')
    server=http.server.ThreadingHTTPServer((HOST,args.port),Handler);server.daemon_threads=True;server.closing=False;server.cookie_name='vehicle_session_'+str(server.server_port)
    url=f'http://{HOST}:{server.server_port}/?token={TOKEN}';server.desktop=Desktop(server,args,url)
    write_json(ROOT/'tmp/session.json',dict(url=url,pid=os.getpid(),port=server.server_port,version=VERSION))
    serving=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.25});serving.start()
    try:
        if not args.no_browser:server.desktop.start()
        while serving.is_alive():
            serving.join(.5)
            if not server.desktop.process and time.monotonic()-LAST_ACTIVE>600:server.shutdown()
    finally:
        export_jobs.shutdown()
        server.shutdown();server.server_close()
        if mutex:kernel.ReleaseMutex.argtypes=[wintypes.HANDLE];kernel.ReleaseMutex(mutex);kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(mutex)
