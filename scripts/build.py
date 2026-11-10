"""Build the Windows app from source and a clean release resource package."""
from pathlib import Path
import argparse,json,os,re,shutil,struct,subprocess,zipfile

ROOT=Path(__file__).resolve().parent.parent

def aligned(raw):return raw+bytes((-len(raw))%4)
def block(key,value=b'',children=(),typ=1):
    raw=aligned(struct.pack('<3H',0,len(value)//2 if typ==1 else len(value),typ)+(key+'\0').encode('utf-16le'))+value
    for child in children:raw=aligned(raw)+child
    return struct.pack('<H',len(raw))+raw[2:]
def resource(kind,name,data):return struct.pack('<II4HI2H2I',len(data),32,0xffff,kind,0xffff,name,0,0x1030,0x0804,0,0)+aligned(data)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    base=parser.add_mutually_exclusive_group(required=True)
    base.add_argument('--base-package',type=Path);base.add_argument('--base-directory',type=Path)
    parser.add_argument('--output',type=Path);parser.add_argument('--go',default=shutil.which('go'))
    parser.add_argument('--rebuild-reader',action='store_true');args=parser.parse_args()
    if os.name!='nt':parser.error('Build on Windows x64')
    if not args.go:parser.error('Install Go 1.25 or newer, or pass --go')
    version=json.loads((ROOT/'app/version.json').read_text(encoding='utf8'))
    output=(args.output or ROOT/'dist'/('VehiclePaintStudio-'+version['version'])).resolve()
    if output.exists():parser.error('Output already exists; choose a new directory')
    if args.base_directory:
        source=args.base_directory.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):parser.error('Base and output directories must be separate')
        shutil.copytree(source,output,ignore=shutil.ignore_patterns('src','mod','__pycache__','*.pyc','*.log'))
    else:
        output.mkdir(parents=True)
        with zipfile.ZipFile(args.base_package) as archive:
            for item in archive.infolist():
                target=(output/item.filename).resolve()
                if not target.is_relative_to(output):raise ValueError('Invalid package path')
                if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(item) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest,8<<20)
    for item in ['runtime/python.exe','app/desktop/Microsoft.Web.WebView2.Core.dll','app/engine/verify_mod.exe','app/data/catalog.json']:
        if not (output/item).is_file():raise ValueError('Incomplete base package: '+item)
    for source in (ROOT/'app').glob('*.py'):shutil.copy2(source,output/'app'/source.name)
    shutil.copy2(ROOT/'app/version.json',output/'app/version.json')
    shutil.copytree(ROOT/'app/web',output/'app/web',dirs_exist_ok=True)
    shutil.copytree(ROOT/'licenses',output/'licenses',dirs_exist_ok=True)
    for name in ('LICENSE','AUTHORSHIP.md','THIRD_PARTY.md'):shutil.copy2(ROOT/name,output/name)
    (output/'settings.json').write_text(json.dumps(dict(dataDirectory='../载具涂装数据'),ensure_ascii=False,indent=2),encoding='utf8')
    (output/'mod').mkdir(exist_ok=True)
    build=ROOT/'build'/('windows-'+version['version']);build.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,TEMP=str(build),TMP=str(build),GOCACHE=str(build/'go-cache'),GOTMPDIR=str(build),CGO_ENABLED='0')
    framework=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319'
    desktop=ROOT/'native/desktop';window=output/'app/desktop'
    manifest=build/'desktop.manifest'
    manifest.write_text(re.sub(r'(<assemblyIdentity version=")[^"]+',lambda m:m[1]+version['assembly_version'],(desktop/'desktop.manifest').read_text(encoding='utf8')),encoding='utf8')
    info=build/'VersionInfo.cs'
    info.write_text('using System.Reflection;\n'+'\n'.join('[assembly: '+key+'('+json.dumps(value,ensure_ascii=False)+')]' for key,value in [('AssemblyTitle',version['title']),('AssemblyVersion',version['assembly_version']),('AssemblyFileVersion',version['assembly_version'])])+'\ninternal static class AppInfo { public const string Title = '+json.dumps(version['title'],ensure_ascii=False)+'; }\n',encoding='utf8')
    subprocess.run([str(framework/'csc.exe'),'/nologo','/target:winexe','/platform:x64','/optimize+','/utf8output','/out:'+str(window/'VehicleStudioWindow.exe'),'/win32manifest:'+str(manifest),'/r:System.Windows.Forms.dll','/r:System.Drawing.dll','/r:System.Web.Extensions.dll','/r:'+str(window/'Microsoft.Web.WebView2.Core.dll'),'/r:'+str(window/'Microsoft.Web.WebView2.WinForms.dll'),str(desktop/'DesktopWindow.cs'),str(info)],check=True,env=env)
    launcher=build/'launcher';shutil.copytree(ROOT/'native/launcher',launcher,dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.syso'))
    (launcher/'version.go').write_text('package main\nconst displayTitle = '+json.dumps(version['title'],ensure_ascii=False)+'\n',encoding='utf8')
    n=[int(x) for x in version['assembly_version'].split('.')];ms=n[0]<<16|n[1];ls=n[2]<<16|n[3]
    fixed=struct.pack('<13I',0xfeef04bd,0x10000,ms,ls,ms,ls,0x3f,0,0x40004,1,0,0,0)
    strings={'FileDescription':version['title'],'ProductName':version['title'],'FileVersion':version['assembly_version'],'ProductVersion':version['version'],'OriginalFilename':'载具替换工具.exe'}
    info=block('StringFileInfo',children=[block('080404b0',children=[block(k,(value+'\0').encode('utf-16le')) for k,value in strings.items()])])
    translation=block('VarFileInfo',children=[block('Translation',struct.pack('<HH',0x0804,1200),typ=0)])
    icon=(ROOT/'docs/assets/app.ico').read_bytes();_,_,count=struct.unpack_from('<HHH',icon)
    res=bytearray(struct.pack('<II4HI2H2I',0,32,0xffff,0,0xffff,0,0,0,0,0,0));group=bytearray(struct.pack('<HHH',0,1,count))
    for i in range(count):
        w,h,c,z,planes,bits,size,offset=struct.unpack_from('<BBBBHHII',icon,6+i*16)
        res+=resource(3,i+1,icon[offset:offset+size]);group+=struct.pack('<BBBBHHIH',w,h,c,z,planes,bits,size,i+1)
    res+=resource(14,1,group);res+=resource(24,1,manifest.read_bytes());res+=resource(16,1,block('VS_VERSION_INFO',fixed,[info,translation],typ=0))
    (build/'launcher.res').write_bytes(res);syso=launcher/'resources_windows_amd64.syso'
    subprocess.run([str(framework/'cvtres.exe'),'/nologo','/machine:x64','/out:'+str(syso),str(build/'launcher.res')],check=True,env=env)
    raw=bytearray(syso.read_bytes());_,_,_,offset,count,_,_=struct.unpack_from('<HHIIIHH',raw);i=0
    while i<count:
        entry=offset+i*18
        if raw[entry:entry+8]==b'@comp.id' and struct.unpack_from('<h',raw,entry+12)[0]==-1:struct.pack_into('<h',raw,entry+12,-2)
        i+=1+raw[entry+17]
    syso.write_bytes(raw)
    subprocess.run([args.go,'build','-buildvcs=false','-trimpath','-ldflags','-H windowsgui -s -w','-o',str(output/'载具替换工具.exe'),'.'],cwd=launcher,env=env,check=True)
    if args.rebuild_reader:
        reader=build/'reader';shutil.copytree(ROOT/'native/reader',reader,dirs_exist_ok=True)
        module=reader/'go.mod';module.write_text(module.read_text(encoding='utf8').replace('../../third_party/filediver',(ROOT/'third_party/filediver').as_posix()),encoding='utf8')
        subprocess.run([args.go,'build','-mod=mod','-buildvcs=false','-trimpath','-ldflags','-s -w','-o',str(output/'app/engine/verify_mod.exe'),'.'],cwd=reader,env=env,check=True)
    print('Built',output)

if __name__=='__main__':main()
