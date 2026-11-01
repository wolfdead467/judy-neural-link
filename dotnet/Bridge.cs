using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace JudyNeuralLink;

/// <summary>The methods the launcher page calls (same names and result shapes as the old pywebview Api).</summary>
internal sealed class Bridge
{
    private readonly AgentRunner _agent = new();
    private readonly Func<string?> _pickFolder; // must run on the UI thread, the form provides it

    public Bridge(Func<string?> pickFolder) => _pickFolder = pickFolder;

    public void Shutdown() => _agent.Stop();

    private static JsonObject Obj(params (string K, JsonNode? V)[] items)
    {
        var o = new JsonObject();
        foreach (var (k, v) in items) o[k] = v;
        return o;
    }

    private static string Arg(JsonArray? args, int i) =>
        args != null && i < args.Count ? args[i]?.ToString() ?? "" : "";

    private static void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (Exception e)
        {
            Log.Write($"[launcher] could not open {url}: {e.Message}");
        }
    }

    /// <summary>Runs on a worker thread. Returns the JSON result for the page.</summary>
    public async Task<JsonNode?> CallAsync(string method, JsonArray? args)
    {
        switch (method)
        {
            case "state": return State();
            case "save_groq":
            {
                string key = Arg(args, 0).Trim();
                Config.Save(("groq_key", key), ("api_key", key));
                return JsonValue.Create(true);
            }
            case "save_provider":
            {
                string key = Arg(args, 0).Trim();
                string provider = Arg(args, 1).Trim().ToLowerInvariant();
                if (provider is not ("groq" or "gemini" or "openai")) provider = "groq";
                var updates = new List<(string, string)> { ("api_key", key), ("ai_provider", provider) };
                if (provider == "groq") updates.Add(("groq_key", key));
                Config.Save(updates.ToArray());
                return JsonValue.Create(true);
            }
            case "open_key_page":
            {
                string p = Arg(args, 0).Trim().ToLowerInvariant();
                OpenUrl(p switch
                {
                    "gemini" => "https://aistudio.google.com/apikey",
                    "openai" => "https://platform.openai.com/api-keys",
                    _ => "https://console.groq.com/keys",
                });
                return JsonValue.Create(true);
            }
            case "open_groq":
                OpenUrl("https://console.groq.com/keys");
                return JsonValue.Create(true);
            case "open_colab":
                OpenUrl(Config.GetNotebookUrl());
                return JsonValue.Create(true);
            case "browse_game": return BrowseGame();
            case "install_cet": return InstallCet();
            case "start_agent": return StartAgent();
            case "stop_agent":
                _agent.Stop();
                return Obj(("ok", true), ("message", "Link closed."));
            case "colab_status": return await ColabStatusAsync().ConfigureAwait(false);
            case "agent_status":
            {
                int? code = _agent.ExitCode;
                return Obj(("running", _agent.Running), ("exit_code", code.HasValue ? JsonValue.Create(code.Value) : null));
            }
            default:
                throw new InvalidOperationException("Unknown method: " + method);
        }
    }

    private JsonNode State()
    {
        string game = Config.FindGameDir();
        string cet = Config.FindCetDir();
        return Obj(
            ("groq_key", Config.GetApiKey()),
            ("ai_provider", Config.GetAiProvider()),
            ("session_code", Config.GetSessionCode(create: true)),
            ("game_dir", game),
            ("cet_dir", cet),
            ("notebook_url", Config.GetNotebookUrl()),
            ("hf_repo", Config.HfRepo),
            ("agent_running", _agent.Running));
    }

    private JsonNode BrowseGame()
    {
        string? picked = _pickFolder();
        if (string.IsNullOrEmpty(picked)) return Obj(("ok", false), ("error", ""));
        string probe = Path.GetFullPath(picked);
        for (int i = 0; i < 4; i++)
        {
            if (File.Exists(Path.Combine(probe, "bin", "x64", "Cyberpunk2077.exe")))
            {
                Config.Save(("game_dir", probe));
                return Obj(("ok", true), ("game_dir", probe));
            }
            string? parent = Path.GetDirectoryName(probe);
            if (string.IsNullOrEmpty(parent) || parent == probe) break;
            probe = parent;
        }
        return Obj(("ok", false), ("error", "That folder does not contain Cyberpunk 2077 (bin\\x64\\Cyberpunk2077.exe)."));
    }

    private static bool SameFile(string a, string b)
    {
        try
        {
            return File.ReadAllBytes(a).AsSpan().SequenceEqual(File.ReadAllBytes(b));
        }
        catch (IOException)
        {
            return false;
        }
    }

    private JsonNode InstallCet()
    {
        string game = Config.FindGameDir();
        if (game.Length == 0) return Obj(("ok", false), ("error", "Cyberpunk 2077 not found."));
        string cet = Config.CetDirFor(game);
        if (!Directory.Exists(cet))
            return Obj(("ok", false), ("error", "Cyber Engine Tweaks is not installed. Install it first."));

        string dest = Path.Combine(cet, "mods", "judy_ai_mod");
        try
        {
            Directory.CreateDirectory(dest);
            foreach (string name in new[] { "init.lua", "judy_story.lua" })
            {
                string src = Config.ResourcePath(Path.Combine("cet_mod", name));
                if (!File.Exists(src)) return Obj(("ok", false), ("error", $"cet_mod/{name} is missing."));
                string target = Path.Combine(dest, name);
                // Keep the player's own edits: back up init.lua if it differs.
                if (name == "init.lua" && File.Exists(target) && !SameFile(src, target))
                    File.Copy(target, target + ".bak", overwrite: true);
                File.Copy(src, target, overwrite: true);
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return Obj(("ok", false), ("error",
                $"Could not write to the game folder ({e.Message}). Close the game and try running the launcher as administrator."));
        }
        Config.Save(("game_dir", game));
        return Obj(("ok", true));
    }

    private JsonNode StartAgent()
    {
        if (Config.GetApiKey().Length == 0) return Obj(("ok", false), ("message", "Enter your API key first."));
        var (ok, msg) = _agent.Start();
        return Obj(("ok", ok), ("message", msg));
    }

    private static async Task<JsonNode> ColabStatusAsync()
    {
        string url;
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            url = await RemoteVoiceEngine.ResolveUrlAsync(cts.Token).ConfigureAwait(false);
        }
        catch (Exception)
        {
            url = "";
        }
        if (url.Length == 0) return Obj(("ok", false), ("message", "Colab not found. Run the notebook."));

        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(6));
            using var req = new HttpRequestMessage(HttpMethod.Get, url.TrimEnd('/') + "/health");
            req.Headers.TryAddWithoutValidation("User-Agent", Net.RelayUserAgent);
            string token = Config.GetSessionCode(create: false);
            if (token.Length > 0) req.Headers.TryAddWithoutValidation("X-Judy-Token", token);
            using var resp = await Net.Http.SendAsync(req, cts.Token).ConfigureAwait(false);
            return Obj(("ok", (int)resp.StatusCode == 200));
        }
        catch (Exception)
        {
            return Obj(("ok", false), ("message", "Colab is not answering. Is the notebook running?"));
        }
    }
}
