package main
import("os";"path/filepath";"testing";"encoding/json";"reflect";"strings")
func TestDataDirectory(t *testing.T) {
 t.Setenv("HD2_VEHICLE_DATA_DIR","")
 root:=filepath.Join(t.TempDir(),"program");if e:=os.MkdirAll(root,0755);e!=nil{t.Fatal(e)}
 value,e:=dataDirectory(root);if e!=nil||value!=filepath.Join(filepath.Dir(root),"载具涂装数据"){t.Fatal(value,e)}
 write:=func(data any){raw,_:=json.Marshal(data);if e:=os.WriteFile(filepath.Join(root,"settings.json"),raw,0644);e!=nil{t.Fatal(e)}}
 write(map[string]string{"dataDirectory":"../user-data"});value,e=dataDirectory(root);if e!=nil||value!=filepath.Join(filepath.Dir(root),"user-data"){t.Fatal(value,e)}
 override:=filepath.Join(filepath.Dir(root),"override");t.Setenv("HD2_VEHICLE_DATA_DIR",override);value,e=dataDirectory(root);if e!=nil||value!=override{t.Fatal(value,e)}
 t.Setenv("HD2_VEHICLE_DATA_DIR","");write(map[string]string{"dataDirectory":"nested/data"});if _,e=dataDirectory(root);e==nil{t.Fatal("Accepted data inside program")}
 write(map[string]string{"dataDirectory":"."});if _,e=dataDirectory(root);e==nil{t.Fatal("Accepted program root")}
 if e=os.WriteFile(filepath.Join(root,"settings.json"),[]byte("broken JSON"),0644);e!=nil{t.Fatal(e)};if _,e=dataDirectory(root);e==nil{t.Fatal("Accepted malformed config")}
}
func TestExeLaunchNeedsNoBatch(t *testing.T) {
 root:=filepath.Join(t.TempDir(),"program");data:=filepath.Join(filepath.Dir(root),"data")
 t.Setenv("TEMP","old-temp");t.Setenv("TMP","old-tmp");t.Setenv("HD2_VEHICLE_DATA_DIR","old-data")
 cmd:=launchCommand(root,data,[]string{"--capture"})
 expected:=[]string{filepath.Join(root,"runtime","pythonw.exe"),"-B","-X","utf8",filepath.Join(root,"app","server.py"),"--capture"}
 if !reflect.DeepEqual(cmd.Args,expected)||cmd.Dir!=root{t.Fatal(cmd.Args,cmd.Dir)}
 values:=map[string]string{};counts:=map[string]int{};for _,row:=range cmd.Env{key,value,_:=strings.Cut(row,"=");key=strings.ToUpper(key);values[key]=value;counts[key]++}
 for key,value:=range map[string]string{"TEMP":filepath.Join(data,"tmp"),"TMP":filepath.Join(data,"tmp"),"HD2_VEHICLE_DATA_DIR":data,"PYTHONDONTWRITEBYTECODE":"1"}{if counts[key]!=1||values[key]!=value{t.Fatal(key,counts[key],values[key])}}
 if cmd.SysProcAttr==nil||!cmd.SysProcAttr.HideWindow{t.Fatal("Console would be visible")}
}
