using System.Runtime.InteropServices;
using System.Text;

namespace JudyNeuralLink;

/// <summary>Keyboard injection (opens / closes the CET overlay), foreground title, WAV playback.</summary>
internal static class Win32
{
    private const uint InputKeyboard = 1;
    private const uint KeyEventFKeyUp = 0x0002;
    private const uint KeyEventFScanCode = 0x0008;
    private const uint SndAsync = 0x0001;
    private const uint SndFileName = 0x00020000;

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseInput
    {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KeybdInput
    {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct HardwareInput
    {
        public uint uMsg;
        public ushort wParamL;
        public ushort wParamH;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MouseInput mi;
        [FieldOffset(0)] public KeybdInput ki;
        [FieldOffset(0)] public HardwareInput hi;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Input
    {
        public uint type;
        public InputUnion u;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint nInputs, Input[] pInputs, int cbSize);

    [DllImport("user32.dll")]
    private static extern uint MapVirtualKeyW(uint uCode, uint uMapType);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder text, int maxCount);

    [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
    private static extern bool PlaySound(string? pszSound, IntPtr hmod, uint fdwSound);

    private static void SendKey(int vk, bool up)
    {
        uint scan = MapVirtualKeyW((uint)vk, 0);
        uint flags = KeyEventFScanCode | (up ? KeyEventFKeyUp : 0u);
        var input = new Input { type = InputKeyboard };
        input.u.ki = new KeybdInput
        {
            wVk = (ushort)vk,
            wScan = (ushort)scan,
            dwFlags = flags,
            time = 0,
            dwExtraInfo = IntPtr.Zero,
        };
        SendInput(1, new[] { input }, Marshal.SizeOf<Input>());
    }

    public static void PressKeys(IReadOnlyList<int> vks, double holdSeconds = 0.15)
    {
        foreach (int vk in vks)
        {
            SendKey(vk, up: false);
            Thread.Sleep(10);
        }
        Thread.Sleep((int)(holdSeconds * 1000));
        for (int i = vks.Count - 1; i >= 0; i--)
        {
            SendKey(vks[i], up: true);
            Thread.Sleep(10);
        }
    }

    public static string ForegroundTitle()
    {
        IntPtr hwnd = GetForegroundWindow();
        var sb = new StringBuilder(256);
        GetWindowTextW(hwnd, sb, 256);
        return sb.ToString();
    }

    public static void PlayWavAsync(string path) => PlaySound(path, IntPtr.Zero, SndFileName | SndAsync);
}
