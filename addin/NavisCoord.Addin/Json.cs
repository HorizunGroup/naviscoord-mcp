using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// Minimal JSON reader/writer.
    /// </summary>
    /// <remarks>
    /// Deliberately hand-rolled rather than referencing Newtonsoft. This
    /// assembly loads into the Navisworks process, which already has its own
    /// dependency graph; adding a JSON library invites an assembly binding
    /// conflict that surfaces as an add-in that simply never appears. The
    /// payloads here are ours on both ends, so a small parser is enough.
    /// </remarks>
    internal static class Json
    {
        public const int MaxDepth = 128;
        // ------------------------------------------------------------ write

        public static string Write(object value)
        {
            var sb = new StringBuilder(1024);
            WriteValue(sb, value);
            return sb.ToString();
        }

        private static void WriteValue(StringBuilder sb, object value)
        {
            switch (value)
            {
                case null:
                    sb.Append("null");
                    return;
                case string s:
                    WriteString(sb, s);
                    return;
                case bool b:
                    sb.Append(b ? "true" : "false");
                    return;
                case double d:
                    sb.Append(IsFinite(d) ? d.ToString("R", CultureInfo.InvariantCulture) : "null");
                    return;
                case float f:
                    WriteValue(sb, (double)f);
                    return;
                case IDictionary<string, object> map:
                    WriteObject(sb, map);
                    return;
                case IEnumerable list when !(value is string):
                    WriteArray(sb, list);
                    return;
                default:
                    if (value is IFormattable formattable &&
                        (value is int || value is long || value is short || value is decimal))
                    {
                        sb.Append(formattable.ToString(null, CultureInfo.InvariantCulture));
                        return;
                    }
                    WriteString(sb, Convert.ToString(value, CultureInfo.InvariantCulture));
                    return;
            }
        }

        private static bool IsFinite(double d) => !double.IsNaN(d) && !double.IsInfinity(d);

        private static void WriteObject(StringBuilder sb, IDictionary<string, object> map)
        {
            sb.Append('{');
            var first = true;
            foreach (var pair in map)
            {
                if (!first) sb.Append(',');
                first = false;
                WriteString(sb, pair.Key);
                sb.Append(':');
                WriteValue(sb, pair.Value);
            }
            sb.Append('}');
        }

        private static void WriteArray(StringBuilder sb, IEnumerable list)
        {
            sb.Append('[');
            var first = true;
            foreach (var item in list)
            {
                if (!first) sb.Append(',');
                first = false;
                WriteValue(sb, item);
            }
            sb.Append(']');
        }

        private static void WriteString(StringBuilder sb, string value)
        {
            sb.Append('"');
            foreach (var c in value ?? string.Empty)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c < ' ')
                        {
                            sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            sb.Append(c);
                        }
                        break;
                }
            }
            sb.Append('"');
        }

        // ------------------------------------------------------------- read

        public static Dictionary<string, object> ParseObject(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return new Dictionary<string, object>();
            var index = 0;
            var value = ParseValue(text, ref index, 0);
            return value as Dictionary<string, object> ?? new Dictionary<string, object>();
        }

        /// <summary>
        /// Whether the text is one complete, well-formed JSON object.
        /// </summary>
        /// <remarks>
        /// The parser above is deliberately forgiving — it never throws, so a
        /// junk body reaches a handler as "no arguments" instead of a 500 —
        /// but forgiving turned out to mean REPAIRING. Given
        /// <c>{"canonical": "sin cerrar</c> it happily returned a dictionary
        /// with a complete-looking value, so a payload cut off in transit was
        /// acted on as though the caller had sent all of it, and answered 200.
        /// Found by the live smoke test, not by any unit test: the tests fed
        /// it junk and only checked that it did not throw.
        ///
        /// This is a separate scanner rather than a stricter parser because
        /// the forgiving behaviour is still what the handlers want; what was
        /// missing is the transport being able to tell the difference and
        /// answer 400.
        /// </remarks>
        public static bool IsWellFormedObject(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return false;
            var i = 0;
            SkipWhitespace(text, ref i);
            if (i >= text.Length || text[i] != '{') return false;
            if (!ScanValue(text, ref i, 0)) return false;
            SkipWhitespace(text, ref i);
            return i >= text.Length;   // trailing junk is not well-formed either
        }

        private static bool ScanValue(string s, ref int i, int depth)
        {
            if (depth > MaxDepth) return false;
            SkipWhitespace(s, ref i);
            if (i >= s.Length) return false;
            switch (s[i])
            {
                case '{': return ScanMap(s, ref i, depth + 1);
                case '[': return ScanList(s, ref i, depth + 1);
                case '"': return ScanString(s, ref i);
                case 't': return ScanLiteral(s, ref i, "true");
                case 'f': return ScanLiteral(s, ref i, "false");
                case 'n': return ScanLiteral(s, ref i, "null");
                default: return ScanNumber(s, ref i);
            }
        }

        private static bool ScanMap(string s, ref int i, int depth)
        {
            i++; // '{'
            SkipWhitespace(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return true; }
            while (true)
            {
                SkipWhitespace(s, ref i);
                if (i >= s.Length || s[i] != '"') return false;
                if (!ScanString(s, ref i)) return false;
                SkipWhitespace(s, ref i);
                if (i >= s.Length || s[i] != ':') return false;
                i++;
                if (!ScanValue(s, ref i, depth)) return false;
                SkipWhitespace(s, ref i);
                if (i >= s.Length) return false;
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return true; }
                return false;
            }
        }

        private static bool ScanList(string s, ref int i, int depth)
        {
            i++; // '['
            SkipWhitespace(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return true; }
            while (true)
            {
                if (!ScanValue(s, ref i, depth)) return false;
                SkipWhitespace(s, ref i);
                if (i >= s.Length) return false;
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return true; }
                return false;
            }
        }

        private static bool ScanString(string s, ref int i)
        {
            if (i >= s.Length || s[i] != '"') return false;
            i++;
            while (i < s.Length)
            {
                var c = s[i];
                if (c == '\\')
                {
                    i++;
                    if (i >= s.Length) return false;
                    if (s[i] == 'u')
                    {
                        if (i + 4 >= s.Length) return false;
                        for (var k = 1; k <= 4; k++)
                        {
                            if (!Uri.IsHexDigit(s[i + k])) return false;
                        }
                        i += 4;
                    }
                    else if ("\"\\/bfnrt".IndexOf(s[i]) < 0)
                    {
                        return false;
                    }
                    i++;
                    continue;
                }
                if (c == '"') { i++; return true; }
                if (c < ' ') return false;   // a raw control character is not legal in a string
                i++;
            }
            return false;   // ran off the end: the string was never closed
        }

        private static bool ScanLiteral(string s, ref int i, string literal)
        {
            if (i + literal.Length > s.Length) return false;
            if (string.CompareOrdinal(s, i, literal, 0, literal.Length) != 0) return false;
            i += literal.Length;
            return true;
        }

        private static bool ScanNumber(string s, ref int i)
        {
            var start = i;
            if (i < s.Length && s[i] == '-') i++;
            if (i >= s.Length) return false;

            if (s[i] == '0')
            {
                i++;
                // Leading zeroes are not JSON numbers.
                if (i < s.Length && char.IsDigit(s[i])) return false;
            }
            else if (s[i] >= '1' && s[i] <= '9')
            {
                while (i < s.Length && char.IsDigit(s[i])) i++;
            }
            else
            {
                return false;
            }

            if (i < s.Length && s[i] == '.')
            {
                i++;
                var fraction = i;
                while (i < s.Length && char.IsDigit(s[i])) i++;
                if (i == fraction) return false;
            }

            if (i < s.Length && (s[i] == 'e' || s[i] == 'E'))
            {
                i++;
                if (i < s.Length && (s[i] == '+' || s[i] == '-')) i++;
                var exponent = i;
                while (i < s.Length && char.IsDigit(s[i])) i++;
                if (i == exponent) return false;
            }

            var token = s.Substring(start, i - start);
            return double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) &&
                   IsFinite(value);
        }

        private static object ParseValue(string s, ref int i, int depth)
        {
            if (depth > MaxDepth) return null;
            SkipWhitespace(s, ref i);
            if (i >= s.Length) return null;

            switch (s[i])
            {
                case '{': return ParseMap(s, ref i, depth + 1);
                case '[': return ParseList(s, ref i, depth + 1);
                case '"': return ParseString(s, ref i);
                case 't': i += 4; return true;
                case 'f': i += 5; return false;
                case 'n': i += 4; return null;
                default: return ParseNumber(s, ref i);
            }
        }

        private static Dictionary<string, object> ParseMap(string s, ref int i, int depth)
        {
            var map = new Dictionary<string, object>(StringComparer.Ordinal);
            i++; // '{'
            SkipWhitespace(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return map; }

            while (i < s.Length)
            {
                SkipWhitespace(s, ref i);
                var key = ParseString(s, ref i);
                SkipWhitespace(s, ref i);
                if (i < s.Length && s[i] == ':') i++;
                map[key] = ParseValue(s, ref i, depth);
                SkipWhitespace(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == '}') { i++; break; }
                break;
            }
            return map;
        }

        private static List<object> ParseList(string s, ref int i, int depth)
        {
            var list = new List<object>();
            i++; // '['
            SkipWhitespace(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return list; }

            while (i < s.Length)
            {
                list.Add(ParseValue(s, ref i, depth));
                SkipWhitespace(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == ']') { i++; break; }
                break;
            }
            return list;
        }

        private static string ParseString(string s, ref int i)
        {
            var sb = new StringBuilder();
            if (i < s.Length && s[i] == '"') i++;
            while (i < s.Length && s[i] != '"')
            {
                if (s[i] == '\\' && i + 1 < s.Length)
                {
                    i++;
                    switch (s[i])
                    {
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'u':
                            var hex = s.Substring(i + 1, 4);
                            sb.Append((char)Convert.ToInt32(hex, 16));
                            i += 4;
                            break;
                        default: sb.Append(s[i]); break;
                    }
                }
                else
                {
                    sb.Append(s[i]);
                }
                i++;
            }
            i++; // closing quote
            return sb.ToString();
        }

        private static object ParseNumber(string s, ref int i)
        {
            var start = i;
            while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '-' || s[i] == '+' ||
                                    s[i] == '.' || s[i] == 'e' || s[i] == 'E'))
            {
                i++;
            }
            var slice = s.Substring(start, i - start);
            return double.TryParse(slice, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)
                ? d
                : (object)0.0;
        }

        private static void SkipWhitespace(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
        }

        // ------------------------------------------------------- accessors

        public static string Str(IDictionary<string, object> map, string key, string fallback = "")
            => map != null && map.TryGetValue(key, out var v) && v != null
                ? Convert.ToString(v, CultureInfo.InvariantCulture)
                : fallback;

        public static double Num(IDictionary<string, object> map, string key, double fallback = 0.0)
            => map != null && map.TryGetValue(key, out var v) && v is double d ? d : fallback;

        public static int Int(IDictionary<string, object> map, string key, int fallback = 0)
            => (int)Num(map, key, fallback);

        public static bool Bool(IDictionary<string, object> map, string key, bool fallback = false)
            => map != null && map.TryGetValue(key, out var v) && v is bool b ? b : fallback;

        public static List<object> Arr(IDictionary<string, object> map, string key)
            => map != null && map.TryGetValue(key, out var v) && v is List<object> list
                ? list
                : new List<object>();

        public static List<string> StrArr(IDictionary<string, object> map, string key)
        {
            var result = new List<string>();
            foreach (var item in Arr(map, key))
            {
                if (item != null) result.Add(Convert.ToString(item, CultureInfo.InvariantCulture));
            }
            return result;
        }
    }
}
