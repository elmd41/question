# 科技馆数字人实时语音系统

这个目录现在包含一套可运行的 `FastAPI + React/Vite` 项目，按“按钮唤起式”数字人方案实现：

- 访客页默认待机，点击 `开始对话` 后才申请麦克风、建立实时会话
- 支持手动结束、页面断开回收和静默超时自动结束，后台可切换策略
- 前端明确展示 `正在听你说话`
- 后台支持密码登录、草稿保存、版本发布和会话重置
- 后端默认支持 `mock` 模式，未配置上游密钥时也能验证主流程

## 目录结构

- `backend/`：FastAPI、SQLite、后台登录、CSRF、实时语音桥接
- `frontend/`：React、访客页、后台页、AudioWorklet、GLB 舞台
- `realtime_dialog/`：原始上游 Python demo，作为协议参考保留

## 本地启动

### 一键启动

```powershell
cd question
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```

或直接运行：

```bat
question\start-local.bat
```

这个脚本会：

- 使用仓库内唯一的熊猫模型 `frontend/public/models/panda-v2.glb`
- 安装后端和前端依赖
- 从 `backend/.env.local` 读取本机火山实时语音配置
- 构建前端并在 `4800` 端口启动后端
- 自动打开访客页

后台入口：`http://127.0.0.1:4800/admin/login`

默认后台密码：`MuseumAdmin123!`

首次使用前，请先在 `backend/.env.local` 中填写本机实时语音配置。仓库内只保留 `backend/.env.example` 模板，不提交真实凭证。

### 1. 后端

```powershell
cd D:\DeLu\question\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

### 2. 前端开发模式

```powershell
cd D:\DeLu\question\frontend
npm install
npm run dev
```

开发模式下前端默认代理到 `http://127.0.0.1:8000`。

### 3. 前端打包并由后端托管

```powershell
cd D:\DeLu\question\frontend
npm run build
```

构建完成后，后端会自动读取 `frontend/dist` 并托管页面。

## 环境变量

后端默认使用 `mock` 模式。要接真实上游实时语音服务，请至少配置：

```powershell
$env:UPSTREAM_MODE="volcengine"
$env:UPSTREAM_APP_ID="你的 App ID"
$env:UPSTREAM_ACCESS_KEY="你的 Access Key"
$env:UPSTREAM_RESOURCE_ID="volc.speech.dialog"
$env:UPSTREAM_APP_KEY="你的 App Key"
$env:ADMIN_PASSWORD="你的后台密码"
$env:SESSION_SECRET="随机长字符串"
```

如果没有设置 `UPSTREAM_MODE`，系统会自动判断：

- 配了 `UPSTREAM_APP_ID + UPSTREAM_ACCESS_KEY`：使用真实上游
- 没配：使用 `mock`

## 已验证

- `backend`: `pytest` 通过
- `frontend`: `npm run build` 通过

## 当前实现边界

- v1 只按单机展厅模式设计
- v1 只明确支持桌面 Chrome/Chromium 系浏览器
- GLB 模型支持能力探测和降级显示；如果没有可用模型，会回退到抽象数字人形态
