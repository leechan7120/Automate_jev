using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Automation;

namespace AutomateJev.WindowsHost
{
    internal static class Program
    {
        private const int MaxMessageBytes = 65536;
        private const string SupportedAction = "notepad.fill-required-text";
        private const string SmokeAction = "smoke-target.fill-required-text";
        private const string ApprovedDemoText = "DEMO_APPROVED";
        private const uint WmGetText = 0x000D;
        private const uint WmGetTextLength = 0x000E;
        private const uint WmSetText = 0x000C;
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = MaxMessageBytes };
        private static readonly Dictionary<string, string> RegisteredActions = new Dictionary<string, string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ExecutedKeys = new HashSet<string>(StringComparer.Ordinal);
        private static string _sessionId;
        private static int _registryVersion;

        [STAThread]
        private static int Main()
        {
            Console.InputEncoding = Encoding.UTF8;
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.Out.Flush();
            string line;
            while ((line = Console.ReadLine()) != null)
            {
                if (Encoding.UTF8.GetByteCount(line) > MaxMessageBytes)
                {
                    Write(Error(null, "MALFORMED_REQUEST"));
                    continue;
                }
                Dictionary<string, object> request = null;
                object requestId = null;
                try
                {
                    request = Json.DeserializeObject(line) as Dictionary<string, object>;
                    if (request == null || !request.TryGetValue("id", out requestId))
                    {
                        throw new InvalidDataException();
                    }
                    Write(Handle(request, requestId));
                }
                catch
                {
                    Write(Error(requestId, "MALFORMED_REQUEST"));
                }
            }
            return 0;
        }

        private static Dictionary<string, object> Handle(Dictionary<string, object> request, object requestId)
        {
            string action = GetString(request, "action");
            Dictionary<string, object> payload = GetDictionary(request, "payload");
            if (action == "observe")
            {
                return Success(requestId, Observe().ToDictionary());
            }
            if (action == "register_actions")
            {
                return Register(requestId, payload);
            }
            if (action == "execute_registered")
            {
                return Execute(requestId, payload);
            }
            if (action == "emergency_stop")
            {
                return Success(requestId, new Dictionary<string, object> { { "stopped", true } });
            }
            return Error(requestId, "MALFORMED_REQUEST");
        }

        private static Dictionary<string, object> Register(object requestId, Dictionary<string, object> payload)
        {
            string sessionId = GetString(payload, "session_id");
            int version = GetInt(payload, "registry_version");
            object rawActions;
            object[] actions;
            if (string.IsNullOrWhiteSpace(sessionId) || version < 1 || !payload.TryGetValue("actions", out rawActions) || (actions = rawActions as object[]) == null || actions.Length < 1 || actions.Length > 12)
            {
                return Error(requestId, "MALFORMED_REQUEST");
            }
            bool sameSession = string.Equals(sessionId, _sessionId, StringComparison.Ordinal);
            if (sameSession && version <= _registryVersion)
            {
                return Error(requestId, "INVALID_ACTION");
            }
            Dictionary<string, string> next = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (object rawAction in actions)
            {
                Dictionary<string, object> item = rawAction as Dictionary<string, object>;
                if (item == null)
                {
                    return Error(requestId, "MALFORMED_REQUEST");
                }
                string id = GetString(item, "id");
                string hash = GetString(item, "action_hash");
                if (string.IsNullOrWhiteSpace(id) || !hash.StartsWith("sha256:", StringComparison.Ordinal) || next.ContainsKey(id))
                {
                    return Error(requestId, "MALFORMED_REQUEST");
                }
                next.Add(id, hash);
            }
            RegisteredActions.Clear();
            foreach (KeyValuePair<string, string> item in next)
            {
                RegisteredActions.Add(item.Key, item.Value);
            }
            if (!sameSession)
            {
                ExecutedKeys.Clear();
            }
            _sessionId = sessionId;
            _registryVersion = version;
            return Success(requestId, new Dictionary<string, object> { { "registered", next.Count } });
        }

