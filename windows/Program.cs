using System;
using System.IO;
using System.Diagnostics;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace OpsdemoDesktop {
 internal static class Program {
  [STAThread] static void Main(string[] args) {
   if(args.Length==1 && args[0]=="--check") {
    try {File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"runtime-check.txt"),"WebView2 runtime: "+CoreWebView2Environment.GetAvailableBrowserVersionString());}
    catch(Exception error){File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"runtime-check.txt"),"Unavailable: "+error.GetType().Name);Environment.ExitCode=1;}
    return;
   }
   Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new Workspace());
  }
 }
 internal sealed class Workspace:Form {
  readonly WebView2 web=new WebView2();readonly Uri server=new Uri(CompanyConfig.ServerUrl);
  readonly Label status=new Label();
  readonly Button back=new Button(),home=new Button(),refresh=new Button();
  bool arabic=false,connected=false;
  Button NavigationButton(Button button,string text){
   button.Text=text;button.AutoSize=false;button.Width=112;button.Height=36;
   button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderSize=0;
   button.FlatAppearance.MouseOverBackColor=System.Drawing.Color.FromArgb(235,235,239);
   button.FlatAppearance.MouseDownBackColor=System.Drawing.Color.FromArgb(220,220,226);
   button.BackColor=System.Drawing.Color.White;button.ForeColor=System.Drawing.Color.FromArgb(35,35,40);
   button.Font=new System.Drawing.Font("Segoe UI",10F);button.Cursor=Cursors.Hand;
   button.Margin=new Padding(3,0,3,0);return button;
  }
  void UpdateLabels(){
   back.Text=arabic?"→  رجوع":"←  Back";
   home.Text=arabic?"⌂  الرئيسية":"⌂  Home";
   refresh.Text=arabic?"↻  تحديث":"↻  Refresh";
   status.Text=connected?(CompanyConfig.Trial?(arabic?"●  نسخة تجريبية محلية":"●  Local trial"):(arabic?"●  متصل":"●  Connected")):(arabic?"●  جارٍ الاتصال":"●  Connecting");
   status.ForeColor=connected?System.Drawing.Color.FromArgb(56,105,75):System.Drawing.Color.FromArgb(110,110,120);
  }
  public Workspace(){
   Icon=new System.Drawing.Icon(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"opsdemo.ico"));
   Text=CompanyConfig.Title;Width=1280;Height=850;MinimumSize=new System.Drawing.Size(800,600);StartPosition=FormStartPosition.CenterScreen;
   var toolbar=new Panel{Dock=DockStyle.Top,Height=58,Padding=new Padding(12,10,12,10),BackColor=System.Drawing.Color.White};
   var navigation=new FlowLayoutPanel{Dock=DockStyle.Left,Width=365,WrapContents=false,BackColor=System.Drawing.Color.White};
   NavigationButton(back,"←  Back");NavigationButton(home,"⌂  Home");NavigationButton(refresh,"↻  Refresh");
   back.Click+=async(sender,args)=>{if(web.CoreWebView2!=null){var handled=await web.CoreWebView2.ExecuteScriptAsync("(()=>{const e=new Event('opsdemo-native-back',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;})()");if(handled!="true"&&web.CanGoBack)web.GoBack();}};
   home.Click+=(sender,args)=>{if(web.CoreWebView2!=null)web.CoreWebView2.Navigate(server.AbsoluteUri);};
   refresh.Click+=(sender,args)=>{if(web.CoreWebView2!=null)web.Reload();};
   status.Dock=DockStyle.Right;status.Width=245;status.TextAlign=System.Drawing.ContentAlignment.MiddleRight;
   status.Font=new System.Drawing.Font("Segoe UI",9F);UpdateLabels();
   navigation.Controls.AddRange(new Control[]{back,home,refresh});toolbar.Controls.Add(navigation);toolbar.Controls.Add(status);
   var divider=new Panel{Dock=DockStyle.Bottom,Height=1,BackColor=System.Drawing.Color.FromArgb(230,230,234)};toolbar.Controls.Add(divider);
   web.Dock=DockStyle.Fill;Controls.Add(web);Controls.Add(toolbar);
   Shown+=Start;
  }
  bool Internal(Uri url){return url.Scheme==server.Scheme&&url.Host==server.Host&&url.Port==server.Port;}
  void External(string value){Uri url;if(Uri.TryCreate(value,UriKind.Absolute,out url)&&(url.Scheme=="https"||url.Scheme=="tel"))Process.Start(new ProcessStartInfo(value){UseShellExecute=true});}
  async void Start(object sender,EventArgs args){
   try{
    var folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"OPSDEMO",CompanyConfig.Trial?"Trial":"Operations");
    var environment=await CoreWebView2Environment.CreateAsync(null,folder);await web.EnsureCoreWebView2Async(environment);
    web.CoreWebView2.Settings.AreDevToolsEnabled=false;web.CoreWebView2.Settings.AreDefaultContextMenusEnabled=false;web.CoreWebView2.Settings.IsPasswordAutosaveEnabled=false;
    web.CoreWebView2.NavigationStarting+=(source,e)=>{Uri url;if(!Uri.TryCreate(e.Uri,UriKind.Absolute,out url)||!Internal(url)){e.Cancel=true;External(e.Uri);}};
    web.CoreWebView2.NewWindowRequested+=(source,e)=>{e.Handled=true;External(e.Uri);};
    web.CoreWebView2.PermissionRequested+=(source,e)=>{e.State=CoreWebView2PermissionState.Deny;};
    web.CoreWebView2.WebMessageReceived+=(source,e)=>{
     Uri origin;if(!Uri.TryCreate(e.Source,UriKind.Absolute,out origin)||!Internal(origin))return;
     string message;try{message=e.TryGetWebMessageAsString();}catch(ArgumentException){return;}
     if(message=="opsdemo-language:ar"||message=="opsdemo-language:en"){arabic=message.EndsWith(":ar");UpdateLabels();}
    };
    web.CoreWebView2.NavigationCompleted+=async(source,e)=>{
     connected=e.IsSuccess;UpdateLabels();
     if(!e.IsSuccess){status.Text=arabic?"●  تعذر الاتصال — اضغط تحديث":"●  Offline — press Refresh";status.ForeColor=System.Drawing.Color.FromArgb(170,55,45);return;}
     await web.CoreWebView2.ExecuteScriptAsync("(()=>{const send=()=>window.chrome.webview.postMessage('opsdemo-language:'+(document.documentElement.lang==='ar'?'ar':'en'));send();new MutationObserver(send).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});})()");
    };
    web.CoreWebView2.Navigate(server.AbsoluteUri);
   }catch(WebView2RuntimeNotFoundException){MessageBox.Show("Install Microsoft Edge WebView2 Runtime, then reopen OPSDEMO.\nhttps://developer.microsoft.com/microsoft-edge/webview2/","OPSDEMO");}
   catch(Exception error){MessageBox.Show("Could not open OPSDEMO: "+error.GetType().Name,"OPSDEMO");}
  }
 }
}
