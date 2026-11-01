namespace JudyNeuralLink;

internal static class Net
{
    /// <summary>One shared client. Every call sets its own timeout with a CancellationTokenSource.</summary>
    public static readonly HttpClient Http = new() { Timeout = Timeout.InfiniteTimeSpan };

    public const string RelayUserAgent = "Mozilla/5.0 JudyNeuralLink/1.0";
}
