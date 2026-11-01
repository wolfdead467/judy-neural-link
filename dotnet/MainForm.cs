using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace JudyNeuralLink;

public sealed class MainForm : Form
{
    // Defines window.pywebview.api.<method>(...) -> Promise, so ui/launcher.html needs no change.
    private const string Shim = @"
(function () {
  var pending = {}, nextId = 1;
  var methods = ['state','save_groq','save_provider','open_key_page','open_groq','open_colab',
                 'browse_game','install_cet','start_agent','stop_agent','colab_status','agent_status'];
  var api = {};
  methods.forEach(function (m) {
    api[m] = function () {
      var args = Array.prototype.slice.call(arguments);
      return new Promise(function (resolve, reject) {
        var id = nextId++;
        pending[id] = { resolve: resolve, reject: reject };
        window.chrome.webview.postMessage({ id: id, method: m, args: args });
      });
    };
  });
  window.pywebview = { api: api };
  window.chrome.webview.addEventListener('message', function (e) {
    var d = e.data, p = pending[d.id];
    if (!p) return;
    delete pending[d.id];
    if (d.error) p.reject(new Error(d.error)); else p.resolve(d.result);
  });
  document.addEventListener('DOMContentLoaded', function () {
    window.dispatchEvent(new Event('pywebviewready'));
  });
})();";

    private readonly WebView2 _web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(5, 6, 10) };
    private readonly Bridge _bridge;

    public MainForm()
    {
        Text = "Judy Neural-Link";
        ClientSize = new Size(440, 740);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(5, 6, 10);
        try
        {
            string ico = Path.Combine(AppContext.BaseDirectory, "judy.ico");
            if (File.Exists(ico)) Icon = new Icon(ico);
        }
        catch (Exception)
        {
        }

        _bridge = new Bridge(PickFolder);
        Controls.Add(_web);
        Load += async (_, _) => await InitWebAsync();
        FormClosing += (_, _) => _bridge.Shutdown();
    }

    private string? PickFolder()
    {
        string? result = null;
        Invoke(() =>
        {
            using var dlg = new FolderBrowserDialog { Description = "Select the Cyberpunk 2077 folder", UseDescriptionForTitle = true };
            if (dlg.ShowDialog(this) == DialogResult.OK) result = dlg.SelectedPath;
        });
        return result;
    }

    private async Task InitWebAsync()
    {
        try
        {
            string userData = Path.Combine(Config.DataDir, "webview2");
            var env = await CoreWebView2Environment.CreateAsync(null, userData);
            await _web.EnsureCoreWebView2Async(env);
        }
        catch (Exception e)
        {
            Log.Write($"[launcher] WebView2 failed: {e}");
            MessageBox.Show(
                "Judy Neural-Link needs the Microsoft Edge WebView2 Runtime, and it could not be started.\n\n" +
                "Install it from https://developer.microsoft.com/microsoft-edge/webview2/ and open the app again.\n\n" + e.Message,
                "Judy Neural-Link", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
            return;
        }

        var core = _web.CoreWebView2;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsZoomControlEnabled = false;
        core.NewWindowRequested += (_, e) => e.Handled = true; // links open through the Bridge, never in-app

        await core.AddScriptToExecuteOnDocumentCreatedAsync(Shim);
        core.WebMessageReceived += OnMessage;

        string page = Path.Combine(AppContext.BaseDirectory, "ui", "launcher.html");
        if (!File.Exists(page))
        {
            Log.Write("[launcher] ui\\launcher.html is missing: " + page);
            MessageBox.Show("ui\\launcher.html is missing next to the app.", "Judy Neural-Link", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
            return;
        }
        core.Navigate(new Uri(page).AbsoluteUri);
    }

    private void OnMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string json = e.WebMessageAsJson;
        _ = Task.Run(async () =>
        {
            int id = 0;
            JsonNode reply;
            try
            {
                var msg = JsonNode.Parse(json) as JsonObject ?? throw new InvalidOperationException("bad message");
                id = msg["id"]?.GetValue<int>() ?? 0;
                string method = msg["method"]?.ToString() ?? "";
                var result = await _bridge.CallAsync(method, msg["args"] as JsonArray);
                reply = new JsonObject { ["id"] = id, ["result"] = result };
            }
            catch (Exception ex)
            {
                Log.Write($"[launcher] call failed: {ex.Message}");
                reply = new JsonObject { ["id"] = id, ["error"] = ex.Message };
            }

            string text = reply.ToJsonString();
            try
            {
                BeginInvoke(() =>
                {
                    if (!IsDisposed && _web.CoreWebView2 != null) _web.CoreWebView2.PostWebMessageAsJson(text);
                });
            }
            catch (InvalidOperationException)
            {
                // window already closed
            }
        });
    }
}
