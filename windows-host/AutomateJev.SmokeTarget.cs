using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace AutomateJev.SmokeTarget
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Form form = new Form
            {
                Text = "Automate Jev Synthetic Target",
                Width = 640,
                Height = 360,
                StartPosition = FormStartPosition.CenterScreen
            };
            TextBox editor = new TextBox
            {
                Name = "SyntheticEditor",
                AccessibleName = "SyntheticEditor",
                Multiline = true,
                Dock = DockStyle.Fill,
                Font = new Font("Consolas", 14),
                Text = string.Empty
            };
            form.Controls.Add(editor);
            form.Shown += delegate
            {
                form.TopMost = true;
                form.Activate();
                SetForegroundWindow(form.Handle);
                editor.Focus();
                form.TopMost = false;
            };
            Application.Run(form);
        }

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr window);
    }
}
