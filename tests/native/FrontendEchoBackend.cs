// Harmless transport fixture. It performs no FANUC, native or file operation.
using System;
using System.IO;
using System.Linq;
using System.Text;
namespace FanucMcp {
    internal static class EchoBackend {
        static int Main(string[] args) {
            Console.SetIn(new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false, true)));
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false, true)) { AutoFlush = true });
            string folder = Path.GetDirectoryName(args.First(value => value.EndsWith("Backend.py", StringComparison.Ordinal)));
            Json catalog = Json.Parse(File.ReadAllText(Path.Combine(folder, "catalog.json"), Encoding.UTF8));
            string line;
            while ((line = Console.ReadLine()) != null) {
                Json request = Json.Parse(line);
                if (request["id"] == null) continue;
                string method = request.String("method");
                string result = method == "initialize" ? catalog["initialize"].Raw : method == "tools/list" ? catalog["static"]["tools/list"].Raw :
                    "{\"echo\":" + (request["params"] == null ? "null" : request["params"].Raw) + ",\"content\":[{\"type\":\"image\",\"data\":\"YmFzZTY0\",\"mimeType\":\"image/png\"}],\"_meta\":{\"unicode\":\"机器人 Ω\"}}";
                Console.WriteLine("{\"jsonrpc\":\"2.0\",\"id\":" + request["id"].Raw + ",\"result\":" + result + "}");
            }
            return 0;
        }
    }
}
