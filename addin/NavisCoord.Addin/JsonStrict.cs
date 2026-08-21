using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// A JSON reader that refuses anything it cannot prove is JSON.
    /// </summary>
    /// <remarks>
    /// The reader in <see cref="Json"/> is deliberately forgiving, and
    /// forgiving turned out to mean REPAIRING: given a profile with its last
    /// brace cut off in transit it returned a complete-looking dictionary, and
    /// the add-in installed it. Its number scanner was worse — it accepted
    /// <c>1..2</c>, <c>+1</c>, <c>01</c> and <c>1e</c>, because it looped over
    /// "characters that appear in numbers" and only required one digit
    /// somewhere. And both halves recursed with no depth limit, so a body
    /// nested a few thousand deep took Navisworks down with a
    /// StackOverflowException, which is not catchable.
    ///
    /// This is the reader that trust boundaries use. It is a single pass, it
    /// never repairs, it bounds depth and size before it bounds anything else,
    /// and every refusal comes back as a code the transport can turn into a
    /// status rather than as an empty dictionary that looks like "no
    /// arguments".
    ///
    /// The grammar for numbers is the one from the spec and nothing else:
    ///
    ///     -?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?
    /// </remarks>
    internal static class JsonStrict
    {
        // ------------------------------------------------------------ codes

        public const string InvalidJson = "invalid_json";
        public const string TooDeep = "json_too_deep";
        public const string TooLarge = "json_too_large";
        public const string DuplicateKey = "json_duplicate_key";
        public const string NotAnObject = "json_not_an_object";
        public const string TrailingContent = "json_trailing_content";

        // ------------------------------------------------------------ bounds
        //
        // Chosen against the real profiles rather than picked round: the
        // shipped default nests 6 deep, has 214 properties and its longest
        // string is a 180-character description, so every limit here is more
        // than an order of magnitude above what production uses. They exist to
        // stop a hostile or corrupt body, not to constrain an author.

        /// <summary>Deepest nesting accepted, shared by scan and parse.</summary>
        public const int MaxDepth = 128;

        /// <summary>Most properties in one document, across every object.</summary>
        public const int MaxProperties = 20000;

        /// <summary>Longest object key.</summary>
        public const int MaxKeyLength = 256;

        /// <summary>Longest string value.</summary>
        public const int MaxStringLength = 64 * 1024;

        /// <summary>Most elements in one array.</summary>
        public const int MaxArrayLength = 50000;

        /// <summary>What went wrong, and where.</summary>
        internal sealed class Failure
        {
            public string Code = InvalidJson;
            public string Detail = string.Empty;
            public int Position;

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["error"] = Code,
                    ["detail"] = Detail,
                    ["position"] = (double)Position
                };
        }

        /// <summary>
        /// Parse one complete JSON object, or say precisely why not.
        /// </summary>
        /// <remarks>
        /// One pass: there is no separate "is it well formed" scan followed by
        /// a permissive parse, because two readers over one string is two
        /// grammars and the second one is the one that decides what the
        /// handler sees.
        /// </remarks>
        public static bool TryParseObject(
            string text, out Dictionary<string, object> value, out Failure failure)
        {
            value = null;
            failure = null;

            if (text == null || text.Length == 0)
            {
                failure = Fail(InvalidJson, "el cuerpo está vacío", 0);
                return false;
            }
            if (text.Length > MaxStringLength * 64)
            {
                // Bounded before anything walks it. A size check that happens
                // after parsing is a size check that already paid the cost.
                failure = Fail(TooLarge, "el documento supera el tamaño máximo", 0);
                return false;
            }

            var reader = new Reader(text);
            try
            {
                reader.SkipWhitespace();
                if (!reader.More || reader.Current != '{')
                {
                    failure = Fail(NotAnObject,
                        "se esperaba un objeto JSON en la raíz", reader.Position);
                    return false;
                }

                var parsed = reader.ReadObject(1);
                reader.SkipWhitespace();
                if (reader.More)
                {
                    failure = Fail(TrailingContent,
                        "hay contenido después del objeto raíz", reader.Position);
                    return false;
                }
                value = parsed;
                return true;
            }
            catch (JsonRejected rejected)
            {
                failure = Fail(rejected.Code, rejected.Message, rejected.Position);
                return false;
            }
        }

        /// <summary>Whether the text is one complete, strictly valid object.</summary>
        public static bool IsStrictObject(string text)
            => TryParseObject(text, out _, out _);

        private static Failure Fail(string code, string detail, int position)
            => new Failure { Code = code, Detail = detail, Position = position };

        /// <summary>A refusal with a position, thrown to unwind the reader.</summary>
        private sealed class JsonRejected : Exception
        {
            public readonly string Code;
            public readonly int Position;

            public JsonRejected(string code, string message, int position)
                : base(message)
            {
                Code = code;
                Position = position;
            }
        }

        private sealed class Reader
        {
            private readonly string _s;
            private int _i;
            private int _properties;

            public Reader(string text) { _s = text; }

            public bool More => _i < _s.Length;
            public char Current => _s[_i];
            public int Position => _i;

            public void SkipWhitespace()
            {
                // Only the four the spec allows. A comment or a stray control
                // character is content, and content that is not JSON is a
                // refusal rather than something to step over.
                while (_i < _s.Length &&
                       (_s[_i] == ' ' || _s[_i] == '\t' || _s[_i] == '\n' || _s[_i] == '\r'))
                {
                    _i++;
                }
            }

            private void Reject(string code, string detail)
                => throw new JsonRejected(code, detail, _i);

            private void Depth(int depth)
            {
                if (depth > MaxDepth)
                {
                    // Checked on the way IN, so the throw happens before the
                    // next frame is pushed. Checking after recursing is how a
                    // depth limit still overflows the stack.
                    Reject(TooDeep, "el anidamiento supera " + MaxDepth + " niveles");
                }
            }

            public object ReadValue(int depth)
            {
                Depth(depth);
                SkipWhitespace();
                if (!More) Reject(InvalidJson, "se esperaba un valor y se acabó el texto");

                switch (Current)
                {
                    case '{': return ReadObject(depth + 1);
                    case '[': return ReadArray(depth + 1);
                    case '"': return ReadString(MaxStringLength);
                    case 't': ReadLiteral("true"); return true;
                    case 'f': ReadLiteral("false"); return false;
                    case 'n': ReadLiteral("null"); return null;
                    default: return ReadNumber();
                }
            }

            public Dictionary<string, object> ReadObject(int depth)
            {
                Depth(depth);
                var map = new Dictionary<string, object>(StringComparer.Ordinal);
                _i++;   // '{'
                SkipWhitespace();
                if (More && Current == '}') { _i++; return map; }

                while (true)
                {
                    SkipWhitespace();
                    if (!More || Current != '"')
                    {
                        Reject(InvalidJson, "se esperaba una clave entre comillas");
                    }
                    var key = ReadString(MaxKeyLength);

                    if (map.ContainsKey(key))
                    {
                        // Two readers can keep different values for a repeated
                        // key — first wins, last wins — so a profile with one
                        // could pass validation on one side and mean something
                        // else on the other. Neither answer is right; the
                        // document is.
                        Reject(DuplicateKey, "la clave '" + key + "' aparece dos veces");
                    }
                    if (++_properties > MaxProperties)
                    {
                        Reject(TooLarge, "el documento supera " + MaxProperties + " propiedades");
                    }

                    SkipWhitespace();
                    if (!More || Current != ':') Reject(InvalidJson, "se esperaba ':'");
                    _i++;

                    map[key] = ReadValue(depth);

                    SkipWhitespace();
                    if (!More) Reject(InvalidJson, "el objeto no se cerró");
                    if (Current == ',')
                    {
                        _i++;
                        SkipWhitespace();
                        // A trailing comma is the shape a hand-edited profile
                        // takes, and accepting it means accepting a document
                        // half the world's parsers reject.
                        if (More && Current == '}') Reject(InvalidJson, "coma final antes de '}'");
                        continue;
                    }
                    if (Current == '}') { _i++; return map; }
                    Reject(InvalidJson, "se esperaba ',' o '}'");
                }
            }

            public List<object> ReadArray(int depth)
            {
                Depth(depth);
                var list = new List<object>();
                _i++;   // '['
                SkipWhitespace();
                if (More && Current == ']') { _i++; return list; }

                while (true)
                {
                    list.Add(ReadValue(depth));
                    if (list.Count > MaxArrayLength)
                    {
                        Reject(TooLarge, "un array supera " + MaxArrayLength + " elementos");
                    }
                    SkipWhitespace();
                    if (!More) Reject(InvalidJson, "el array no se cerró");
                    if (Current == ',')
                    {
                        _i++;
                        SkipWhitespace();
                        if (More && Current == ']') Reject(InvalidJson, "coma final antes de ']'");
                        continue;
                    }
                    if (Current == ']') { _i++; return list; }
                    Reject(InvalidJson, "se esperaba ',' o ']'");
                }
            }

            private void ReadLiteral(string literal)
            {
                if (_i + literal.Length > _s.Length ||
                    string.CompareOrdinal(_s, _i, literal, 0, literal.Length) != 0)
                {
                    Reject(InvalidJson, "literal inválido; se esperaba '" + literal + "'");
                }
                _i += literal.Length;
            }

            public string ReadString(int limit)
            {
                var sb = new StringBuilder();
                _i++;   // opening quote
                while (_i < _s.Length)
                {
                    var c = _s[_i];
                    if (c == '"')
                    {
                        _i++;
                        if (sb.Length > limit)
                        {
                            Reject(TooLarge, "una cadena supera " + limit + " caracteres");
                        }
                        return sb.ToString();
                    }
                    if (c == '\\')
                    {
                        _i++;
                        if (_i >= _s.Length) Reject(InvalidJson, "escape sin terminar");
                        switch (_s[_i])
                        {
                            case '"': sb.Append('"'); break;
                            case '\\': sb.Append('\\'); break;
                            case '/': sb.Append('/'); break;
                            case 'b': sb.Append('\b'); break;
                            case 'f': sb.Append('\f'); break;
                            case 'n': sb.Append('\n'); break;
                            case 'r': sb.Append('\r'); break;
                            case 't': sb.Append('\t'); break;
                            case 'u':
                                if (_i + 4 >= _s.Length) Reject(InvalidJson, "escape \\u incompleto");
                                var hex = 0;
                                for (var k = 1; k <= 4; k++)
                                {
                                    var d = _s[_i + k];
                                    if (!Uri.IsHexDigit(d))
                                    {
                                        Reject(InvalidJson, "escape \\u con dígito no hexadecimal");
                                    }
                                    hex = hex * 16 + Convert.ToInt32(d.ToString(), 16);
                                }
                                sb.Append((char)hex);
                                _i += 4;
                                break;
                            default:
                                Reject(InvalidJson, "escape desconocido '\\" + _s[_i] + "'");
                                break;
                        }
                        _i++;
                        continue;
                    }
                    if (c < ' ')
                    {
                        Reject(InvalidJson, "carácter de control sin escapar en una cadena");
                    }
                    sb.Append(c);
                    _i++;
                    if (sb.Length > limit)
                    {
                        Reject(TooLarge, "una cadena supera " + limit + " caracteres");
                    }
                }
                Reject(InvalidJson, "la cadena no se cerró");
                return null;   // unreachable: Reject throws
            }

            /// <summary>
            /// A number, by the grammar and nothing else.
            /// </summary>
            /// <remarks>
            /// Written as an explicit walk rather than a loop over "characters
            /// numbers contain", which is what let <c>1..2</c>, <c>+1</c>,
            /// <c>01</c> and <c>1e</c> through. Every branch here corresponds
            /// to one clause of the production, so a form the grammar does not
            /// name has nowhere to be accepted.
            ///
            /// NaN and Infinity are not special cases to exclude: they simply
            /// do not start with a digit or a minus, so they never reach this
            /// function — they hit the literal branch and are refused there.
            /// </remarks>
            private object ReadNumber()
            {
                var start = _i;

                if (More && Current == '+') Reject(InvalidJson, "un número no puede empezar por '+'");
                if (More && Current == '-') _i++;

                if (!More || !IsDigit(Current)) Reject(InvalidJson, "se esperaba un dígito");
                if (Current == '0')
                {
                    _i++;
                    if (More && IsDigit(Current))
                    {
                        Reject(InvalidJson, "un número no puede llevar ceros a la izquierda");
                    }
                }
                else
                {
                    while (More && IsDigit(Current)) _i++;
                }

                if (More && Current == '.')
                {
                    _i++;
                    if (!More || !IsDigit(Current))
                    {
                        Reject(InvalidJson, "se esperaba al menos un dígito tras el punto decimal");
                    }
                    while (More && IsDigit(Current)) _i++;
                    if (More && Current == '.') Reject(InvalidJson, "dos puntos decimales");
                }

                if (More && (Current == 'e' || Current == 'E'))
                {
                    _i++;
                    if (More && (Current == '+' || Current == '-')) _i++;
                    if (!More || !IsDigit(Current))
                    {
                        Reject(InvalidJson, "el exponente no lleva dígitos");
                    }
                    while (More && IsDigit(Current)) _i++;
                }

                var text = _s.Substring(start, _i - start);
                if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture,
                        out var parsed))
                {
                    // Reached only for values outside double's range. Degrading
                    // to zero, which the old ParseNumber did, turns a refusal
                    // into a plausible-looking tolerance.
                    Reject(InvalidJson, "el número '" + text + "' no es representable");
                }
                if (double.IsNaN(parsed) || double.IsInfinity(parsed))
                {
                    Reject(InvalidJson, "el número '" + text + "' no es finito");
                }
                return parsed;
            }

            private static bool IsDigit(char c) => c >= '0' && c <= '9';
        }
    }
}