        private static Dictionary<string, object> Execute(object requestId, Dictionary<string, object> payload)
        {
            string sessionId = GetString(payload, "session_id");
            int version = GetInt(payload, "registry_version");
            string actionId = GetString(payload, "action_id");
            string actionHash = GetString(payload, "action_hash");
            string idempotencyKey = GetString(payload, "idempotency_key");
            string expectedRevision = GetString(payload, "expected_state_revision");
            string registeredHash;
            if (string.IsNullOrWhiteSpace(idempotencyKey) || sessionId != _sessionId || version != _registryVersion || !RegisteredActions.TryGetValue(actionId, out registeredHash) || !FixedEquals(registeredHash, actionHash))
            {
                return Error(requestId, "INVALID_ACTION");
            }
            if (ExecutedKeys.Contains(idempotencyKey))
            {
                return Error(requestId, "DUPLICATE");
            }
            DateTimeOffset expiry;
            if (!DateTimeOffset.TryParse(GetString(payload, "expires_at"), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out expiry) || DateTimeOffset.UtcNow >= expiry.ToUniversalTime())
            {
                return Error(requestId, "EXPIRED");
            }
            HostObservation before = Observe();
            if (!FixedEquals(before.StateRevision, expectedRevision))
            {
                return Error(requestId, "STALE_ACTION");
            }
            string expectedProcess;
            if (actionId == SupportedAction)
            {
                expectedProcess = "notepad.exe";
            }
            else if (actionId == SmokeAction)
            {
                expectedProcess = "AutomateJev.SmokeTarget.exe";
            }
            else
            {
                return Error(requestId, "POLICY_BLOCKED");
            }
            Dictionary<string, object> target = GetDictionary(payload, "target");
            Dictionary<string, object> arguments = GetDictionary(payload, "arguments");
            if (!string.Equals(GetString(target, "process"), expectedProcess, StringComparison.OrdinalIgnoreCase) || GetString(arguments, "text") != ApprovedDemoText)
            {
                return Error(requestId, "TARGET_MISMATCH");
            }
            if (!string.Equals(before.ProcessName, expectedProcess, StringComparison.OrdinalIgnoreCase) || before.WindowHandle == IntPtr.Zero)
            {
                return Error(requestId, "TARGET_MISMATCH");
            }
            AutomationElement editor = FindNotepadEditor(before.WindowHandle, before.ProcessId);
            if (editor == null)
            {
                return Error(requestId, "TARGET_MISMATCH");
            }
            try
            {
                editor.SetFocus();
            }
            catch
            {
                return Error(requestId, "TARGET_MISMATCH");
            }
            HostObservation fresh = Observe();
            if (fresh.WindowHandle != before.WindowHandle || fresh.ProcessId != before.ProcessId || !FixedEquals(fresh.StateRevision, expectedRevision))
            {
                return Error(requestId, "STALE_ACTION");
            }
            if (!AppendApprovedText(editor))
            {
                return Error(requestId, "UNAVAILABLE");
            }
            ExecutedKeys.Add(idempotencyKey);
            return Success(requestId, new Dictionary<string, object> { { "execution_state", "confirmed" } });
        }

