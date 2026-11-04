// App-owned discovery frontend. Windows/.NET Framework 4.8; no Python at discovery.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace FanucMcp {
    internal sealed class Frontend : IDisposable {
        const int FrameLimit = 8 * 1024 * 1024;
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        readonly List<IDisposable> pins = new List<IDisposable>();
        readonly HashSet<string> pinnedDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        readonly object outputLock = new object(), stateLock = new object();
        readonly Stream input = Console.OpenStandardInput(), output = Console.OpenStandardOutput();
        readonly BlockingCollection<byte[]> incoming = new BlockingCollection<byte[]>(16);
        readonly HashSet<string> cancelled = new HashSet<string>(StringComparer.Ordinal);
        readonly JavaScriptSerializer serializer = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024, RecursionLimit = 64 };
        readonly Dictionary<string, object> identity, parent;
        readonly IntPtr parentHandle, job;
        readonly string root, folder, snapshot, token, receiptPath, diagnostics;
        Dictionary<string, object> record;
        Json manifest, catalog;
        byte[] initialization, initializedNotification;
        Process backend;
        IntPtr originalBackend;
        Dictionary<string, object> backendIdentity;
        volatile bool activated;
        bool attempted;
        Task parentTask, outputTask, errorTask;
        volatile bool eof, stop;
        Exception inputFailure;

        static void Need(bool valid, string message) { if (!valid) throw new InvalidDataException(message); }
        string Encode(object value) { lock (serializer) return serializer.Serialize(value); }
        static string Full(string path) { return Path.GetFullPath(path); }
        static string Child(string directory, string relative) {
            Need(!String.IsNullOrEmpty(relative) && !Path.IsPathRooted(relative) && !relative.Contains(":") && !relative.Contains("\\"), "Invalid manifest member");
            string result = Full(Path.Combine(directory, relative.Replace('/', Path.DirectorySeparatorChar)));
            Need(result.StartsWith(Full(directory) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase), "Manifest path escapes its root");
            return result;
        }
        void PinParents(string directory) {
            if (directory == null || pinnedDirectories.Contains(directory)) return;
            PinParents(Path.GetDirectoryName(directory));
            Need((File.GetAttributes(directory) & FileAttributes.ReparsePoint) == 0, "Linked paths are unsupported");
            pins.Add(Windows.PinDirectory(directory)); pinnedDirectories.Add(directory);
        }
        byte[] Pin(string path, string checksum, long expectedSize, int maximum, bool content = false) {
            path = Full(path); PinParents(Path.GetDirectoryName(path));
            Need((File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0, "Linked payload refused");
            FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            pins.Add(stream);
            Windows.CheckOrdinary(stream.SafeFileHandle, false);
            Need((expectedSize < 0 || stream.Length == expectedSize) && stream.Length <= maximum, "Pinned payload size differs");
            string actual; using (SHA256 sha = SHA256.Create()) actual = Windows.Hex(sha.ComputeHash(stream));
            Need(actual == checksum, "Pinned payload hash differs");
            stream.Position = 0;
            // Large runtime files stay pinned without an additional retained buffer.
            if (!content) return null;
            byte[] result = new byte[(int)stream.Length]; int used = 0, count;
            while (used < result.Length && (count = stream.Read(result, used, result.Length - used)) != 0) used += count;
            Need(used == result.Length, "Incomplete pinned payload"); stream.Position = 0; return result;
        }
        void PinTree(string directory, Json entries) {
            Need(entries != null && entries.Array != null && entries.Array.Count < 20000, "Invalid payload inventory");
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (Json row in entries.Array) {
                string member = Child(directory, row.String("path")); Need(seen.Add(member), "Duplicate payload path");
                long size; Need(Int64.TryParse(row["bytes"].Raw, out size) && size >= 0, "Invalid payload size");
                Pin(member, row.String("sha256"), size, 256 * 1024 * 1024);
            }
        }
        static byte[] ReadFile(string path, int maximum) {
            Need((File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0, "Linked metadata refused");
            using (FileStream source = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) {
                Need(source.Length <= maximum, "Metadata exceeds its limit");
                byte[] result = new byte[(int)source.Length]; int used = 0, count;
                while (used < result.Length && (count = source.Read(result, used, result.Length - used)) != 0) used += count;
                Need(used == result.Length, "Metadata changed during read"); return result;
            }
        }
        void Atomic(string path, object value) {
            string temporary = Path.Combine(Path.GetDirectoryName(path), ".lazy-" + Guid.NewGuid().ToString("N"));
            byte[] bytes = Utf8.GetBytes(Encode(value) + "\n");
            using (FileStream stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
            if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path);
        }
        FileStream Admission() {
            string path = Path.Combine(root, "sessions", "admission.lock");
            FileStream stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
            if (stream.Length == 0) { stream.WriteByte(48); stream.Flush(); }
            Stopwatch deadline = Stopwatch.StartNew();
            while (true) {
                try { stream.Lock(0, 1); return stream; }
                catch (IOException) { if (deadline.ElapsedMilliseconds >= 5000) { stream.Dispose(); throw; } Thread.Sleep(20); }
            }
        }
        void WriteState(string state, object children, string reason) {
            lock (stateLock) {
                record["state"] = state; record["app_created_children"] = children; record["reason"] = reason;
                Atomic(receiptPath, record);
            }
        }
        void Event(string name, object details) {
            if (diagnostics == null) return;
            lock (stateLock) File.AppendAllText(diagnostics, Encode(new Dictionary<string, object> { {"event", name}, {"details", details}, {"utc", DateTime.UtcNow.ToString("o")} }) + "\n", Utf8);
        }
        internal Frontend(string manifestPath, string hash, string diagnosticPath) {
            job = Windows.Contain(); // Two original actors share the former one-actor 128 MiB budget.
            folder = Path.GetDirectoryName(Full(manifestPath));
            manifest = Json.Parse(Utf8.GetString(Pin(manifestPath, hash, -1, 8 * 1024 * 1024, true)));
            Need(manifest["schema_version"].Raw == "1" && manifest.String("app") == "fanuc-mcp-server" && manifest.String("profile") == "standalone-offline", "Unsupported frontend profile");
            root = Full(manifest.String("backend_root")); snapshot = manifest.String("snapshot_id");
            Json owner = Json.Parse(Utf8.GetString(Pin(Path.Combine(root, "application.json"), manifest.String("application_sha256"), -1, 65536, true)));
            Need(owner.String("app") == "fanuc-mcp-server" && owner["schema_version"].Raw == "1", "Installation ownership differs");
            Need(snapshot != null && System.Text.RegularExpressions.Regex.IsMatch(snapshot, "^[a-f0-9]{64}$"), "Invalid pinned selection");
            HashSet<string> members = new HashSet<string>(StringComparer.Ordinal);
            foreach (Json row in manifest["files"].Array) {
                Need(members.Add(row.String("path")), "Duplicate frontend member");
                Pin(Child(folder, row.String("path")), row.String("sha256"), Int64.Parse(row["bytes"].Raw), 8 * 1024 * 1024);
            }
            Need(members.SetEquals(new [] {"FanucMcpFrontend.exe", "FanucMcpFrontend.exe.config", "Backend.py", "LegacyEntry.py", "catalog.json"}), "Incomplete frontend closure");
            string executing = Full(Assembly.GetExecutingAssembly().Location);
            Need(String.Equals(executing, Full(Path.Combine(folder, "FanucMcpFrontend.exe")), StringComparison.OrdinalIgnoreCase), "Executing frontend is outside its closure");
            string selectedPath = Path.Combine(root, "launcher", "snapshots", snapshot, "manifest.json");
            Json selected = Json.Parse(Utf8.GetString(Pin(selectedPath, snapshot, -1, 8 * 1024 * 1024, true)));
            Need(selected.String("profile") == "standalone-offline" && selected.String("sdk") == "2.3.0" && selected.String("framework") == "26.1103.174507", "Selected profile or dependency pin differs");
            Need(manifest.String("runtime") == selected.String("runtime"), "Backend runtime differs");
            string runtime = Child(root, "runtimes/" + selected.String("runtime"));
            PinTree(Path.Combine(root, "python"), selected["python_files"]); PinTree(runtime, selected["runtime_files"]);
            Json policy = Json.Parse(Utf8.GetString(ReadFile(Path.Combine(runtime, "policy.json"), 1024 * 1024)));
            Need(policy["robots"].Object.Count == 0 && policy["allow_writes"].Raw == "false", "Offline policy required");
            Need(Windows.Digest(ReadFile(Path.Combine(folder, "LegacyEntry.py"), 1024 * 1024)) == selected.String("launcher_sha256"), "Legacy entry binding differs");
            catalog = Json.Parse(Utf8.GetString(ReadFile(Path.Combine(folder, "catalog.json"), 8 * 1024 * 1024)));
            Need(catalog.String("snapshot_id") == snapshot, "Catalog selection differs");
            identity = Windows.Identity(Process.GetCurrentProcess().Handle, Process.GetCurrentProcess().Id);
            int parentPid = Windows.ParentPid(); parentHandle = Windows.OpenProcess(0x101000, false, parentPid);
            if (parentHandle == IntPtr.Zero) throw new Win32Exception();
            parent = Windows.Identity(parentHandle, parentPid);
            token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string sessions = Path.Combine(root, "sessions"); Directory.CreateDirectory(sessions); PinParents(sessions);
            receiptPath = Path.Combine(sessions, Guid.NewGuid().ToString("N") + ".json");
            diagnostics = diagnosticPath == null ? null : Full(diagnosticPath);
            record = NewRecord("starting", new object[0]);
            using (FileStream gate = Admission()) {
                int active = 0, legacy = 0;
                foreach (string path in Directory.GetFiles(sessions, "*.json")) {
                    Json prior = Json.Parse(Utf8.GetString(ReadFile(path, 65536)));
                    Need(prior.String("profile") == "standalone-offline" && prior["native_intents"].Raw == "false", "Uncertain prior ownership");
                    bool pending = prior["app_created_children"].Array.Count != 0;
                    string state = Windows.State(prior["identity"]);
                    Need(!pending || (prior["lazy_frontend"] != null && prior["lazy_frontend"].Raw == "true" && state == "alive"), "Uncertain prior children need inspection");
                    bool terminal = new [] {"closed", "failed", "parent-exited", "reconciled-dead"}.Contains(prior.String("state"));
                    if (state == "alive" || (state == "unknown" && terminal)) {
                        active++; Json limit = prior["containment"];
                        if (limit == null || Int64.Parse(limit["commit_bytes"].Raw) > 128L * 1024 * 1024) legacy++;
                    } else Need(state == "absent" || state == "different", "Prior process is unverifiable");
                }
                Need(active < 32 && legacy <= 8, "Offline connection capacity reached; peers unchanged");
                Atomic(receiptPath, record); gate.Unlock(0, 1);
            }
            Event("frontend_ready", new { identity, parent, snapshot, backend_starts = 0 });
            parentTask = Task.Factory.StartNew(delegate {
                while (!stop && Windows.WaitForSingleObject(parentHandle, 100) != 0) { }
                if (!stop) { Event("parent_exited", parent); Environment.Exit(74); }
            }, TaskCreationOptions.LongRunning);
        }
        Dictionary<string, object> NewRecord(string state, object children) {
            return new Dictionary<string, object> { {"schema_version", 1}, {"profile", "standalone-offline"}, {"snapshot", new { schema_version = 1, snapshot_id = snapshot }},
                {"identity", identity}, {"parent", parent}, {"state", state}, {"app_created_children", children}, {"native_intents", false}, {"lazy_frontend", true},
                {"containment", new { actor_limit = 2, commit_bytes = 134217728, kill_on_owner_exit = true }},
                {"budget", new { connections = 32, steady_max_bytes = 4294967296L, transition_max_bytes = 7516192768L }}, {"reason", "Pinned frontend and optional backend share one connection/memory budget"} };
        }
        static byte[] Frame(Stream stream) {
            using (MemoryStream line = new MemoryStream()) {
                int value;
                while ((value = stream.ReadByte()) != -1) {
                    if (line.Length >= FrameLimit) throw new InvalidDataException("Pre-activation frame exceeds 8 MiB");
                    line.WriteByte((byte)value); if (value == 10) return line.ToArray();
                }
                Need(line.Length == 0, "Incomplete application frame at EOF"); return null;
            }
        }
        void Send(string value) { byte[] bytes = Utf8.GetBytes(value + "\n"); lock (outputLock) { output.Write(bytes, 0, bytes.Length); output.Flush(); } }
        void Reply(Json request, string result) { Send("{\"jsonrpc\":\"2.0\",\"id\":" + request["id"].Raw + ",\"result\":" + result + "}"); }
        void Error(Json request, string message, int code) { if (request["id"] != null) Send("{\"jsonrpc\":\"2.0\",\"id\":" + request["id"].Raw + ",\"error\":{\"code\":" + code + ",\"message\":" + Json.Quote(message) + "}}"); }
        void ReadInput() {
            try {
                while (true) {
                    byte[] frame = null;
                    Json node = null;
                    try {
                    if (activated) {
                        byte[] block = new byte[8192]; int count = input.Read(block, 0, block.Length);
                        if (count == 0) break;
                        frame = new byte[count]; Buffer.BlockCopy(block, 0, frame, 0, count);
                    } else frame = Frame(input);
                    if (frame == null) break;
                    if (!activated) {
                        node = Json.Parse(Utf8.GetString(frame));
                        if (node.String("method") == "notifications/cancelled" && node["params"] != null && node["params"]["requestId"] != null)
                            lock (cancelled) cancelled.Add(IdKey(node["params"]["requestId"]));
                    }
                    incoming.Add(frame);
                    } finally { frame = null; node = null; }
                }
            } catch (Exception error) { inputFailure = error; }
            finally { eof = true; incoming.CompleteAdding(); }
        }
        static string IdKey(Json id) { return id.Text == null ? "n:" + id.Raw : "s:" + id.Text; }
        bool IsCancelled(Json request) { lock (cancelled) return request["id"] != null && cancelled.Contains(IdKey(request["id"])); }
        static bool CacheableParams(Json parameters) {
            if (parameters == null || parameters.Raw == "null") return true;
            return parameters.Object != null && parameters.Object.All(pair =>
                (pair.Key == "cursor" && pair.Value.Raw == "null") ||
                (pair.Key == "_meta" && (pair.Value.Raw == "null" || (pair.Value.Object != null && pair.Value.Object.All(meta =>
                    meta.Key == "progressToken" && (meta.Value.Text != null || meta.Value.Raw == "null" ||
                        System.Text.RegularExpressions.Regex.IsMatch(meta.Value.Raw, @"^-?(?:0|[1-9][0-9]*)$")))))));
        }
        string InitializeResult(Json request) {
            string version = request["params"] == null ? null : request["params"].String("protocolVersion");
            string[] supported = catalog["protocol_versions"].Array.Select(value => value.Text).ToArray();
            if (!supported.Contains(version)) version = catalog["initialize"].String("protocolVersion");
            Json result = catalog["initialize"];
            return "{" + String.Join(",", result.Object.Select(pair => Json.Quote(pair.Key) + ":" + (pair.Key == "protocolVersion" ? Json.Quote(version) : pair.Value.Raw)).ToArray()) + "}";
        }
        static string QuoteArgument(string argument) {
            StringBuilder b = new StringBuilder("\""); int slashes = 0;
            foreach (char c in argument) {
                if (c == '\\') { slashes++; continue; }
                b.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); b.Append(c); slashes = 0;
            }
            return b.Append('\\', slashes * 2).Append('"').ToString();
        }
        void Activate() {
            Need(!attempted && initialization != null && initializedNotification != null, "Backend activation is unavailable before initialization"); attempted = true;
            lock (stateLock) {
                record["handoff_nonce"] = token;
                WriteState("activating", new object[] { new { state = "launch-intent" } }, "One backend launch intent; no operation dispatched");
            }
            ProcessStartInfo start = new ProcessStartInfo(Path.Combine(root, "python", "pythonw.exe"));
            string[] args = {"-I", "-B", Path.Combine(folder, "Backend.py"), "--pinned-root", root, "--pinned-snapshot", snapshot,
                "--legacy-entry", Path.Combine(folder, "LegacyEntry.py"), "--lazy-handoff", receiptPath, "--handoff-nonce", token};
            start.Arguments = String.Join(" ", args.Select(QuoteArgument).ToArray()); start.WorkingDirectory = root;
            start.UseShellExecute = false; start.CreateNoWindow = true; start.WindowStyle = ProcessWindowStyle.Hidden;
            start.RedirectStandardInput = start.RedirectStandardOutput = start.RedirectStandardError = true;
            start.EnvironmentVariables.Remove("PYTHONPATH"); start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"; start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"; start.EnvironmentVariables["PYTHONUTF8"] = "1";
            backend = new Process { StartInfo = start };
            Need(backend.Start(), "Backend did not start"); originalBackend = backend.Handle;
            backendIdentity = Windows.Identity(originalBackend, backend.Id);
            bool member; Need(Windows.IsProcessInJob(originalBackend, job, out member) && member, "Original backend is outside frontend containment");
            errorTask = Task.Factory.StartNew(delegate { backend.StandardError.BaseStream.CopyTo(Console.OpenStandardError()); }, TaskCreationOptions.LongRunning);
            lock (stateLock) {
                record["handoff_backend"] = backendIdentity;
                WriteState("activating", new object[] { backendIdentity }, "Original child identity bound; no operation dispatched");
            }
            Event("backend_started", new { identity = backendIdentity, parent = identity, snapshot });
            backend.StandardInput.BaseStream.Write(initialization, 0, initialization.Length); backend.StandardInput.BaseStream.Flush();
            Task<byte[]> responseTask = Task.Factory.StartNew(delegate { return Frame(backend.StandardOutput.BaseStream); }, TaskCreationOptions.LongRunning);
            Need(responseTask.Wait(10000), "Backend initialization deadline elapsed; no retry");
            byte[] response = responseTask.Result; Need(response != null, "Backend initialization closed");
            Json node = Json.Parse(Utf8.GetString(response)); Json initial = Json.Parse(Utf8.GetString(initialization));
            Need(node["id"] != null && node["id"].Raw == initial["id"].Raw && node["error"] == null, "Backend handshake differs");
            Need(node["result"].Raw == Json.Parse(InitializeResult(initial)).Raw || Equivalent(node["result"], Json.Parse(InitializeResult(initial))), "Backend identity/capabilities differ from pinned catalog");
            backend.StandardInput.BaseStream.Write(initializedNotification, 0, initializedNotification.Length); backend.StandardInput.BaseStream.Flush();
            string catalogId = "__fanuc_catalog_" + token;
            byte[] catalogRequest = Utf8.GetBytes("{\"jsonrpc\":\"2.0\",\"id\":" + Json.Quote(catalogId) + ",\"method\":\"tools/list\",\"params\":{}}\n");
            backend.StandardInput.BaseStream.Write(catalogRequest, 0, catalogRequest.Length); backend.StandardInput.BaseStream.Flush();
            Task<byte[]> toolsTask = Task.Factory.StartNew(delegate { return Frame(backend.StandardOutput.BaseStream); }, TaskCreationOptions.LongRunning);
            Need(toolsTask.Wait(10000) && toolsTask.Result != null, "Backend catalog handshake failed");
            Json actualTools = Json.Parse(Utf8.GetString(toolsTask.Result));
            Need(actualTools.String("id") == catalogId && actualTools["error"] == null && Equivalent(actualTools["result"], catalog["static"]["tools/list"]), "Backend catalog differs from pinned descriptors");
            activated = true;
            initialization = initializedNotification = null;
            outputTask = Task.Factory.StartNew(delegate {
                try { backend.StandardOutput.BaseStream.CopyTo(new LockedOutput(output, outputLock)); }
                catch (Exception error) { Event("output_failure", new { type = error.GetType().Name }); throw; }
            }, TaskCreationOptions.LongRunning);
            Event("backend_ready", new { identity = backendIdentity, starts = 1 });
        }
        static bool Equivalent(Json left, Json right) {
            if (left == null || right == null) return left == right;
            if (left.Object != null && right.Object != null) return left.Object.Count == right.Object.Count && left.Object.All(pair => Equivalent(pair.Value, right[pair.Key]));
            if (left.Array != null && right.Array != null) return left.Array.Count == right.Array.Count && left.Array.Zip(right.Array, Equivalent).All(value => value);
            return left.Text != null && right.Text != null ? left.Text == right.Text : left.Raw == right.Raw;
        }
        internal int Run() {
            WriteState("active", new object[0], "Static discovery; no backend launch intent");
            Task.Factory.StartNew(ReadInput, TaskCreationOptions.LongRunning);
            while (!incoming.IsCompleted) {
                byte[] frame = null;
                Json request = null;
                try {
                if (outputTask != null && outputTask.IsFaulted) outputTask.GetAwaiter().GetResult();
                if (errorTask != null && errorTask.IsFaulted) errorTask.GetAwaiter().GetResult();
                if (activated && backend.HasExited) throw new IOException("Original backend exited; reconnect without replay");
                if (!incoming.TryTake(out frame, 100)) continue;
                if (activated) { backend.StandardInput.BaseStream.Write(frame, 0, frame.Length); backend.StandardInput.BaseStream.Flush(); continue; }
                try { request = Json.Parse(Utf8.GetString(frame)); }
                catch (FormatException) { Send("{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32700,\"message\":\"Invalid JSON\"}}"); continue; }
                string method = request.String("method"); Event("frontend_method", new { method });
                if (method == "initialize" && initialization == null && !attempted) { Need(request["id"] != null, "Initialize requires an ID"); initialization = frame; Reply(request, InitializeResult(request)); continue; }
                if (method == "notifications/initialized" && initialization != null) { initializedNotification = frame; continue; }
                if (method == "notifications/cancelled") continue;
                if (initialization == null || initializedNotification == null) { Error(request, "Initialize the connection first", -32000); continue; }
                bool emptyParams = CacheableParams(request["params"]);
                Json cached = catalog["static"][method ?? ""];
                if (cached != null && emptyParams && request["id"] != null) { Reply(request, cached.Raw); continue; }
                if (cached != null) Event("static_cache_miss", new { method, parameters = request["params"] == null ? null : request["params"].Raw });
                if (IsCancelled(request) || eof) { Error(request, "Request cancelled before backend dispatch", -32800); continue; }
                try { Activate(); }
                catch (Exception error) { Event("activation_failed", new { type = error.GetType().Name }); Error(request, "Backend activation failed; reconnect after inspection", -32000); break; }
                if (IsCancelled(request) || eof) Error(request, "Request cancelled before backend dispatch", -32800);
                else { backend.StandardInput.BaseStream.Write(frame, 0, frame.Length); backend.StandardInput.BaseStream.Flush(); }
                } finally { frame = null; request = null; }
            }
            if (inputFailure != null) throw new IOException("Input transport failed", inputFailure);
            return 0;
        }
        public void Dispose() {
            try {
                if (backend != null && originalBackend != IntPtr.Zero) {
                    // Reserve frontend residence BEFORE draining the backend. Its own
                    // generation receipt stays unchanged, including uncertain outcomes.
                    string closing = Path.Combine(root, "sessions", Guid.NewGuid().ToString("N") + ".json");
                    Dictionary<string, object> closingRecord = NewRecord("closing", new object[] { backendIdentity });
                    using (FileStream gate = Admission()) { Atomic(closing, closingRecord); gate.Unlock(0, 1); }
                    backend.StandardInput.Close(); backend.WaitForExit();
                    try {
                        if (outputTask != null) outputTask.GetAwaiter().GetResult();
                        if (errorTask != null) errorTask.GetAwaiter().GetResult();
                    } finally {
                        try {
                            Event("backend_exited", new { identity = backendIdentity, exit_code = backend.ExitCode });
                            closingRecord["state"] = "closed"; closingRecord["app_created_children"] = new object[0]; Atomic(closing, closingRecord);
                            Json prior = Json.Parse(Utf8.GetString(ReadFile(receiptPath, 65536)));
                            if (Equivalent(prior["identity"], Json.Parse(Encode(identity))))
                                WriteState("failed", new object[0], "Original backend departed before handoff; no operation replay");
                        } finally { backend.Dispose(); originalBackend = IntPtr.Zero; }
                    }
                } else if (!attempted) WriteState("closed", new object[0], "Frontend EOF; backend was never started");
                Event("frontend_closed", new { activation_attempts = attempted ? 1 : 0, backend_starts = backendIdentity != null ? (object)1 : attempted ? null : (object)0 });
            } finally {
                stop = true;
                if (parentTask != null) parentTask.GetAwaiter().GetResult();
                if (parentHandle != IntPtr.Zero && !Windows.CloseHandle(parentHandle)) throw new Win32Exception();
                for (int index = pins.Count - 1; index >= 0; index--) pins[index].Dispose();
                // Self-containment HANDLE belongs to the OS until actual process exit.
                GC.KeepAlive(job);
            }
        }
        sealed class LockedOutput : Stream {
            readonly Stream stream; readonly object gate;
            internal LockedOutput(Stream value, object mutex) { stream = value; gate = mutex; }
            public override void Write(byte[] buffer, int offset, int count) { lock (gate) { stream.Write(buffer, offset, count); stream.Flush(); } }
            public override bool CanRead { get { return false; } } public override bool CanWrite { get { return true; } } public override bool CanSeek { get { return false; } }
            public override long Length { get { throw new NotSupportedException(); } } public override long Position { get { throw new NotSupportedException(); } set { throw new NotSupportedException(); } }
            public override void Flush() { lock (gate) stream.Flush(); } public override int Read(byte[] b, int o, int c) { throw new NotSupportedException(); }
            public override long Seek(long o, SeekOrigin s) { throw new NotSupportedException(); } public override void SetLength(long l) { throw new NotSupportedException(); }
        }
        [STAThread] static int Main(string[] args) {
            try {
                Need(args.Length == 4 || args.Length == 6, "Use --manifest PATH --sha256 HASH [--diagnostics PATH]");
                Need(args[0] == "--manifest" && args[2] == "--sha256" && (args.Length == 4 || args[4] == "--diagnostics"), "Unsupported frontend arguments");
                using (Frontend app = new Frontend(args[1], args[3], args.Length == 6 ? args[5] : null)) return app.Run();
            } catch (Exception error) { Console.Error.WriteLine("FANUC frontend refused: " + error.GetType().Name + ": " + error.Message); return 1; }
        }
    }
}
