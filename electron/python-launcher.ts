import { spawn, ChildProcess } from "child_process";
import * as crypto from "crypto";
import * as path from "path";
import * as fs from "fs";
import * as http from "http";
import { app } from "electron";

interface LauncherConfig {
  port: number;
  userDataPath: string;
  startupTimeout?: number;
  healthCheckInterval?: number;
  maxRestarts?: number;
  restartCooldown?: number;
}

interface LauncherEvents {
  onReady: () => void;
  onError: (message: string) => void;
  onExit: (code: number | null, signal: string | null) => void;
  onRestart: (attempt: number) => void;
}

const DEFAULT_CONFIG = {
  port: 4800,
  startupTimeout: 180000, // 3 分钟，给开机时磁盘 I/O 高峰期更多时间
  healthCheckInterval: 5000,
  maxRestarts: 10, // 增加重试次数到 10 次，开机时可能需要多次尝试
  restartCooldown: 10000, // 重启间隔增加到 10 秒，让系统有时间释放资源
};

/**
 * Python 后端进程管理器
 *
 * 功能：
 * - 启动/停止 Python 后端子进程
 * - 健康检查和自动重启
 * - 防止僵尸进程（内存泄漏防护）
 * - 优雅关闭
 */
export class PythonLauncher {
  private config: Required<LauncherConfig>;
  private events: LauncherEvents;
  private process: ChildProcess | null = null;
  private isShuttingDown = false;
  private isReady = false;
  private restartCount = 0;
  private healthCheckTimer: ReturnType<typeof setInterval> | null = null;
  private startupTimer: ReturnType<typeof setTimeout> | null = null;
  private lastHealthCheck = 0;
  private consecutiveFailures = 0;

  private appendLauncherLog(message: string): void {
    try {
      const logDir = path.join(this.config.userDataPath, "logs");
      if (!fs.existsSync(logDir)) {
        fs.mkdirSync(logDir, { recursive: true });
      }
      const logPath = path.join(logDir, "backend-launcher.log");
      const timestamp = new Date().toISOString();
      fs.appendFileSync(logPath, `[${timestamp}] ${message}\n`, { encoding: "utf-8" });
    } catch {
      // ignore
    }
  }

  private terminateProcess(proc: ChildProcess): void {
    if (process.platform === "win32") {
      proc.kill();
      return;
    }
    proc.kill("SIGTERM");
  }

