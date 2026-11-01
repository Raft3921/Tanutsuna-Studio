// Windows 11 WASAPI shared-loopback capture. No virtual cable or OBS required.
// Compiled locally once by the built-in .NET Framework compiler.
using System;
using System.IO;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class EnumeratorClass {}
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int flow, uint mask, out IntPtr devices);
    [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice device);
    int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, uint ctx, IntPtr activation, [MarshalAs(UnmanagedType.IUnknown)] out object obj);
}
[ComImport, Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioClient {
    [PreserveSig] int Initialize(int mode, uint flags, long duration, long periodicity, IntPtr format, IntPtr session);
    [PreserveSig] int GetBufferSize(out uint frames);
    [PreserveSig] int GetStreamLatency(out long latency);
    [PreserveSig] int GetCurrentPadding(out uint frames);
    [PreserveSig] int IsFormatSupported(int mode, IntPtr format, out IntPtr closest);
    [PreserveSig] int GetMixFormat(out IntPtr format);
    [PreserveSig] int GetDevicePeriod(out long normal, out long minimum);
    [PreserveSig] int Start();
    [PreserveSig] int Stop();
    [PreserveSig] int Reset();
    [PreserveSig] int SetEventHandle(IntPtr handle);
    [PreserveSig] int GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object obj);
}
[ComImport, Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioCaptureClient {
    [PreserveSig] int GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong position, out ulong qpc);
    [PreserveSig] int ReleaseBuffer(uint frames);
    [PreserveSig] int GetNextPacketSize(out uint frames);
}
class Loopback {
    static void Check(int hr) { Marshal.ThrowExceptionForHR(hr); }
    static void Log(string message) {
        Console.Error.WriteLine(DateTime.UtcNow.ToString("o") + " " + message);
        Console.Error.Flush();
    }
    static void WriteZeros(BinaryWriter w, long count) {
        byte[] silence = new byte[65536];
        while (count > 0) { int n = (int)Math.Min(count, silence.Length); w.Write(silence, 0, n); count -= n; }
    }
    public static int Main(string[] args) {
        try {
            Log("PC音録音ヘルパーを開始しました。");
            if (args.Length < 2) throw new Exception("Output WAV path and stop-signal path required");
            string stopPath = args[1];
            IMMDevice device;
            var enumerator = (IMMDeviceEnumerator)new EnumeratorClass();
            Check(enumerator.GetDefaultAudioEndpoint(0, 0, out device));
            Guid clientId = new Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"); object obj;
            Check(device.Activate(ref clientId, 23, IntPtr.Zero, out obj));
            var client = (IAudioClient)obj;
            IntPtr format; Check(client.GetMixFormat(out format));
            ushort tag = (ushort)Marshal.ReadInt16(format, 0);
            ushort channels = (ushort)Marshal.ReadInt16(format, 2);
            int rate = Marshal.ReadInt32(format, 4);
            ushort align = (ushort)Marshal.ReadInt16(format, 12), bits = (ushort)Marshal.ReadInt16(format, 14);
            ushort extra = (ushort)Marshal.ReadInt16(format, 16);
            int formatSize = 18 + extra;
            byte[] fmt = new byte[formatSize]; Marshal.Copy(format, fmt, 0, formatSize);
            // Event-driven WASAPI is supported by Windows 11; bounded 20ms periods.
            using (var ready = new AutoResetEvent(false)) {
                Check(client.Initialize(0, 0x00020000 | 0x00040000, 1000000, 0, format, IntPtr.Zero));
                Marshal.FreeCoTaskMem(format);
                Check(client.SetEventHandle(ready.SafeWaitHandle.DangerousGetHandle()));
                Guid captureId = new Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317");
                Check(client.GetService(ref captureId, out obj)); var capture = (IAudioCaptureClient)obj;
                using (var file = new FileStream(args[0], FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                using (var w = new BinaryWriter(file)) {
                    w.Write(System.Text.Encoding.ASCII.GetBytes("RF64")); w.Write(UInt32.MaxValue);
                    w.Write(System.Text.Encoding.ASCII.GetBytes("WAVEds64")); w.Write(28);
                    long ds64 = file.Position; w.Write((long)0); w.Write((long)0); w.Write((long)0); w.Write(0);
                    w.Write(System.Text.Encoding.ASCII.GetBytes("fmt ")); w.Write(formatSize); w.Write(fmt);
                    w.Write(System.Text.Encoding.ASCII.GetBytes("data")); long lengthPosition = file.Position; w.Write(UInt32.MaxValue);
                    long dataStart = file.Position, writtenFrames = 0;
                    double epoch = (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
                    Check(client.Start());
                    Log("WASAPIループバックを開始しました。停止ファイルを監視しています。");
                    Console.WriteLine("{\"type\":\"ready\",\"role\":\"desktop\",\"pts\":" + epoch.ToString("R", System.Globalization.CultureInfo.InvariantCulture) + "}");
                    while (!File.Exists(stopPath)) {
                        ready.WaitOne(100);
                        uint packets; Check(capture.GetNextPacketSize(out packets));
                        // Keep draining while packets are available during recording, but
                        // never let a continuously busy render device starve the stop signal.
                        while (packets > 0 && !File.Exists(stopPath)) {
                            IntPtr data; uint frames, flags; ulong position, qpc;
                            Check(capture.GetBuffer(out data, out frames, out flags, out position, out qpc));
                            try {
                                // QPC timestamp is expressed in 100ns units by WASAPI.
                                long desired = (long)Math.Round((qpc / 10000000.0 - epoch) * rate);
                                if ((flags & 4) == 0 && desired > writtenFrames) {
                                    long gap = desired - writtenFrames;
                                    WriteZeros(w, gap * align); writtenFrames += gap;
                                }
                                int size = checked((int)frames * align);
                                if ((flags & 2) != 0) WriteZeros(w, size);
                                else { byte[] bytes = new byte[size]; Marshal.Copy(data, bytes, 0, size); w.Write(bytes); }
                                writtenFrames += frames;
                            } finally { Check(capture.ReleaseBuffer(frames)); }
                            Check(capture.GetNextPacketSize(out packets));
                        }
                    }
                    Log("停止ファイルを検出し、音声パケットの取り込みを終了します。");
                    Check(client.Stop());
                    Log("WASAPIを停止しました。RF64ヘッダーを確定します。");
                    // Default render endpoints may send no packets during silence.
                    long target = (long)(((double)Stopwatch.GetTimestamp() / Stopwatch.Frequency - epoch) * rate);
                    if (target > writtenFrames) WriteZeros(w, (target-writtenFrames)*align);
                    long length = file.Length - dataStart;
                    file.Position = ds64; w.Write(file.Length-8); w.Write(length); w.Write(length / align);
                    w.Flush(); file.Flush(true);
                    Log("RF64ファイルを確定しました。" + length + " bytes");
                    Console.WriteLine("{\"type\":\"done\"}");
                }
            }
            return 0;
        } catch (Exception e) { Log("PC音録音エラー: " + e); return 1; }
    }
}
