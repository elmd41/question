import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  nativeImage,
  session,
  shell,
  Tray,
} from "electron";
import * as path from "path";
import * as fs from "fs";
import { execFile } from "child_process";
import { PythonLauncher } from "./python-launcher";

// GPU 硬件加速对 Three.js 3D 场景至关重要，不要禁用
// 如果个别机器有 GPU 兼容问题，可通过启动参数 --disable-gpu 临时关闭
// 双显卡笔记本/一体机：强制使用高性能 GPU（NVIDIA/AMD 独显）
app.commandLine.appendSwitch("force_high_performance_gpu");

// 禁用系统硬件回声消除（AEC），改用 Chromium 内置软件 AEC
// 部分一体机预装语音程序的 AEC 驱动会残留/冲突，导致 getUserMedia 失败
// 禁用后 Chromium 会自动使用自己的软件 AEC，效果相同且更稳定
app.commandLine.appendSwitch("disable-features", "HardwareAudioKeyHandling");

// 单实例锁
// 如果旧实例没有正常退出（崩溃/强杀），系统可能认为旧进程还在，
// 导致新实例拿不到锁直接退出，看起来就是"不能自启动"
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  // 第二个实例：通知已有实例显示窗口，然后退出
  console.log("[Main] Another instance is already running, quitting this one");
  app.quit();
  process.exit(0);
}

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let pythonLauncher: PythonLauncher | null = null;
let isQuitting = false;
let isExiting = false;
let shutdownPromise: Promise<void> | null = null;

// 配置
const BACKEND_PORT = 4800;
const APP_URL = `http://127.0.0.1:${BACKEND_PORT}`;

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildStatusPage(title: string, message: string, detail?: string): string {
  const safeTitle = escapeHtml(title);
  const safeMessage = escapeHtml(message);
  const safeDetail = detail ? `<div class="detail">${escapeHtml(detail)}</div>` : "";
  const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${safeTitle}</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #0f172a 0%, #111827 100%);
      color: #e5e7eb;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .panel {
      width: min(720px, calc(100vw - 64px));
      padding: 40px;
      border-radius: 24px;
      background: rgba(17, 24, 39, 0.88);
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.35);
      border: 1px solid rgba(148, 163, 184, 0.22);
    }
    .title {
      font-size: 32px;
      font-weight: 700;
      margin-bottom: 16px;
    }
    .message {
      font-size: 20px;
      line-height: 1.7;
      color: #cbd5e1;
    }
    .detail {
      margin-top: 20px;
      padding: 16px 18px;
      border-radius: 14px;
      background: rgba(15, 23, 42, 0.72);
      color: #fca5a5;
      font-size: 16px;
      line-height: 1.7;
      white-space: pre-wrap;
      word-break: break-word;
    }
  </style>
</head>
<body>
  <main class="panel">
    <div class="title">${safeTitle}</div>
    <div class="message">${safeMessage}</div>
    ${safeDetail}
  </main>
