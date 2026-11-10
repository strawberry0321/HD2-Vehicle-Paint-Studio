package main

import (
 "encoding/json"
 "fmt"
 "os"
 "os/exec"
 "path/filepath"
 "strings"
 "syscall"
 "unsafe"
)

func dataDirectory(root string) (string,error) {
 value:=os.Getenv("HD2_VEHICLE_DATA_DIR")
 if value=="" {
  raw,err:=os.ReadFile(filepath.Join(root,"settings.json"))
  if err==nil {
   var config struct {DataDirectory string `json:"dataDirectory"`}
   if err=json.Unmarshal(raw,&config);err!=nil{return "",err}
   value=config.DataDirectory
  } else if !os.IsNotExist(err) {return "",err}
 }
 if value=="" {value=filepath.Join("..","载具涂装数据")}
 if !filepath.IsAbs(value) {value=filepath.Join(root,value)}
 value,err:=filepath.Abs(value);if err!=nil{return "",err}
 if !strings.EqualFold(filepath.VolumeName(root),filepath.VolumeName(value)) {return value,nil}
 rel,err:=filepath.Rel(root,value);if err!=nil{return "",err}
 if rel=="." || (rel!=".." && !strings.HasPrefix(rel,".."+string(filepath.Separator))) {return "",fmt.Errorf("数据目录需要位于程序目录之外")}
 return value,nil
}

func launchCommand(root,data string,args []string) *exec.Cmd {
 python:=filepath.Join(root,"runtime","pythonw.exe");server:=filepath.Join(root,"app","server.py")
 cmd:=exec.Command(python,append([]string{"-B","-X","utf8",server},args...)...);cmd.Dir=root
 temp:=filepath.Join(data,"tmp")
 for _,v:=range os.Environ(){u:=strings.ToUpper(v);if !strings.HasPrefix(u,"TEMP=")&&!strings.HasPrefix(u,"TMP=")&&!strings.HasPrefix(u,"PYTHONDONTWRITEBYTECODE=")&&!strings.HasPrefix(u,"HD2_VEHICLE_DATA_DIR="){cmd.Env=append(cmd.Env,v)}}
 cmd.Env=append(cmd.Env,"TEMP="+temp,"TMP="+temp,"PYTHONDONTWRITEBYTECODE=1","HD2_VEHICLE_DATA_DIR="+data)
 cmd.SysProcAttr=&syscall.SysProcAttr{HideWindow:true,CreationFlags:0x08000000}
 return cmd
}

func warn(text string){
 p,_:=syscall.UTF16PtrFromString(text);title,_:=syscall.UTF16PtrFromString(displayTitle)
 syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0,uintptr(unsafe.Pointer(p)),uintptr(unsafe.Pointer(title)),0x10)
}
func main(){
 self,e:=os.Executable();if e!=nil{warn(e.Error());return};root:=filepath.Dir(self)
 python:=filepath.Join(root,"runtime","pythonw.exe");server:=filepath.Join(root,"app","server.py")
 for _,file:=range []string{python,server,filepath.Join(root,"app","desktop","VehicleStudioWindow.exe")} {
  if _,e=os.Stat(file);e!=nil{warn("请完整解压软件包，保持 app、runtime 和本程序在一起。");return}
 }
 data,e:=dataDirectory(root);if e!=nil{warn("无法读取数据目录设置：\n"+e.Error());return}
 temp:=filepath.Join(data,"tmp");if e=os.MkdirAll(temp,0755);e!=nil{warn("无法创建数据目录：\n"+e.Error());return}
 cmd:=launchCommand(root,data,os.Args[1:])
 if e=cmd.Start();e!=nil{warn("无法启动内置运行时：\n"+e.Error())}
}
