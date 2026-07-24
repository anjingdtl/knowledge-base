using System.Diagnostics;
using System.Runtime.InteropServices;

internal static class Program
{
    private const string ServiceName = "ShineHeMCP";
    private const uint ServiceWin32OwnProcess = 0x10;
    private const uint ServiceStartPending = 2;
    private const uint ServiceRunning = 4;
    private const uint ServiceStopPending = 3;
    private const uint ServiceStopped = 1;
    private const uint ServiceAcceptStop = 1;
    private const uint ServiceControlStop = 1;

    private static readonly ServiceMainDelegate MainDelegate = ServiceMain;
    private static readonly HandlerDelegate ControlDelegate = Control;
    private static EventWaitHandle? _stopEvent;
    private static IntPtr _statusHandle;
    private static Process? _mcp;
    private static string _projectRoot = AppContext.BaseDirectory;

    private static int Main(string[] args)
    {
        var rootIndex = Array.IndexOf(args, "--project-root");
        if (rootIndex >= 0 && rootIndex + 1 < args.Length)
            _projectRoot = Path.GetFullPath(args[rootIndex + 1]);

        if (args.Contains("--console"))
        {
            StartMcp();
            Console.CancelKeyPress += (_, e) => { e.Cancel = true; StopMcp(); };
            _mcp?.WaitForExit();
            return _mcp?.ExitCode ?? 1;
        }

        var table = new[]
        {
            new ServiceTableEntry { ServiceName = ServiceName, ServiceMain = MainDelegate },
            new ServiceTableEntry(),
        };
        return StartServiceCtrlDispatcher(table) ? 0 : Marshal.GetLastWin32Error();
    }

    private static void ServiceMain(uint _, IntPtr __)
    {
        _statusHandle = RegisterServiceCtrlHandlerEx(ServiceName, ControlDelegate, IntPtr.Zero);
        if (_statusHandle == IntPtr.Zero) return;

        _stopEvent = new EventWaitHandle(false, EventResetMode.ManualReset);
        SetStatus(ServiceStartPending, 30000);
        try
        {
            StartMcp();
            SetStatus(ServiceRunning);
            _stopEvent.WaitOne();
        }
        catch (Exception exception)
        {
            EventLog("启动失败: " + exception);
        }
        finally
        {
            SetStatus(ServiceStopPending, 10000);
            StopMcp();
            SetStatus(ServiceStopped);
            _stopEvent?.Dispose();
        }
    }

    private static uint Control(uint control, uint _, IntPtr __, IntPtr ___)
    {
        if (control == ServiceControlStop)
        {
            SetStatus(ServiceStopPending, 10000);
            _stopEvent?.Set();
        }
        return 0;
    }

    private static void StartMcp()
    {
        var python = Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) throw new FileNotFoundException("找不到项目 Python 运行时", python);
        var script = Path.Combine(_projectRoot, "run_mcp.py");
        if (!File.Exists(script)) throw new FileNotFoundException("找不到 MCP 入口", script);
        var logDirectory = Path.Combine(_projectRoot, "data", "logs");
        Directory.CreateDirectory(logDirectory);

        var info = new ProcessStartInfo(python)
        {
            Arguments = $"\"{script}\" -t streamable-http --host 127.0.0.1 -p 9000",
            WorkingDirectory = _projectRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        info.Environment["SHINEHE_HOME"] = _projectRoot;
        _mcp = Process.Start(info) ?? throw new InvalidOperationException("无法创建 MCP 子进程");
        var log = Path.Combine(logDirectory, "windows-service-mcp.log");
        _mcp.OutputDataReceived += (_, e) => Append(log, e.Data);
        _mcp.ErrorDataReceived += (_, e) => Append(log, e.Data);
        _mcp.BeginOutputReadLine();
        _mcp.BeginErrorReadLine();
        _mcp.EnableRaisingEvents = true;
        _mcp.Exited += (_, _) =>
        {
            EventLog("MCP 子进程已退出，代码=" + _mcp?.ExitCode);
            // 让 SCM 明确看到服务停止，继而应用既有的失败恢复策略；不能仅让
            // 服务宿主空转为 RUNNING 而 MCP 实际已经离线。
            _stopEvent?.Set();
        };
    }

    private static void StopMcp()
    {
        if (_mcp is { HasExited: false })
        {
            _mcp.Kill(true);
            _mcp.WaitForExit(10000);
        }
        _mcp?.Dispose();
        _mcp = null;
    }

    private static void Append(string path, string? line)
    {
        if (!String.IsNullOrWhiteSpace(line)) File.AppendAllText(path, $"{DateTimeOffset.Now:O} {line}{Environment.NewLine}");
    }

    private static void EventLog(string message)
    {
        try
        {
            var log = Path.Combine(_projectRoot, "data", "logs", "windows-service-host.log");
            Directory.CreateDirectory(Path.GetDirectoryName(log)!);
            File.AppendAllText(log, $"{DateTimeOffset.Now:O} {message}{Environment.NewLine}");
        }
        catch { }
    }

    private static void SetStatus(uint state, uint waitHint = 0)
    {
        if (_statusHandle == IntPtr.Zero) return;
        var status = new ServiceStatus
        {
            ServiceType = ServiceWin32OwnProcess,
            CurrentState = state,
            ControlsAccepted = state == ServiceRunning ? ServiceAcceptStop : 0,
            WaitHint = waitHint,
        };
        SetServiceStatus(_statusHandle, ref status);
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ServiceTableEntry { public string? ServiceName; public ServiceMainDelegate? ServiceMain; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ServiceStatus { public uint ServiceType, CurrentState, ControlsAccepted, Win32ExitCode, ServiceSpecificExitCode, CheckPoint, WaitHint; }
    private delegate void ServiceMainDelegate(uint argc, IntPtr argv);
    private delegate uint HandlerDelegate(uint control, uint eventType, IntPtr eventData, IntPtr context);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool StartServiceCtrlDispatcher([In] ServiceTableEntry[] table);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr RegisterServiceCtrlHandlerEx(string name, HandlerDelegate handler, IntPtr context);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool SetServiceStatus(IntPtr handle, ref ServiceStatus status);
}
