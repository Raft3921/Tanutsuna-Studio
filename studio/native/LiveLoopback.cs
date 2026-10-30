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
class LiveLoopback {
    static volatile bool stopping;
    static void Check(int hr) { Marshal.ThrowExceptionForHR(hr); }
    public static int Main(string[] args) {
        try {
            IMMDevice device; var enumerator=(IMMDeviceEnumerator)new EnumeratorClass();
            Check(enumerator.GetDefaultAudioEndpoint(0,0,out device));
            Guid id=new Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2");object obj;
            Check(device.Activate(ref id,23,IntPtr.Zero,out obj));var client=(IAudioClient)obj;
            IntPtr format;Check(client.GetMixFormat(out format));
            ushort tag=(ushort)Marshal.ReadInt16(format,0),channels=(ushort)Marshal.ReadInt16(format,2);
            int rate=Marshal.ReadInt32(format,4),align=(ushort)Marshal.ReadInt16(format,12),bits=(ushort)Marshal.ReadInt16(format,14);
            bool floating=tag==3 || (tag==65534 && Marshal.ReadInt32(format,24)==3);
            using(var ready=new AutoResetEvent(false)) {
                Check(client.Initialize(0,0x00020000|0x00040000,1000000,0,format,IntPtr.Zero));Marshal.FreeCoTaskMem(format);
                Check(client.SetEventHandle(ready.SafeWaitHandle.DangerousGetHandle()));
                id=new Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317");Check(client.GetService(ref id,out obj));var capture=(IAudioCaptureClient)obj;
                Check(client.Start());
                Console.WriteLine(rate+" "+channels+" "+bits+" "+(floating?"float":"int"));Console.Out.Flush();
                var output=Console.OpenStandardOutput();
                new Thread(delegate(){Console.ReadLine();stopping=true;}){IsBackground=true}.Start();
                while(!stopping) {
                    ready.WaitOne(100);uint packets;Check(capture.GetNextPacketSize(out packets));
                    while(packets>0) {
                        IntPtr data;uint frames,flags;ulong position,qpc;
                        Check(capture.GetBuffer(out data,out frames,out flags,out position,out qpc));
                        try {
                            byte[] bytes=new byte[checked((int)frames*align)];
                            if((flags&2)==0)Marshal.Copy(data,bytes,0,bytes.Length);
                            output.Write(bytes,0,bytes.Length);output.Flush();
                        }finally{Check(capture.ReleaseBuffer(frames));}
                        Check(capture.GetNextPacketSize(out packets));
                    }
                }
                Check(client.Stop());
            }
            return 0;
        }catch{Console.Error.WriteLine("PC音を開始できません。出力機器を確認してください。");return 1;}
    }
}
