// Small JSON reader: keep original number and ID tokens, reject duplicate keys.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace FanucMcp {
    internal sealed class Json {
        public string Raw;
        public string Text;
        public Dictionary<string, Json> Object;
        public List<Json> Array;
        public Json this[string key] { get { Json value; return Object != null && Object.TryGetValue(key, out value) ? value : null; } }
        public string String(string key) { Json value = this[key]; return value == null ? null : value.Text; }
        public static string Quote(string value) {
            StringBuilder b = new StringBuilder("\"");
            foreach (char c in value) {
                if (c == '"' || c == '\\') { b.Append('\\'); b.Append(c); }
                else if (c < 32) { b.Append("\\u"); b.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture)); }
                else b.Append(c);
            }
            return b.Append('"').ToString();
        }
        public static Json Parse(string value) {
            Reader reader = new Reader(value);
            Json result = reader.Value(0);
            reader.White();
            if (reader.Position != value.Length) throw new FormatException("Trailing JSON data");
            return result;
        }
        sealed class Reader {
            readonly string source;
            public int Position;
            public Reader(string value) { source = value; }
            public void White() { while (Position < source.Length && " \t\r\n".IndexOf(source[Position]) >= 0) Position++; }
            void Require(char c) { White(); if (Position >= source.Length || source[Position++] != c) throw new FormatException("Invalid JSON"); }
            string String() {
                Require('"');
                StringBuilder b = new StringBuilder();
                while (Position < source.Length) {
                    char c = source[Position++];
                    if (c == '"') return b.ToString();
                    if (c < 32) throw new FormatException("Control character in JSON string");
                    if (c == '\\') {
                        if (Position == source.Length) throw new FormatException("Invalid JSON escape");
                        c = source[Position++];
                        int escape = "\"\\/bfnrt".IndexOf(c);
                        if (escape >= 0) c = "\"\\/\b\f\n\r\t"[escape];
                        else if (c == 'u' && Position + 4 <= source.Length) {
                            ushort number;
                            if (!UInt16.TryParse(source.Substring(Position, 4), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out number)) throw new FormatException("Invalid Unicode escape");
                            c = (char)number; Position += 4;
                        } else throw new FormatException("Invalid JSON escape");
                    }
                    b.Append(c);
                }
                throw new FormatException("Unterminated JSON string");
            }
            public Json Value(int depth) {
                if (depth > 64) throw new FormatException("JSON nesting exceeds 64");
                White(); int start = Position;
                if (Position == source.Length) throw new FormatException("Incomplete JSON");
                Json value = new Json(); char c = source[Position];
                if (c == '"') value.Text = String();
                else if (c == '{' || c == '[') {
                    Position++; White(); bool obj = c == '{'; char end = obj ? '}' : ']';
                    if (obj) value.Object = new Dictionary<string, Json>(StringComparer.Ordinal); else value.Array = new List<Json>();
                    if (Position < source.Length && source[Position] == end) Position++;
                    else while (true) {
                        string key = null;
                        if (obj) { key = String(); Require(':'); }
                        Json item = Value(depth + 1);
                        if (obj) {
                            if (value.Object.ContainsKey(key)) throw new FormatException("Duplicate JSON key");
                            value.Object.Add(key, item);
                        } else value.Array.Add(item);
                        White();
                        if (Position < source.Length && source[Position] == end) { Position++; break; }
                        Require(',');
                    }
                } else {
                    while (Position < source.Length && ",]} \t\r\n".IndexOf(source[Position]) < 0) Position++;
                    string token = source.Substring(start, Position - start);
                    if (token != "true" && token != "false" && token != "null" && !Regex.IsMatch(token, @"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")) throw new FormatException("Invalid JSON token");
                }
                value.Raw = source.Substring(start, Position - start);
                return value;
            }
        }
    }
}
