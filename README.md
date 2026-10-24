# Auto Hus

自动续期 [hus.co.id](https://hus.co.id) VPS 订阅的工具：通过浏览器自动化完成登录、Cloudflare 验证、续期全流程，并在遇到验证码拦截时自动更换 WARP IP 重试，最后通过 Telegram 推送结果、VPS 信息与截图。

## 工作原理

1. 用 **DrissionPage** 启动 Chromium（Linux 下由 `pyvirtualdisplay`/Xvfb 提供虚拟显示），打开 hus.co.id 面板
2. 通过 Telegram Widget 登录：脚本用 **Telethon** 监听授权对话并自动点击 `Confirm` 完成授权
3. 自动处理 **Cloudflare Turnstile** 挑战
4. 枚举账户下所有 VPS，逐一执行续期
5. 验证码自适应求解：
   - **reCAPTCHA**：DrissionPage 进入音频 iframe，下载音频后用语音识别（speech_recognition）求解
   - **算术验证码**：ddddocr OCR 识别
6. 若验证码被服务端拒绝，自动**重启 WARP 更换出口 IP** 后重新提交（多层重试，见下方调参）
7. 续期结果检测基于结果页 `page.html` 正则：
   - `#response` 内 `alert-success` → 成功
   - `#response` 内 `alert-danger` / `alert-warning` → 失败
   - 整页含 `renewed until` 作为兜底成功判定
8. 通过 **Telegram Bot** 推送续期结果、VPS 信息及截图

## 快速开始

### 环境要求

- Python 3.11+（CI 使用 3.13）
- Linux 系统（需 Xvfb 虚拟显示）
- Chromium 浏览器
- ffmpeg（reCAPTCHA 音频解码需要）

### 安装依赖

```bash
# 系统依赖
sudo apt-get update && sudo apt-get install -y xvfb chromium ffmpeg unzip curl wget jq ca-certificates

# Python 依赖（版本已固定）
pip install -r requirements.txt
```

### 配置

通过 `HUS_BATCH` 环境变量配置账号，多个账号用分号 `;` 或换行分隔。

> **推荐做法**：一个账号一个 workflow 文件（见下方「多账号部署」），并行跑、各自独立超时和出口 IP。同一个 workflow 里塞多个账号是串行执行，一个账号卡住会吃掉后面的时间预算。

#### HUS_BATCH 格式

```
phone,tg_bot_token,tg_chat_id,tg_api_id,tg_api_hash,tg_session
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `phone` | ✅ | 手机号，格式 `+国家码-号码`，如 `+506-60012545` |
| `tg_bot_token` | ✅ | 用于推送通知的 Telegram Bot Token |
| `tg_chat_id` | ✅ | 接收通知的 Telegram Chat ID |
| `tg_api_id` | ✅ | 该账号对应的 Telegram API ID（https://my.telegram.org） |
| `tg_api_hash` | ✅ | 该账号对应的 Telegram API Hash |
| `tg_session` | ✅ | 该账号的 Telegram StringSession（运行 `setup_tg_session.py` 生成） |

> TG 凭据（后三个字段）由每个账号独立提供，互不影响。

#### 示例

单账号：
```
+506-60012545,7126463574:AAH...,453472000,34604959,96c51d9e7a31...,1AZWar...
```

多账号（分号分隔）：
```
+506-60012545,7126463574:AAH...,453472000,34604959,96c51d9e7a31...,1AZWar...;+503-72796921,7200000001:AAF...,453472001,34604960,abc123def456...,2BVta...
```

#### 多账号：JSON 数组写法（推荐）

账号多了以后，分号那串逗号字段很难维护（数错一个逗号就串位）。`HUS_BATCH` 整段以 `[` 开头时按 **JSON 数组** 解析，每个账号一个对象，字段名和上面表格一一对应：

```json
[
  {
    "phone": "+506-60012545",
    "tg_bot_token": "7126463574:AAH...",
    "tg_chat_id": "453472000",
    "tg_api_id": "34604959",
    "tg_api_hash": "96c51d9e7a31...",
    "tg_session": "1AZWar..."
  },
  {
    "phone": "+503-72796921",
    "tg_bot_token": "7200000001:AAF...",
    "tg_chat_id": "453472001",
    "tg_api_id": "34604960",
    "tg_api_hash": "abc123def456...",
    "tg_session": "2BVta..."
  }
]
```

字段名兼容短写：`token` / `chat` / `api_id` / `api_hash` / `session` 等价于上面带 `tg_` 前缀的写法。

#### 多账号行为说明

| 行为 | 说明 |
|---|---|
| 执行方式 | **串行**：一个账号跑完（含其所有服务器）再跑下一个，共享同一个浏览器和 WARP 出口 |
| 失败隔离 | 单个账号抛异常只影响它自己，后续账号继续跑，并在结束时汇总 |
| 凭据隔离 | 每个账号用自己那套 TG 凭据；没填的字段回落到全局值，**不会**继承上一个账号的值 |
| 重复账号 | 同一手机号出现多次只保留第一条，并打印警告（CI 预检会直接报错） |
| 无机器账号 | 账号下没有 VPS 机器时**跳过续期**：打一行日志 + 发一条 TG「⏭ **账号下没有 VPS 机器**，跳过本次续期（不是失败）」，汇总表状态显示 `⏭️ 账号无机器`，退出码仍为 0 |
| 代理失效 | 代理挂了不影响运行：启动时配不起来 → 直接直连；跑着跑着挂了 → 自动切直连重试当前账号，后续账号也走直连 |
| 截图 | 按账号分目录：`screenshots/acc1/`、`screenshots/acc2/`… |
| 通知 | 单账号只发每台服务器的报告；多账号额外按账号各发一份 |
| 汇总 | 结束时打印表格：`# 账号 服务器 状态`（全部成功 / 部分成功 / 账号无机器 / 失败） |
| 退出码 | 只要**有任何一台服务器**续期成功就返回 0；全部失败返回 1（CI 变红）；所有账号都没机器时返回 0 |
| 耗时 | 单账号极端情况 10–20 分钟（IP 一直换不到），多账号按数量线性叠加，CI 超时设 120 分钟 |

#### 多账号部署（推荐：一个账号一个 workflow）

多个账号塞进同一个 workflow 是**串行**的，总时长线性叠加，还会共享同一个出口 IP。改成每个账号一个 workflow 文件后，各账号**并行**跑，互不影响。

公开库 `auto_scripts` 里的文件结构：

| 文件 | 状态 | cron (UTC) | concurrency group | 读的 secret | 时间文件 |
|---|---|---|---|---|---|
| `hus-renew.yml` | ✅ 启用 | `07 0 */2 * *` | `Hus_Renew_1` | `HUS_BATCH` | `hus/1/time.txt` |
| `hus-renew-2.yml.disabled` | ⏸ 停用 | `17 0 */2 * *` | `Hus_Renew_2` | `HUS_BATCH_2` | `hus/2/time.txt` |
| `hus-renew-3.yml.disabled` | ⏸ 停用 | `27 0 */2 * *` | `Hus_Renew_3` | `HUS_BATCH_3` | `hus/3/time.txt` |

> 目前只配置了 1 个账号，所以 ②③ 处于停用状态。停用方式是加 `.disabled` 后缀——GitHub 只运行 `.yml`/`.yaml` 文件，改名即停用，内容原封不动。

##### 启用（恢复）第 ②③ 个账号

按顺序做四步，缺一步都会在「校验 HUS_BATCH」步骤失败：

**1. 建 secret**（两个都要建，只建一个的话另一个仍然红）

```bash
# 网页：auto_scripts → Settings → Secrets and variables → Actions → New repository secret
# 或者用 gh CLI：
gh secret set HUS_BATCH_2 --repo jyucoeng/auto_scripts
gh secret set HUS_BATCH_3 --repo jyucoeng/auto_scripts
```

secret 内容 = **单个账号**的 6 个字段，不要带分号：

```
phone,tg_bot_token,tg_chat_id,tg_api_id,tg_api_hash,tg_session
```

**2. 去掉 `.disabled` 后缀**

```bash
cd auto_scripts
git mv .github/workflows/hus-renew-2.yml.disabled .github/workflows/hus-renew-2.yml
git mv .github/workflows/hus-renew-3.yml.disabled .github/workflows/hus-renew-3.yml
git commit -m "Enable account 2 and 3 workflows"
git push
```

**3. 确认文件名编号和 secret 编号对得上**

| 文件名 | 必须读 | 必须用 |
|---|---|---|
| `hus-renew-2.yml` | `secrets.HUS_BATCH_2` | `group: Hus_Renew_2` |
| `hus-renew-3.yml` | `secrets.HUS_BATCH_3` | `group: Hus_Renew_3` |

文件顶部注释里有「本文件 = 第 N 个账号 | group | secret | 时间文件」的速查行，对一眼就知道有没有串号。

**4. 手动验证一次**（别等到定时才发现问题）

Actions → 选对应 workflow → Run workflow。看第一个步骤的输出：

```
HUS_BATCH 预检通过：1 个账号（分号格式），凭据内容已隐藏
```

出现这行就说明 secret 配对了。如果看到 `HUS_BATCH_2 未设置或为空`，回第 1 步。

##### 停用某个账号

```bash
git mv .github/workflows/hus-renew-2.yml .github/workflows/hus-renew-2.yml.disabled
```

停用后该账号不再被续期，secret 留着不用删。

##### 新增第 4 个账号

复制任意一份 → 改文件名（`hus-renew-4.yml`）→ 改下面五处 → 再去建 `HUS_BATCH_4`。

**五处必须改、漏了会出事的地方**：

| 位置 | 不改的后果 |
|---|---|
| `name:` | Actions 列表里全是同名 workflow，出问题分不清谁是谁 |
| `concurrency.group` | GitHub 的 concurrency group 是**仓库级、跨 workflow 共享**的。同名 = 所有 workflow 串行排队，并行收益归零 |
| `HUS_BATCH_N`（两处） | 读到别人的 secret → 同一个账号被续两遍（还并发）；两处不一致 → 预检读不到值直接失败 |
| `cron` | 同一秒启动：git push 互相撞、同时向 WARP 注册容易被限流 |
| `TIME_DIR` | 多份同时写同一个 `time.txt`，并发提交互相覆盖 |

**不需要改的**：artifact 名（`hus-screenshots-${{ github.run_id }}`，`run_id` 每次运行唯一）；`PROJECT_DIR`、`PRIVATE_REPO`、代理与 token 相关 secret（各份共用）；步骤内容（脚本是私有库里的同一份，改逻辑只改一处）。

**建议**：`timeout-minutes` 保持 120（最坏路径 = 20 次 captcha 重试 × 20 轮 IP 轮换，被硬砍会留下续了一半的状态）；「清理旧的工作流运行记录」只保留在第 ① 份，其余删掉，避免多份互相清理对方的记录。

#### 其他环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `SCREENSHOT_DIR` | ❌ | `output/screenshots` | 截图保存目录（CI 中设为 `screenshots`） |
| `DEBUG_FLAG` | ❌ | `0` | 设为 `1` 输出调试日志 |
| `HUS_HY2_PROXY_URL` | ❌ | - | 代理 URL（Secret，CI 中映射为 `PROXY_CONTENT` 环境变量，由 `gen_singbox_config.py` 转 sing-box 配置） |
| `SOCKS_PORT` | ❌ | `10808` | 本地 SOCKS 代理端口（sing-box 监听） |
| `PROXY_RETRY_COUNT` | ❌ | `3` | 代理启动失败的重试次数 |
| `MAX_LOGIN_RETRIES` | ❌ | `1` | 登录重试次数 |
| `MAX_RENEW_RETRIES` | ❌ | `5` | 外层续期流程重跑次数（实际执行 = 该值 + 1） |
| `MAX_WARP_RETRIES` | ❌ | `20` | 中层：验证码被拒后「换 WARP IP → 重新提交」循环次数 |
| `MAX_WARP_ROTATE_ATTEMPTS` | ❌ | `20` | 内层：单次换 IP 内部 `warp-cli` 重注册轮数 |
| `WARP_SAME_IP_GIVEUP` | ❌ | `10` | 连续几次拿到同一个出口 IP 就认定出口被固定，停止轮换并改切通道 |
| `MAX_WARP_ROTATE_SECONDS` | ❌ | `600` | 单次换 IP 的总时间预算（需容纳 GIVEUP 次尝试），超了就用当前 IP 继续 |
| `MAX_CHANNEL_SWITCHES` | ❌ | `30` | 单次运行最多切换几次出口通道（代理 ↔ WARP），到顶就干净失败 |
| `WARP_CMD_TIMEOUT` | ❌ | `10` | 单条 `warp-cli` 命令超时秒数（超时强杀进程组防止卡死） |

### 获取 Telegram API ID 和 API Hash

1. 访问 https://my.telegram.org， 用你的tg手机号登录（会收到 Telegram 验证码）
2. 点击 **API development tools**
3. 填写表单（App title / Short name 随便填，如 `husauto`）
4. 提交后页面显示 **App api_id** 和 **App api_hash**

> ⚠️ api_id 和 api_hash 是固定的，创建一次后不要重复创建，否则旧的会失效。

## Telegram Session 生成

每个 TG 账号需要独立的 session string。通过 `setup_tg_session.py` 生成：

### 用法

单次交互式运行（同一进程内完成发码与登录，**不接收命令行参数**，验证码/密码通过运行时的交互提示输入）：

```bash
HUS_BATCH='phone,tg_bot_token,tg_chat_id,tg_api_id,tg_api_hash,' \
  python3 setup_tg_session.py
```

- 第 6 字段（`tg_session`）留空即可，脚本会自动生成并输出带 session 的完整行
- 运行后脚本会发送验证码，并依次提示：

```
请输入验证码:
请输入两步验证密码（若无直接回车）:
```

- 若 `HUS_BATCH` 第 6 字段已填入旧 session，脚本会先校验其是否有效；有效则直接输出原行，失效才重新登录

### 输出

脚本会输出完整的 HUS_BATCH 行（第 6 字段已填入生成的 session），直接复制替换即可：

```
==================================================
复制以下内容替换 HUS_BATCH 中对应的行（第6字段为 session）:
==================================================
+506-63532545,7126463574:AAH...,453472000,34604959,96c51d9e7a31...,1AZWar...
==================================================
```

## GitHub Actions 自动化

项目内置 GitHub Actions 工作流 `.github/workflows/hus-renew.yml`。

### 调度

```yaml
on:
  schedule:
    - cron: '06 1,13 * * *'   # 每天 UTC 01:06 与 13:06 各执行一次
  workflow_dispatch:          # 支持手动触发
```

还支持 `workflow_dispatch` 手动触发。

### 仓库结构

工作流会先检出**公开仓库**（本仓库），再用 `PRIVATE_REPO_TOKEN` 检出**私有仓库**（含 `hus-renew.py`、`gen_singbox_config.py`、`requirements.txt`）到子目录运行。

| 变量 / Secret | 必填 | 说明 |
|---|---|---|
| `HUS_BATCH` | 是 | 账号列表，格式见上 |
| `HUS_HY2_PROXY_URL` | 否 | 代理 URL（映射为 `PROXY_CONTENT` 环境变量） |
| `SOCKS_PORT` | 否 | SOCKS 端口（默认 10808） |
| `PRIVATE_REPO_TOKEN` | 是 | 用于检出私有仓库的 Personal Access Token |
| `PRIVATE_REPO`（工作流 env） | 否 | 私有仓库坐标，默认 `jyucoeng/auto_hus_private` |
| `PROJECT_DIR`（工作流 env） | 否 | 私有仓库检出子目录，默认 `hus` |

### 工作流行为

- 执行前随机延迟 0–120 秒，避免请求集中
- 启用 **WARP**（`fscarmen/warp-on-actions`）提供出口 IP，并在验证码被拒时轮换
- 配置 `HUS_HY2_PROXY_URL` 时自动下载并启动 **sing-box** 作为浏览器代理
- 截图与 `hus-renew.log` 作为 Artifact 上传，保留 7 天
- 运行结束后清理本地截图目录
- 每次执行后将时间戳写入 `<PROJECT_DIR>/time.txt` 并推送到仓库（带并发重试）
- 自动清理超过 24 小时且已完成的旧工作流运行记录，保留未完成的与最近 24 小时内的

## 代理配置

通过 Secret `HUS_HY2_PROXY_URL` 传入代理 URL，由 `gen_singbox_config.py` 自动转换为 sing-box 配置（脚本读取 `PROXY_CONTENT` 环境变量）。支持协议：

| 协议 | 传输层 | TLS 选项 |
|---|---|---|
| VLESS | TCP / WebSocket / gRPC / HTTPUpgrade / SPLITHTP | Reality / TLS / None |
| VMess | TCP / WebSocket / gRPC / HTTPUpgrade | TLS / None |
| Trojan | TCP / WebSocket / gRPC | TLS |
| Shadowsocks | - | - |
| Hysteria2 | - | TLS |
| TUIC | - | TLS |
| AnyTLS | - | TLS |
| SOCKS5 | - | - |

未配置代理时脚本以 WARP/直连模式运行。

## 项目结构

```
.
├── .github/workflows/
│   └── hus-renew.yml             # 自动续期工作流
├── hus-renew.py                  # 主程序：登录、续期、验证码、WARP 轮换、通知
├── setup_tg_session.py           # TG Session 生成脚本
├── gen_singbox_config.py         # 代理 URL 解析与 sing-box 配置生成
└── README.md
```

## 注意事项

- 脚本在 Linux 下通过 Xvfb 虚拟显示运行 Chromium（DrissionPage），无需图形界面
- 浏览器自动化使用 **DrissionPage**（Chromium 内核），Cloudflare 绕过依赖其 undetected 模式
- 手机号在日志和 TG 通知中会被部分遮蔽（如 `+506-63**2545`，国家代码后前 2 位可见）
- reCAPTCHA 解决依赖音频语音识别（speech_recognition + ffmpeg），部分 IP 可能被 Google 限制
- 算术验证码通过 ddddocr OCR 识别，成功率较高
- Telegram 登录授权通过 Telethon 后台线程自动点击 `Confirm` 完成
- 每个 TG 账号的 API 凭据独立，互不影响
- 续期结果检测基于结果页 DOM（`#response` 的 alert 样式 + `renewed until` 兜底），不依赖 JS 执行返回值
- 多账号时仅发送一条汇总报告；单账号发送各自独立报告
