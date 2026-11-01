using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace JudyNeuralLink;

internal sealed record Answer(string Text, string Emotion, bool Ok, double RetryAfter = 0);

/// <summary>Port of ask_judy(): builds the prompt (RAG + story + history), calls the LLM, parses the JSON reply.</summary>
internal sealed class Brain
{
    public const string Fallback = "...the link dropped for a sec, V. Say that again?";

    private const string VoiceRule =
        "1. VOICE RULE: Always respond strictly in English. Keep replies concise and intimate " +
        "(1 to 2 short spoken sentences, maximum 22 words total) so real-time voice synthesis stays instant. " +
        "Do not use asterisks or stage directions inside the 'text' field.";

    private static readonly Regex MultilingualRe =
        new(@"1\. MULTILINGUAL RULE:.*?reply in Russian\)\.", RegexOptions.Singleline | RegexOptions.Compiled);
    private static readonly Regex RetryRe =
        new(@"try again in (?:(\d+)m)?([\d.]+)s", RegexOptions.Compiled);
    private static readonly Regex ThinkRe =
        new(@"<think>.*?</think>", RegexOptions.Singleline | RegexOptions.Compiled);

    private readonly StoryState _story;
    private readonly object _gate = new();
    private readonly List<(string Role, string Content)> _history = new();
    private JudyRag? _rag;

    public Brain(StoryState story) => _story = story;

    private JudyRag LoadRag()
    {
        lock (_gate)
        {
            if (_rag != null) return _rag;
            string raw;
            try
            {
                raw = File.ReadAllText(Config.ResourcePath("RAG"), Encoding.UTF8);
            }
            catch (Exception e)
            {
                Log.Write($"[-] Could not read the RAG file: {e.Message}");
                raw = "You are Judy Alvarez from Cyberpunk 2077.";
            }
            var rag = new JudyRag(raw);
            int before = rag.Chunks.Count;
            int n = rag.AddChunks(LoadBranchChunks());
            Log.Write($"[judy-rag] {before} memories + {n} branch chunks loaded");
            _rag = rag;
            return rag;
        }
    }

