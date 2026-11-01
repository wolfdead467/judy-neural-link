using System.Text;

namespace JudyNeuralLink;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        // Needed for the Turkish code page (cp1254) used when reading files written by the game.
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

        using var mutex = new Mutex(true, @"Local\JudyNeuralLink.SingleInstance", out bool first);
        if (!first)
        {
            MessageBox.Show("Judy Neural-Link is already running.", "Judy Neural-Link", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
    }
}