  private forceKillProcess(proc: ChildProcess): void {
    if (process.platform === "win32" && proc.pid) {
      spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
        stdio: "ignore",
        windowsHide: true,
      });
      return;
    }
    proc.kill("SIGKILL");
  }

  constructor(config: LauncherConfig, events: LauncherEvents) {
    this.config = { ...DEFAULT_CONFIG, ...config } as Required<LauncherConfig>;
    this.events = events;
  }

  /**
   * 启动 Python 后端
   */
  async start(): Promise<void> {
    if (this.process) {
      console.log("[PythonLauncher] Process already running");
      return;
    }

    this.isShuttingDown = false;
    this.isReady = false;

    const exePath = this.resolveExePath();
    if (!fs.existsSync(exePath)) {
      throw new Error(`Python backend not found: ${exePath}`);
    }

    console.log("[PythonLauncher] Starting Python backend:", exePath);
    this.appendLauncherLog(`Starting backend: exe=${exePath}, port=${this.config.port}, packaged=${app.isPackaged}`);

    // 开机自启时系统可能还不稳定，等待更长时间让系统就绪
    // 检测是否是开机后短时间内启动（系统启动后 10 分钟内）
    const systemUptime = require("os").uptime();
    if (systemUptime < 600) {
      // 开机后 2 分钟内等待 30 秒，2-5 分钟内等待 15 秒，5-10 分钟内等待 5 秒
      let waitSeconds = 5;
      if (systemUptime < 120) {
        waitSeconds = 30;
      } else if (systemUptime < 300) {
        waitSeconds = 15;
      }
      console.log(`[PythonLauncher] System just booted (uptime=${systemUptime}s), waiting ${waitSeconds}s before starting backend...`);
      this.appendLauncherLog(`System uptime=${systemUptime}s, waiting ${waitSeconds}s before starting backend`);
      await new Promise((resolve) => setTimeout(resolve, waitSeconds * 1000));
    }

    // 设置环境变量
    const env = {
      ...process.env,
      // 用户数据目录
      MUSEUM_DATA_DIR: path.join(this.config.userDataPath, "data"),
      // 日志目录
      MUSEUM_LOG_DIR: path.join(this.config.userDataPath, "logs"),
      // 端口
      MUSEUM_PORT: String(this.config.port),
    };

    // 确保用户数据目录存在
    const dataDir = path.join(this.config.userDataPath, "data");
    const logDir = path.join(this.config.userDataPath, "logs");
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }
    if (!fs.existsSync(logDir)) {
      fs.mkdirSync(logDir, { recursive: true });
    }

    const stdoutPath = path.join(logDir, "backend-stdout.log");
    const stderrPath = path.join(logDir, "backend-stderr.log");

    // 种子数据库：seed 内容变化或首次安装时，从打包的种子覆盖用户数据库
    if (app.isPackaged) {
      const userDb = path.join(dataDir, "museum.db");
      const seedDb = path.join(process.resourcesPath, "seed", "museum.db");
      const hashFile = path.join(dataDir, ".seed_hash");

      if (fs.existsSync(seedDb)) {
        const seedHash = crypto
          .createHash("md5")
          .update(fs.readFileSync(seedDb))
          .digest("hex");
        const installedHash = fs.existsSync(hashFile)
          ? fs.readFileSync(hashFile, "utf-8").trim()
          : "";

        if (seedHash !== installedHash) {
          console.log(
            `[PythonLauncher] Seed changed (${installedHash || "none"} -> ${seedHash}), re-seeding database`
          );
          fs.copyFileSync(seedDb, userDb);
          fs.writeFileSync(hashFile, seedHash, "utf-8");
        }
      }
    }

    // 启动子进程
    this.process = spawn(exePath, [], {
      env,
      cwd: app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "../../backend"),
      stdio: ["ignore", "pipe", "pipe"],
      detached: false, // 确保子进程随父进程退出
      windowsHide: true, // Windows 下隐藏控制台窗口
    });

    this.process.stdout?.on("data", (data: Buffer) => {
      try {
        fs.appendFileSync(stdoutPath, data);
      } catch {
        // ignore
      }
    });

    this.process.stderr?.on("data", (data: Buffer) => {
      try {
        fs.appendFileSync(stderrPath, data);
      } catch {
        // ignore
      }
    });

    this.setupProcessHandlers();
    this.startHealthCheck();
    this.startStartupTimeout();

    // 等待后端就绪
    await this.waitForReady();
  }

  /**
   * 停止 Python 后端
   */
  async stop(): Promise<void> {
    if (!this.process) {
      return;
    }

    console.log("[PythonLauncher] Stopping Python backend");
    this.isShuttingDown = true;
    this.stopHealthCheck();
    this.clearStartupTimeout();
    this.isReady = false;

    const proc = this.process;

    return new Promise<void>((resolve) => {
      const settled = () => {
        clearTimeout(graceTimeout);
        clearTimeout(absoluteTimeout);
        if (this.process === proc) {
          this.process = null;
        }
        resolve();
      };

      // 5s 后强杀进程树
      const graceTimeout = setTimeout(() => {
        console.log("[PythonLauncher] Force killing process");
        this.forceKillProcess(proc);
      }, 5000);

      // 8s 绝对兜底：即使 exit 事件未触发也必须放行
      const absoluteTimeout = setTimeout(() => {
        console.error("[PythonLauncher] Absolute stop timeout, giving up");
        settled();
      }, 8000);

      proc.once("exit", settled);

      // 先尝试优雅关闭
      this.terminateProcess(proc);
    });
  }

  /**
   * 检查后端是否就绪
   */
  isBackendReady(): boolean {
    return this.isReady;
  }

  /**
   * 解析 exe 路径
   */
  private resolveExePath(): string {
    // 开发模式：使用 Python 解释器
    if (app.isPackaged === false) {
      const backendDir = path.resolve(__dirname, "../../backend");
      return path.join(backendDir, ".venv/Scripts/python.exe");
    }

    // 生产模式：使用打包的 exe
    const resourcesPath = process.resourcesPath;
    return path.join(resourcesPath, "backend.exe");
  }

  /**
   * 设置进程事件处理器
   */
  private setupProcessHandlers(): void {
    if (!this.process) return;

    // 标准输出
    this.process.stdout?.on("data", (data: Buffer) => {
      const output = data.toString().trim();
      if (output) {
        console.log("[Python stdout]", output);
      }
    });

    // 标准错误
    this.process.stderr?.on("data", (data: Buffer) => {
      const output = data.toString().trim();
      if (output) {
        console.error("[Python stderr]", output);
      }
    });

    // 进程退出
    this.process.on("exit", (code, signal) => {
      console.log(`[PythonLauncher] Process exited: code=${code}, signal=${signal}`);
      this.appendLauncherLog(`Backend exited: code=${code}, signal=${signal}`);
      this.process = null;
      this.isReady = false;
      this.events.onExit(code, signal);

      // 非正常退出且未在关闭中，尝试重启
      if (!this.isShuttingDown && code !== 0) {
        this.handleCrash();
      }
    });

    // 进程错误
    this.process.on("error", (error) => {
      console.error("[PythonLauncher] Process error:", error);
      this.appendLauncherLog(`Backend process error: ${error.message}`);
      this.events.onError(`Process error: ${error.message}`);
    });
  }

  /**
   * 处理进程崩溃
   */
  private handleCrash(): void {
    if (this.isShuttingDown) return;

    this.restartCount++;
    console.log(
      `[PythonLauncher] Crash detected, restart attempt ${this.restartCount}/${this.config.maxRestarts}`
    );

    if (this.restartCount > this.config.maxRestarts) {
      console.error("[PythonLauncher] Max restarts exceeded");
      this.events.onError("后端服务多次重启失败，请检查日志或重启应用。");
      return;
    }

    this.events.onRestart(this.restartCount);

    // 冷却后重启，开机时使用更长的冷却时间
    const systemUptime = require("os").uptime();
    const cooldown = systemUptime < 600 
      ? Math.max(this.config.restartCooldown, 20000) // 开机时至少等 20 秒
      : this.config.restartCooldown;
    
    this.appendLauncherLog(`Scheduling restart in ${cooldown}ms (uptime=${systemUptime}s)`);
    
    setTimeout(() => {
      if (!this.isShuttingDown) {
        this.start().catch((error) => {
          console.error("[PythonLauncher] Restart failed:", error);
          this.appendLauncherLog(`Restart failed: ${error.message}`);
        });
      }
    }, cooldown);
  }

  /**
   * 启动健康检查
   */
  private startHealthCheck(): void {
    this.healthCheckTimer = setInterval(() => {
      this.checkHealth();
    }, this.config.healthCheckInterval);
  }

  /**
   * 停止健康检查
   */
  private stopHealthCheck(): void {
    if (this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer);
      this.healthCheckTimer = null;
    }
  }

  /**
   * 健康检查
   */
  private checkHealth(): void {
    if (!this.process || this.isShuttingDown) return;

    const now = Date.now();
    if (now - this.lastHealthCheck < this.config.healthCheckInterval / 2) {
      return;
    }
    this.lastHealthCheck = now;

    const url = `http://127.0.0.1:${this.config.port}/api/health`;
    const req = http.get(url, { timeout: 3000 }, (res) => {
      if (res.statusCode === 200) {
        this.consecutiveFailures = 0;
        if (!this.isReady) {
          this.isReady = true;
          this.clearStartupTimeout();
          this.events.onReady();
          console.log("[PythonLauncher] Backend is ready");
        }
      }
      res.resume();
    });

    req.on("error", (error) => {
      this.consecutiveFailures++;
      console.log(
        `[PythonLauncher] Health check failed (${this.consecutiveFailures}):`,
        error.message
      );
      // 连续多次失败，可能进程已死
      if (this.consecutiveFailures >= 3 && this.process) {
        console.error("[PythonLauncher] Backend unresponsive, killing process");
        this.forceKillProcess(this.process);
      }
    });

    req.on("timeout", () => {
      req.destroy();
      this.consecutiveFailures++;
    });
  }

  /**
   * 启动超时检查
   */
  private startStartupTimeout(): void {
    this.startupTimer = setTimeout(() => {
      if (!this.isReady) {
        console.error("[PythonLauncher] Startup timeout");
        this.events.onError("后端服务启动超时，请检查配置或重启应用。");
        this.stop();
      }
    }, this.config.startupTimeout);
  }

  /**
   * 清除启动超时
   */
  private clearStartupTimeout(): void {
    if (this.startupTimer) {
      clearTimeout(this.startupTimer);
      this.startupTimer = null;
    }
  }

  /**
   * 等待后端就绪
   */
  async waitForReady(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const check = () => {
        if (this.isReady) {
          resolve();
        } else if (this.isShuttingDown) {
          reject(new Error("Backend shutdown"));
        } else {
          setTimeout(check, 100);
        }
      };
      check();
    });
  }
}
