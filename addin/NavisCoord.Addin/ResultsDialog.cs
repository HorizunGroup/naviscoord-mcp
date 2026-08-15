using System;
using System.Drawing;
using System.Windows.Forms;

namespace NavisCoord
{
    /// <summary>
    /// The results dialog the ribbon buttons show: a readable panel instead of
    /// a flat MessageBox — heading per step, colour for warnings and
    /// confirmations, scroll for long reports, and a Copy button so the
    /// summary can be pasted into an email or a chat.
    /// </summary>
    /// <remarks>
    /// Still modal, and still only ever raised after a human clicked — the
    /// bridge's rule of "never a dialog in a headless flow" is untouched. If
    /// building it fails it falls back to MessageBox: reporting badly beats
    /// not reporting.
    /// </remarks>
    internal static class ResultsDialog
    {
        internal static void Show(string heading, string body, bool isError)
        {
            try
            {
                using (var form = Build(heading, body, isError))
                {
                    form.ShowDialog();
                }
            }
            catch
            {
                MessageBox.Show(body, heading, MessageBoxButtons.OK,
                    isError ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
            }
        }

        private static Form Build(string heading, string body, bool isError)
        {
            var form = new Form
            {
                Text = "NavisCoord",
                StartPosition = FormStartPosition.CenterScreen,
                FormBorderStyle = FormBorderStyle.FixedDialog,
                MinimizeBox = false,
                MaximizeBox = false,
                ShowIcon = false,
                ClientSize = new Size(640, 480),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9.5f)
            };

            var header = new Panel
            {
                Dock = DockStyle.Top,
                Height = 58,
                BackColor = isError ? Color.FromArgb(198, 40, 40) : Color.FromArgb(21, 101, 192)
            };
            header.Controls.Add(new Label
            {
                Text = heading,
                ForeColor = Color.White,
                Font = new Font("Segoe UI Semibold", 13f),
                AutoSize = false,
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleLeft,
                Padding = new Padding(16, 0, 0, 0)
            });

            var footer = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 52,
                BackColor = Color.FromArgb(245, 245, 245)
            };
            var close = new Button
            {
                Text = "Cerrar",
                DialogResult = DialogResult.OK,
                Size = new Size(100, 30),
                Anchor = AnchorStyles.Right | AnchorStyles.Bottom
            };
            close.Location = new Point(form.ClientSize.Width - 116, 11);
            var copy = new Button
            {
                Text = "Copiar resumen",
                Size = new Size(130, 30),
                Anchor = AnchorStyles.Right | AnchorStyles.Bottom
            };
            copy.Location = new Point(form.ClientSize.Width - 256, 11);
            copy.Click += (s, e) =>
            {
                try { Clipboard.SetText(heading + Environment.NewLine + body); } catch { /* clipboard busy */ }
                copy.Text = "¡Copiado!";
            };
            footer.Controls.Add(close);
            footer.Controls.Add(copy);

            var text = new RichTextBox
            {
                Dock = DockStyle.Fill,
                ReadOnly = true,
                BorderStyle = BorderStyle.None,
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9.75f),
                Margin = new Padding(16)
            };

            foreach (var raw in body.Replace("\r\n", "\n").Split('\n'))
            {
                var line = raw.TrimEnd();
                Color colour;
                var bold = false;
                if (line.StartsWith("⚠")) { colour = Color.FromArgb(198, 40, 40); bold = true; }
                else if (line.StartsWith("✔")) { colour = Color.FromArgb(46, 125, 50); bold = true; }
                else if (line.StartsWith("💾") || line.StartsWith("Sigue:") || line.StartsWith("Listo"))
                {
                    colour = Color.FromArgb(21, 101, 192);
                    bold = true;
                }
                else if (line.StartsWith("[")) { colour = Color.FromArgb(60, 60, 60); bold = true; }
                else colour = Color.FromArgb(40, 40, 40);

                text.SelectionStart = text.TextLength;
                text.SelectionColor = colour;
                text.SelectionFont = bold
                    ? new Font("Segoe UI Semibold", 9.75f)
                    : new Font("Segoe UI", 9.75f);
                text.AppendText("  " + line + "\n");
            }
            text.SelectionStart = 0;
            text.ScrollToCaret();

            form.Controls.Add(text);
            form.Controls.Add(header);
            form.Controls.Add(footer);
            form.AcceptButton = close;
            form.CancelButton = close;
            return form;
        }
    }
}
