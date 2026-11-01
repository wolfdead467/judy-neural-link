using System.Text.Json;

namespace JudyNeuralLink;

/// <summary>
/// The agent: watches the CET mod folder, answers chat requests through the LLM, speaks through Colab and
/// presses the CET overlay key. Port of judy_agent.py; runs as a background thread inside the app.
/// </summary>
internal sealed class AgentRunner
{
    private const double PollSec = 0.2;
    private const double BrainPollSec = 2.0;
    private const double OverlayDelaySec = 1.0;
    private const double VoiceReconnectSec = 30.0;
    private const string TtsLanguage = "en";

    private readonly object _gate = new();
    private readonly SemaphoreSlim _inflight = new(1, 1);
    private readonly SemaphoreSlim _tts = new(1, 1);
    private readonly StoryState _story = new();
    private readonly Brain _brain;

    private Thread? _thread;
    private CancellationTokenSource? _cts;
    private volatile RemoteVoiceEngine? _voice;
    private int _exitCode = -1; // -1 = not finished
    private bool _running;

    private string _modDir = "";
    private string TriggerFile => Path.Combine(_modDir, "judy_chat_trigger.txt");
    private string RequestFile => Path.Combine(_modDir, "judy_chat_request.txt");
    private string ResponseFile => Path.Combine(_modDir, "judy_chat_response.txt");
    private string StateFile => Path.Combine(_modDir, "judy_overlay_state.txt");
    private string CloseFile => Path.Combine(_modDir, "judy_chat_close.txt");
    private string VramFile => Path.Combine(_modDir, "judy_vram_state.txt");
    private string BrainFile => Path.Combine(_modDir, "judy_brain.json");

    public AgentRunner() => _brain = new Brain(_story);

    public bool Running
    {
        get { lock (_gate) return _running; }
    }

    /// <summary>null while running or never started; otherwise the code the agent ended with (0 normal, 2 = CET key unreadable).</summary>
    public int? ExitCode
    {
        get { lock (_gate) return _running || _exitCode < 0 ? null : _exitCode; }
    }

    public (bool Ok, string Message) Start()
    {
        lock (_gate)
        {
            if (_running) return (true, "Already running.");
            _exitCode = -1;
            _running = true;
            _cts = new CancellationTokenSource();
            var token = _cts.Token;
            _thread = new Thread(() => Run(token)) { IsBackground = true, Name = "JudyAgent" };
            _thread.Start();
        }
        return (true, "Judy is listening. Go back to the game.");
    }

    public void Stop()
    {
        CancellationTokenSource? cts;
        Thread? t;
        lock (_gate)
        {
            cts = _cts;
            t = _thread;
        }
        if (cts == null) return;
        try { cts.Cancel(); } catch (ObjectDisposedException) { }
        t?.Join(3000);
        lock (_gate) _running = false;
    }

    private void Finish(int code)
    {
        lock (_gate)
        {
            _exitCode = code;
            _running = false;
        }
    }

    // ------------------------------------------------------------ CET overlay key
    private static List<int> LoadOverlayVks(string bindingsFile)
    {
        string? over = Environment.GetEnvironmentVariable("JUDY_CET_KEY_VK");
        if (!string.IsNullOrWhiteSpace(over))
        {
            over = over.Trim();
            return new List<int> { over.StartsWith("0x", StringComparison.OrdinalIgnoreCase) ? Convert.ToInt32(over[2..], 16) : int.Parse(over) };
        }

        string raw = AgentFiles.ReadText(bindingsFile) ?? throw new IOException("bindings.json not readable");
        using var doc = JsonDocument.Parse(raw);
        var key = doc.RootElement.GetProperty("cet").GetProperty("overlay_key");
        ulong bind = key.TryGetUInt64(out ulong u) ? u : (ulong)key.GetDouble();
        var vks = new List<int>();
        for (int i = 0; i < 4; i++)
        {
            int v = (int)((bind >> (16 * i)) & 0xFFFF);
            if (v != 0) vks.Add(v);
        }
        return vks;
    }

    private bool OverlayIsOpen() => AgentFiles.ReadText(StateFile) == "1";

    private bool WaitOverlayState(bool wantOpen, double timeoutSec, CancellationToken ct)
    {
        var end = DateTime.UtcNow.AddSeconds(timeoutSec);
        while (DateTime.UtcNow < end && !ct.IsCancellationRequested)
        {
            if (OverlayIsOpen() == wantOpen) return true;
            Thread.Sleep(50);
        }
        return false;
    }

    private void ToggleOverlay(List<int> vks, bool wantOpen, CancellationToken ct)
    {
        Log.Write($"[judy] overlay state file = '{AgentFiles.ReadText(StateFile)}', want_open = {wantOpen}");
        if (OverlayIsOpen() == wantOpen)
        {
            Log.Write("[judy] already in the wanted state, not pressing anything");
            return;
        }
        Log.Write($"[judy] foreground window: '{Win32.ForegroundTitle()}'");
        Win32.PressKeys(vks);
        bool ok = WaitOverlayState(wantOpen, 1.5, ct);
        Log.Write($"[judy] overlay {(wantOpen ? "opened" : "closed")}: {(ok ? "OK" : "no confirmation from Lua")}");
    }

