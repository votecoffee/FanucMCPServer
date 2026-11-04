// Optional offline-only quiescence. No process termination or request replay.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;

namespace FanucMcp {
    internal sealed partial class Frontend {
        bool idleEnabled, parked, retirementBlocked;
        double idleSeconds;
        int idleEpoch, activationAttempts, backendStarts, checkpointMilliseconds, drainMilliseconds;
        long forwardedBytes;
        bool frameBoundary = true;
        string controlDirectory, lastOffer;
        IDisposable controlPin;

        void ConfigureIdle(Json options) {
            if (options == null) return;
            Need(options.Object != null && options.Object.Keys.OrderBy(x => x).SequenceEqual(
                new [] {"checkpoint_seconds", "drain_seconds", "protocol", "seconds"}), "Unsupported idle descriptor");
            Need(options["protocol"].Raw == "1", "Unsupported idle protocol");
            idleSeconds = IdleBound(options["seconds"], .05, 3600);
            checkpointMilliseconds = (int)(1000 * IdleBound(options["checkpoint_seconds"], .05, 30));
            drainMilliseconds = (int)(1000 * IdleBound(options["drain_seconds"], .05, 30));
            idleEnabled = true;
        }
        static double IdleBound(Json value, double minimum, double maximum) {
            double number;
            Need(value != null && Double.TryParse(value.Raw, NumberStyles.Float, CultureInfo.InvariantCulture, out number), "Invalid idle bound");
            number = Double.Parse(value.Raw, CultureInfo.InvariantCulture);
            Need(!Double.IsNaN(number) && !Double.IsInfinity(number) && number >= minimum && number <= maximum, "Idle bound outside reviewed range");
            return number;
        }
        void PrepareIdleActivation() {
            if (!idleEnabled) return;
            idleEpoch++; token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string connection = Path.Combine(root, "sessions", "idle", Path.GetFileNameWithoutExtension(receiptPath));
            Directory.CreateDirectory(connection); PinParents(connection);
            controlDirectory = Path.Combine(connection, idleEpoch.ToString(CultureInfo.InvariantCulture));
            Need(!Directory.Exists(controlDirectory), "Idle epoch already exists; preserve its checkpoint");
            Directory.CreateDirectory(controlDirectory); controlPin = Windows.PinDirectory(controlDirectory);
            record["idle_epoch"] = idleEpoch;
            lastOffer = null; forwardedBytes = 0; frameBoundary = true;
        }
        Dictionary<string, object> IdleEnvelope() {
            return new Dictionary<string, object> { {"protocol", 1}, {"epoch", idleEpoch}, {"nonce", token},
                {"snapshot", new { schema_version = 1, snapshot_id = snapshot }},
                {"frontend", identity}, {"backend", backendIdentity}, {"seconds", idleSeconds} };
        }
        bool IdleBinding(Json value) {
            return value != null && IdleEnvelope().All(pair => Equivalent(value[pair.Key], Json.Parse(Encode(pair.Value))));
        }
        void WriteBackend(byte[] value) {
            backend.StandardInput.BaseStream.Write(value, 0, value.Length);
            backend.StandardInput.BaseStream.Flush();
            if (idleEnabled && value.Length != 0) {
                forwardedBytes += value.Length; frameBoundary = value[value.Length - 1] == 10;
            }
        }
        string ReceiptHash() { return Windows.Digest(ReadFile(receiptPath, 65536)); }
        void BlockRetirement(string reason) {
            retirementBlocked = true;
            Event("idle_checkpoint_protected", new { epoch = idleEpoch, reason, backend = backendIdentity });
            Console.Error.WriteLine("FANUC idle checkpoint protected: " + reason + "; no relaunch or replay");
        }
        void WithdrawIdle(string attempt) {
            using (FileStream gate = Admission()) {
                Json prior = Json.Parse(System.Text.Encoding.UTF8.GetString(ReadFile(receiptPath, 65536)));
                if (Equivalent(prior["identity"], Json.Parse(Encode(backendIdentity))) && prior.String("state") == "active") {
                    Dictionary<string, object> withdrawal = IdleEnvelope(); withdrawal["attempt"] = attempt;
                    Atomic(Path.Combine(controlDirectory, "withdraw.json"), withdrawal);
                    gate.Unlock(0, 1);
                    Event("idle_withdrawn", new { epoch = idleEpoch, attempt });
                    return;
                }
                gate.Unlock(0, 1);
            }
            BlockRetirement("Writer changed before checkpoint response");
        }
        void TryRetire() {
            if (!idleEnabled || backend == null || retirementBlocked || !frameBoundary || incoming.Count != 0) return;
            string offerPath = Path.Combine(controlDirectory, "offer.json");
            if (!File.Exists(offerPath) || File.Exists(Path.Combine(controlDirectory, "failure.json"))) return;
            Json offer = Json.Parse(System.Text.Encoding.UTF8.GetString(ReadFile(offerPath, 65536)));
            if (!IdleBinding(offer) || offer.String("state") != "offered" || offer.String("offer") == lastOffer) return;
            lastOffer = offer.String("offer");
            string attempt = Guid.NewGuid().ToString("N");
            Dictionary<string, object> fence = IdleEnvelope(); fence["attempt"] = attempt; fence["bytes"] = forwardedBytes;
            Stopwatch deadline = Stopwatch.StartNew();
            Atomic(Path.Combine(controlDirectory, "fence.json"), fence);
            Json answer = null;
            while (deadline.ElapsedMilliseconds < checkpointMilliseconds) {
                string path = Path.Combine(controlDirectory, "answer.json");
                if (File.Exists(path)) {
                    Json value = Json.Parse(System.Text.Encoding.UTF8.GetString(ReadFile(path, 65536)));
                    if (IdleBinding(value) && value.String("attempt") == attempt) { answer = value; break; }
                }
                Thread.Sleep(10);
            }
            if (answer == null) { WithdrawIdle(attempt); return; }
            if (answer.String("state") == "retained") return;
            if (answer.String("state") != "quiescent") { BlockRetirement("Invalid quiescence response"); return; }
            string expected = answer.String("receipt_sha256");
            using (FileStream gate = Admission()) {
                Json prior = Json.Parse(System.Text.Encoding.UTF8.GetString(ReadFile(receiptPath, 65536)));
                if (ReceiptHash() != expected || !Equivalent(prior["identity"], Json.Parse(Encode(identity))) ||
                    prior.String("state") != "closing" || prior.String("idle_attempt") != attempt) {
                    gate.Unlock(0, 1); BlockRetirement("Transferred writer checkpoint differs"); return;
                }
                gate.Unlock(0, 1);
            }
            Stopwatch drain = Stopwatch.StartNew();
            backend.StandardInput.Close();
            if (drain.ElapsedMilliseconds >= drainMilliseconds || !backend.WaitForExit(Math.Max(1, drainMilliseconds - (int)drain.ElapsedMilliseconds))) { BlockRetirement("Original child did not drain within its bound"); return; }
            try {
                if (outputTask != null && !outputTask.Wait(Math.Max(1, drainMilliseconds - (int)drain.ElapsedMilliseconds))) { BlockRetirement("Output did not drain"); return; }
                if (errorTask != null && !errorTask.Wait(Math.Max(1, drainMilliseconds - (int)drain.ElapsedMilliseconds))) { BlockRetirement("Stderr did not drain"); return; }
            } catch (Exception) { BlockRetirement("Original output pump failed"); return; }
            string terminalPath = Path.Combine(controlDirectory, "terminal.json");
            if (backend.ExitCode != 0 || !File.Exists(terminalPath)) { BlockRetirement("Original child exit or terminal checkpoint failed"); return; }
            Json terminal = Json.Parse(System.Text.Encoding.UTF8.GetString(ReadFile(terminalPath, 65536)));
            if (!IdleBinding(terminal) || terminal.String("attempt") != attempt || terminal.String("state") != "closed" || terminal.String("receipt_sha256") != expected) {
                BlockRetirement("Terminal checkpoint differs"); return;
            }
            using (FileStream gate = Admission()) {
                if (ReceiptHash() != expected) { gate.Unlock(0, 1); BlockRetirement("Writer changed during drain"); return; }
                record = NewRecord("active", new object[0]); record["idle_epoch"] = idleEpoch;
                record["idle_terminal_sha256"] = Windows.Digest(ReadFile(terminalPath, 65536));
                record["reason"] = "Original child exited zero and drained; frontend remains admitted";
                Atomic(receiptPath, record); gate.Unlock(0, 1);
            }
            Event("backend_idle_retired", new { epoch = idleEpoch, identity = backendIdentity, exit_code = backend.ExitCode });
            backend.Dispose(); backend = null; originalBackend = IntPtr.Zero; backendIdentity = null;
            outputTask = errorTask = null; controlPin.Dispose(); controlPin = null; parked = true;
        }
    }
}
