using System;
using System.IO;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// Reads a request body, bounded in BYTES.
    /// </summary>
    /// <remarks>
    /// The bound used to be counted in what <c>StreamReader</c> returns, which
    /// is CHARACTERS. Every non-ASCII character costs two to four bytes in
    /// UTF-8, so a body of Spanish text — or, deliberately, of astral
    /// codepoints — occupied several times the announced limit before the
    /// check fired: a 32 MB cap that admitted around 96 MB, and up to 128 MB
    /// for a body chosen to be worst-case. The listener runs inside the user's
    /// modelling session, so that is Navisworks' memory.
    ///
    /// Now the raw stream is read into a byte buffer, counted as it arrives,
    /// and only decoded once it is known to fit. Decoding is STRICT: a body
    /// that is not valid UTF-8 is a bad request, and letting the decoder
    /// substitute U+FFFD would turn corrupt input into a plausible-looking
    /// payload with replacement characters in the middle of a GUID.
    ///
    /// <c>Content-Length</c> is used only to refuse early. It is a claim by
    /// the caller, so it never sizes a buffer — a small body declaring 2 GB
    /// would otherwise allocate 2 GB — and a chunked request, which declares
    /// nothing, is bounded by the same running count.
    ///
    /// Split out of <see cref="HttpBridge"/> with no Navisworks reference so
    /// the limit can be exercised against a real stream in the test runner;
    /// as a private method behind an <c>HttpListenerRequest</c> it could only
    /// be checked by reading it.
    /// </remarks>
    internal static class BodyReader
    {
        public sealed class TooLargeException : Exception
        {
            public TooLargeException(string message) : base(message) { }
        }

        public sealed class UnreadableException : Exception
        {
            public UnreadableException(string message) : base(message) { }
        }

        /// <summary>UTF-8 that throws rather than substituting U+FFFD.</summary>
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(
            encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

        /// <param name="declared">
        /// Content-Length, or a value &lt;= 0 when the request is chunked and
        /// declares nothing.
        /// </param>
        public static string Read(Stream stream, long declared, long limit)
        {
            if (stream == null) return string.Empty;

            if (declared > limit)
            {
                throw new TooLargeException(
                    "El cuerpo declara " + declared + " bytes y el máximo para esta ruta es " +
                    limit + ".");
            }

            // Sized from the DECLARED length only when it is small and
            // plausible; growth is driven by bytes actually received.
            var initial = declared > 0 && declared <= 64 * 1024 ? (int)declared : 16 * 1024;

            using (var accumulated = new MemoryStream(initial))
            {
                var buffer = new byte[16 * 1024];
                long total = 0;
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    total += read;
                    if (total > limit)
                    {
                        throw new TooLargeException(
                            "El cuerpo superó el máximo de " + limit + " bytes para esta ruta.");
                    }
                    accumulated.Write(buffer, 0, read);
                }

                if (total == 0) return string.Empty;
                if (declared > 0 && total < declared)
                {
                    // The upload stopped early. Parsing what arrived would
                    // silently act on half a payload.
                    throw new UnreadableException(
                        "El cuerpo se cortó: llegaron " + total + " bytes de los " + declared +
                        " declarados.");
                }

                try
                {
                    return StrictUtf8.GetString(accumulated.GetBuffer(), 0, (int)total);
                }
                catch (DecoderFallbackException ex)
                {
                    throw new UnreadableException("El cuerpo no es UTF-8 válido: " + ex.Message);
                }
            }
        }
    }
}
