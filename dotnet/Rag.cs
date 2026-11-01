using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace JudyNeuralLink;

/// <summary>One memory chunk from the RAG file or RAG_branches.json.</summary>
internal sealed class Chunk
{
    public string Id = "";
    public string Content = "";
    public string EmotionalState = "";
    public List<string> Keywords = new();
    public List<string> Anchor = new();
    public List<string> Requires = new();
    public List<string> RequiresNot = new();
    public bool Enabled = true; // only an explicit "enabled": false switches a chunk off
    public double Boost;

    public static Chunk? From(JsonElement e)
    {
        if (e.ValueKind != JsonValueKind.Object) return null;
        string id = Str(e, "id");
        string content = Str(e, "content");
        if (id.Length == 0 || content.Length == 0) return null;

        var c = new Chunk
        {
            Id = id,
            Content = content,
            EmotionalState = Str(e, "emotional_state"),
            Keywords = StrList(e, "keywords"),
            Anchor = StrList(e, "anchor"),
            Requires = StrList(e, "requires"),
            RequiresNot = StrList(e, "requires_not"),
        };
        if (e.TryGetProperty("enabled", out var en) && en.ValueKind == JsonValueKind.False) c.Enabled = false;
        if (e.TryGetProperty("boost", out var b))
        {
            if (b.ValueKind == JsonValueKind.Number) c.Boost = b.GetDouble();
            else if (b.ValueKind == JsonValueKind.String &&
                     double.TryParse(b.GetString(), System.Globalization.NumberStyles.Float,
                         System.Globalization.CultureInfo.InvariantCulture, out double d)) c.Boost = d;
        }
        return c;
    }

    private static string Str(JsonElement e, string name)
    {
        if (!e.TryGetProperty(name, out var p)) return "";
        return p.ValueKind switch
        {
            JsonValueKind.String => p.GetString() ?? "",
            JsonValueKind.Null or JsonValueKind.Undefined => "",
            _ => p.ToString(),
        };
    }

    private static List<string> StrList(JsonElement e, string name)
    {
        var list = new List<string>();
        if (e.TryGetProperty(name, out var p) && p.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in p.EnumerateArray())
                list.Add(item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.ToString());
        }
        return list;
    }
}

/// <summary>
/// Port of judy_rag.py. The core (personality + rules + output format) always goes to the model;
/// memory chunks are added only when they match what V just said. No model call, fully local.
/// </summary>
internal sealed class JudyRag
{
    private const int MaxChunks = 2;
    private const double MinScore = 1.0;
    private const int MaxChunkChars = 1400;
    private const string LoreMark = "# LORE AND MEMORY DATABASE";
    private const string OutputMark = "# OUTPUT FORMAT RULE";

    private static readonly HashSet<string> Generic = new()
    {
        "v", "bd", "help", "past", "memory", "system", "company", "night city", "plan", "boss",
        "power", "lies", "fight", "danger", "death", "doll", "relationship", "sacrifice",
        "promotion", "office", "security", "poor", "mocking", "justice", "guilt", "trauma",
        "relic", "child", "farewell",
    };

    private static readonly Dictionary<string, string[]> Extra = new()
    {
        ["memory_evelyn_suicide"] = new[] { "evie", "evelyn", "banyo", "kuvet", "intihar", "bathroom" },
        ["memory_evelyn_heist_prep"] = new[] { "evie", "evelyn", "soygun", "braindance", "heist" },
        ["memory_evelyn_rescue_scavs"] = new[] { "evie", "evelyn", "scav", "kurtar", "rescue" },
        ["memory_maiko_breakup_office"] = new[] { "maiko", "ayrilik", "ayrildin", "eski sevgili" },
        ["memory_maiko_clothes_argument"] = new[] { "maiko", "kiyafet" },
        ["memory_maiko_clouds_betrayal"] = new[] { "maiko", "ihanet", "hiromi" },
        ["memory_maiko_jealousy_mox_anniversary"] = new[] { "maiko", "kiskanc", "kiskanma" },
        ["memory_clouds_maiko_start"] = new[] { "clouds", "maiko" },
        ["memory_childhood_origins"] = new[] { "cocukluk", "ailen", "annen", "baban", "dedesi", "family", "granddad" },
        ["memory_childhood_streets"] = new[] { "cocukken", "hamburger", "hockey" },
        ["memory_grandmother_ainara"] = new[] { "babaanne", "nine", "grandma", "sinirli", "temper" },
        ["memory_jenni_chapman_doll"] = new[] { "jenni", "ilk ask", "first crush" },
        ["memory_laguna_bend_eviction"] = new[] { "laguna", "baraj", "reservoir" },
        ["memory_firetruck_injustice"] = new[] { "itfaiye", "polis", "hapis", "cops", "cop" },
        ["memory_lizzies_bar_mox"] = new[] { "mox", "lizzie", "susie" },
        ["memory_clouds_takeover_plan"] = new[] { "pizza", "roxanne", "seizure", "nobet" },
    };

    private readonly string _head;
    private readonly string _tail = "";
    public List<Chunk> Chunks { get; } = new();

