using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace JudyNeuralLink;

/// <summary>
/// Colab -> app tunnel link relay over ntfy.sh (no account needed). Colab publishes the Cloudflare tunnel
/// link to a channel derived from the session code; we derive the same channel and read the latest link.
/// The channel name is a SHA-256 of the code, so the relay never sees the code (= the server password).
/// </summary>
internal static class Relay
{
    private const string Base = "https://ntfy.sh";
    private static readonly Regex UrlRe = new(@"^https://[a-z0-9-]+\.trycloudflare\.com$", RegexOptions.Compiled);

    public static string TopicFor(string code)
    {
        byte[] hash = SHA256.HashData(Encoding.UTF8.GetBytes("judy-topic:" + code));
        return "judy-" + Convert.ToHexString(hash).ToLowerInvariant()[..32];
    }

    /// <summary>Latest valid trycloudflare link on the channel, or null.</summary>
    public static async Task<string?> FetchLatestAsync(string code, CancellationToken ct, string since = "12h", double timeoutSec = 10)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(TimeSpan.FromSeconds(timeoutSec));
        using var req = new HttpRequestMessage(HttpMethod.Get, $"{Base}/{TopicFor(code)}/json?poll=1&since={since}");
        req.Headers.TryAddWithoutValidation("User-Agent", Net.RelayUserAgent);
        using var resp = await Net.Http.SendAsync(req, cts.Token).ConfigureAwait(false);
        resp.EnsureSuccessStatusCode();
        string body = await resp.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);

        string? last = null;
        foreach (string line in body.Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            try
            {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                if (root.ValueKind != JsonValueKind.Object) continue;
                if (!root.TryGetProperty("event", out var ev) || ev.GetString() != "message") continue;
                string msg = (root.TryGetProperty("message", out var m) ? m.GetString() : null)?.Trim() ?? "";
                if (UrlRe.IsMatch(msg)) last = msg;
            }
            catch (JsonException)
            {
                // not a JSON line, skip
            }
        }
        return last;
    }
}