        private static bool AppendApprovedText(AutomationElement editor)
        {
            try
            {
                IntPtr nativeHandle = new IntPtr(editor.Current.NativeWindowHandle);
                if (nativeHandle != IntPtr.Zero)
                {
                    int length = SendMessage(nativeHandle, WmGetTextLength, IntPtr.Zero, IntPtr.Zero).ToInt32();
                    if (length >= 0 && length <= 65536)
                    {
                        StringBuilder currentText = new StringBuilder(length + 1);
                        SendMessage(nativeHandle, WmGetText, new IntPtr(currentText.Capacity), currentText);
                        string current = currentText.ToString();
                        if (current.Contains(ApprovedDemoText))
                        {
                            return true;
                        }
                        return SendMessage(nativeHandle, WmSetText, IntPtr.Zero, current + ApprovedDemoText) != IntPtr.Zero;
                    }
                }
            }
            catch
            {
                // Fall through to UIA ValuePattern.
            }
            try
            {
                object pattern;
                if (editor.TryGetCurrentPattern(ValuePattern.Pattern, out pattern))
                {
                    ValuePattern valuePattern = (ValuePattern)pattern;
                    if (!valuePattern.Current.IsReadOnly)
                    {
                        string current = valuePattern.Current.Value ?? string.Empty;
                        if (current.Contains(ApprovedDemoText))
                        {
                            return true;
                        }
                        valuePattern.SetValue(current + ApprovedDemoText);
                        return true;
                    }
                }
            }
            catch
            {
                // Some UIA providers advertise ValuePattern but reject SetValue.
                // Fall through to verified Unicode input while the same target is focused.
            }
            try
            {
                SendUnicodeText(ApprovedDemoText);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static HostObservation Observe()
        {
            IntPtr window = GetForegroundWindow();
            uint processIdValue;
            GetWindowThreadProcessId(window, out processIdValue);
            int processId = unchecked((int)processIdValue);
            string processName = string.Empty;
            try
            {
                processName = processId > 0 ? Process.GetProcessById(processId).ProcessName + ".exe" : string.Empty;
            }
            catch
            {
                processName = string.Empty;
            }
            bool notepad = string.Equals(processName, "notepad.exe", StringComparison.OrdinalIgnoreCase);
            bool smokeTarget = string.Equals(processName, "AutomateJev.SmokeTarget.exe", StringComparison.OrdinalIgnoreCase);
            bool markerPresent = false;
            if ((notepad || smokeTarget) && window != IntPtr.Zero)
            {
                AutomationElement editor = FindNotepadEditor(window, processId);
                markerPresent = ReadTextContainsMarker(editor);
            }
            string title = GetWindowTitle(window);
            string fingerprint = string.Join("|", new string[] {
                processId.ToString(CultureInfo.InvariantCulture),
                window.ToInt64().ToString("X", CultureInfo.InvariantCulture),
                processName,
                title,
                notepad ? "1" : "0",
                smokeTarget ? "1" : "0",
                markerPresent ? "1" : "0"
            });
            return new HostObservation
            {
                ProcessId = processId,
                WindowHandle = window,
                ProcessName = processName,
                NotepadForeground = notepad,
                SupportedTargetForeground = notepad || smokeTarget,
                RequiredTextPresent = markerPresent,
                StateRevision = "sha256:" + Sha256(fingerprint)
            };
        }

        private static AutomationElement FindNotepadEditor(IntPtr window, int processId)
        {
            try
            {
                AutomationElement focused = AutomationElement.FocusedElement;
                if (focused != null && focused.Current.ProcessId == processId && IsTextControl(focused))
                {
                    return focused;
                }
                AutomationElement root = AutomationElement.FromHandle(window);
                if (root == null)
                {
                    return null;
                }
                OrCondition condition = new OrCondition(
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Document),
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit)
                );
                return root.FindFirst(TreeScope.Descendants, condition);
            }
            catch
            {
                return null;
            }
        }

        private static bool IsTextControl(AutomationElement element)
        {
            ControlType type = element.Current.ControlType;
            return type == ControlType.Document || type == ControlType.Edit;
        }

        private static bool ReadTextContainsMarker(AutomationElement editor)
        {
            if (editor == null)
            {
                return false;
            }
            try
            {
                object pattern;
                if (editor.TryGetCurrentPattern(ValuePattern.Pattern, out pattern))
                {
                    return (((ValuePattern)pattern).Current.Value ?? string.Empty).Contains(ApprovedDemoText);
                }
                if (editor.TryGetCurrentPattern(TextPattern.Pattern, out pattern))
                {
                    return ((TextPattern)pattern).DocumentRange.GetText(4096).Contains(ApprovedDemoText);
                }
            }
            catch
            {
                return false;
            }
            return false;
        }

        private static void SendUnicodeText(string text)
        {
            foreach (char character in text)
            {
                NativeInput[] inputs = new NativeInput[2];
                inputs[0] = CreateUnicodeInput(character, false);
                inputs[1] = CreateUnicodeInput(character, true);
                uint sent = SendInput(2, inputs, Marshal.SizeOf(typeof(NativeInput)));
                if (sent != 2)
                {
                    throw new InvalidOperationException();
                }
            }
        }

        private static NativeInput CreateUnicodeInput(char character, bool keyUp)
        {
            NativeInput input = new NativeInput();
            input.Type = 1;
            input.Union.Keyboard = new KeyboardInput
            {
                VirtualKey = 0,
                ScanCode = character,
                Flags = 0x0004u | (keyUp ? 0x0002u : 0u),
                Time = 0,
                ExtraInfo = IntPtr.Zero
            };
            return input;
        }