    public JudyRag(string raw)
    {
        raw = raw.Replace("\r\n", "\n");
        int iLore = raw.IndexOf(LoreMark, StringComparison.Ordinal);
        int iOut = raw.IndexOf(OutputMark, StringComparison.Ordinal);
        if (iLore < 0 || iOut < 0 || iOut < iLore)
        {
            _head = raw.Trim();
            Log.Write("[judy-rag] WARNING: RAG headings not found, everything is treated as core.");
            return;
        }

        _head = raw[..iLore].TrimEnd();
        _tail = raw[iOut..].Trim();
        string lore = raw[iLore..iOut];
        int a = lore.IndexOf('[');
        int b = lore.LastIndexOf(']');
        if (a >= 0 && b > a)
        {
            try
            {
                using var doc = JsonDocument.Parse(lore.Substring(a, b - a + 1),
                    new JsonDocumentOptions { AllowTrailingCommas = true, CommentHandling = JsonCommentHandling.Skip });
                if (doc.RootElement.ValueKind == JsonValueKind.Array)
                {
                    foreach (var el in doc.RootElement.EnumerateArray())
                    {
                        var c = Chunk.From(el);
                        if (c != null) Chunks.Add(c);
                    }
                }
            }
            catch (JsonException ex)
            {
                Log.Write($"[judy-rag] WARNING: could not read the JSON array, continuing without chunks: {ex.Message}");
            }
        }
    }

    /// <summary>Adds branch chunks. Invalid entries are skipped (already filtered by Chunk.From).</summary>
    public int AddChunks(IEnumerable<Chunk> chunks)
    {
        int n = 0;
        foreach (var c in chunks)
        {
            Chunks.Add(c);
            n++;
        }
        return n;
    }

    private static bool Eligible(Chunk c, HashSet<string> state)
    {
        if (!c.Enabled) return false;
        foreach (string r in c.Requires)
            if (!state.Contains(r)) return false;
        foreach (string n in c.RequiresNot)
            if (state.Contains(n)) return false;
        return true;
    }

    /// <summary>Folds Turkish letters so spellings like "ihanet" and "kiskanc" still match.</summary>
    private static string Fold(string? s)
    {
        string lower = (s ?? "").ToLowerInvariant();
        var sb = new StringBuilder(lower.Length);
        foreach (char ch in lower)
        {
            sb.Append(ch switch
            {
                'ç' => 'c', // c cedilla
                'ğ' => 'g', // g breve
                'ı' => 'i', // dotless i
                'ö' => 'o',
                'ş' => 's', // s cedilla
                'ü' => 'u',
                'İ' => 'i', // capital I with dot
                _ => ch,
            });
        }
        return sb.ToString().Replace("i̇", "i");
    }

    /// <summary>Whole-word search. Short words (3 letters or fewer) must match exactly; longer ones may take a suffix.</summary>
    private static bool Has(string text, string kw)
    {
        kw = kw.Trim().ToLowerInvariant();
        if (kw.Length == 0) return false;
        string tail = kw.Length <= 3 ? "(?![a-z0-9])" : "[a-z]{0,3}(?![a-z0-9])";
        return Regex.IsMatch(text, "(?<![a-z0-9])" + Regex.Escape(kw) + tail, RegexOptions.CultureInvariant);
    }

    private static double Score(Chunk c, string query, string recent)
    {
        if (c.Anchor.Count > 0 && !c.Anchor.Any(a => Has(query, Fold(a)) || Has(recent, Fold(a))))
            return 0.0;

        var kws = new List<string>(c.Keywords);
        if (Extra.TryGetValue(c.Id, out var extra)) kws.AddRange(extra);

        double s = 0.0;
        var seen = new HashSet<string>();
        foreach (string kw in kws)
        {
            string k = Fold(kw);
            if (!seen.Add(k)) continue;
            if (Generic.Contains(k)) continue;
            bool strong = k.Contains(' ') || k.Length > 5;
            if (Has(query, k)) s += strong ? 2.0 : 1.0;
            else if (recent.Length > 0 && Has(recent, k)) s += strong ? 0.5 : 0.25;
        }
        if (s >= MinScore) s += c.Boost;
        return s;
    }

    public List<Chunk> Select(string userText, string recentText, HashSet<string> state)
    {
        string q = Fold(userText);
        string r = Fold(recentText);
        return Chunks
            .Where(c => Eligible(c, state))
            .Select(c => (Score: Score(c, q, r), Chunk: c))
            .Where(x => x.Score >= MinScore)
            .OrderByDescending(x => x.Score) // stable, same as Python's sort
            .Take(MaxChunks)
            .Select(x => x.Chunk)
            .ToList();
    }

    public string BuildPrompt(string userText, string recentText, string extra, HashSet<string> state)
    {
        var picked = Select(userText, recentText, state);
        var parts = new List<string> { _head };
        if (picked.Count > 0)
        {
            parts.Add("# MEMORIES RELEVANT TO WHAT V JUST SAID\n" +
                      "These are your own memories. Use them naturally, in your own words. Do not recite them.");
            foreach (var c in picked)
            {
                string content = c.Content;
                if (content.Length > MaxChunkChars)
                {
                    string cut = content[..MaxChunkChars];
                    int sp = cut.LastIndexOf(' ');
                    content = (sp >= 0 ? cut[..sp] : cut) + "...";
                }
                parts.Add($"- {content}\n  (Feeling: {c.EmotionalState})");
            }
        }
        else
        {
            parts.Add("# MEMORIES\nNo specific memory is triggered. Talk from your normal life and feelings.");
        }
        if (extra.Length > 0) parts.Add(extra);
        parts.Add(_tail);
        return string.Join("\n\n", parts.Where(p => p.Length > 0));
    }

    public List<string> PickedIds(string userText, string recentText, HashSet<string> state) =>
        Select(userText, recentText, state).Select(c => c.Id).ToList();
}