</body>
</html>`;
  return `data:text/html;charset=UTF-8,${encodeURIComponent(html)}`;
}

function showStatusPage(win: BrowserWindow, title: string, message: string, detail?: string): Promise<void> {
  return win.loadURL(buildStatusPage(title, message, detail));
}

function showStartupPage(win: BrowserWindow): Promise<void> {
  return showStatusPage(win, "系统启动中", "正在启动讲解服务，请稍候。", "首次启动或设备性能较慢时，等待时间可能稍长。");
}

function showBackendErrorPage(win: BrowserWindow, detail: string): Promise<void> {
  return showStatusPage(win, "启动失败", "讲解服务启动失败，请联系管理员或重启应用。", detail);
}

async function loadAppPage(win: BrowserWindow): Promise<void> {
  try {
    await win.loadURL(APP_URL);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("[Main] Failed to load app URL:", message);
    await showBackendErrorPage(win, `页面加载失败：${message}`);
  }
}

function setupMediaPermissions(): void {
  const defaultSession = session.defaultSession;
  if (!defaultSession) {
    return;
  }

  defaultSession.setPermissionCheckHandler((_webContents, permission, requestingOrigin, details) => {
    if (permission === "media") {
      const mediaType = details.mediaType ?? "unknown";
      const origin = details.securityOrigin ?? requestingOrigin;
      console.log(`[Permissions] Check allow: permission=${permission}, mediaType=${mediaType}, origin=${origin}`);
      return true;
    }
    return false;
  });

  defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    if (permission === "media") {
      const mediaTypes = "mediaTypes" in details ? (details.mediaTypes?.join(",") ?? "unknown") : "unknown";
      const origin = "securityOrigin" in details ? (details.securityOrigin ?? "unknown") : "unknown";
      console.log(`[Permissions] Request allow: permission=${permission}, mediaTypes=${mediaTypes}, origin=${origin}`);
      callback(true);
      return;
    }

    const origin = "securityOrigin" in details ? (details.securityOrigin ?? "unknown") : "unknown";
    console.log(`[Permissions] Request deny: permission=${permission}, origin=${origin}`);
    callback(false);
  });
}

/**
 * 创建主窗口
 */
function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    show: false, // 先隐藏，加载完成后显示
    fullscreen: true, // 启动即全屏
    frame: false, // 无边框（全屏模式下隐藏标题栏）
    autoHideMenuBar: true,
    icon: getIconPath(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
    },
  });

  // 窗口加载完成后显示
  win.once("ready-to-show", () => {
    win.show();
    win.setFullScreen(true);
  });

  // 禁止 ESC 退出全屏（保持全屏状态）
  win.on("leave-full-screen", () => {
    setTimeout(() => {
      if (!isQuitting && win && !win.isDestroyed()) {
        win.setFullScreen(true);
      }
    }, 100);
  });

  // 外部链接用默认浏览器打开
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });

  win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame) {
      return;
    }
    console.error(
      `[Main] Main frame load failed: code=${errorCode}, url=${validatedURL}, error=${errorDescription}`
    );
    if (validatedURL.startsWith(APP_URL)) {
      void showBackendErrorPage(win, `页面加载失败：${errorDescription} (${errorCode})`);
    }
  });

  // 关闭窗口时最小化到托盘（不退出）
  win.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      win.hide();
    }
  });

  win.on("closed", () => {
    mainWindow = null;
  });

  return win;
}

/**
 * 创建系统托盘
 */
function createTray(): Tray {
  const icon = nativeImage.createFromPath(getIconPath());
  const trayIcon = new Tray(icon.resize({ width: 16, height: 16 }));

  const contextMenu = Menu.buildFromTemplate([
    {
      label: "显示窗口",
      click: () => {
        mainWindow?.show();
        mainWindow?.focus();
      },
    },
    {
      label: "重启服务",
      click: async () => {
        if (pythonLauncher) {
          await pythonLauncher.stop();
          await pythonLauncher.start();
        }
      },
    },
    { type: "separator" },
    {
      label: "退出",
      click: () => {
        void shutdownApplication();
      },
    },
  ]);

  trayIcon.setToolTip("科技馆数字人讲解系统");
  trayIcon.setContextMenu(contextMenu);

  // 双击托盘图标显示窗口
  trayIcon.on("double-click", () => {
    mainWindow?.show();
    mainWindow?.focus();
  });

  return trayIcon;
}

/**
 * 获取图标路径
 */
function getIconPath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "icon.ico");
  }
  return path.join(__dirname, "../build/icon.ico");
}

/**
 * 获取用户数据目录
 */
function getUserDataPath(): string {
  return app.getPath("userData");
}

/**
 * 启动 Python 后端
 */
async function startPythonBackend(): Promise<void> {
  const userDataPath = getUserDataPath();
  pythonLauncher = new PythonLauncher(
    {
      port: BACKEND_PORT,
      userDataPath,
      startupTimeout: 180000, // 3 分钟，给慢机器/开机高峰期更多时间
    },
    {
      onReady: () => {
        console.log("[Main] Python backend ready");
        if (mainWindow?.webContents.getURL().startsWith(APP_URL)) {
          mainWindow.webContents.send("backend-ready");
          return;
        }
        if (mainWindow) {
          void loadAppPage(mainWindow);
        }
      },
      onError: (message: string) => {
        console.error("[Main] Python backend error:", message);
        mainWindow?.webContents.send("backend-error", message);
        if (mainWindow && !mainWindow.webContents.getURL().startsWith(APP_URL)) {
          void showBackendErrorPage(mainWindow, message);
        }
      },
      onExit: (code: number | null, signal: string | null) => {
        console.log(`[Main] Python backend exited: code=${code}, signal=${signal}`);
      },
      onRestart: (attempt: number) => {
        console.log(`[Main] Python backend restarting (attempt ${attempt})`);
        mainWindow?.webContents.send(
          "backend-error",
          `后端服务异常，正在重启 (${attempt})...`
        );
      },
    }
  );

  await pythonLauncher.start();
}

/**
 * 执行系统命令
 */
function runCommand(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout: 10000, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        reject(error);
        return;
      }
      resolve(stdout);
    });
  });
}

/**
 * 设置 IPC 处理器
 */
function setupIpcHandlers(): void {
  // 获取应用版本
  ipcMain.handle("get-version", () => {
    return app.getVersion();
  });

  // 退出应用
  ipcMain.on("app-quit", () => {
    void shutdownApplication();
  });

  // 重启应用
  ipcMain.on("app-restart", async () => {
    await shutdownApplication({ relaunch: true });
  });

  // 日志
  ipcMain.on("log", (_, { level, message }: { level: string; message: string }) => {
    const logPath = path.join(getUserDataPath(), "logs", "electron.log");
    const timestamp = new Date().toISOString();
    const logLine = `[${timestamp}] [${level.toUpperCase()}] ${message}\n`;
    fs.appendFileSync(logPath, logLine, { encoding: "utf-8" });
  });

  // 音频诊断
  ipcMain.handle("diagnose-audio", async () => {
    const result: Record<string, unknown> = { platform: process.platform };

    if (process.platform === "win32") {
      try {
        // 检查音频服务状态
        const audioServiceStatus = await runCommand("powershell", [
          "-NoProfile",
          "-Command",
          "Get-Service -Name Audiosrv | Select-Object -ExpandProperty Status",
        ]);
        result.audioServiceStatus = audioServiceStatus.trim();
      } catch (e) {
        result.audioServiceStatus = `查询失败: ${e instanceof Error ? e.message : String(e)}`;
      }

      try {
        // 列出音频播放设备
        const playbackDevices = await runCommand("powershell", [
          "-NoProfile",
          "-Command",
          "Get-CimInstance Win32_SoundDevice | Select-Object Name, Status, StatusInfo | ConvertTo-Json",
        ]);
        result.soundDevices = JSON.parse(playbackDevices || "[]");
      } catch (e) {
        result.soundDevices = `查询失败: ${e instanceof Error ? e.message : String(e)}`;
      }

      try {
        // 检查麦克风隐私设置（Windows 10+）
        const micPrivacy = await runCommand("powershell", [
          "-NoProfile",
          "-Command",
          "Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue",
        ]);
        result.microphonePrivacyValue = micPrivacy.trim() || "unknown";
      } catch {
        result.microphonePrivacyValue = "查询失败（可能需要管理员权限）";
      }

      try {
        // 检查默认录音设备
        const defaultMic = await runCommand("powershell", [
          "-NoProfile",
          "-Command",
          "Get-CimInstance Win32_SoundDevice | Where-Object { $_.Status -eq 'OK' } | Select-Object Name, Status | ConvertTo-Json",
        ]);
        result.activeSoundDevices = JSON.parse(defaultMic || "[]");
      } catch (e) {
        result.activeSoundDevices = `查询失败: ${e instanceof Error ? e.message : String(e)}`;
      }

      try {
        // 检测可能占用音频设备的进程（语音助手、语音交互程序等）
        const audioProcesses = await runCommand("powershell", [
          "-NoProfile",
          "-Command",
          `Get-Process | Where-Object { $_.MainWindowTitle -ne '' -or $_.Name -match 'voice|speech|audio|mic|record|rekoda|siri|cortana|xiaowei|dueros|iflytek' } | Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json`,
        ]);
        result.potentialAudioProcesses = JSON.parse(audioProcesses || "[]");
      } catch (e) {
        result.potentialAudioProcesses = `查询失败: ${e instanceof Error ? e.message : String(e)}`;
      }

      try {
        // 检查 Windows 麦克风"允许应用访问"开关
        const micAccess = await runCommand("powershell", [
          "-NoProfile",
          "-Command",
          `try { $val = (Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone' -ErrorAction Stop).Value; if ($val -eq 0) { 'Deny' } elseif ($val -eq 1) { 'Allow' } else { "Value=$val" } } catch { 'NotSet_or_AccessDenied' }`,
        ]);
        result.microphoneAppAccess = micAccess.trim();
      } catch {
        result.microphoneAppAccess = "查询失败";
      }
    }

    return result;
  });
}

