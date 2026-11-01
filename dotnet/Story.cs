using System.Text.Json;
using System.Text.RegularExpressions;

namespace JudyNeuralLink;

/// <summary>
/// Story tracking: the Lua mod writes judy_brain.json ({"done": ["heist", ...]}); this turns it into
/// the CURRENT GAME STATE values and the "shared history" block of the prompt. Port of poll_brain().
/// </summary>
internal sealed class StoryState
{
    // (id, Judy knows about it, English sentence). Chronological order lives here.
    private static readonly (string Id, bool Knows, string Text)[] Events =
    {
        ("heist", true, "The Konpeki Plaza heist went wrong, and Jackie died in it."),
        ("heroes", true, "Jackie's funeral happened."),
        ("both_sides", true, "Evelyn took her own life in Judy's apartment, and V helped Judy afterwards."),
        ("ex_factor", true, "Judy and V went through the mess with Maiko and Clouds together."),
        ("pizza", true, "Judy, V, Maiko, Tom and Roxanne planned the Clouds takeover over pizza."),
        ("pisces", true, "The Clouds takeover is over, and Hiromi Sato is dead."),
        ("maiko_dead", true, "V killed Maiko in the Clouds showdown, and Judy was in shock about it."),
        ("maiko_alive", true, "Maiko came out of the takeover alive."),
        ("judy_lover", true, "V and Judy are together."),
        ("judy_friend", true, "V and Judy stayed friends, nothing romantic."),
        ("sinnerman", false, "the Sinnerman job"),
    };

    private const int SharedHistoryMax = 12;
    private const string DefaultRomance = "Active";

    private readonly object _gate = new();
    private readonly Dictionary<string, string> _game = new()
    {
        ["V_ROMANCE_STATUS"] = DefaultRomance,
        ["EVELYN_STATUS"] = "unknown",
        ["MAIKO_STATUS"] = "unknown",
        ["CURRENT_TIME_WEATHER"] = "night, quiet in the apartment",
        ["V_OUTFIT_STYLE"] = "casual",
        ["LATEST_QUEST_CONTEXT"] = "unknown",
    };

    private long _brainStamp;
    private HashSet<string> _done = new();   // ids that have a summary sentence
    private HashSet<string> _state = new();  // every id Lua wrote (plus derived ones)

    /// <summary>Never modified after it is published, so callers can read it without a lock.</summary>
    public HashSet<string> StateIds
    {
        get { lock (_gate) return _state; }
    }

    public int DoneCount
    {
        get { lock (_gate) return _done.Count; }
    }

    public string Get(string key)
    {
        lock (_gate) return _game.TryGetValue(key, out var v) ? v : "unknown";
    }

    /// <summary>Replaces {PLACEHOLDER} markers in the prompt with game-state values.</summary>
    public string Fill(string text) =>
        Regex.Replace(text, @"\{\s*([A-Z_]+)\s*\}", m => Get(m.Groups[1].Value));

    public void Poll(string brainFile)
    {
        long stamp;
        try
        {
            if (!File.Exists(brainFile)) return;
            stamp = File.GetLastWriteTimeUtc(brainFile).Ticks;
        }
        catch
        {
            return;
        }
        if (stamp == _brainStamp) return;

        string? raw = AgentFiles.ReadText(brainFile);
        if (string.IsNullOrEmpty(raw)) return;

        var all = new HashSet<string>();
        try
        {
            using var doc = JsonDocument.Parse(raw);
            if (doc.RootElement.ValueKind != JsonValueKind.Object) return;
            if (doc.RootElement.TryGetProperty("done", out var done) && done.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in done.EnumerateArray())
                {
                    string s = (item.ValueKind == JsonValueKind.String ? item.GetString() : item.ToString())?.Trim() ?? "";
                    if (s.Length > 0) all.Add(s);
                }
            }
        }
        catch (JsonException)
        {
            return; // Lua may be writing right now; the stamp is not updated, so we try again next round
        }
        _brainStamp = stamp;

        // Derived outcome: the takeover finished and Maiko is not dead -> alive.
        if (all.Contains("pisces") && !all.Contains("maiko_dead")) all.Add("maiko_alive");

        var withSummary = new HashSet<string>(Events.Select(e => e.Id));
        var ids = new HashSet<string>(all.Where(withSummary.Contains));
        var doneEvents = Events.Where(e => ids.Contains(e.Id)).ToList();
        var hidden = doneEvents.Where(e => !e.Knows).ToList();

        lock (_gate)
        {
            _state = all;
            _done = ids;
            _game["LATEST_QUEST_CONTEXT"] = hidden.Count > 0 ? hidden[^1].Text : "unknown";
            _game["EVELYN_STATUS"] = ids.Contains("both_sides") ? "Dead (she took her own life)" : "unknown";
            if (ids.Contains("maiko_dead")) _game["MAIKO_STATUS"] = "Dead";
            else if (ids.Contains("maiko_alive")) _game["MAIKO_STATUS"] = "Alive (she survived the Clouds takeover)";
            else _game["MAIKO_STATUS"] = "unknown";
            _game["V_ROMANCE_STATUS"] = ids.Contains("judy_friend") && !ids.Contains("judy_lover")
                ? "Friends only (not romantic)"
                : DefaultRomance;
        }

        Log.Write($"[judy-brain] finished: [{string.Join(", ", doneEvents.Select(e => e.Id))}] | latest(hidden): {Get("LATEST_QUEST_CONTEXT")}");
        Log.Write($"[judy-brain] all state ids: [{string.Join(", ", all.OrderBy(x => x, StringComparer.Ordinal))}]");
        Log.Write($"[judy-brain] romance='{Get("V_ROMANCE_STATUS")}' maiko='{Get("MAIKO_STATUS")}' evelyn='{Get("EVELYN_STATUS")}'");
    }

    /// <summary>Things Judy lived through, as one short prompt block. Empty if nothing happened yet.</summary>
    public string SharedHistory()
    {
        HashSet<string> done;
        lock (_gate) done = _done;
        var lines = Events.Where(e => e.Knows && done.Contains(e.Id)).Select(e => e.Text).ToList();
        if (lines.Count > SharedHistoryMax) lines = lines.GetRange(lines.Count - SharedHistoryMax, SharedHistoryMax);
        if (lines.Count == 0) return "";
        return "# SHARED HISTORY WITH V (things you lived through together)\n"
               + string.Join(" ", lines)
               + "\nThese events and the statuses in CURRENT GAME STATE are CERTAIN knowledge: you were there or you know the outcome."
               + "\nIf V asks about any of them, answer directly and confidently with your real feelings. Never say you are unsure,"
               + " never say you only heard rumors, and do not ask V whether they saw something about these."
               + "\nRule 7 (rumors) applies ONLY to the single job named in rule 6, never to anything listed here."
               + "\nDo not bring these up yourself unless V does.";
    }
}