    private static List<Chunk> LoadBranchChunks()
    {
        var list = new List<Chunk>();
        string path = Config.ResourcePath("RAG_branches.json");
        try
        {
            if (!File.Exists(path)) return list;
            using var doc = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8),
                new JsonDocumentOptions { AllowTrailingCommas = true, CommentHandling = JsonCommentHandling.Skip });
            if (doc.RootElement.ValueKind == JsonValueKind.Array)
            {
                foreach (var el in doc.RootElement.EnumerateArray())
                {
                    var c = Chunk.From(el);
                    if (c != null) list.Add(c);
                }
            }
        }
        catch (Exception e)
        {
            Log.Write($"[judy-rag] WARNING: RAG_branches.json could not be read, branches are off: {e.Message}");
        }
        return list;
    }

    private string RecentText()
    {
        lock (_gate)
        {
            var last = _history.Skip(Math.Max(0, _history.Count - 2)).Select(h => h.Content);
            return string.Join(" ", last);
        }
    }

    private string BuildUserText(string userText)
    {
        lock (_gate)
        {
            if (_history.Count == 0) return userText;
            var lines = _history.Skip(Math.Max(0, _history.Count - 4))
                .Select(h => $"{(h.Role == "user" ? "V" : "Judy")}: {h.Content}");
            return "[RECENT CONVERSATION]\n" + string.Join("\n", lines) + $"\n\n[V NOW SAYS]\n{userText}";
        }
    }

    private string SystemForTurn(string userText)
    {
        string recent = RecentText();
        var rag = LoadRag();
        var state = _story.StateIds;
        string system = rag.BuildPrompt(userText, recent, _story.SharedHistory(), state);
        Log.Write($"[judy-rag] memories: [{string.Join(", ", rag.PickedIds(userText, recent, state))}]");
        Log.Write($"[judy-brain] prompt: maiko='{_story.Get("MAIKO_STATUS")}' history_events={_story.DoneCount}");
        system = MultilingualRe.Replace(system, VoiceRule.Replace("$", "$$"), 1);
        return _story.Fill(system);
    }

    private static double? RetrySeconds(string err)
    {
        var m = RetryRe.Match(err.ToLowerInvariant());
        if (!m.Success) return null;
        try
        {
            int min = m.Groups[1].Success ? int.Parse(m.Groups[1].Value) : 0;
            double sec = double.Parse(m.Groups[2].Value, System.Globalization.CultureInfo.InvariantCulture);
            return min * 60 + sec;
        }
        catch (FormatException)
        {
            return null;
        }
    }

    public async Task<Answer> AskAsync(string userText, CancellationToken ct)
    {
        string provider = Config.GetAiProvider();
        string apiKey = Config.GetApiKey();
        if (apiKey.Length == 0)
        {
            Log.Write("[judy] ERROR: API key is missing.");
            return new Answer(Fallback, "calm", false);
        }

        string url, model;
        if (provider == "gemini")
        {
            url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions";
            model = "gemini-3.8-flash";
        }
        else if (provider == "openai")
        {
            url = "https://api.openai.com/v1/chat/completions";
            model = "gpt-4o-mini";
        }
        else
        {
            provider = "groq";
            url = "https://api.groq.com/openai/v1/chat/completions";
            model = "qwen/qwen3.8-27b";
        }

        var payload = new JsonObject
        {
            ["model"] = model,
            ["messages"] = new JsonArray
            {
                new JsonObject { ["role"] = "system", ["content"] = SystemForTurn(userText) },
                new JsonObject { ["role"] = "user", ["content"] = BuildUserText(userText) },
            },
            ["temperature"] = 0.7,
            ["response_format"] = new JsonObject { ["type"] = "json_object" },
        };

        Log.Write($"[~] Judy is thinking ({provider.ToUpperInvariant()} - {model})...");
        double timeoutSec = provider == "gemini" ? 60.0 : 15.0;

        try
        {
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(timeoutSec));
            using var req = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new StringContent(payload.ToJsonString(), Encoding.UTF8, "application/json"),
            };
            req.Headers.TryAddWithoutValidation("Authorization", "Bearer " + apiKey);
            req.Headers.TryAddWithoutValidation("User-Agent", "JudyNeuralLink/1.0");

            using var resp = await Net.Http.SendAsync(req, cts.Token).ConfigureAwait(false);
            string body = await resp.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);

            if (!resp.IsSuccessStatusCode)
            {
                if (resp.StatusCode == HttpStatusCode.TooManyRequests)
                {
                    double? wait = RetrySeconds(body);
                    if (wait.HasValue) return new Answer("", "calm", false, wait.Value);
                }
                Log.Write($"[judy] API error ({provider}) HTTP {(int)resp.StatusCode}: {body}");
                return new Answer(Fallback, "calm", false);
            }

            string raw = JsonNode.Parse(body)?["choices"]?[0]?["message"]?["content"]?.ToString() ?? "";
            raw = ThinkRe.Replace(raw, "").Trim();

            string answer, emotion;
            try
            {
                var parsed = JsonNode.Parse(raw) as JsonObject ?? throw new JsonException("not an object");
                answer = (parsed["text"]?.ToString() ?? "").Trim();
                emotion = (parsed["emotion"]?.ToString() ?? "").Trim().ToLowerInvariant();
                if (emotion.Length == 0) emotion = "calm";
            }
            catch (Exception)
            {
                answer = raw;
                emotion = "calm";
            }

            if (answer.Length == 0) return new Answer(Fallback, "calm", false);

            lock (_gate)
            {
                _history.Add(("user", userText));
                _history.Add(("assistant", answer));
            }
            return new Answer(answer, emotion, true);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception e)
        {
            Log.Write($"[judy] Connection error: {e.Message}");
            return new Answer(Fallback, "calm", false);
        }
    }
}
