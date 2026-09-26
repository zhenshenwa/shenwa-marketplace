# shenwa-marketplace · 深挖投研专家市场

一个可供 **订阅** 的 WorkBuddy 专家市场，包含两个金融投研专家：

| 专家 | 版本 | 定位 |
|---|---|---|
| **shenwa-agent**（深挖AI） | 1.0.5 | 单点能力强：基于深挖平台生成公司 / 行业 / 事件深度调查报告，覆盖市场复盘、卖方日报、乖离率、知识库检索 |
| **shenwa-hybrid-research**（深挖混合投研模式） | 1.0.2 | 流程能力强：深挖出分析主体、数据源做事实校验的 **50/50 分工**，输出附逐项校验表，客户偏好外置可配 |

> 两个专家**共用同一份**凭据文件，订阅两个也只需配置一次。

---

## 一、订阅（三步）

### 第 1 步 · 添加市场

按你使用的客户端选一种：

**A. 命令行 / 支持斜杠命令的会话**

```
/plugin marketplace add <本仓库地址>
```

地址写法：

```bash
# GitHub 简写（owner/repo）
/plugin marketplace add your-name/shenwa-marketplace

# 其他 Git 服务（HTTPS）
/plugin marketplace add https://gitlab.com/your-name/shenwa-marketplace.git

# SSH
/plugin marketplace add git@gitlab.com:your-name/shenwa-marketplace.git

# 锁定到某个分支或标签（推荐生产使用，先钉住再按需升级）
/plugin marketplace add https://gitlab.com/your-name/shenwa-marketplace.git#v1.0.0
```

**B. 纯桌面客户端**

在设置里声明市场（写入 `%USERPROFILE%\.workbuddy\settings.json`）：

```json
{
  "extraKnownMarketplaces": {
    "shenwa-marketplace": {
      "source": {
        "source": "github",
        "repo": "your-name/shenwa-marketplace"
      }
    }
  },
  "enabledPlugins": {
    "shenwa-agent@shenwa-marketplace": true,
    "shenwa-hybrid-research@shenwa-marketplace": true
  }
}
```

重启客户端后会自动拉取市场并安装上面 `enabledPlugins` 里列出的专家（首次可能需要你在界面上确认信任）。

> 只想要其中一个专家？把不需要的那行从 `enabledPlugins` 删掉即可。

### 第 2 步 · 配置深挖账号（必做，否则装了也用不了）

本市场**不含任何账号**。你需要有自己的深挖账号（手机号 + 邀请码），任选一种方式配置：

**方式 A · 凭据文件（推荐）** —— 把仓库根目录的 `shenwa_credentials.example.json` 复制到下面的位置并填入自己的账号：

```
%USERPROFILE%\.workbuddy\shenwa_credentials.json
```

```json
{
  "userID": "你的手机号",
  "inviteCode": "你的邀请码"
}
```

**方式 B · 环境变量**

```bash
export SHENWA_USER_ID=你的手机号
export SHENWA_INVITE_CODE=你的邀请码
```

优先级：环境变量 > 凭据文件。两者都没有时，脚本会给出明确报错和配置指引，**不会回退到任何内置账号**。

### 第 3 步 · 自检

订阅完成后，在会话里说一句：

> 查一下深挖账户信息

能正常返回账户信息即配置成功。然后可以试跑：

> 帮我深挖一下宁德时代最近的动作，生成一份公司研究报告

---

## 二、更新（改了版本之后）

第三方市场**默认关闭自动更新**，需要显式开启，否则你改版后订阅方拿不到新版本。

| 做法 | 说明 |
|---|---|
| 手动更新 | `/plugin marketplace update shenwa-marketplace`，然后 `/plugin install shenwa-agent@shenwa-marketplace` |
| 界面开启 | `/plugin` → Marketplaces → 选本市场 → Enable auto-update |
| 全局开启第三方自动更新 | 设环境变量 `CODEBUDDY_AUTO_UPDATE_THIRD_PARTY_MARKETPLACES=true` |

自动更新机制：启动后延迟执行，只更新距上次更新超过 24 小时的市场，失败不影响其他市场。

---

## 三、仓库结构

```
shenwa-marketplace/
├── .codebuddy-plugin/marketplace.json   ← 市场清单（订阅入口，必须有）
├── plugins/
│   ├── shenwa-agent/                    ← 专家包本体
│   └── shenwa-hybrid-research/          ← 专家包本体
├── scripts/sync.py                      ← 维护者用：从本机同步并自动升版本
├── shenwa_credentials.example.json      ← 凭据模板（无真实账号）
└── .gitignore                           ← 挡住凭据与本地元数据
```

## 四、维护者：改完专家后怎么发新版本

```bash
# 1) 从本机已注册的专家目录同步进仓库，并自动递增 patch 版本
python scripts/sync.py

# 2) 指定版本跨度
python scripts/sync.py --minor

# 3) 只同步不升版本
python scripts/sync.py --no-bump

# 4) 只检查有没有凭据泄漏，不写任何文件
python scripts/sync.py --check
```

脚本会做四件事：同步文件（自动排除 `.created-by-session` / `.shenwa_token` / `__pycache__`）→ 递增 `plugin.json` 版本 → 同步更新 `marketplace.json` 里的版本号 → **全仓扫描凭据**（有命中直接报错退出，防止误发）。

改完提交推送即可：

```bash
git add -A && git commit -m "update experts" && git push
```

订阅方执行一次 update 就能拿到新版本。

---

## 五、安全与合规

- **仓库内不含任何账号凭据**。所有账号由使用者各自配置，报告互相不可见。
- `.gitignore` 已挡住 `.shenwa_token`、`shenwa_credentials.json`、`.created-by-session`、`__pycache__`。
- ⚠️ **若仓库公开**：专家的提示词、分析框架与示例报告将对外可见，请自行确认可公开；同时 `owner` 邮箱会一并公开。
- ⚠️ **不要用「资料库 / 知识库」的上传按钮传专家包**：那条通道只收含 `index.html` 入口的静态网站包，会报 `failCode=11607`。订阅走本 README 的三种方式。

> 本市场产出的内容基于公开数据与量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