        private static string GetWindowTitle(IntPtr window)
        {
            int length = GetWindowTextLength(window);
            if (length <= 0 || length > 4096)
            {
                return string.Empty;
            }
            StringBuilder builder = new StringBuilder(length + 1);
            GetWindowText(window, builder, builder.Capacity);
            return builder.ToString();
        }

        private static string Sha256(string value)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                byte[] digest = algorithm.ComputeHash(Encoding.UTF8.GetBytes(value));
                StringBuilder builder = new StringBuilder(digest.Length * 2);
                foreach (byte item in digest)
                {
                    builder.Append(item.ToString("x2", CultureInfo.InvariantCulture));
                }
                return builder.ToString();
            }
        }

        private static bool FixedEquals(string first, string second)
        {
            if (first == null || second == null)
            {
                return false;
            }
            byte[] left = Encoding.UTF8.GetBytes(first);
            byte[] right = Encoding.UTF8.GetBytes(second);
            int difference = left.Length ^ right.Length;
            int length = Math.Max(left.Length, right.Length);
            for (int index = 0; index < length; index++)
            {
                byte leftValue = index < left.Length ? left[index] : (byte)0;
                byte rightValue = index < right.Length ? right[index] : (byte)0;
                difference |= leftValue ^ rightValue;
            }
            return difference == 0;
        }

        private static Dictionary<string, object> GetDictionary(Dictionary<string, object> source, string key)
        {
            object value;
            Dictionary<string, object> dictionary;
            if (!source.TryGetValue(key, out value) || (dictionary = value as Dictionary<string, object>) == null)
            {
                return new Dictionary<string, object>();
            }
            return dictionary;
        }

        private static string GetString(Dictionary<string, object> source, string key)
        {
            object value;
            return source.TryGetValue(key, out value) && value != null ? Convert.ToString(value, CultureInfo.InvariantCulture) : string.Empty;
        }

        private static int GetInt(Dictionary<string, object> source, string key)
        {
            object value;
            int parsed;
            return source.TryGetValue(key, out value) && int.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed) ? parsed : 0;
        }

        private static Dictionary<string, object> Success(object requestId, Dictionary<string, object> result)
        {
            return new Dictionary<string, object> { { "id", requestId }, { "ok", true }, { "result", result } };
        }

        private static Dictionary<string, object> Error(object requestId, string code)
        {
            return new Dictionary<string, object>
            {
                { "id", requestId },
                { "ok", false },
                { "error", new Dictionary<string, object> { { "code", code }, { "message", "request rejected" } } }
            };
        }

        private static void Write(Dictionary<string, object> response)
        {
            Console.WriteLine(Json.Serialize(response));
            Console.Out.Flush();
        }

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr window, StringBuilder text, int maximumCount);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextLength(IntPtr window);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint SendInput(uint inputCount, NativeInput[] inputs, int inputSize);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wordParameter, IntPtr longParameter);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wordParameter, StringBuilder longParameter);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wordParameter, string longParameter);

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeInput
        {
            public uint Type;
            public InputUnion Union;
        }

        [StructLayout(LayoutKind.Explicit)]
        private struct InputUnion
        {
            [FieldOffset(0)]
            public KeyboardInput Keyboard;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct KeyboardInput
        {
            public ushort VirtualKey;
            public ushort ScanCode;
            public uint Flags;
            public uint Time;
            public IntPtr ExtraInfo;
        }
    }

    internal sealed class HostObservation
    {
        public int ProcessId;
        public IntPtr WindowHandle;
        public string ProcessName;
        public bool NotepadForeground;
        public bool SupportedTargetForeground;
        public bool RequiredTextPresent;
        public string StateRevision;

        public Dictionary<string, object> ToDictionary()
        {
            return new Dictionary<string, object>
            {
                { "schema_version", "1.0" },
                { "state_revision", StateRevision },
                { "foreground_surface", "desktop" },
                { "facts", new Dictionary<string, object>
                    {
                        { "process_name", ProcessName ?? string.Empty },
                        { "notepad_foreground", NotepadForeground },
                        { "supported_target_foreground", SupportedTargetForeground },
                        { "required_text_present", RequiredTextPresent }
                    }
                }
            };
        }
    }
}
