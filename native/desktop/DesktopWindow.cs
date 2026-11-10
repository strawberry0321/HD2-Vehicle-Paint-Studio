using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class StudioWindow : Form {
    WebView2 web;
    Panel overlay;
    Label message;
    Button retry;
    System.Windows.Forms.Timer timer;
    DateTime started;
    bool polling, ready, captured;
    string url, root, install, profile;
    bool capture, allowClose, closeDialog;
    int debugPort;
    EventWaitHandle focusEvent;
    JavaScriptSerializer json = new JavaScriptSerializer();

    public StudioWindow(string address, bool takeCapture, int port) {
        url=address;capture=takeCapture;debugPort=port;
        Uri uri=new Uri(url);
        if(uri.Scheme!="http" || uri.Host!="127.0.0.1") throw new Exception("工作台地址无效");
        install=Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"..",".."));
        string data=Environment.GetEnvironmentVariable("HD2_VEHICLE_DATA_DIR");
        if(String.IsNullOrEmpty(data)) {
            string settings=Path.Combine(install,"settings.json");
            if(File.Exists(settings)) {
                var config=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(settings));
                if(config.ContainsKey("dataDirectory"))data=Convert.ToString(config["dataDirectory"]);
            }
            if(String.IsNullOrEmpty(data))data=Path.Combine("..","载具涂装数据");
        }
        root=Path.GetFullPath(Path.IsPathRooted(data)?data:Path.Combine(install,data));
        if(root.Equals(install,StringComparison.OrdinalIgnoreCase)||root.StartsWith(install+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new Exception("数据目录需要位于程序目录之外");
        Directory.CreateDirectory(Path.Combine(root,"tmp"));
        profile=Path.Combine(root,"tmp","webview-profile");
        Text=AppInfo.Title;BackColor=Color.FromArgb(243,242,239);
        AutoScaleMode=AutoScaleMode.Dpi;
        Rectangle screen=Screen.PrimaryScreen.WorkingArea;
        float dpiScale;using(var graphics=CreateGraphics()){dpiScale=graphics.DpiX/96f;}
        ClientSize=new Size(Math.Min((int)(1500*dpiScale),screen.Width-70),Math.Min((int)(950*dpiScale),screen.Height-100));
        MinimumSize=new Size(Math.Min((int)(1120*dpiScale),screen.Width-40),Math.Min((int)(740*dpiScale),screen.Height-40));StartPosition=FormStartPosition.CenterScreen;
        RestorePlacement();
        string icon=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"studio.ico");if(File.Exists(icon))Icon=new Icon(icon);
        web=new WebView2();web.Dock=DockStyle.Fill;web.DefaultBackgroundColor=BackColor;Controls.Add(web);
        overlay=new Panel();overlay.BackColor=BackColor;overlay.Dock=DockStyle.Fill;
        var box=new TableLayoutPanel();box.AutoSize=true;box.ColumnCount=1;box.RowCount=3;box.Anchor=AnchorStyles.None;
        var title=new Label();title.Text=AppInfo.Title;title.Font=new Font("Microsoft YaHei UI",22,FontStyle.Regular);title.ForeColor=Color.FromArgb(80,87,98);title.AutoSize=true;title.Margin=new Padding(0,0,0,20);
        message=new Label();message.Text="正在准备 3D 工作台，请稍候…";message.Font=new Font("Microsoft YaHei UI",11);message.ForeColor=Color.FromArgb(114,120,131);message.MaximumSize=new Size(650,300);message.AutoSize=true;message.Margin=new Padding(0,0,0,18);
        retry=new Button();retry.Text="重新加载";retry.AutoSize=true;retry.FlatStyle=FlatStyle.Flat;retry.BackColor=Color.FromArgb(230,235,242);retry.ForeColor=Color.FromArgb(87,101,121);retry.Padding=new Padding(14,6,14,6);retry.Visible=false;retry.Click+=async delegate{await StartView();};
        box.Controls.Add(title,0,0);box.Controls.Add(message,0,1);box.Controls.Add(retry,0,2);overlay.Controls.Add(box);overlay.Resize+=delegate{box.Left=Math.Max(20,(overlay.Width-box.Width)/2);box.Top=Math.Max(20,(overlay.Height-box.Height)/2);};
        Controls.Add(overlay);overlay.BringToFront();
        timer=new System.Windows.Forms.Timer();timer.Interval=350;timer.Tick+=async delegate{await PollReady();};
        focusEvent=new EventWaitHandle(false,EventResetMode.AutoReset,FocusEventName(root));
        var thread=new Thread(delegate(){while(true){focusEvent.WaitOne();try{BeginInvoke((Action)delegate{if(WindowState==FormWindowState.Minimized)WindowState=FormWindowState.Normal;Show();Activate();BringToFront();});}catch{return;}}});thread.IsBackground=true;thread.Start();
        Shown+=async delegate{await StartView();};FormClosing+=async delegate(object sender,FormClosingEventArgs e){if(allowClose||!ready||web.CoreWebView2==null)return;e.Cancel=true;if(closeDialog)return;closeDialog=true;try{await web.CoreWebView2.ExecuteScriptAsync("window.studio?.requestClose()");}catch{allowClose=true;Close();}finally{closeDialog=false;}};FormClosed+=delegate{timer.Stop();};
    }
    static string FocusEventName(string path) {using(var h=SHA256.Create()){return "Local\\HD2VehicleStudio_"+BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(path.ToLowerInvariant()))).Replace("-","").Substring(0,24);}}
    void SavePlacement(){try{var bounds=WindowState==FormWindowState.Normal?Bounds:RestoreBounds;var folder=Path.Combine(root,"workspaces");Directory.CreateDirectory(folder);var data=new Dictionary<string,object>{{"x",bounds.X},{"y",bounds.Y},{"width",bounds.Width},{"height",bounds.Height},{"maximized",WindowState==FormWindowState.Maximized}};File.WriteAllText(Path.Combine(folder,"window_placement.json"),json.Serialize(data),new UTF8Encoding(false));}catch{}}
    void RestorePlacement(){try{var path=Path.Combine(root,"workspaces","window_placement.json");if(!File.Exists(path))return;var d=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));var desired=new Rectangle(Convert.ToInt32(d["x"]),Convert.ToInt32(d["y"]),Convert.ToInt32(d["width"]),Convert.ToInt32(d["height"]));var area=Screen.FromRectangle(desired).WorkingArea;desired.Width=Math.Max(MinimumSize.Width,Math.Min(desired.Width,area.Width));desired.Height=Math.Max(MinimumSize.Height,Math.Min(desired.Height,area.Height));desired.X=Math.Max(area.Left,Math.Min(desired.X,area.Right-desired.Width));desired.Y=Math.Max(area.Top,Math.Min(desired.Y,area.Bottom-desired.Height));StartPosition=FormStartPosition.Manual;Bounds=desired;if(Convert.ToBoolean(d["maximized"]))WindowState=FormWindowState.Maximized;}catch{}}
    void Fail(string text){ready=false;timer.Stop();message.Text="工作台未能载入。\r\n\r\n"+text+"\r\n\r\n请确认软件已完整解压，再点击重新加载。";retry.Visible=true;overlay.Visible=true;overlay.BringToFront();}
    void HandleWebMessage(object sender,CoreWebView2WebMessageReceivedEventArgs e){
        string command=null;try{command=e.TryGetWebMessageAsString();}catch{}
        if(command!=null){
            if(command=="studio-save-layout")BeginInvoke((Action)delegate{SavePlacement();});
            else if(command=="studio-close-approved")BeginInvoke((Action)delegate{allowClose=true;Close();});
            else if(command=="studio-close")BeginInvoke((Action)delegate{Close();});
            return;
        }
        string requestId=null;
        try{
            Uri origin;if(!Uri.TryCreate(e.Source,UriKind.Absolute,out origin)||origin.GetLeftPart(UriPartial.Authority)!=new Uri(url).GetLeftPart(UriPartial.Authority))return;
            var data=json.Deserialize<Dictionary<string,object>>(e.WebMessageAsJson);
            if(data==null||!data.ContainsKey("type"))return;
            if(Convert.ToString(data["type"])=="open-author-link"){
                string target=data.ContainsKey("url")?Convert.ToString(data["url"]):"";
                if(target=="https://space.bilibili.com/437268141"||target=="https://github.com/strawberry0321/HD2-Vehicle-Paint-Studio")Process.Start(new ProcessStartInfo(target){UseShellExecute=true});
                return;
            }
            if(Convert.ToString(data["type"])!="choose-export-directory")return;
            Guid id;if(!data.ContainsKey("requestId")||!Guid.TryParse(Convert.ToString(data["requestId"]),out id))return;
            requestId=id.ToString();string initial=data.ContainsKey("directory")?Convert.ToString(data["directory"]):"";
            var result=new Dictionary<string,object>{{"type","export-directory-selected"},{"requestId",requestId},{"cancelled",true}};
            using(var dialog=new FolderBrowserDialog()){
                dialog.Description="选择 MOD 压缩包保存文件夹";dialog.ShowNewFolderButton=true;
                dialog.SelectedPath=Directory.Exists(initial)?initial:Path.Combine(install,"mod");
                if(dialog.ShowDialog(this)==DialogResult.OK){result["cancelled"]=false;result["directory"]=dialog.SelectedPath;}
            }
            web.CoreWebView2.PostWebMessageAsJson(json.Serialize(result));
        }catch(Exception error){
            if(requestId!=null)try{web.CoreWebView2.PostWebMessageAsJson(json.Serialize(new Dictionary<string,object>{{"type","export-directory-selected"},{"requestId",requestId},{"error","无法选择文件夹："+error.Message}}));}catch{}
        }
    }
    async Task StartView(){
        ready=false;captured=false;started=DateTime.UtcNow;overlay.Visible=true;overlay.BringToFront();retry.Visible=false;message.Text="正在载入真实载具模型…";
        try{
            if(web.CoreWebView2==null){
                var options=new CoreWebView2EnvironmentOptions();if(debugPort>0)options.AdditionalBrowserArguments="--remote-debugging-port="+debugPort;
                var environment=await CoreWebView2Environment.CreateAsync(null,profile,options);
                await web.EnsureCoreWebView2Async(environment);
                web.CoreWebView2.Settings.IsStatusBarEnabled=false;
                web.CoreWebView2.Settings.AreDefaultContextMenusEnabled=false;
                web.CoreWebView2.WebMessageReceived+=HandleWebMessage;
                web.CoreWebView2.NavigationCompleted+=delegate(object sender,CoreWebView2NavigationCompletedEventArgs e){if(!e.IsSuccess)Fail("页面连接失败："+e.WebErrorStatus);};
                web.CoreWebView2.ProcessFailed+=delegate(object sender,CoreWebView2ProcessFailedEventArgs e){Fail("3D 显示进程异常："+e.ProcessFailedKind);};
                web.CoreWebView2.NavigationStarting+=delegate(object sender,CoreWebView2NavigationStartingEventArgs e){Uri next;if(Uri.TryCreate(e.Uri,UriKind.Absolute,out next)&&next.Scheme=="http"&&(next.Host!="127.0.0.1"||next.Port!=new Uri(url).Port))e.Cancel=true;};
            }
            web.CoreWebView2.Navigate(url);timer.Start();
        }catch(Exception e){Fail("窗口初始化失败："+e.Message);}
    }
    async Task PollReady(){
        if(polling||ready||web.CoreWebView2==null)return;polling=true;
        try{
            string result=await web.CoreWebView2.ExecuteScriptAsync("({ready:window.__studioReady===true,error:window.__startupError||null,title:document.title,meshes:window.studio?.getModel()?.children?.length||0,triangles:window.studio?.getModel()?.children?.reduce((n,m)=>n+m.geometry.attributes.position.count/3,0)||0})");
            var state=json.Deserialize<Dictionary<string,object>>(result);
            if(state!=null&&state.ContainsKey("error")&&state["error"]!=null){Fail(Convert.ToString(state["error"]));return;}
            if(state!=null&&state.ContainsKey("ready")&&Convert.ToBoolean(state["ready"])&&Convert.ToInt32(state["meshes"])>0){
                ready=true;timer.Stop();overlay.Visible=false;web.Focus();state["native_window"]=true;state["visible"]=Visible;state["pid"]=Process.GetCurrentProcess().Id;state["focus_event"]=FocusEventName(root);state["clientWidth"]=ClientSize.Width;state["clientHeight"]=ClientSize.Height;state["dpi"]=DeviceDpi;
                if(capture&&!captured){captured=true;await Task.Delay(700);using(var stream=File.Create(Path.Combine(root,"tmp","desktop_ready.png"))){await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream);}}
            }else if((DateTime.UtcNow-started).TotalSeconds>45)Fail("模型加载超时，请重新加载。");
        }catch(Exception e){if((DateTime.UtcNow-started).TotalSeconds>10)Fail("读取工作台状态失败："+e.Message);}
        finally{polling=false;}
    }
    [STAThread] static void Main(string[] args){
        try{Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);if(args.Length<1)throw new Exception("缺少启动地址，请使用工具根目录的启动入口。");bool capture=Array.IndexOf(args,"--capture")>=0;int port=0;foreach(string arg in args)if(arg.StartsWith("--debug-port="))int.TryParse(arg.Substring(13),out port);Application.Run(new StudioWindow(args[0],capture,port));}
        catch(Exception e){MessageBox.Show(e.Message,AppInfo.Title,MessageBoxButtons.OK,MessageBoxIcon.Error);}
    }
}
