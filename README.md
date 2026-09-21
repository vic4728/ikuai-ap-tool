<div align="center">

# 爱快路由 - AP 终端工具

**iKuai Router AP Terminal Tool**

基于 PySide6 + Playwright 的爱快路由器 AP 管理图形工具，
支持爱快 **3.x / 4.x 两代固件**，绿色免安装。

[界面预览](#%EF%B8%8F-界面预览) · [功能特性](#-功能特性) · [快速开始](#-快速开始) · [键盘与鼠标](#%EF%B8%8F-键盘与鼠标) · [工作原理](#%EF%B8%8F-工作原理) · [常见问题](#-常见问题)

</div>

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 📋 **AP 终端列表** | 自动读取 AC 管理下的全部 AP：分组 / 名称 / 型号 / IP / MAC / 运行时间 / 状态 / 备注 |
| 🔁 **单台/批量重启** | 操作列按钮重启单台，或勾选多台批量重启；带确认弹窗防误触 |
| 👁 **重启监视** | 三态状态机（等待掉线 → 已断开 → 已恢复），状态栏实时显示恢复进度，杜绝"假恢复" |
| 📝 **备注读写** | 读取路由器内置的 AP 终端备注，双击单元格即可修改并回写（3.x 走弹窗流程，4.x 新固件走 API 整传） |
| 🔃 **列排序** | 表头点击排序：IP 数值序、运行时长语义序、空值恒垫底；自动刷新不打乱排序与选中 |
| 🔄 **自动刷新** | 1/3/5/10 秒可选，差量渲染不闪烁；可随时暂停 |
| 🔐 **加密配置** | 密码 AES-256 加密落盘，密钥绑定本机特征，拷贝配置到其他机器无法解密 |
| 🌐 **双代适配** | 自动探测 3.x（Element UI）与 4.x（Ant Design / Vite 新固件），选择器互不污染 |
| 📦 **绿色单文件 exe** | 配置与输出目录跟随 exe，免安装；机器绑定加密，无明文密码 |

## 🖼 界面预览

![界面预览](https://github.com/vic4728/ikuai-ap-tool/releases/download/v1.0/ikuai-ap-tool-hot.png)

离线 AP 行底色变红、重启中变黄，状态列彩色文字，一目了然。

## 🚀 快速开始

### 方式一：下载 exe（推荐）

1. 到 [Releases](../../releases) 下载 `爱快路由-AP终端工具.exe`（约 85 MB）
2. 放到任意目录，双击运行
3. 首次使用需准备 Playwright 浏览器（见下方 [常见问题](#-常见问题)）

### 方式二：源码运行

```bash
# 1. 克隆
git clone https://github.com/vic4728/ikuai-ap-tool.git
cd ikuai-ap-tool

# 2. 创建环境（Python 3.10+，需含 tkinter 的官方 Python）
python -m venv .venv-gui
.venv-gui\Scripts\activate

# 3. 安装依赖
pip install playwright PySide6 pyyaml

# 4. 安装浏览器（一次性，约 120 MB）
playwright install chromium

# 5. 启动
python ikuai_gui_qt.py        # PySide6 版（推荐）
python ikuai_gui.py           # Tkinter 备份版
```


### 首次连接

1. 填写路由器 IP / 端口 / 协议 / 账号密码
2. 点 **连接** → 自动登录并拉取 AP 列表
3. 勾选 **自动登录** 下次免输入；勾选 **记住密码** 密码加密保存

## 🖱️ 键盘与鼠标

| 操作 | 效果 |
|------|------|
| 双击「备注」单元格 | 修改该 AP 备注（回写路由器） |
| 双击行（其他列） | 重启选中 AP（带确认） |
| 点击操作列「重启」按钮 | 重启该行 AP |
| 点击表头 | 排序（首点降序，再点切换） |
| 右键行 | 菜单：重启 / 修改备注 / 复制 MAC / 刷新等 |
| Ctrl / Shift + 点击 | 多选 AP 后批量重启 |

## ⚙️ 工作原理

```
┌──────────────┐    Qt 信号(跨线程)    ┌──────────────────┐
│  PySide6 UI  │ ◄══════════════════► │   服务层(单线程)   │
│ QTableView   │                      │ Playwright 任务队列 │
│ MVC + QSS    │   _submit() 投递      │ 3.x / 4.x 双 schema │
└──────────────┘                      └──────────────────┘
```

- **不调私有 API 改配置**：所有操作模拟真实浏览器行为（登录 → 导航 → 弹窗），与人工操作完全一致
- **同名 AP 安全定位**：先按 MAC 精确匹配（唯一可靠），名称仅作回退且必须唯一命中，宁可报错也不猜
- **重启成功判定**：确认弹窗必须关闭才算下发成功，随后进入三态监视等设备真正恢复
- **双 schema 隔离**：两代后台的选择器差异全部收在 schema 配置里，业务代码零硬编码

## ❓ 常见问题

**Q: exe 首次连接报 `Executable doesn't exist ... local-browsers ...`？**

Playwright 浏览器未安装。任选其一：

```bash
# 方式 A：装个 Python + playwright 后执行
pip install playwright && playwright install chromium

# 方式 B：从已可用的机器整目录拷贝
# %LOCALAPPDATA%\ms-playwright\  →  新机器同位置（约 400 MB）
```

**Q: 密码存在哪里？安全吗？**

存在 exe 同目录的 `ikuai_config.json`，密码经 **AES-256 加密**，
密钥由本机硬件特征派生——配置文件拷到别的机器无法解出密码。

**Q: 支持哪些爱快固件？**

已实测 3.7.26（Element UI）与 4.0.301 新固件（Ant Design + Vite 构建）。
其余 3.x/4.x 版本理论上兼容，遇选择器差异欢迎提 issue。

**Q: 4.x 新固件备注为什么走 API？**

新固件把编辑表单整体 disabled（Vue 持续重渲染，UI 自动化不可行）。
工具改用 `ac_server/edit` 全量回传（拉取整条 AP 记录 → 只改 comment →
整条回传），即官方前端"保存"按钮的真实行为，其余字段零改动。

**Q: 重启会不会误操作同名 AP？**

不会。定位严格按 **MAC 唯一匹配**；12 台同名 AP 的现场已验证。

## 🧪 测试

12 套离线回归测试，300+ 断言，不连真机即可跑：

```bash
python _qttest.py        # PySide6 界面（31 项）
python _threadtest.py    # 线程模型（97 项）
python _remarktest.py    # 备注功能（29 项）
python _restarttest.py   # 重启流程（21 项）
# ... 详见 README 各测试文件
```

## 📁 目录结构

```
ikuai-ap-tool/
├── ikuai_gui_qt.py        # ★ PySide6 主程序
├── ikuai_gui.py           # Tkinter 备份版
├── ikuai_service.py       # 服务层（Playwright + 双 schema）
├── ikuai_config.py        # 加密配置
├── scripts/               # 辅助脚本
├── _*test.py              # 12 套离线测试
└── ikuai_config.py        # 加密配置模块
```

## 📄 许可

仅供学习与内部运维使用。请遵守爱快官方用户协议，勿用于未授权设备。

---

<div align="center">

**iKuai AP Terminal Tool** · v1.0 · Jcsit & viclai

⭐ 如果对你有帮助，欢迎 Star

</div>