    /// <summary>One agent takes the request; the same line never goes to the LLM twice.</summary>
    private string? ClaimRequest()
    {
        if (!File.Exists(RequestFile)) return null;
        string claimed = RequestFile + ".claimed";
        try
        {
            File.Move(RequestFile, claimed, overwrite: true);
        }
        catch (IOException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
        string? text = AgentFiles.ReadText(claimed);
        AgentFiles.RemoveQuiet(claimed);
        return string.IsNullOrEmpty(text) ? null : text;
    }

    // ------------------------------------------------------------ voice
    private async Task<bool> ConnectVoiceAsync(bool quiet, CancellationToken ct)
    {
        string url = await RemoteVoiceEngine.ResolveUrlAsync(ct).ConfigureAwait(false);
        if (url.Length == 0) return false;
        if (!quiet) Log.Write($"[*] Judy voice engine: Colab (XTTSv2 + RVC v2) -> {url}");
        _voice = await RemoteVoiceEngine.ConnectAsync(url, ct).ConfigureAwait(false);
        Log.Write("[+] Colab voice engine ready! (local VRAM use: 0 MB)");
        return true;
    }

    private async Task VoiceReconnectLoopAsync(CancellationToken ct)
    {
        // A fresh Cloudflare tunnel needs a few seconds before its DNS name resolves: retry fast first.
        var quick = new Queue<double>(new[] { 5.0, 5.0, 10.0, 10.0 });
        try
        {
            while (_voice == null && !ct.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(quick.Count > 0 ? quick.Dequeue() : VoiceReconnectSec), ct).ConfigureAwait(false);
                try
                {
                    if (await ConnectVoiceAsync(true, ct).ConfigureAwait(false)) return;
                }
                catch (OperationCanceledException) when (ct.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception)
                {
                    // Colab is still down; try again next round
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private async Task InitVoiceAsync(CancellationToken ct)
    {
        if (Environment.GetEnvironmentVariable("JUDY_ENABLE_TTS") == "0")
        {
            Log.Write("[*] TTS disabled (JUDY_ENABLE_TTS=0).");
            return;
        }
        try
        {
            if (await ConnectVoiceAsync(false, ct).ConfigureAwait(false)) return;
            Log.Write("[!] No Colab URL yet. Run the Colab notebook with your session code. Text only for now.");
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception e)
        {
            Log.Write($"[!] Could not reach the Colab voice. Text only: {e.Message}");
        }
        Log.Write($"[*] Retrying Colab every {VoiceReconnectSec:0} s in the background (no LLM requests are sent).");
        _ = Task.Run(() => VoiceReconnectLoopAsync(ct), ct);
    }

    private static string NewWavPath() => Path.Combine(Config.DataDir, $"judy_say_{AgentFiles.NextStamp()}.wav");

    private static void CleanupOldWavs(int keep = 2)
    {
        try
        {
            var files = Directory.GetFiles(Config.DataDir, "judy_say_*.wav").OrderBy(f => f, StringComparer.Ordinal).ToList();
            foreach (string f in files.Take(Math.Max(0, files.Count - keep)))
                AgentFiles.RemoveQuiet(f); // still playing? Windows refuses; it gets removed next time
        }
        catch (IOException)
        {
        }
    }

    private void WriteResponse(string text)
    {
        foreach (var (a, b) in new[]
                 {
                     ("\u2019", "'"), ("\u2018", "'"), ("\u201c", "\""), ("\u201d", "\""),
                     ("\u2014", "-"), ("\u2013", "-"), ("\u2026", "..."),
                 })
            text = text.Replace(a, b);
        AgentFiles.WriteAtomic(ResponseFile, $"{AgentFiles.NextStamp()}\n{text}");
    }

    // ------------------------------------------------------------ request handling
    private async Task HandleRequestAsync(string text, CancellationToken ct)
    {
        try
        {
            Log.Write($"[V] {text}");
            var ans = await _brain.AskAsync(text, ct).ConfigureAwait(false);
            string answer = ans.Text, emotion = ans.Emotion;
            bool ok = ans.Ok;

            if (!ok)
            {
                if (ans.RetryAfter > 0)
                {
                    double wait = Math.Min(ans.RetryAfter + 0.5, 30.0);
                    Log.Write($"[judy] Rate limit reached. Waiting {wait:0.0} seconds...");
                    await Task.Delay(TimeSpan.FromSeconds(wait), ct).ConfigureAwait(false);
                    ans = await _brain.AskAsync(text, ct).ConfigureAwait(false);
                    answer = ans.Text; emotion = ans.Emotion; ok = ans.Ok;
                    if (!ok && ans.RetryAfter > 0) answer = Brain.Fallback;
                }
                else
                {
                    Log.Write("[judy] Timeout or critical error. Not retrying.");
                }
            }
            if (string.IsNullOrEmpty(answer)) answer = Brain.Fallback;

            Log.Write($"[Judy] {answer}");

            string? wav = null;
            var engine = _voice; // the reconnect task may swap it at any time
            if (engine != null && ok)
            {
                await _tts.WaitAsync(ct).ConfigureAwait(false);
                try
                {
                    string path = NewWavPath(); // one file per line, so a playing one is never overwritten
                    double dt = await engine.SynthesizeAsync(answer, TtsLanguage, emotion, path, ct).ConfigureAwait(false);
                    wav = path;
                    Log.Write($"[judy-tts] Voice ready ({dt:0.00}s) -> playing in sync!");
                }
                catch (OperationCanceledException) when (ct.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception e)
                {
                    Log.Write($"[judy-tts] Synthesis error: {e.Message}");
                }
                finally
                {
                    _tts.Release();
                }
            }

            WriteResponse(answer);
            if (wav != null && File.Exists(wav))
            {
                Win32.PlayWavAsync(wav);
                CleanupOldWavs();
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception e)
        {
            Log.Write($"[judy] request error: {e}");
        }
        finally
        {
            _inflight.Release();
        }
    }

    // ------------------------------------------------------------ main loop
    private void Run(CancellationToken ct)
    {
        try
        {
            string cet = Config.FindCetDir();
            if (cet.Length == 0)
                cet = Config.CetDirFor(@"C:\Program Files (x86)\Steam\steamapps\common\Cyberpunk 2077");
            _modDir = Path.Combine(cet, "mods", "judy_ai_mod");

            List<int> vks;
            try
            {
                vks = LoadOverlayVks(Path.Combine(cet, "bindings.json"));
                if (vks.Count == 0) throw new InvalidDataException("overlay_key is empty");
            }
            catch (Exception e) when (e is IOException or JsonException or KeyNotFoundException or InvalidOperationException
                                          or FormatException or InvalidDataException or OverflowException or UnauthorizedAccessException)
            {
                Log.Write($"[!] Cannot read the Cyber Engine Tweaks overlay key ({e.Message}).");
                Log.Write("    Start the game once, open the CET overlay and bind its hotkey, then jack in again.");
                Finish(2);
                return;
            }

            foreach (string p in new[] { TriggerFile, RequestFile, ResponseFile, StateFile, CloseFile, VramFile })
                AgentFiles.RemoveQuiet(p);

            InitVoiceAsync(ct).GetAwaiter().GetResult();

            Log.Write("=========================================================");
            Log.Write("[+] Judy Neural-Link Agent ACTIVE (Colab XTTSv2 + RVC v2)");
            Log.Write($"    Language: {TtsLanguage.ToUpperInvariant()}");
            Log.Write($"    CET overlay key VK codes: [{string.Join(", ", vks.Select(v => "0x" + v.ToString("x")))}]");
            Log.Write($"    Watching: {_modDir}");
            Log.Write("    Waiting for V to Cuddle...");
            Log.Write("=========================================================");

            DateTime? openOverlayAt = null;
            DateTime nextBrainPoll = DateTime.MinValue;

            while (!ct.IsCancellationRequested)
            {
                try
                {
                    var now = DateTime.UtcNow;
                    if (now >= nextBrainPoll)
                    {
                        nextBrainPoll = now.AddSeconds(BrainPollSec);
                        _story.Poll(BrainFile);
                    }

                    if (File.Exists(VramFile)) AgentFiles.RemoveQuiet(VramFile); // Colab keeps its GPU loaded; nothing to do locally

                    if (File.Exists(TriggerFile))
                    {
                        AgentFiles.RemoveQuiet(TriggerFile);
                        Log.Write($"[judy] Cuddle trigger -> overlay opens in {OverlayDelaySec}s (waiting for V to settle in)");
                        openOverlayAt = DateTime.UtcNow.AddSeconds(OverlayDelaySec);
                    }

                    if (openOverlayAt.HasValue && DateTime.UtcNow >= openOverlayAt.Value)
                    {
                        openOverlayAt = null;
                        Log.Write($"[judy] Opening CET overlay (state='{AgentFiles.ReadText(StateFile)}')");
                        Win32.PressKeys(vks);
                        if (WaitOverlayState(true, 1.5, ct)) Log.Write("[judy] overlay opened: OK");
                        else Log.Write("[judy] overlay did not open / no confirmation from Lua (not pressing again)");
                    }

                    if (File.Exists(CloseFile))
                    {
                        AgentFiles.RemoveQuiet(CloseFile);
                        Log.Write("[judy] Close button -> closing CET overlay");
                        ToggleOverlay(vks, false, ct);
                    }

                    if (_inflight.Wait(0))
                    {
                        string? req = ClaimRequest();
                        if (req != null) _ = Task.Run(() => HandleRequestAsync(req, ct));
                        else _inflight.Release();
                    }
                }
                catch (Exception e)
                {
                    Log.Write($"[judy] loop error: {e.Message}");
                }

                if (ct.WaitHandle.WaitOne(TimeSpan.FromSeconds(PollSec))) break;
            }
            Finish(ct.IsCancellationRequested ? -1 : 0);
        }
        catch (OperationCanceledException)
        {
            Finish(-1);
        }
        catch (Exception e)
        {
            Log.Write($"[judy] agent crashed: {e}");
            Finish(1);
        }
    }
}
