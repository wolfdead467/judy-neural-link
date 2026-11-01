using System.Text;

namespace JudyNeuralLink;

/// <summary>File helpers for the CET file protocol (same behavior as the Python agent).</summary>
internal static class AgentFiles
{
    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private static readonly UnicodeEncoding StrictUtf16Le = new(false, false, true);
    private static readonly UnicodeEncoding StrictUtf16Be = new(true, false, true);
    private static readonly object StampGate = new();
    private static long _lastStamp;

    /// <summary>
    /// Reads a text file the game may be writing at the same moment. Tries UTF-8, UTF-16, then Turkish cp1254,
    /// strips NUL characters and whitespace. Returns null if it cannot be read or decoded.
    /// </summary>
    public static string? ReadText(string path)
    {
        byte[] raw;
        try
        {
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var ms = new MemoryStream();
            fs.CopyTo(ms);
            raw = ms.ToArray();
        }
        catch (IOException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }

        string? text = TryDecode(raw);
        return text?.Replace("\0", "").Trim();
    }

    private static string? TryDecode(byte[] raw)
    {
        try
        {
            int skip = raw.Length >= 3 && raw[0] == 0xEF && raw[1] == 0xBB && raw[2] == 0xBF ? 3 : 0;
            return StrictUtf8.GetString(raw, skip, raw.Length - skip);
        }
        catch (DecoderFallbackException)
        {
        }

        try
        {
            if (raw.Length >= 2 && raw[0] == 0xFE && raw[1] == 0xFF)
                return StrictUtf16Be.GetString(raw, 2, raw.Length - 2);
            int off = raw.Length >= 2 && raw[0] == 0xFF && raw[1] == 0xFE ? 2 : 0;
            return StrictUtf16Le.GetString(raw, off, raw.Length - off);
        }
        catch (DecoderFallbackException)
        {
        }

        try
        {
            return Encoding.GetEncoding(1254, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback).GetString(raw);
        }
        catch (Exception)
        {
            return null;
        }
    }

    public static void RemoveQuiet(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch
        {
            // already gone, or still in use: not a problem
        }
    }

    /// <summary>Write to a temp file, then move it over the target so the game never reads half a file.</summary>
    public static void WriteAtomic(string path, string content)
    {
        string tmp = path + ".tmp";
        File.WriteAllText(tmp, content, new UTF8Encoding(false));
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                File.Move(tmp, path, overwrite: true);
                return;
            }
            catch (IOException) when (attempt < 4)
            {
                Thread.Sleep(50);
            }
            catch (UnauthorizedAccessException) when (attempt < 4)
            {
                Thread.Sleep(50);
            }
        }
    }

    /// <summary>Unique, always increasing number (the Lua side only checks that it changed).</summary>
    public static long NextStamp()
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() * 1_000_000L;
        lock (StampGate)
        {
            if (now <= _lastStamp) now = _lastStamp + 1;
            _lastStamp = now;
            return now;
        }
    }
}
