using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace JudyNeuralLink;

/// <summary>Talks to the Colab XTTSv2 + RVC server through the Cloudflare tunnel. Nothing runs on the local GPU.</summary>
internal sealed class RemoteVoiceEngine
{
    private string _baseUrl;
    private readonly string _token;
    private const double TtsTimeoutSec = 90;

    private RemoteVoiceEngine(string baseUrl, string token)
    {
        _baseUrl = baseUrl.TrimEnd('/');
        _token = token;
    }

    /// <summary>Order: JUDY_COLAB_URL env, relay (session code), colab_url.txt, config.json.</summary>
    public static async Task<string> ResolveUrlAsync(CancellationToken ct)
    {
        string env = (Environment.GetEnvironmentVariable("JUDY_COLAB_URL") ?? "").Trim();
        if (env.Length > 0) return env.TrimEnd('/');

        string code = Config.GetSessionCode(create: false);
        if (code.Length > 0)
        {
            try
            {
                string? found = await Relay.FetchLatestAsync(code, ct).ConfigureAwait(false);
                if (!string.IsNullOrEmpty(found)) return found.TrimEnd('/');
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception)
            {
                // relay unreachable, fall through to the older sources
            }
        }

        string legacy = Path.Combine(Config.DataDir, "colab_url.txt");
        try
        {
            if (File.Exists(legacy))
            {
                foreach (string raw in File.ReadLines(legacy))
                {
                    string line = raw.Trim();
                    if (line.StartsWith("http", StringComparison.Ordinal)) return line.TrimEnd('/');
                }
            }
        }
        catch (IOException)
        {
        }

        return (Config.Load()["colab_url"]?.ToString() ?? "").Trim().TrimEnd('/');
    }

    /// <summary>Connects (GET /health). Throws when the server cannot be reached.</summary>
    public static async Task<RemoteVoiceEngine> ConnectAsync(string baseUrl, CancellationToken ct)
    {
        string token = (Environment.GetEnvironmentVariable("JUDY_TOKEN") ?? "").Trim();
        if (token.Length == 0) token = Config.GetSessionCode(create: false);
        var engine = new RemoteVoiceEngine(baseUrl, token);

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(TimeSpan.FromSeconds(20));
        using var req = engine.NewRequest(HttpMethod.Get, "/health");
        using var resp = await Net.Http.SendAsync(req, cts.Token).ConfigureAwait(false);
        resp.EnsureSuccessStatusCode();
        string info = await resp.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
        Log.Write($"[+] Connected to the Colab server: {engine._baseUrl} ({info})");
        return engine;
    }

    private HttpRequestMessage NewRequest(HttpMethod method, string path)
    {
        var req = new HttpRequestMessage(method, _baseUrl + path);
        req.Headers.TryAddWithoutValidation("User-Agent", Net.RelayUserAgent);
        req.Headers.TryAddWithoutValidation("ngrok-skip-browser-warning", "1");
        if (_token.Length > 0) req.Headers.TryAddWithoutValidation("X-Judy-Token", _token);
        return req;
    }

    private async Task<bool> RefreshUrlAsync(CancellationToken ct)
    {
        string fresh = await ResolveUrlAsync(ct).ConfigureAwait(false);
        if (fresh.Length > 0 && fresh != _baseUrl)
        {
            Log.Write($"[judy-tts] Found a new tunnel link: {fresh}");
            _baseUrl = fresh;
            return true;
        }
        return false;
    }

    /// <summary>Returns the number of seconds it took. Writes the WAV to outPath.</summary>
    public async Task<double> SynthesizeAsync(string text, string language, string emotion, string outPath, CancellationToken ct)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        string payload = new JsonObject { ["text"] = text, ["language"] = language, ["emotion"] = emotion }.ToJsonString();
        byte[]? audio = null;
        string lastErr = "";

        for (int attempt = 0; attempt < 2; attempt++)
        {
            try
            {
                using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
                cts.CancelAfter(TimeSpan.FromSeconds(TtsTimeoutSec));
                using var req = NewRequest(HttpMethod.Post, "/tts");
                req.Content = new StringContent(payload, Encoding.UTF8, "application/json");
                using var resp = await Net.Http.SendAsync(req, cts.Token).ConfigureAwait(false);
                if (resp.StatusCode == HttpStatusCode.Unauthorized)
                    throw new InvalidOperationException("The session code does not match the one in Colab (401).");
                if (!resp.IsSuccessStatusCode)
                {
                    lastErr = $"HTTP {(int)resp.StatusCode}";
                }
                else
                {
                    audio = await resp.Content.ReadAsByteArrayAsync(cts.Token).ConfigureAwait(false);
                    break;
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (InvalidOperationException)
            {
                throw;
            }
            catch (Exception e)
            {
                lastErr = e.Message;
            }

            if (attempt == 0)
            {
                // the tunnel may have been renewed: wait, refresh the link from the relay, try once more
                await Task.Delay(2000, ct).ConfigureAwait(false);
                try { await RefreshUrlAsync(ct).ConfigureAwait(false); }
                catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
                catch (Exception) { }
            }
        }

        if (audio == null) throw new IOException($"Colab TTS unreachable: {lastErr}");

        string tmp = outPath + ".tmp";
        await File.WriteAllBytesAsync(tmp, audio, ct).ConfigureAwait(false);
        File.Move(tmp, outPath, overwrite: true);
        return sw.Elapsed.TotalSeconds;
    }
}
