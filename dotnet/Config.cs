using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace JudyNeuralLink;

/// <summary>Port of judy_config.py: config.json, session code, game and CET folder discovery.</summary>
internal static class Config
{
    public const string HfRepo = "wolfdead/judy-neural-link-voice";
    public const string ColabNotebookUrl =
        "https://colab.research.google.com/notebook#fileId=https%3A//huggingface.co/wolfdead/judy-neural-link-voice.ipynb";
    public const string AppName = "JudyNeuralLink";

    private static readonly object Gate = new();

    /// <summary>Writable user data: %APPDATA%\JudyNeuralLink.</summary>
    public static string DataDir
    {
        get
        {
            string d = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), AppName);
            Directory.CreateDirectory(d);
            return d;
        }
    }

    public static string ConfigFile => Path.Combine(DataDir, "config.json");

    /// <summary>Read-only files shipped next to the exe (RAG, RAG_branches.json, cet_mod).</summary>
    public static string ResourcePath(string rel) => Path.Combine(AppContext.BaseDirectory, "data", rel);

    // ---------------------------------------------------------------- config.json
    public static JsonObject Load()
    {
        lock (Gate)
        {
            try
            {
                string text = File.ReadAllText(ConfigFile, Encoding.UTF8); // strips a BOM if present
                return JsonNode.Parse(text) as JsonObject ?? new JsonObject();
            }
            catch
            {
                return new JsonObject();
            }
        }
    }

    public static void Save(params (string Key, string Value)[] updates)
    {
        lock (Gate)
        {
            JsonObject cfg;
            try
            {
                cfg = JsonNode.Parse(File.ReadAllText(ConfigFile, Encoding.UTF8)) as JsonObject ?? new JsonObject();
            }
            catch
            {
                cfg = new JsonObject();
            }

            foreach (var (key, value) in updates) cfg[key] = value;

            string tmp = ConfigFile + ".tmp";
            File.WriteAllText(tmp, cfg.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), new UTF8Encoding(false));
            File.Move(tmp, ConfigFile, overwrite: true);
        }
    }

    private static string Str(JsonObject o, string key)
    {
        try
        {
            return o[key]?.ToString() ?? "";
        }
        catch
        {
            return "";
        }
    }

    private static string Env(string name) => (Environment.GetEnvironmentVariable(name) ?? "").Trim();

    private static string First(params string[] values)
    {
        foreach (string v in values)
            if (!string.IsNullOrWhiteSpace(v)) return v.Trim();
        return "";
    }

    public static string GetGroqKey() => First(Env("JUDY_GROQ_KEY"), Str(Load(), "groq_key"));

    public static string GetApiKey()
    {
        var cfg = Load();
        return First(Env("JUDY_API_KEY"), Str(cfg, "api_key"), First(Env("JUDY_GROQ_KEY"), Str(cfg, "groq_key")));
    }

    public static string GetAiProvider() =>
        First(Env("JUDY_AI_PROVIDER"), Str(Load(), "ai_provider"), "groq").ToLowerInvariant();

    public static string GetNotebookUrl() => First(Str(Load(), "notebook_url"), ColabNotebookUrl);

    private static string TokenUrlSafe(int byteCount) =>
        Convert.ToBase64String(RandomNumberGenerator.GetBytes(byteCount)).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    /// <summary>16-character code: derives the relay channel and doubles as the Colab server password.</summary>
    public static string GetSessionCode(bool create = true)
    {
        string code = First(Env("JUDY_SESSION_CODE"), Str(Load(), "session_code"));
        if (code.Length == 0 && create)
        {
            code = TokenUrlSafe(12);
            Save(("session_code", code));
        }
        return code;
    }

    // ---------------------------------------------------------------- game / CET discovery
    private static List<string> SteamLibraries()
    {
        var roots = new List<string>();
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            if (key?.GetValue("SteamPath") is string steamPath && steamPath.Length > 0)
                roots.Add(steamPath.Replace('/', '\\'));
        }
        catch
        {
            // no registry entry
        }
        roots.Add(@"C:\Program Files (x86)\Steam");
        roots.Add(@"C:\Program Files\Steam");

        var libs = new List<string>();
        foreach (string root in roots)
        {
            libs.Add(root);
            string vdf = Path.Combine(root, "steamapps", "libraryfolders.vdf");
            try
            {
                string text = File.ReadAllText(vdf);
                foreach (Match m in Regex.Matches(text, "\"path\"\\s+\"([^\"]+)\""))
                    libs.Add(m.Groups[1].Value.Replace("\\\\", "\\"));
            }
            catch
            {
                // library file missing or unreadable
            }
        }
        return libs;
    }

    private static List<string> GameCandidates()
    {
        var list = SteamLibraries().Select(lib => Path.Combine(lib, "steamapps", "common", "Cyberpunk 2077")).ToList();
        list.Add(@"C:\GOG Games\Cyberpunk 2077");
        list.Add(@"C:\Program Files (x86)\GOG Galaxy\Games\Cyberpunk 2077");
        list.Add(@"C:\Program Files\Epic Games\Cyberpunk2077");
        list.Add(@"C:\Program Files (x86)\Epic Games\Cyberpunk2077");
        return list;
    }

    public static string FindGameDir()
    {
        var candidates = new List<string>();
        string cfgDir = Str(Load(), "game_dir");
        if (cfgDir.Length > 0) candidates.Add(cfgDir);
        candidates.AddRange(GameCandidates());

        foreach (string d in candidates)
            if (!string.IsNullOrEmpty(d) && File.Exists(Path.Combine(d, "bin", "x64", "Cyberpunk2077.exe")))
                return d;
        return "";
    }

    public static string CetDirFor(string gameDir) => Path.Combine(gameDir, "bin", "x64", "plugins", "cyber_engine_tweaks");

    /// <summary>CET folder of the installed game, or "" when the game or CET is missing.</summary>
    public static string FindCetDir()
    {
        string g = FindGameDir();
        if (g.Length == 0) return "";
        string d = CetDirFor(g);
        return Directory.Exists(d) ? d : "";
    }
}