async function shutdownApplication(options?: { relaunch?: boolean }): Promise<void> {
  if (shutdownPromise) {
    return shutdownPromise;
  }

  shutdownPromise = (async () => {
    isQuitting = true;
    try {
      if (pythonLauncher) {
        await pythonLauncher.stop();
        pythonLauncher = null;
      }
    } finally {
      tray?.destroy();
      tray = null;
      if (options?.relaunch) {
        app.relaunch();
      }
      isExiting = true;
      app.exit(0);
    }
  })();

  return shutdownPromise;
}

/**
 * 写日志到文件
 */
function writeLog(level: string, message: string): void {
  const logPath = path.join(getUserDataPath(), "logs", "electron.log");
  const logDir = path.dirname(logPath);
  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir, { recursive: true });
  }
  const timestamp = new Date().toISOString();
  const logLine = `[${timestamp}] [${level.toUpperCase()}] ${message}\n`;
  fs.appendFileSync(logPath, logLine, { encoding: "utf-8" });
}

/**
 * 启动时自动运行音频诊断并写日志
 */
async function runAudioDiagnosticsOnStartup(): Promise<void> {
  writeLog("info", "=== 应用启动，开始音频诊断 ===");

  // 系统基本信息
  writeLog("info", `平台: ${process.platform}, 架构: ${process.arch}, Electron: ${process.versions.electron}, Chrome: ${process.versions.chrome}`);

  if (process.platform === "win32") {
    try {
      const audioServiceStatus = await runCommand("powershell", [
        "-NoProfile", "-Command",
        "Get-Service -Name Audiosrv | Select-Object -ExpandProperty Status",
      ]);
      writeLog("info", `音频服务状态: ${audioServiceStatus.trim()}`);
    } catch (e) {
      writeLog("warn", `音频服务查询失败: ${e instanceof Error ? e.message : String(e)}`);
    }

    try {
      const soundDevices = await runCommand("powershell", [
        "-NoProfile", "-Command",
        "Get-CimInstance Win32_SoundDevice | Select-Object Name, Status | ConvertTo-Json",
      ]);
      writeLog("info", `声音设备: ${soundDevices.trim()}`);
    } catch (e) {
      writeLog("warn", `声音设备查询失败: ${e instanceof Error ? e.message : String(e)}`);
    }

    try {
      const micAccess = await runCommand("powershell", [
        "-NoProfile", "-Command",
        `try { $val = (Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone' -ErrorAction Stop).Value; if ($val -eq 0) { 'Deny' } elseif ($val -eq 1) { 'Allow' } else { "Value=$val" } } catch { 'NotSet_or_AccessDenied' }`,
      ]);
      writeLog("info", `麦克风应用访问权限: ${micAccess.trim()}`);
    } catch (e) {
      writeLog("warn", `麦克风隐私查询失败: ${e instanceof Error ? e.message : String(e)}`);
    }

    try {
      const audioProcesses = await runCommand("powershell", [
        "-NoProfile", "-Command",
        `Get-Process | Where-Object { $_.Name -match 'voice|speech|audio|mic|record|rekoda|siri|cortana|xiaowei|dueros|iflytek' } | Select-Object Id, ProcessName | ConvertTo-Json`,
      ]);
      writeLog("info", `可能占用音频的进程: ${audioProcesses.trim()}`);
    } catch (e) {
      writeLog("warn", `音频进程查询失败: ${e instanceof Error ? e.message : String(e)}`);
    }

    try {
      // 检查默认音频设备音量和静音状态
      const volumeInfo = await runCommand("powershell", [
        "-NoProfile", "-Command",
        `
        try {
          Add-Type -TypeDefinition '@
          using System.Runtime.InteropServices;
          public class Audio {
            [DllImport("kernel32.dll", SetLastError=true)]
            public static extern IntPtr GetConsoleWindow();
          }
          '@ -ErrorAction SilentlyContinue
          $wshShell = New-Object -ComObject WScript.Shell
          # 检查系统静音状态（通过注册表）
          $mutePath = 'HKCU:\Software\Microsoft\Multimedia\Audio'
          if (Test-Path $mutePath) {
            $audioReg = Get-ItemProperty -Path $mutePath -ErrorAction SilentlyContinue
            "AudioReg: $(ConvertTo-Json $audioReg -Compress)"
          } else {
            "AudioReg: not_found"
          }
        } catch {
          "VolumeCheck: failed - $_"
        }
        `,
      ]);
      writeLog("info", `音量信息: ${volumeInfo.trim()}`);
    } catch (e) {
      writeLog("warn", `音量查询失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  writeLog("info", "=== 音频诊断完成 ===");
}

/**
 * 清理残留的旧进程（Windows）
 * 场景：上一次应用崩溃/被强杀，但进程未完全退出，
 * 导致 requestSingleInstanceLock 认为旧实例还在，新实例无法启动。
 * 在 app.requestSingleInstanceLock 成功后调用，确保自己是唯一的。
 */
async function killStaleProcesses(): Promise<void> {
  if (process.platform !== "win32") return;

  const currentPid = process.pid;
  const exeName = path.basename(app.getPath("exe"), ".exe");

  try {
    const result = await runCommand("powershell", [
      "-NoProfile", "-Command",
      `Get-Process -Name '${exeName}' -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne ${currentPid} } | Select-Object Id, ProcessName, StartTime | ConvertTo-Json`,
    ]);

    const staleProcesses = JSON.parse(result || "[]");
    const staleList = Array.isArray(staleProcesses) ? staleProcesses : [staleProcesses];

    if (staleList.length > 0) {
      writeLog("warn", `发现 ${staleList.length} 个残留进程: ${JSON.stringify(staleList)}`);

      for (const proc of staleList) {
        try {
          writeLog("warn", `正在杀掉残留进程: PID=${proc.Id}`);
          await runCommand("taskkill", ["/PID", String(proc.Id), "/F"]);
          writeLog("info", `已杀掉残留进程: PID=${proc.Id}`);
        } catch (e) {
          writeLog("error", `杀掉残留进程 PID=${proc.Id} 失败: ${e instanceof Error ? e.message : String(e)}`);
        }
      }

      // 等待进程完全退出
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  } catch (e) {
    writeLog("warn", `残留进程检测失败: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/**
 * 应用启动
 */
async function bootstrap(): Promise<void> {
  console.log("[Main] Application starting...");

  setupMediaPermissions();

  // 清理残留的旧进程（防止上次崩溃后旧进程未退出导致的问题）
  await killStaleProcesses();

  // 启动时自动运行音频诊断，结果写入日志文件
  void runAudioDiagnosticsOnStartup();

  // 创建窗口
  mainWindow = createMainWindow();
  await showStartupPage(mainWindow);

  // 创建托盘
  tray = createTray();

  // 设置 IPC
  setupIpcHandlers();

  // 启动 Python 后端
  try {
    await startPythonBackend();
  } catch (error) {
    console.error("[Main] Failed to start Python backend:", error);
    const message = `后端服务启动失败: ${error instanceof Error ? error.message : String(error)}`;
    mainWindow?.webContents.send("backend-error", message);
    if (mainWindow) {
      await showBackendErrorPage(mainWindow, message);
    }
  }
}

// 应用就绪
app.whenReady().then(bootstrap);

// 所有窗口关闭时（Windows/Linux）不退出，保持在托盘
app.on("window-all-closed", () => {
  // macOS 上保持运行
});

// 应用激活（macOS 点击 Dock 图标）
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    mainWindow = createMainWindow();
  } else {
    mainWindow?.show();
  }
});

// 应用退出前清理
app.on("before-quit", (event) => {
  if (isExiting) {
    return;
  }
  console.log("[Main] Application quitting...");
  event.preventDefault();
  void shutdownApplication();
});

// 第二个实例启动时，聚焦现有窗口
app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    mainWindow.show();
    mainWindow.focus();
  }
});
