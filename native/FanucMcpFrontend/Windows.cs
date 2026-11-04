// Original, query-only identities and a raw job HANDLE; no PID adoption/control.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

namespace FanucMcp {
    internal static class Windows {
        [DllImport("kernel32.dll", SetLastError=true)] internal static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError=true)] internal static extern IntPtr OpenProcess(uint rights, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError=true)] internal static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr handle, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr handle, uint flags, StringBuilder image, ref int length);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Limits limits, uint size);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError=true)] internal static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern SafeFileHandle CreateFile(string path, uint access, uint sharing, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetFileInformationByHandleEx(SafeFileHandle handle, int kind, out Tag tag, uint size);
        [StructLayout(LayoutKind.Sequential)] struct Tag { public uint attributes, tag; }
        [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr process, int kind, ref BasicProcess data, uint size, out uint returned);
        [StructLayout(LayoutKind.Sequential)] struct BasicProcess { public IntPtr reserved, peb, r1, r2, pid, parent; }
        [StructLayout(LayoutKind.Sequential)] struct BasicLimits { public long user, jobUser; public uint flags; public UIntPtr minimum, maximum; public uint active; public UIntPtr affinity; public uint priority, scheduling; }
        [StructLayout(LayoutKind.Sequential)] struct IO { public ulong read, write, other, readBytes, writeBytes, otherBytes; }
        [StructLayout(LayoutKind.Sequential)] struct Limits { public BasicLimits basic; public IO io; public UIntPtr processMemory, jobMemory, peakProcess, peakJob; }
        static string host;
        internal static string Host {
            get {
                if (host == null) using (RegistryKey hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                using (RegistryKey key = hklm.OpenSubKey(@"SOFTWARE\Microsoft\Cryptography"))
                    host = Digest(Encoding.UTF8.GetBytes(Guid.Parse((string)key.GetValue("MachineGuid")).ToString()));
                return host;
            }
        }
        internal static string Digest(byte[] bytes) { using (SHA256 sha = SHA256.Create()) return Hex(sha.ComputeHash(bytes)); }
        internal static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
        internal static Dictionary<string, object> Identity(IntPtr handle, int pid) {
            long created, exited, kernel, user; int length = 32768; StringBuilder image = new StringBuilder(length);
            if (!GetProcessTimes(handle, out created, out exited, out kernel, out user) || !QueryFullProcessImageName(handle, 0, image, ref length)) throw new Win32Exception();
            return new Dictionary<string, object> { {"host_id", Host}, {"pid", pid}, {"created", created.ToString(System.Globalization.CultureInfo.InvariantCulture)}, {"executable", Path.GetFullPath(image.ToString()).ToLowerInvariant()} };
        }
        internal static int ParentPid() {
            BasicProcess info = new BasicProcess(); uint returned;
            if (NtQueryInformationProcess(Process.GetCurrentProcess().Handle, 0, ref info, (uint)Marshal.SizeOf(info), out returned) != 0) throw new IOException("Original client parent is unavailable");
            return info.parent.ToInt32();
        }
        internal static string State(Json identity) {
            if (identity.String("host_id") != Host) return "unknown";
            int pid; if (!Int32.TryParse(identity["pid"].Raw, out pid) || pid < 5) return "unknown";
            IntPtr handle = OpenProcess(0x101000, false, pid);
            if (handle == IntPtr.Zero) return Marshal.GetLastWin32Error() == 87 ? "absent" : "unknown";
            try {
                Dictionary<string, object> actual = Identity(handle, pid);
                if ((string)actual["created"] != identity.String("created") || (string)actual["executable"] != identity.String("executable")) return "different";
                return WaitForSingleObject(handle, 0) == 0 ? "absent" : "alive";
            } catch (Win32Exception) { return "unknown"; }
            finally { if (!CloseHandle(handle)) throw new Win32Exception(); }
        }
        internal static IntPtr Contain() {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception();
            Limits limits = new Limits(); limits.basic.flags = 0x2000 | 0x8 | 0x200;
            limits.basic.active = 2; limits.jobMemory = new UIntPtr(128UL * 1024 * 1024);
            if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits))) { CloseHandle(job); throw new Win32Exception(); }
            if (!AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle)) { CloseHandle(job); throw new Win32Exception(); }
            // This self job remains open until OS process exit. Early close kills us.
            return job;
        }
        internal static SafeFileHandle PinDirectory(string path) {
            SafeFileHandle handle = CreateFile(path, 0x80000000, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            if (handle.IsInvalid) throw new Win32Exception();
            try { CheckOrdinary(handle, true); } catch { handle.Dispose(); throw; }
            return handle;
        }
        internal static void CheckOrdinary(SafeFileHandle handle, bool directory) {
            Tag tag;
            if (!GetFileInformationByHandleEx(handle, 9, out tag, (uint)Marshal.SizeOf(typeof(Tag)))) throw new Win32Exception();
            if ((tag.attributes & 0x400) != 0 || ((tag.attributes & 0x10) != 0) != directory) throw new InvalidDataException("Pinned object type/link differs");
        }
    }
}
