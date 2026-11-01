using System.Text;

namespace JudyNeuralLink;

/// <summary>Appends to %APPDATA%\JudyNeuralLink\agent.log (the launcher tells players to open it).</summary>
internal static class Log
{
    private static readonly object Gate = new();
    private static readonly UTF8Encoding Utf8 = new(false);

    public static string FilePath => Path.Combine(Config.DataDir, "agent.log");

    public static void Write(string line)
    {
        try
        {
            lock (Gate)
            {
                File.AppendAllText(FilePath, $"{DateTime.Now:HH:mm:ss} {line}{Environment.NewLine}", Utf8);
            }
        }
        catch
        {
            // Logging must never crash the app.
        }
    }
}
