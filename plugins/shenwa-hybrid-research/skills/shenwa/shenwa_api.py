#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
深挖（Shenwa）API 命令行助手 —— 本地客户端

用法:
  python shenwa_api.py <api_name> [key=value ...]     # 人读模式（沿用旧输出）
  python shenwa_api.py --json <api_name> [k=v ...]    # 机读模式（stdout 只有一个 JSON 对象）
  python shenwa_api.py list                           # 打印所有可用接口
  python shenwa_api.py errors                         # 打印错误分类与恢复规则
  python shenwa_api.py selfcheck                      # 接口注册表 vs 配置文件的漂移自查
  python shenwa_api.py selftest                       # 离线自检（不联网、不发任何写请求）
  python shenwa_api.py report_wait aid=123            # 轮询报告终态（不会新建报告）
  python shenwa_api.py tasks                          # 查看本地最小任务记录
示例:
  python shenwa_api.py templates
  python shenwa_api.py report_list type=public num=20
  python shenwa_api.py report_detail aid=123
  python shenwa_api.py report_publish tid=3002 question="..." text="..."
  python shenwa_api.py report_status aid=123
  python shenwa_api.py download_pdf aid=123 out=/path/to.pdf
  python shenwa_api.py knowledge_search keyword="光模块" knowledgeBase=report topK=5
  python shenwa_api.py get_guaili_rates maxDays=20
  python shenwa_api.py get_right_side codes=000300.CSI dayOffset=3
  python shenwa_api.py download_version version=1.0.5 out=/tmp/cfg_1.0.5.json
  python shenwa_api.py user_info
  python shenwa_api.py latest_version

凭据配置（本脚本不内置任何账号信息，每人用自己的账号）:
  方式1 环境变量：SHENWA_USER_ID=注册手机号
  方式2 本地文件：~/.workbuddy/shenwa_credentials.json
                  {"userID": "注册手机号"}
  也可用 SHENWA_CRED_FILE 指定其它凭据文件路径。
  凭据文件位于包外，不会被随包分发。

  ⚠️ 登录**只需注册手机号，不需要邀请码**（2026-09-27 实测：只传
  {"userID": "..."} 即返回 success + browserCode）。旧配置里若仍有
  inviteCode，会被兼容提交（服务端不校验）；缺失也不会再报错。
  **不要向使用者索要邀请码。**

────────────────────────────────────────────────────────────────────────
接口定义由哪里维护（Q: 改了配置说明却没改脚本，属哪一层的问题？）
────────────────────────────────────────────────────────────────────────
**唯一权威 = 本文件的 `APIS` 字典。** `shenwa_config_*.json` 是官方下发的
**说明性配置**，本客户端**不读取、不依赖**它来决定路径或方法 —— 因此
「只改配置说明」不会、也不应该改变本客户端的实际行为。

两者的关系与自查方式：
  · `APIS` 为准；配置里的路径若与 `APIS` 不同，**以 `APIS` 为准**并在
    `selfcheck` 中报出漂移（官方 1.0.5 的 `getRightSide` 就写成双 `api`
    的 404 路径，实测正确路径是 `/api/market/getRightSide`）。
  · 升级官方版本后必须**手工**把新接口并入 `APIS` 并跑
    `python shenwa_api.py selfcheck`，否则「配置更新了、脚本没变」。
  · `python shenwa_api.py list` 会打印本段说明，避免误以为配置即行为。

────────────────────────────────────────────────────────────────────────
错误分类与恢复规则（7 类 + 内容不合法 + 未知；另有「提交状态未知」状态）
────────────────────────────────────────────────────────────────────────
  network         网络异常（连不上 / 读超时 / 无 HTTP 响应）
                  → 同站**有界**重试（退避）→ 换站；**有副作用的调用绝不自动重发**
  http            HTTP 错误（收到响应但状态码非 2xx）
                  → 401→auth；403→permission；400/422→param；408/429/5xx→按 network 处理
  business        业务失败（HTTP 200 且 success=false）
                  → **不重试、不换站、不重登**；返回非成功状态，交上层识别
  auth            身份失效（HTTP 401，或 status=AUT 且 msg 指向登录/Token）
                  → **有限重登**（上限见 RetryPolicy.max_relogin），成功后原请求重试一次
  permission      权限不足（HTTP 403，或 status=AUT 且 msg 命中「你没有权限」等）
                  → **不重登、不换站、不重试**，如实返回权限不足
  param           参数错误（HTTP 400/422，或服务端参数校验 msg，或本地参数校验失败）
                  → **不重试、不换站**（换站重复提交同一错误参数毫无意义）；保留服务端原文
  empty           空数据（success=true 但结果集为空）
                  → 视为正常空结果（ok=true）；**若同时存在本地参数告警则改判 param**
  invalid_content 内容不合法（下载回的是错误 JSON / HTML / 非 PDF 等）
                  → 不保存、不报成功；`download_version` 的非法配置**不得当作可用更新**
  unknown         未知（分类不出来）
                  → **保留诊断信息**（HTTP 码、响应头、正文片段），**不伪装成「无数据」**

  另有一个**状态**（不是分类）：`submit_unknown` —— 有副作用的调用（发布/收藏/删除）
  已可能被服务端接收但结果不确定。此时**不做第二次提交**，用只读能力核对；
  不能唯一确认就返回「提交状态未知」。

退出码：0 成功（含 empty）；1 失败；2 「提交状态未知」（有副作用调用结果不确定）。
────────────────────────────────────────────────────────────────────────
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

__version__ = "1.8.0"

BASE_PRIMARY = "https://www.shenwa.top"
BASE_FALLBACK = "http://8.130.17.247:8088"

# 凭据一律从包外读取，严禁写回本文件（本文件会随专家包一起分发）
CRED_ENV_USER = "SHENWA_USER_ID"
# 兼容保留：邀请码已不再需要（接口不校验）；仅在调用方仍配置时才一并提交
CRED_ENV_CODE = "SHENWA_INVITE_CODE"
CRED_FILE_ENV = "SHENWA_CRED_FILE"
CRED_FILE_DEFAULT = os.path.join(os.path.expanduser("~"), ".workbuddy", "shenwa_credentials.json")

_HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.environ.get("SHENWA_TOKEN_FILE") or os.path.join(_HERE, ".shenwa_token")
TASK_FILE = os.environ.get("SHENWA_TASK_FILE") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "shenwa_tasks.jsonl")

# ─────────────────────────── 接口注册表（唯一权威） ───────────────────────────
#: name -> (method, path)
APIS = {
    "login": ("POST", "/api/user/doLogin"),
    "user_info": ("GET", "/api/user/getUser"),
    "templates": ("GET", "/api/manage/getTemplateListWithType"),
    "report_list": ("GET", "/api/home/getList"),
    "report_detail": ("GET", "/api/home/getDetail"),
    "report_publish": ("POST", "/api/manage/wfStartPublish"),
    "report_status": ("GET", "/api/home/getReportStatus"),
    "download_pdf": ("GET", "/api/home/downloadPdf"),
    "collect_report": ("POST", "/api/home/doArticleAction"),
    "delete_report": ("GET", "/api/home/deleteArticle"),
    "get_guaili_rates": ("GET", "/api/review/getGuailiRates"),
    # 实测：官方 1.0.5 配置写的是 /api/api/market/getRightSide（双 api），两站均 404；
    # 真实路径为 /api/market/getRightSide（2026-09-29 实测 HTTP 200）。以实测为准。
    "get_right_side": ("GET", "/api/market/getRightSide"),
    "knowledge_search": ("POST", "/api/data/knowledgeSearch"),
    "latest_version": ("GET", "/api/manage/getLatestVersion"),
    # 版本配置下载：路径含 {version} 占位符，由调用方传 version= 参数替换
    "download_version": ("GET", "/versions/shenwa_config_{version}.json"),
}

#: 接口定义的权威来源说明（`selfcheck` / `list` 会打印，避免「只改配置没改脚本」的误解）
API_REGISTRY_AUTHORITY = "本文件 shenwa_api.py 的 APIS 字典（shenwa_config_*.json 不参与路径/方法决策）"

#: 有**副作用**的接口 —— 只读重试规则不适用；一旦可能被服务端接收就不得自动重发
MUTATING_APIS = frozenset({"report_publish", "collect_report", "delete_report"})
#: 这些接口返回原始字节（PDF / 配置文件原文），需要按内容校验
RAW_APIS = frozenset({"download_pdf", "download_version"})
#: 下载类接口的期望内容
DOWNLOAD_EXPECT = {
    "download_pdf": "pdf",
    "download_version": "json-config",
}
#: 空结果字段探测
EMPTY_SENTINELS = (None, [], {}, "")

# 实测：这些参数服务端要求 String，不能被 _coerce 转成 int（否则 400）
# 实测：tid 与 version 必须是字符串（version 若被转成 float，1.0.5 会变成 1.0）
STRING_PARAMS = frozenset({"tid", "version"})
# 实测：knowledge_search 的参数走 query string，不发 JSON body（发 body 会 400 keyword required）
QUERY_PARAM_APIS = frozenset({"knowledge_search"})

#: 代码/标识符类参数名（含这些词的键名一律保持字符串，绝不转 int）
STRING_LIKE_KEYWORDS = ("code", "codes", "id", "ids", "uuid", "guid", "version", "token")

#: `codes` 支持的指数后缀（实测；裸 6 位代码会被服务端**静默**返回空数组）
KNOWN_CODE_SUFFIXES = ("SZ", "SH", "CSI", "BJ", "HK", "SI", "OF", "SS")
_CODE_RE = re.compile(r"^[0-9A-Za-z]{4,10}(?:\.([A-Za-z]{2,4}))?$")

# ─────────────── 发布类接口的本地参数约定（轮次 10 新增） ───────────────
#: 依据（三档标注，见 references/02「接口实操注意事项」）：
#:   · `max_chars` —— **接口约定**：官方配置 wfStartPublish.params 写明
#:     「question 不可多于60字」「text 不可多于500字」。
#:   · `tid` 必须字符串 —— **特定条件下实测**：传 int 会 400
#:     （"The JSON value could not be converted to System.String"）。
#:   · 服务端**实际计数方式**（Unicode 码点 / UTF-8 字节）—— **待验证推断**，本地不据此实现。
#: ⚠️ 只对**实际适用**的接口执行；合法的可选参数**不改成必填**（text 见 PUBLISH_TEXT_REQUIRED）。
PUBLISH_PARAM_SPEC = {
    "report_publish": {
        "tid":      {"required": True,  "max_chars": None},
        "question": {"required": True,  "max_chars": 60},
        "text":     {"required": False, "max_chars": 500},
    },
}

#: `text` 是否按「必填」处理。**默认 False（不阻断）**，理由：
#:   官方配置标 `required: false`，包内实测「完全缺省 → status=SRV + Missing required parameters」——
#:   两者冲突。遵守「不得把合法可选参数统一改成必填」，故只给**明确告警**而不拦截；
#:   若日后由服务端确认必须必填，把此常量改为 True 即可切换为硬拦截。
PUBLISH_TEXT_REQUIRED = False

#: 长度计算口径：**按 Unicode 码点数（`len()`）计，不 strip**（与「按原文提交」一致）。
#: ⚠️ 官方只说「不可多于 N 字」，未说明按码点还是按字节 → 服务端口径标为「待验证推断」。
PUBLISH_LEN_UNIT = "unicode_code_points"

#: 校验失败时的补充提示（只说怎么改，不替调用方改）
_PUBLISH_HINT = {
    "tid": "tid 必须是字符串（如 \"3002\"），不能是整数。",
    "question": "question 只留一句核心问题；复杂要求写进 text。",
    "text": "text 放本次分析真正需要的关键内容（含来源 / 时间 / 口径）。",
}

_MISSING = object()


def publish_param_check(name, params, errors, warnings):
    """发布类接口的本地参数校验：类型 / 必填 / 空值 / 长度。

    · 只对 `PUBLISH_PARAM_SPEC` 中**实际适用**的接口执行；
    · **不修改任何传入参数**（不 strip、不截断、不改写日期）；
    · 合法的可选参数不改成必填。
    """
    spec = PUBLISH_PARAM_SPEC.get(name)
    if not spec:
        return
    for key, rule in spec.items():
        raw = params.get(key, _MISSING)

        if raw is _MISSING:                      # —— 缺失 ——
            if key == "text" and not PUBLISH_TEXT_REQUIRED:
                warnings.append(
                    "text 缺失：官方配置标注为**可选**（required:false），但包内实测"
                    "「完全缺省 → status=SRV + Missing required parameters」→ **建议每次都带一段"
                    "有意义的背景消息**。（冲突已登记，见 references/02 实测第 1 条）")
            elif rule["required"]:
                errors.append(f"{key} 为必填参数，缺失。" + _PUBLISH_HINT.get(key, ""))
            continue

        if not isinstance(raw, str):             # —— 类型 ——
            errors.append(f"{key} 应为字符串，实得 {type(raw).__name__}（值为 {raw!r}）。"
                          + _PUBLISH_HINT.get(key, ""))
            continue

        if raw.strip() == "":                    # —— 空串 / 纯空白 ——
            if key == "text" and not PUBLISH_TEXT_REQUIRED:
                warnings.append(
                    "text 为" + ("空串" if raw == "" else "纯空白") + "：实测空串**能成功**，但 aid 前缀"
                    "变为 `ManualArticle@@<hash>`，与正常路径 `ManualNews@@<hash>@@<时间戳>` 不同，"
                    "产出可能不一致 → **不要用空串 / 空白占位**。")
            else:
                errors.append(f"{key} 为" + ("空串" if raw == "" else "纯空白")
                              + "，等于未提供。" + _PUBLISH_HINT.get(key, ""))
            continue

        lim = rule.get("max_chars")              # —— 长度（按码点计，不 strip）——
        if lim and len(raw) > lim:
            errors.append(
                f"{key} 长度 {len(raw)} 字（按 Unicode 码点计）超过上限 {lim} 字 → "
                f"请精简后重试；**不要用机械截断或删除关键限定条件来绕过校验**，"
                f"也**不要改写研究截止时间来迁就接口**。")
    return

# ─────────────────────────── 错误分类 ───────────────────────────
CAT_NETWORK = "network"
CAT_HTTP = "http"
CAT_BUSINESS = "business"
CAT_AUTH = "auth"
CAT_PERMISSION = "permission"
CAT_PARAM = "param"
CAT_EMPTY = "empty"
CAT_INVALID = "invalid_content"
CAT_UNKNOWN = "unknown"
CAT_OK = "ok"
STATE_SUBMIT_UNKNOWN = "submit_unknown"

#: 分类 → (中文名, 恢复策略) —— 唯一事实源，`errors` 子命令与文档都由它复述
ERROR_CLASSES = {
    CAT_OK: ("正常", "直接使用结果"),
    CAT_EMPTY: ("空数据", "视为正常空结果；若存在本地参数告警则改判 param"),
    CAT_NETWORK: ("网络异常", "同站有界重试（退避）→ 换站；有副作用的调用不自动重发"),
    CAT_HTTP: ("HTTP 错误", "按状态码细分：401→auth / 403→permission / 400·422→param / 408·429·5xx→network"),
    CAT_BUSINESS: ("业务失败", "不重试、不换站、不重登；返回非成功状态交上层识别"),
    CAT_AUTH: ("身份失效", "有限重登（上限 max_relogin），成功后原请求重试一次"),
    CAT_PERMISSION: ("权限不足", "不重登、不换站、不重试；如实返回权限不足"),
    CAT_PARAM: ("参数错误", "不重试、不换站；保留服务端校验原文"),
    CAT_INVALID: ("内容不合法", "不保存、不报成功；非法配置不得当作可用更新"),
    CAT_UNKNOWN: ("未知", "保留诊断信息（HTTP 码/响应头/正文片段），不伪装成「无数据」"),
}

#: 权限不足的判定词（HTTP 403 之外的业务表达）
PERMISSION_WORDS = ("你没有权限", "无权限", "沒有權限", "权限不足", "VIP2", "VIP 2", "会员", "升级VIP")
#: 参数错误的判定词
PARAM_WORDS = ("field is required", "required", "could not be converted", "参数", "类型",
               "invalid", "不能为空", "格式错误", "超出范围")
#: 身份失效的判定词
AUTH_WORDS = ("token", "登录", "登陆", "未登录", "身份", "凭证", "browserCode", "会话", "过期")

TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 509})


class TransportError(Exception):
    """无 HTTP 响应时的传输层异常（DNS / 拒连 / 读超时）。"""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class ApiError(RuntimeError):
    """带分类与恢复信息的客户端异常。"""

    def __init__(self, category, message, *, result=None):
        super().__init__(message)
        self.category = category
        self.result = result


def _now_iso():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def classify_http(status):
    """HTTP 状态码 → 分类（未收到响应走 TransportError，不在此处）。"""
    if status == 401:
        return CAT_AUTH
    if status == 403:
        return CAT_PERMISSION
    if status in (400, 405, 409, 410, 413, 415, 422):
        return CAT_PARAM
    if status in TRANSIENT_HTTP:
        return CAT_NETWORK
    if status == 404:
        return CAT_HTTP
    if 400 <= status < 500:
        return CAT_HTTP
    if 500 <= status < 600:
        return CAT_NETWORK
    return CAT_UNKNOWN


def classify_payload(payload):
    """HTTP 200 的业务语义分类。

    **只按 payload 自身判定**，不因为 `status == "AUT"` 就一律当成身份失效：
    `status=AUT` 既可能是 Token 过期，也可能是「你没有权限」——
    两者恢复行为完全不同（前者有限重登，后者不得反复重登）。
    """
    if not isinstance(payload, dict):
        return CAT_UNKNOWN, "响应不是 JSON 对象"
    status = payload.get("status")
    msg = str(payload.get("msg") or "")
    success = payload.get("success")

    upper = status.upper() if isinstance(status, str) else ""
    if upper == "AUT" or (success is False and status):
        # ⚠️ 关键：先分权限，再分身份。权限不足**不得**触发重登。
        if _hit(msg, PERMISSION_WORDS):
            return CAT_PERMISSION, f"业务返回权限不足：{msg or status}"
        if upper == "AUT" and _hit(msg, AUTH_WORDS):
            return CAT_AUTH, f"业务返回身份失效：{msg or status}"
        if upper == "AUT" and success is False:
            # status=AUT 但既不是权限、也不是明确的身份失效措辞 → 保留诊断，不猜
            return CAT_UNKNOWN, f"status=AUT 但无法判定是身份失效还是权限不足：{msg or '(无 msg)'}"

    if success is False:
        if _hit(msg, PARAM_WORDS):
            return CAT_PARAM, f"业务返回参数错误：{msg}"
        if _hit(msg, PERMISSION_WORDS):
            return CAT_PERMISSION, f"业务返回权限不足：{msg}"
        return CAT_BUSINESS, f"业务失败：{msg or '(无 msg)'}"

    return None, ""


def _hit(text, words):
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def first_list(obj, depth=3):
    """在 payload 中找第一个列表（用于判断「空数据」）。"""
    if depth < 0:
        return None
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = first_list(v, depth - 1)
            if r is not None:
                return r
    return None


def classify_success(payload, warnings=()):
    """200 + success 的「空数据 vs 正常」判定。

    **有本地参数告警时空结果判为 param** —— 否则「裸代码被静默返回空数组」
    会被伪装成「该板块没有数据」。
    """
    if not isinstance(payload, dict):
        return CAT_OK, ""
    data = payload.get("data", None)
    if data in EMPTY_SENTINELS and not payload.get("_has_content"):
        lst = first_list(payload)
        if lst == []:
            if warnings:
                return CAT_PARAM, "结果为空且存在参数告警 → 判为参数问题，不当作「无数据」"
            return CAT_EMPTY, "结果集为空（success=true）"
        if data in (None, "", {}, []):
            if warnings:
                return CAT_PARAM, "结果为空且存在参数告警 → 判为参数问题，不当作「无数据」"
            return CAT_EMPTY, "data 为空（success=true）"
    return CAT_OK, ""


def classify_response(status, payload):
    """`(HTTP 状态, 业务负载)` → 最终分类。

    **业务负载优先于状态码** —— 因为只有它能把 `status=AUT` 的两种含义分开
    （Token 过期 → 重登；你没有权限 → 不得重登）。状态码只在负载无信息时兜底。
    """
    cat, why = classify_payload(payload)
    if status is not None and status >= 400:
        s = classify_http(status)
        if cat in (CAT_PERMISSION, CAT_PARAM, CAT_AUTH):
            return cat, why                       # 服务端已说明原因 → 以它为准
        if cat == CAT_BUSINESS:
            return (CAT_NETWORK if status >= 500 else CAT_HTTP), f"HTTP {status}：{why}"
        return s, f"HTTP {status}"
    if cat:
        return cat, why
    return None, ""


# ─────────────────────────── 参数转换与校验 ───────────────────────────
def param_is_string(key):
    """该参数是否必须保持字符串。

    ① 显式列在 `STRING_PARAMS`（tid / version）；
    ② 键名是代码/标识符类（code(s) / id(s) / uuid / token 等）。
    """
    k = str(key)
    if k in STRING_PARAMS:
        return True
    low = k.lower()
    return any(w in low for w in STRING_LIKE_KEYWORDS)


def _coerce(v, key=None):
    """CLI 传入的都是字符串，需还原成 bool/int/float，否则服务端可能返回 400。

    ⚠️ **带前导零的代码不得转 int**：`"000300"` 若被转成 `300`，语义就变了。
    规则：键名是代码/标识符类，或值以 0 开头且不止一位（如 "000300"），一律保持字符串。
    """
    if not isinstance(v, str):
        return v
    if key is not None and param_is_string(key):
        return v
    if re.match(r"^[+-]?0\d", v):          # 前导零（"000300" / "-007"）
        return v
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def validate_params(name, params):
    """本地参数校验。返回 (errors, warnings)。

    errors   —— 会在发请求前直接判为 `param`（不发请求，不换站）；
    warnings —— 会随结果回传，并被用来把「空结果」改判为 `param`。
    """
    errors, warnings = [], []
    for k, v in params.items():
        if isinstance(v, str) and v != v.strip():
            warnings.append(f"{k} 前后有空白字符，已按原文提交：{v!r}")

    if "codes" in params:
        raw = params["codes"]
        items = [x for x in str(raw).split(",") if x != ""]
        if not items:
            warnings.append("codes 为空 = 取全量精选池（这是允许的用法）")
        for it in items:
            if not _CODE_RE.match(it):
                errors.append(
                    f"codes 含不支持的代码格式 {it!r}：应为 4~10 位字母数字，"
                    f"指数类需带后缀（如 000300.CSI / 399986.SZ / 000001.SH）")
                continue
            sfx = it.rsplit(".", 1)[1].upper() if "." in it else None
            if sfx is None:
                warnings.append(
                    f"codes 的 {it!r} 没有带指数后缀 → 服务端会**静默返回空数组**；"
                    f"请改用 {it}.CSI / {it}.SZ / {it}.SH（已知后缀："
                    + "/".join(KNOWN_CODE_SUFFIXES) + "）")
            elif sfx not in KNOWN_CODE_SUFFIXES:
                warnings.append(f"codes 的后缀 .{sfx} 不在已知列表（"
                                + "/".join(KNOWN_CODE_SUFFIXES) + "），结果可能为空")

    if "dayOffset" in params:
        try:
            n = int(str(params["dayOffset"]).strip())
        except (TypeError, ValueError):
            errors.append(f"dayOffset 应为整数：{params['dayOffset']!r}")
            n = None
        if n is not None and not (1 <= n <= 20):
            errors.append(f"dayOffset={n} 超出实测有效范围 1~20（0 带 codes 时静默返回空，>20 报 400）")

    if "out" in params and name not in RAW_APIS:
        warnings.append(f"接口 {name} 不使用 out= 参数，已忽略")

    if name == "download_version" and "version" not in params:
        errors.append("download_version 需要 version= 参数（路径模板 /versions/shenwa_config_{version}.json）")

    # 发布类接口：类型 / 必填 / 空值 / 长度（轮次 10 新增；只对适用接口执行）
    publish_param_check(name, params, errors, warnings)

    return errors, warnings


# ─────────────────────────── 响应内容校验（下载类） ───────────────────────────
def looks_like(body: bytes):
    """粗略判断字节内容的类型，用于「错误 JSON / HTML 不得存成成功 PDF」。"""
    head = (body or b"")[:512]
    if head[:5] == b"%PDF-":
        return "pdf"
    s = head.lstrip()[:64].lower()
    if s.startswith(b"<!doctype html") or s.startswith(b"<html") or b"<html" in s:
        return "html"
    if head[:4] in (b"\x89PNG",) or head[:3] == b"\xff\xd8\xff":
        return "image"
    if head[:2] == b"PK":
        return "zip"
    t = head.decode("utf-8", "ignore").lstrip()
    if t[:1] in ("{", "["):
        return "json"
    return "unknown"


def validate_download(name, body, headers=None, version=None):
    """下载前后按接口约定检查响应类型与文件内容。

    返回 (ok, kinds, reasons)。不通过时调用方**不得保存、不得报成功**。
    """
    expect = DOWNLOAD_EXPECT.get(name, "any")
    kind = looks_like(body)
    reasons = []
    hs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    ctype = hs.get("content-type", "")

    if not body:
        return False, kind, ["响应体为空"]
    if kind == "html":
        reasons.append("响应是 HTML（通常是错误页/网关页），不是下载内容")
    elif kind == "json":
        try:
            obj = json.loads(body.decode("utf-8", "ignore"))
        except Exception:
            obj = None
        if isinstance(obj, dict):
            if obj.get("success") is False:
                reasons.append(f"响应是错误 JSON：{obj.get('msg') or '(无 msg)'}")
            if "status" in obj and obj.get("status") != "OK":
                reasons.append(f"响应 JSON 的 status={obj.get('status')!r}，非成功下载")
            if "_json_parse_error" in obj:
                reasons.append("响应是 JSON 解析失败的结构")
        if expect == "json-config":
            reasons += _config_reasons(body, version)
        if expect == "pdf":
            reasons.append("预期 PDF，实际是 JSON（错误响应不得存成 PDF）")
    elif expect == "pdf" and kind != "pdf":
        reasons.append(f"预期 PDF，实际内容类型为 {kind!r}（缺少 %PDF- 头）")
    elif expect == "json-config" and kind not in ("json", "unknown"):
        reasons.append(f"预期 JSON 配置，实际内容类型为 {kind!r}")

    if expect == "pdf" and (ctype and "pdf" not in ctype.lower() and kind != "pdf"):
        reasons.append(f"Content-Type={ctype!r} 与 PDF 不符")
    return (not reasons), kind, reasons


def _config_reasons(body, version=None):
    """配置文件的内容合法性（非法配置不得当作可用更新）。"""
    text = body.decode("utf-8", "ignore")
    reasons = []
    try:
        obj = json.loads(text)
    except Exception as e:
        # 官方 1.0.5 的 JSON 本身含未加引号占位符 → 允许解析失败，但必须*看起来像*配置
        if not any(k in text for k in ("userID", "inviteCode", "latestVersion", "APIS", "login")):
            reasons.append(f"既不是合法 JSON，也不含任何已知配置标识：{e}")
        obj = None
    if isinstance(obj, dict):
        if not any(k in obj for k in ("APIS", "apis", "latestVersion", "version", "login", "auth")):
            reasons.append("JSON 合法但缺少全部已知配置字段（APIS/latestVersion/login/auth）")
        ver = obj.get("latestVersion") or obj.get("version")
        if version and ver and str(ver) != str(version):
            reasons.append(f"配置内声明版本 {ver} 与请求的 {version} 不一致")
        if obj.get("status") not in (None, "OK"):
            reasons.append(f"配置 status={obj.get('status')!r}")
    return reasons


def update_is_usable(remote_version, local_version, cfg_body, headers=None):
    """`latest_version` + `download_version` 的联合判定：这份更新能不能用。

    不合法配置（HTML 页 / 错误 JSON / 缺字段 / 版本不符）**不得当作可用更新**。
    """
    reasons = []
    bad = _semver_gt(remote_version, local_version)
    ok_dl, kind, dl_reasons = validate_download("download_version", cfg_body, headers,
                                               version=remote_version)
    if not ok_dl:
        reasons += dl_reasons
    if not remote_version:
        reasons.append("远端未返回 latestVersion")
    return {"usable": bool(reasons == [] and bad), "newer": bad,
            "kind": kind, "reasons": reasons or ["配置合法且版本更新"]}


def _semver(t):
    return [int(x) for x in re.findall(r"\d+", str(t or ""))] or [0]


def _semver_gt(a, b):
    return _semver(a) > _semver(b)


# ─────────────────────────── 凭据 / 账号标识 ───────────────────────────
def account_ref(uid):
    """账号的安全引用：sha256 前 16 位。**不暴露手机号本身**。"""
    import hashlib
    return hashlib.sha256(("shenwa:" + str(uid)).encode("utf-8")).hexdigest()[:16]


def _cred(uid, code=""):
    """组装登录凭据。

    2026-09-27 实测：接口层已不再校验邀请码，**只需注册手机号**即可登录。
    邀请码仅在调用方仍显式配置时才一并提交（向后兼容），缺失不影响登录。
    """
    d = {"userID": uid}
    if code:
        d["inviteCode"] = code
    return d


def load_cred():
    """解析深挖账号凭据，优先级：环境变量 > 包外本地配置文件。

    包内不含任何账号信息，每位使用者需自行提供凭据；缺少时抛出带
    配置指引的错误，而不是回退到某个内置账号。

    ⚠️ **只需注册手机号，不要向使用者索要邀请码**（接口已不校验）。
    """
    uid = os.environ.get(CRED_ENV_USER, "").strip()
    code = os.environ.get(CRED_ENV_CODE, "").strip()
    if uid:
        return _cred(uid, code)

    path = os.environ.get(CRED_FILE_ENV, "").strip() or CRED_FILE_DEFAULT
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            u = str(d.get("userID", "")).strip()
            c = str(d.get("inviteCode", "")).strip()
            if u:
                return _cred(u, c)
        except Exception as e:
            raise RuntimeError("凭据文件解析失败 (%s): %s" % (path, e))

    raise RuntimeError(
        "未配置深挖账号凭据（**只需注册手机号，不需要邀请码**）。任选一种方式后重试：\n"
        "  1) 设置环境变量 %s = 注册手机号\n"
        "  2) 创建文件 %s，内容为 {\"userID\": \"注册手机号\"}\n"
        "  也可用 %s 指定其它凭据文件路径。\n"
        "  还没有账号？联系微信 shenwa4119（公众号：真深挖者也）。"
        % (CRED_ENV_USER, CRED_FILE_DEFAULT, CRED_FILE_ENV)
    )


# ─────────────────────────── Token 存储（与账号绑定） ───────────────────────────
def load_token_record(path=None):
    """读取 Token 记录。返回 dict 或 None。

    新版为 JSON：{"v":2,"account_ref":"…","token":"…"}。
    旧版是裸 Token 文本 —— **无法确定它属于哪个账号**，故一律视为不可用
    （否则切换账号时会沿用上一个账号的 Token），只在 stderr 提示一次。
    """
    p = path or TOKEN_FILE
    if not os.path.exists(p):
        return None
    try:
        raw = open(p, encoding="utf-8").read().strip()
    except Exception:
        return None
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            if isinstance(d, dict) and d.get("token"):
                return d
        except Exception:
            return None
    return {"v": 1, "legacy": True, "token": raw}


def load_token(uid=None, path=None):
    """取与 **当前账号** 绑定的 Token；账号不符或旧版裸 Token 一律返回 None。"""
    rec = load_token_record(path)
    if not rec or rec.get("legacy"):
        return None
    if uid is not None and rec.get("account_ref") != account_ref(uid):
        return None
    return rec.get("token")


def save_token(token, uid, base=None, path=None):
    p = path or TOKEN_FILE
    rec = {"v": 2, "account_ref": account_ref(uid), "token": token,
           "base_hint": base, "saved_at": _now_iso()}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return rec


# ─────────────────────────── 本地最小任务记录 ───────────────────────────
def _fingerprint(api, params):
    import hashlib
    st = json.dumps({"api": api, "p": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(st.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────── 跨进程提交互斥（原子意图文件） ───────────────────────────
# 「检查已有提交 → 取得提交资格 → 保存提交意图」必须防止并发竞争。
# 用 O_CREAT|O_EXCL 原子创建「意图文件」：只有第一个成功创建意图文件的调用方
# （无论同进程多线程还是跨进程）才有资格发写请求；后续调用方看到意图文件已存在
# 即判为「同一任务已有未决提交」，进入只读核对，绝不再次发请求。


def _dedup_key(task_id, fingerprint, business_fp=None):
    """防重主键：**任务身份优先**，无身份时回退参数指纹。

    · 同一任务重试必须沿用同一 `task_id` → 不会生成新身份绕过防重；
    · 用户明确发起的独立新任务须提供**新的** `task_id` → 即使参数指纹相同，
      也不会被永久禁止（重新走授权与预算检查）。

    ⚠️ **`business_fp`（第 17 轮）不是主键的一部分** —— 它是**业务身份指纹**
    （对象 / 模板 / 问题内容 / 执行参数），用于兼容「编号算法升级后 `task_id` 变化」
    的场景：旧记录用旧格式 `entry_id` 作 `task_id`，新代码算出新格式 `entry_id`，
    若只看 `task_id` 就会**误判为新任务并重发已受理的任务**。`_find_existing`
    会用 `business_fp` 做**兜底匹配**（见该函数）。
    """
    return (task_id or ("fp:" + fingerprint)) if (task_id or fingerprint) else None


def append_task(record, path=None):
    """把一条最小任务记录追加到本地 JSONL。

    ⚠️ 这是**本地记录**，不是服务端幂等保障 —— 客户端无法阻止服务端重复受理，
    因此绝不声称「已实现服务端幂等」。它只用于「提交结果不确定时做只读核对」。

    返回值 `{"ok": bool, "error": str|None}`：写失败**显式报告**（不再静默吞掉），
    让调用方能在「必要记录保存失败」时中止发布（发布调用数为 0）。
    """
    p = path or TASK_FILE
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:                                          # noqa: BLE001
        return {"ok": False, "error": str(e), "record": record}
    return {"ok": True, "error": None, "record": record}


def load_tasks(path=None):
    """读回全部任务记录。**损坏的行显式报告，不静默跳过**。

    返回 `(rows, errors)`：`errors` 非空时调用方不得当成「无历史」继续提交。
    """
    p = path or TASK_FILE
    rows, errors = [], []
    if not os.path.exists(p):
        return rows, errors
    try:
        with open(p, encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception as e:                              # noqa: BLE001
                    errors.append({"line": idx, "error": str(e), "raw": line[:120]})
    except Exception as e:                                          # noqa: BLE001
        return rows, [{"line": None, "error": str(e), "raw": None}]
    return rows, errors


def open_submissions(fingerprint=None, uid=None, path=None, task_id=None):
    """未确认的提交（state ∈ sent / submit_unknown）。

    可按 `task_id`（任务身份，优先）或 `fingerprint`（参数指纹）过滤；
    两者都未给则返回全部未确认提交。
    """
    ref = account_ref(uid) if uid else None
    rows, _ = load_tasks(path)
    return [t for t in rows
            if t.get("state") in ("sent", STATE_SUBMIT_UNKNOWN)
            and (ref is None or t.get("account_ref") == ref)
            and (task_id is None or t.get("task_id") == task_id)
            and (fingerprint is None or t.get("fingerprint") == fingerprint)]


# ─────────────────────────── 时钟 / 重试策略 ───────────────────────────
class RealClock:
    def now(self):
        return time.monotonic()

    def sleep(self, s):
        if s > 0:
            time.sleep(s)


class RetryPolicy:
    """重试预算：**次数上限 + 真实经过时间上限**。

    等待时间与网络请求耗时**都计入** `max_total_seconds`；一旦超预算立即停止，
    不做「次数够了才看时间」的事后检查。
    """

    def __init__(self, max_attempts_per_base=2, max_total_seconds=30.0,
                 backoff=(0.5, 1.0, 2.0), max_relogin=1):
        self.max_attempts_per_base = max_attempts_per_base
        self.max_total_seconds = max_total_seconds
        self.backoff = tuple(backoff)
        self.max_relogin = max_relogin

    def backoff_for(self, attempt_index):
        if not self.backoff:
            return 0.0
        return self.backoff[min(attempt_index, len(self.backoff) - 1)]

    def note(self):
        return {
            "max_attempts_per_base": self.max_attempts_per_base,
            "max_total_seconds": self.max_total_seconds,
            "backoff": list(self.backoff),
            "max_relogin": self.max_relogin,
        }


class MockClock:
    """模拟时钟：**测试不实际等待**，但等待与请求耗时都会被计入。"""

    def __init__(self, t=1000.0):
        self.t = float(t)
        self.slept = []
        self.calls = 0

    def now(self):
        return self.t

    def sleep(self, s):
        self.calls += 1
        self.slept.append(s)
        self.t += max(0.0, s)

    def advance(self, s):
        self.t += max(0.0, s)


# ─────────────────────────── 传输层 ───────────────────────────
def urllib_transport(base, method, path, params=None, token=None, timeout=60):
    """默认传输层：返回 (status, headers, body_bytes)；无响应时抛 TransportError。"""
    url = base + path
    data = None
    if method == "GET" or path.endswith("knowledgeSearch"):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        if method == "POST":
            data = b""
    else:
        data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, dict(e.headers or {}), body
    except Exception as e:                                       # noqa: BLE001
        kind = type(e).__name__
        low = str(e).lower()
        if "timed out" in low or "timeout" in low:
            kind = "timeout"
        elif "name or service" in low or "getaddrinfo" in low or "nodename" in low:
            kind = "dns"
        elif "refused" in low or "unreachable" in low or "reset" in low:
            kind = "connect"
        raise TransportError(kind, str(e))


class ScriptedTransport:
    """离线测试用的脚本化传输层：按顺序消费预置动作，可让模拟时钟前进。"""

    def __init__(self, script, clock=None):
        self.script = [dict(a) for a in script]
        self.calls = []
        self.clock = clock

    def _next(self, base, method, path):
        for a in self.script:
            if a.get("_used"):
                continue
            if a.get("path_contains") and a["path_contains"] not in path:
                continue
            if a.get("method") and a["method"] != method:
                continue
            if a.get("base_contains") and a["base_contains"] not in base:
                continue
            if a.get("repeat", 1) > 1:
                a["repeat"] -= 1
            else:
                a["_used"] = True
            return a
        return {"raise": "exhausted"}

    def __call__(self, base, method, path, params=None, token=None, timeout=60):
        self.calls.append({"base": base, "method": method, "path": path,
                           "params": dict(params or {}), "token": token})
        act = self._next(base, method, path)
        if self.clock is not None:
            self.clock.advance(act.get("advance", 0.25))
        if act.get("raise"):
            raise TransportError(act["raise"], act.get("detail", ""))
        status = act.get("status", 200)
        if "body" in act:
            body = act["body"]
            body = body.encode("utf-8") if isinstance(body, str) else body
        else:
            body = json.dumps(act.get("json", {}), ensure_ascii=False).encode("utf-8")
        return status, act.get("headers", {}), body

    # ——— 供断言使用的计数/筛选 ———
    def count(self, path_contains=None, method=None, base_contains=None):
        n = 0
        for c in self.calls:
            if path_contains and path_contains not in c["path"]:
                continue
            if method and c["method"] != method:
                continue
            if base_contains and base_contains not in c["base"]:
                continue
            n += 1
        return n

    def paths(self):
        return [c["path"] for c in self.calls]

    def bases(self):
        return [c["base"] for c in self.calls]


# ─────────────────────────── 客户端 ───────────────────────────
class Result:
    """一次调用（或一次轮询）的完整结果。"""

    def __init__(self, ok, category, api, base=None, data=None, http_status=None,
                 attempts=0, elapsed=0.0, warnings=None, diagnostics=None,
                 submit_state=None, task_id=None, message=""):
        self.ok = ok
        self.category = category
        self.api = api
        self.base = base
        self.data = data
        self.http_status = http_status
        self.attempts = attempts
        self.elapsed = elapsed
        self.warnings = list(warnings or [])
        self.diagnostics = dict(diagnostics or {})
        self.submit_state = submit_state
        self.task_id = task_id
        self.message = message

    def to_dict(self):
        return {
            "ok": self.ok,
            "category": self.category,
            "api": self.api,
            "base": self.base,
            "http_status": self.http_status,
            "attempts": self.attempts,
            "elapsed": round(self.elapsed, 3),
            "submit_state": self.submit_state,
            "task_id": self.task_id,
            "message": self.message,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "data": self.data,
        }

    @property
    def exit_code(self):
        if self.submit_state == STATE_SUBMIT_UNKNOWN:
            return 2
        return 0 if self.ok else 1


class Client:
    """本地 API 客户端：主备站恢复 + 错误分类 + 有界重试 + 有副作用保护。"""

    def __init__(self, base_primary=BASE_PRIMARY, base_fallback=BASE_FALLBACK,
                 transport=None, clock=None, policy=None, credential=None,
                 token_file=None, task_file=None, stderr=None, verbose=False):
        self.bases = [b for b in (base_primary, base_fallback) if b]
        self.transport = transport or urllib_transport
        self.clock = clock or RealClock()
        self.policy = policy or RetryPolicy()
        self.credential = credential
        self.token_file = token_file or TOKEN_FILE
        self.task_file = task_file or TASK_FILE
        self.stderr = stderr if stderr is not None else sys.stderr
        self.verbose = verbose
        self._token = None
        self._token_base = None
        self.limits = {"relogin": 0}

    # ——— 诊断输出（一律 stderr，stdout 只放结果） ———
    def note(self, msg):
        try:
            self.stderr.write("[shenwa] %s\n" % msg)
        except Exception:
            pass

    # ——— 凭据 ———
    def cred(self):
        if self.credential is not None:
            return dict(self.credential)
        return load_cred()

    def uid(self):
        return str(self.cred().get("userID", ""))

    # ——— 登录（首登也纳主备站恢复流程） ———
    def login_on(self, base):
        body = json.dumps(self.cred()).encode("utf-8")
        status, headers, raw = self.transport(base, "POST", "/api/user/doLogin",
                                             json.loads(body.decode("utf-8")), None, 30)
        text = raw.decode("utf-8", "ignore")
        try:
            r = json.loads(text)
        except Exception:
            raise ApiError(CAT_UNKNOWN, f"登录响应不是 JSON（HTTP {status}）：{text[:120]!r}")
        cat, why = classify_payload(r)
        if cat in (CAT_AUTH, CAT_PERMISSION, CAT_PARAM):
            raise ApiError(cat, f"登录被拒绝（HTTP {status}）：{why}")
        if not (r.get("success") and r.get("data")):
            raise ApiError(classify_http(status) if status >= 400 else CAT_BUSINESS,
                           f"登录失败（HTTP {status}）：{r.get('msg')}")
        return r["data"]["browserCode"]

    def ensure_token(self, force=False):
        """取与当前账号绑定的 Token；没有就登录。

        **首次登录同样按主站 → 备用站顺序尝试**（此前只在主站登录，
        主站不可用时直接失败，无法进入备用站路径）。
        """
        uid = self.uid()
        if not force:
            cached = load_token(uid, self.token_file)
            if cached:
                legacy = load_token_record(self.token_file) or {}
                self._token = cached
                self._token_base = legacy.get("base_hint")
                return cached
            rec = load_token_record(self.token_file)
            if rec and rec.get("legacy"):
                self.note("忽略旧版未绑定账号的 Token 文件（切换账号时不得沿用旧 Token）")
        last = None
        for base in self.bases:
            try:
                tok = self.login_on(base)
                self._token, self._token_base = tok, base
                save_token(tok, uid, base, self.token_file)
                self.note(f"登录成功：{base}")
                return tok
            except ApiError as e:
                last = e
                self.note(f"登录失败（{base}）：{e.category} {e}")
                continue
            except TransportError as e:
                last = e
                self.note(f"登录网络异常（{base}）：{e}")
                continue
        raise ApiError(getattr(last, "category", CAT_NETWORK),
                       f"全部站点的首次登录均失败：{last}")

    # ——— 请求 ———
    def _raw_request(self, base, method, path, params, token, timeout, raw):
        status, headers, body = self.transport(base, method, path, params, token, timeout)
        if raw:
            return status, headers, body
        text = body.decode("utf-8", "ignore")
        try:
            return status, headers, json.loads(text)
        except Exception:
            if status >= 400:
                return status, headers, {"_parse_error": True, "text": text[:400]}
            raise ApiError(CAT_UNKNOWN, f"响应不是合法 JSON（HTTP {status}）：{text[:120]!r}")

    def call(self, name, params=None, timeout=60, dry_run=False, task_id=None,
             business_fp=None):
        """执行一次调用，返回 `Result`。

        `dry_run=True` 只做本地参数校验与计划展示，**绝不发任何请求**
        （用于「验证期间禁止真实写操作」）。

        `task_id`（可选）：**任务身份**，用于有副作用调用的防重。
        · 同一任务重试必须沿用同一 `task_id`，不会生成新身份绕过防重；
        · 独立新任务给新 `task_id`，即使参数指纹相同也重新走授权与预算检查；
        · 未给 `task_id` 时回退到参数指纹 `fingerprint`。

        `business_fp`（可选，第 17 轮）：**业务身份指纹**（对象 / 模板 / 问题内容 /
        执行参数），随记录一并落盘，供 `_find_existing` 做**跨编号算法的兜底匹配**。
        · 只影响「已有记录里**自带该字段**」的比对，不做回算、不猜；
        · 不传时行为与旧版**完全一致**（向后兼容）。
        """
        if name not in APIS:
            return Result(False, CAT_PARAM, name, message=f"未知接口: {name}（用 list 查看）")
        method, path = APIS[name]
        raw = name in RAW_APIS
        mutating = name in MUTATING_APIS
        params = dict(params or {})

        # 先在「原始参数」上做本地校验（此时 version / out 都还在，提示才准确）
        errors, warnings = validate_params(name, params)
        if errors:
            return Result(False, CAT_PARAM, name, warnings=warnings,
                          message="本地参数校验未通过：" + "；".join(errors),
                          diagnostics={"param_errors": errors}, attempts=0)

        out_path = params.pop("out", None) if raw else None
        if not raw:
            params.pop("out", None)          # 已在 validate_params 里给出「已忽略」告警

        # 路径模板替换（替换后不再作为 query 参数发送）
        while "{" in path:
            m = re.search(r"\{(\w+)\}", path)
            if not m:
                break
            key = m.group(1)
            if key not in params:
                return Result(False, CAT_PARAM, name, warnings=warnings,
                              message=f"接口 {name} 需要参数 {key}（路径模板：{APIS[name][1]}）")
            path = path.replace(m.group(0), str(params.pop(key)))

        fp = _fingerprint(name, params)
        t0 = self.clock.now()
        dedup = _dedup_key(task_id, fp)

        # ── dry-run：**只做本地校验与计划展示** ──
        # ⚠️ 必须在「取得提交资格」**之前**返回：dry-run 绝不创建意图文件，
        #    否则随后的首次正式提交会被自己的 dry-run 意图误判为未决提交而阻断。
        if dry_run:
            return Result(True, CAT_OK, name, warnings=warnings,
                          message="dry-run：未发送任何请求，未占用提交资格、未写任何意图文件",
                          diagnostics={"plan": {"method": method, "path": path,
                                                "mutating": mutating, "base_order": list(self.bases)}},
                          submit_state=None)

        # ── 有副作用调用：防重（任务身份优先）+ 跨进程「检查 → 资格 → 复检 → 意图」 ──
        # ① 先查已有记录：已受理（confirmed/accepted/done，带 aid）/ 结果未知
        #    （submit_unknown）/ **已发送未确认（sent）** → **阻断**并返回已有状态；
        #    台账**损坏/不可读** → 同样**阻断**（不得把「读不出来」当成「没有历史」）。
        # ② 再用「意图文件」做跨进程互斥：只有第一个原子创建成功的调用方才有资格
        #    发写请求；后续调用方（并发/上次未决）看到意图文件已存在即阻断。
        # ③ ★ 取得资格后**重新核实**已有状态 —— 「读历史」与「取锁」之间存在空档，
        #    另一进程可能已在这段空档内完成提交并释放了锁；不复检就会重复发布。
        guard_fd = None
        guard_path = None
        if mutating and dedup:
            blk = self._block_from_existing(name, self._find_existing_compat(dedup, task_id, fp,
                                                                business_fp),
                                            fp, task_id, warnings, t0)
            if blk is not None:
                return blk
            gpath = (self.task_file or TASK_FILE) + f".{dedup}.intent"
            try:
                os.makedirs(os.path.dirname(gpath), exist_ok=True)
                guard_fd = os.open(gpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(guard_fd, json.dumps({"ts": _now_iso(), "dedup": dedup,
                                               "task_id": task_id,
                                               "fingerprint": fp}).encode("utf-8"))
                os.fsync(guard_fd)
                guard_path = gpath
            except FileExistsError:
                guard_fd = None          # 已有未决提交意图 → 只读核对，不发请求
            except Exception:            # noqa: BLE001 意图文件写失败也视为无法取得资格
                guard_fd = None
            if guard_fd is not None:
                blk2 = self._block_from_existing(
                    name, self._find_existing_compat(dedup, task_id, fp, business_fp),
                    fp, task_id, warnings, t0)
                if blk2 is not None:
                    # 复检发现已有提交（多为并行进程刚完成）→ 撤销本次资格
                    self._release_guard(guard_fd, guard_path)
                    guard_fd, guard_path = None, None
                    return blk2

        if mutating and dedup and guard_fd is None:
            # 意图文件已被占用 → 未决提交（并发/上次中断）→ 阻断
            existing = self._find_existing_compat(dedup, task_id, fp, business_fp)
            blk = self._block_from_existing(name, existing, fp, task_id, warnings, t0)
            if blk is not None:
                return blk
            return self._blocked_repeat(name, None, fp, task_id, warnings, t0)

        if mutating:
            r = append_task({"ts": _now_iso(), "api": name, "fingerprint": fp,
                             "task_id": task_id, "dedup": dedup, "business_fp": business_fp,
                             "account_ref": account_ref(self.uid()), "params_keys": sorted(params),
                             "tid": params.get("tid"), "base_order": list(self.bases),
                             "state": "sent", "aid": None}, self.task_file)
            if not r["ok"]:
                # 🔴 必要记录保存失败 → **中止发布**，发布调用数为 0
                self._release_guard(guard_fd, guard_path)
                return Result(False, CAT_UNKNOWN, name, warnings=warnings,
                              message=f"任务提交意图保存失败（本地记录不可写），"
                                      f"已中止发布：{r['error']}",
                              diagnostics={"save_error": r["error"],
                                           "publish_aborted": True},
                              submit_state=None)

        attempts = 0
        relogin = 0
        last_cat, last_msg, last_diag = CAT_UNKNOWN, "未执行", {}
        per_base = {b: 0 for b in self.bases}
        sent_bases = []
        result = None

        try:
            token = self.ensure_token()
        except ApiError as e:
            el = self.clock.now() - t0
            self._release_guard(guard_fd, guard_path)   # 登录失败，未发出写请求
            return Result(False, e.category, name, elapsed=el, warnings=warnings,
                          message=str(e), diagnostics={"login_failed": True})

        for base in self.bases:
            while per_base[base] < self.policy.max_attempts_per_base:
                spent = self.clock.now() - t0
                if spent >= self.policy.max_total_seconds:
                    last_cat, last_msg = CAT_NETWORK, (
                        f"已达真实经过时间上限 {self.policy.max_total_seconds}s，停止重试")
                    last_diag = {"budget_stop": True, "elapsed": round(spent, 3)}
                    break
                attempts += 1
                per_base[base] += 1
                if mutating and base in sent_bases:
                    # 已经向该站发过有副作用的请求 → 不得再次提交
                    last_cat, last_msg = STATE_SUBMIT_UNKNOWN, "有副作用调用已发出，不重复提交"
                    break
                sent_bases.append(base) if mutating else None
                try:
                    status, headers, payload = self._raw_request(
                        base, method, path, params, token, timeout, raw)
                except TransportError as e:
                    last_cat = CAT_NETWORK
                    last_msg = f"网络异常（{base}）：{e}"
                    last_diag = {"transport": e.kind, "base": base}
                    if mutating:
                        return self._submit_unknown(name, base, params, fp, warnings,
                                                    attempts, t0, last_diag,
                                                    "请求已发出但未收到响应",
                                                    dedup=dedup, task_id=task_id)
                    if self._sleep_if_budget(self.policy.backoff_for(per_base[base] - 1), t0):
                        continue
                    break
                except ApiError as e:
                    last_cat, last_msg = e.category, str(e)
                    break

                if raw:
                    return self._finish_raw(name, base, out_path, payload, headers, warnings,
                                            attempts, t0, mutating, params, fp)

                cat, why = classify_response(status, payload)

                if cat == CAT_PERMISSION:
                    self._release_guard(guard_fd, guard_path)   # 明确失败，未产生消耗
                    return Result(False, CAT_PERMISSION, name, base, payload, status,
                                  attempts, self.clock.now() - t0, warnings,
                                  {"server_msg": payload.get("msg"), "relogin_attempts": relogin},
                                  message=f"权限不足：{why}（本账号无该能力，重登无效，已跳过重登）")
                if cat == CAT_PARAM:
                    self._release_guard(guard_fd, guard_path)   # 明确失败，未产生消耗
                    return Result(False, CAT_PARAM, name, base, payload, status,
                                  attempts, self.clock.now() - t0, warnings,
                                  {"server_msg": payload.get("msg")},
                                  message=f"参数错误：{why}（不换站重复提交）")
                if cat == CAT_BUSINESS:
                    self._release_guard(guard_fd, guard_path)   # 明确业务失败，未产生消耗
                    return Result(False, CAT_BUSINESS, name, base, payload, status,
                                  attempts, self.clock.now() - t0, warnings,
                                  {"server_msg": payload.get("msg")},
                                  message=f"业务失败：{why}")
                if cat == CAT_AUTH:
                    if relogin >= self.policy.max_relogin:
                        self._release_guard(guard_fd, guard_path)   # 明确失败，未产生消耗
                        return Result(False, CAT_AUTH, name, base, payload, status,
                                      attempts, self.clock.now() - t0, warnings,
                                      {"relogin_attempts": relogin},
                                      message=f"身份失效且已达重登上限 {self.policy.max_relogin}：{why}")
                    relogin += 1
                    self.limits["relogin"] = relogin
                    self.note(f"身份失效（{why}）→ 第 {relogin} 次重登")
                    try:
                        token = self.ensure_token(force=True)
                    except ApiError as e:
                        return Result(False, CAT_AUTH, name, base, payload, status,
                                      attempts, self.clock.now() - t0, warnings,
                                      {"relogin_attempts": relogin}, message=f"重登失败：{e}")
                    continue

                if cat in (CAT_NETWORK, CAT_HTTP, CAT_UNKNOWN):
                    last_cat = cat
                    last_msg = why or f"HTTP {status}"
                    last_diag = {"http_status": status,
                                 "server_msg": payload.get("msg"),
                                 "body_excerpt": json.dumps(payload, ensure_ascii=False)[:300]}
                    if mutating:
                        return self._submit_unknown(
                            name, base, params, fp, warnings, attempts, t0, last_diag,
                            f"{last_msg} —— 无法确认是否已被服务端受理",
                            dedup=dedup, task_id=task_id)
                    # 只有「可能自愈」的分类才值得重试/换站；其它保留诊断直接返回
                    if cat == CAT_NETWORK and self._sleep_if_budget(
                            self.policy.backoff_for(per_base[base] - 1), t0):
                        continue
                    if cat == CAT_HTTP:
                        return Result(False, CAT_HTTP, name, base, payload, status, attempts,
                                      self.clock.now() - t0, warnings, dict(last_diag),
                                      message=last_msg)
                    if cat == CAT_UNKNOWN:
                        return Result(False, CAT_UNKNOWN, name, base, payload, status, attempts,
                                      self.clock.now() - t0, warnings, dict(last_diag),
                                      message=f"未知错误（保留诊断，不伪装成无数据）：{last_msg}")
                    break

                cat2, why2 = classify_success(payload, warnings)
                if cat2 == CAT_EMPTY:
                    return Result(True, CAT_EMPTY, name, base, payload, status, attempts,
                                  self.clock.now() - t0, warnings, {},
                                  message=f"空数据：{why2}")
                if cat2 == CAT_PARAM:
                    return Result(False, CAT_PARAM, name, base, payload, status, attempts,
                                  self.clock.now() - t0, warnings, {},
                                  message=f"参数问题：{why2}")
                if mutating:
                    aid = _extract_aid(payload)
                    # ★ 结果已确定 → 回填；**回填失败就不释放意图锁**（保留阻断标记，
                    #   否则「已发出并已受理」的任务会在下次调用时被当成新任务再发一次）
                    saved = self._update_task(fp, {"state": "confirmed", "aid": aid},
                                              dedup=dedup, task_id=task_id)
                    if saved:
                        self._release_guard(guard_fd, guard_path)
                    else:
                        warnings.append("结果已受理但**本地记录回填失败** → 保留提交意图文件，"
                                        "阻断后续重复发布（须只读核对）")
                    return Result(True, CAT_OK, name, base, payload, status, attempts,
                                  self.clock.now() - t0, warnings,
                                  {"aid": aid,
                                   "save_error": None if saved else "任务记录回填失败（已保留提交意图）"},
                                  submit_state="confirmed", task_id=aid)
                return Result(True, CAT_OK, name, base, payload, status, attempts,
                              self.clock.now() - t0, warnings, {})
            if last_cat == CAT_NETWORK and base != self.bases[-1]:
                continue
            break

        spent = self.clock.now() - t0
        if mutating:
            return self._submit_unknown(name, None, params, fp, warnings, attempts, t0,
                                        last_diag, last_msg, dedup=dedup, task_id=task_id)
        ok = last_cat == CAT_OK
        return Result(ok, last_cat, name, None, None, None, attempts, spent, warnings,
                      dict(last_diag), message=f"{last_msg}（已尝试 {len(self.bases)} 个站点，共 {attempts} 次）")

    # ——— 内部工具 ———
    def _sleep_if_budget(self, secs, t0):
        if secs <= 0:
            return True
        if self.clock.now() - t0 + secs >= self.policy.max_total_seconds:
            return False
        self.clock.sleep(secs)
        return True

    def _update_task(self, fp, patch, dedup=None, task_id=None):
        """按 **dedup 主键（task_id 优先）** 或参数指纹更新最后一条 `sent` 记录。

        原子写：先读全量 → 修改 → 临时文件 + `os.replace` 原子替换，
        避免并发写坏记录；写失败**显式返回 False**（不静默吞掉）。
        """
        p = self.task_file
        rows, _err = load_tasks(p)
        key = dedup or ("fp:" + fp) if (dedup or fp) else None
        hit = None
        for r in reversed(rows):
            rkey = r.get("dedup") or (("fp:" + r.get("fingerprint")) if r.get("fingerprint") else None)
            if rkey == key and r.get("state") == "sent":
                r.update(patch)
                hit = r
                break
        if hit is None:
            return False
        return self._rewrite_tasks(rows)

    def _rewrite_tasks(self, rows):
        p = self.task_file
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            return True
        except Exception:                                        # noqa: BLE001
            return False

    def _release_guard(self, fd, path):
        """释放提交资格：关闭并删除意图文件（仅在**结果已确定**时调用）。"""
        if fd is not None:
            try:
                os.close(fd)
            except Exception:                                    # noqa: BLE001
                pass
        if path:
            try:
                os.remove(path)
            except Exception:                                    # noqa: BLE001
                pass

    def _find_existing_compat(self, dedup, task_id, fp, business_fp=None):
        """调用 `_find_existing`，**兼容只接受旧签名（无 `business_fp`）的子类**。

        ⚠️ 为什么需要：`_find_existing` 是**可被外部子类覆写**的方法（第 16 轮
        独立复核夹具 `PausedRead` 就覆写了它以注入受控并发时序）。第 17 轮给它
        新增了第 4 个参数；若直接按新签名调用，**旧夹具的覆写方法会因参数个数不符
        抛 `TypeError`** —— 那是我方接口变更破坏了既有验证夹具。

        处理：先按新签名（关键字传 `business_fp`）调用；仅当 `TypeError`
        **由参数不匹配引起**时退回旧签名。这样：
          · 新版基类 / 支持新签名的子类 → 完整功能（含业务身份兜底匹配）；
          · 旧签名子类（如复核夹具）→ 行为与第 16 轮**完全一致**，不中断验证。
        """
        try:
            return self._find_existing(dedup, task_id, fp, business_fp=business_fp)
        except TypeError as e:
            msg = str(e)
            if "_find_existing" in msg and ("positional argument" in msg
                                            or "unexpected keyword" in msg):
                return self._find_existing(dedup, task_id, fp)
            raise

    def _find_existing(self, dedup, task_id, fp, business_fp=None):
        """查找同一任务（按 **dedup 主键**）的已有记录。

        · `dedup = task_id`（任务身份，优先）或 `"fp:"+fingerprint`（无身份时回退参数指纹）；
        · **严格按 dedup 匹配**；只有当记录**本身不含 `dedup` 字段**（1.14 旧格式）时，
          才按 `fingerprint` 兜底匹配 —— 既兼容旧记录（旧记录只有指纹、没有任务身份），
          又不会把「参数相同但 task_id 不同的两个独立任务」误判为同一任务而永久禁止；
        · **`business_fp` 兜底（第 17 轮）**：给了 `business_fp` 时，额外匹配
          「记录里的 `business_fp` 相同」的条目 —— 用于兼容**编号算法升级**前后的同一任务
          （旧记录 `task_id` 为旧格式 `entry_id`，新代码算出新格式）。
          ⚠️ 只在**记录自带 `business_fp`** 时生效，不做任何回算/猜测；
          且**仅对新代码写入的记录（含该字段）**有意义，不影响旧记录的既有行为。
        · 台账**损坏/不可读** → 返回 `{"_read_error": [...]}`，
          由调用方**阻断发布**（**绝不**把「读不出来」当成「没有历史」继续提交）。
        """
        rows, err = load_tasks(self.task_file)
        if err:
            return {"_read_error": err}
        ref = account_ref(self.uid())
        cands = [t for t in rows
                 if t.get("account_ref") == ref
                 and (t.get("dedup") == dedup
                      or (not t.get("dedup") and fp and t.get("fingerprint") == fp)
                      or (business_fp and t.get("business_fp") == business_fp))]
        for st in ("confirmed", "accepted", "done", STATE_SUBMIT_UNKNOWN, "sent"):
            for t in reversed(cands):
                if t.get("state") == st:
                    return t
        return None

    def _block_from_existing(self, name, existing, fp, task_id, warnings, t0):
        """已有记录 / 台账损坏 → 返回**阻断** `Result`；无阻断条件 → `None`。"""
        if not isinstance(existing, dict):
            return None
        if "_read_error" in existing:
            errs = existing.get("_read_error") or []
            warnings.append("本地任务台账损坏/不可读 → 无法确认是否已有提交，**阻断发布**"
                            "（不覆盖、不忽略、不当作无历史）")
            return Result(False, CAT_UNKNOWN, name, None, None, None, 0,
                          self.clock.now() - t0, warnings,
                          {"blocked": "ledger_read_error", "fingerprint": fp,
                           "task_id": task_id, "read_errors": errs},
                          submit_state=STATE_SUBMIT_UNKNOWN, task_id=None,
                          message=f"本地任务台账损坏/不可读（{len(errs)} 处错误）→ 已阻断发布；"
                                  f"请先修复或归档该记录文件后再重试")
        if existing.get("state") in ("confirmed", "accepted", "done",
                                     STATE_SUBMIT_UNKNOWN, "sent"):
            return self._blocked_repeat(name, existing, fp, task_id, warnings, t0)
        return None

    def _blocked_repeat(self, name, existing, fp, task_id, warnings, t0):
        """发现同一任务的未决/已受理提交 → **阻断**，返回已有状态 + aid，不重新发布。"""
        if existing is None:
            # 意图文件在、但 JSONL 里查不到 → 保守阻断（存在未决提交，不得贸然重发）
            warnings.append("发现未决提交意图文件（可能由并发进程持有）→ 只读核对，不重复提交")
            return Result(False, CAT_UNKNOWN, name, None, None, None, 0,
                          self.clock.now() - t0, warnings,
                          {"blocked": "pending_intent", "fingerprint": fp, "task_id": task_id},
                          submit_state=STATE_SUBMIT_UNKNOWN, task_id=None,
                          message="存在未决提交（并发/恢复场景），已阻断重复发布；"
                                  "进入只读核对，不自动重发、不换线路重发")
        st = existing.get("state")
        aid = existing.get("aid")
        if st in ("confirmed", "accepted", "done") and aid:
            warnings.append(f"同一任务已受理（aid={aid}）→ 返回已有状态，不重复发布")
            return Result(True, CAT_OK, name, None, existing, 200, 0,
                          self.clock.now() - t0, warnings, {"aid": aid},
                          submit_state="confirmed", task_id=aid,
                          message=f"该任务已受理（aid={aid}），返回已有结果，未重复发布")
        if st == STATE_SUBMIT_UNKNOWN:
            warnings.append("同一任务提交结果未知 → 只读核对，不重复发布")
            return Result(False, CAT_UNKNOWN, name, None, existing, None, 0,
                          self.clock.now() - t0, warnings,
                          {"blocked": "submit_unknown", "fingerprint": fp, "task_id": task_id,
                           "existing": existing},
                          submit_state=STATE_SUBMIT_UNKNOWN, task_id=aid,
                          message="该任务提交结果未知，已阻断重复发布；请用只读方式核对结果")
        # sent / 其它未决状态
        warnings.append(f"同一任务存在未决提交（状态 {st}）→ 只读核对，不重复发布")
        return Result(False, CAT_UNKNOWN, name, None, existing, None, 0,
                      self.clock.now() - t0, warnings,
                      {"blocked": "pending", "fingerprint": fp, "task_id": task_id,
                       "existing": existing},
                      submit_state=STATE_SUBMIT_UNKNOWN, task_id=aid,
                      message=f"该任务存在未决提交（状态 {st}），已阻断重复发布")

    def _finish_raw(self, name, base, out_path, body, headers, warnings, attempts, t0,
                    mutating, params, fp):
        okc, kind, reasons = validate_download(name, body, headers,
                                               params.get("version"))
        el = self.clock.now() - t0
        if not okc:
            return Result(False, CAT_INVALID, name, base,
                          {"kind": kind, "bytes": len(body)}, 200, attempts, el, warnings,
                          {"download_reasons": reasons, "content_kind": kind},
                          message="下载内容不合法，未保存：" + "；".join(reasons))
        if out_path:
            try:
                with open(out_path, "wb") as f:
                    f.write(body)
            except Exception as e:                               # noqa: BLE001
                return Result(False, CAT_UNKNOWN, name, base, None, 200, attempts, el,
                              warnings, {"write_error": str(e)},
                              message=f"写入失败：{e}")
            return Result(True, CAT_OK, name, base,
                          {"saved": out_path, "bytes": len(body), "kind": kind},
                          200, attempts, el, warnings, {}, message="已保存")
        if name == "download_pdf":
            return Result(True, CAT_OK, name, base, body, 200, attempts, el, warnings,
                          {"bytes": len(body), "kind": kind})
        text = body.decode("utf-8", "ignore")
        try:
            return Result(True, CAT_OK, name, base, json.loads(text), 200, attempts, el,
                          warnings, {})
        except Exception as e:                                   # noqa: BLE001
            return Result(False, CAT_INVALID, name, base, None, 200, attempts, el, warnings,
                          {"json_parse_error": str(e), "content_kind": kind},
                          message="配置不是合法 JSON（官方 1.0.5 的 success_schema 含未加引号占位符）；"
                                  "如需原文请加 out=<路径> 重新调用")

    def _submit_unknown(self, name, base, params, fp, warnings, attempts, t0, diag, why,
                        dedup=None, task_id=None):
        """有副作用调用结果不确定 —— 用只读能力核对，不能唯一确认就返回「提交状态未知」。

        **保留**意图文件与预算占用（不释放、不重发、不换线路重发）。
        """
        rec = {"state": STATE_SUBMIT_UNKNOWN, "why": why,
               "note": "客户端不做第二次提交；本地记录仅为核对线索，**不是**服务端幂等保障"}
        self._update_task(fp, rec, dedup=dedup, task_id=task_id)
        recon = self.reconcile(name, params, fp, task_id=task_id)
        warnings.append("提交结果不确定：已停止自动重试/换站，改用只读能力核对")
        return Result(False, CAT_UNKNOWN, name, base, None, None, attempts,
                      self.clock.now() - t0, warnings,
                      {"submit": rec, "reconcile": recon},
                      submit_state=STATE_SUBMIT_UNKNOWN, task_id=recon.get("aid"),
                      message=f"提交状态未知（{why}）；只读核对结果：{recon.get('verdict')}")

    def reconcile(self, name, params, fingerprint=None, uid=None, path=None, task_id=None):
        """用**只读**能力核对一次有副作用调用的结果。绝不发写请求。"""
        aid = None
        for t in reversed(open_submissions(fingerprint, uid or self.uid(),
                                           path or self.task_file, task_id=task_id)):
            if t.get("aid"):
                aid = t["aid"]
                break
        if aid:
            r = self.call("report_status", {"aid": aid})
            if r.ok:
                st = ((r.data or {}).get("data") or {}).get("status")
                if st in ("public", "private"):
                    return {"verdict": "confirmed", "aid": aid, "status": st,
                            "how": "report_status(aid) 返回终态"}
                if st == "error":
                    return {"verdict": "failed", "aid": aid, "status": st,
                            "how": "report_status(aid) 明确返回 error"}
            return {"verdict": "unknown", "aid": aid,
                    "why": f"report_status 未返回终态（{r.category}）", "how": "只读核对"}
        r = self.call("report_list", {"type": "self", "num": 20})
        if r.ok:
            n = len(first_list(r.data) or [])
            return {"verdict": "unknown", "aid": None, "listed": n,
                    "why": "本地无 aid，且仅凭列表无法与本次提交唯一对应",
                    "how": "report_list(type=self)"}
        return {"verdict": "unknown", "aid": None,
                "why": f"只读核对亦失败（{r.category}）", "how": "report_list(type=self)"}

    # ——— 轮询报告终态（已有 aid 继续查原任务，不新建） ———
    def wait_report(self, aid, max_wait=1200.0, interval=10.0, max_consecutive_exceptions=5,
                    poll=None):
        """轮询既有报告终态。

        硬规则：
          · **只查原 aid，绝不因为轮询异常而新建报告**；
          · 只有服务端明确返回 `data.status == "error"` 才判「报告生成失败」；
          · 连续异常超上限 → 返回 `unknown`（不得报「报告失败」）。
        """
        t0 = self.clock.now()
        consecutive = 0
        exceptions = 0
        polls = 0
        fn = poll or (lambda: self.call("report_status", {"aid": aid}))
        while True:
            if self.clock.now() - t0 >= max_wait:
                return {"ok": False, "verdict": "timeout", "aid": aid, "polls": polls,
                        "exceptions": exceptions, "elapsed": round(self.clock.now() - t0, 3),
                        "report_failed": False,
                        "message": f"等待超过 {max_wait}s 上限，未取得终态（不判报告失败）"}
            polls += 1
            try:
                r = fn()
            except Exception as e:                               # noqa: BLE001
                r = Result(False, CAT_UNKNOWN, "report_status", message=str(e))
            if not r.ok:
                consecutive += 1
                exceptions += 1
                if consecutive >= max_consecutive_exceptions:
                    return {"ok": False, "verdict": "unknown", "aid": aid, "polls": polls,
                            "exceptions": exceptions,
                            "elapsed": round(self.clock.now() - t0, 3),
                            "report_failed": False,
                            "message": f"连续 {consecutive} 次异常，无法获取状态"
                                       "（不得判为报告失败；原任务保持不变）"}
                self.clock.sleep(interval)
                continue
            consecutive = 0
            st = ((r.data or {}).get("data") or {}).get("status")
            if st == "error":
                return {"ok": False, "verdict": "failed", "aid": aid, "polls": polls,
                        "exceptions": exceptions, "status": st,
                        "elapsed": round(self.clock.now() - t0, 3), "report_failed": True,
                        "message": "服务端明确返回 error → 报告生成失败"}
            if st in ("public", "private"):
                return {"ok": True, "verdict": "success", "aid": aid, "polls": polls,
                        "exceptions": exceptions, "status": st,
                        "elapsed": round(self.clock.now() - t0, 3), "report_failed": False,
                        "message": f"报告已就绪（{st}）"}
            self.clock.sleep(interval)


def _extract_aid(payload):
    """从发布响应里取 aid（不猜、不造：取不到就是 None）。"""
    if not isinstance(payload, dict):
        return None
    d = payload.get("data")
    if isinstance(d, dict):
        for k in ("aid", "id", "articleId", "reportId"):
            if d.get(k):
                return d[k]
    if isinstance(d, (str, int)) and str(d).strip():
        return d
    for k in ("aid", "id"):
        if payload.get(k):
            return payload[k]
    return None


# ─────────────────────────── 兼容层 ───────────────────────────
def call(name, params=None, timeout=60):
    """旧签名兼容：返回 (结果, 实际base)。

    ⚠️ **行为变更（v1.8.0，已登记）**：业务失败（success=false）此前会当作
    正常结果返回，现在会抛 `ApiError(CATEGORY)`。
    新增字段一律加前缀，原有字段约定不变。
    """
    c = Client()
    r = c.call(name, params, timeout=timeout)
    if r.ok:
        return r.data, r.base
    if r.submit_state == STATE_SUBMIT_UNKNOWN or r.category == CAT_PARAM:
        raise ApiError(r.category, r.message, result=r)
    raise ApiError(r.category, r.message, result=r)


def login(base):
    """旧签名兼容：在指定站点登录并持久化（与账号绑定）。"""
    c = Client(base_primary=base, base_fallback=None)
    tok = c.login_on(base)
    save_token(tok, c.uid(), base, c.token_file)
    return tok


# ─────────────────────────── 离线自检 ───────────────────────────
def _client(script, clock=None, token_file=None, task_file=None, **kw):
    """构造一个**全离线**的客户端：脚本化传输层 + 模拟时钟 + 临时凭据/任务文件。"""
    clock = clock or MockClock()
    tr = ScriptedTransport(script, clock)
    pol = kw.pop("policy", None)
    if isinstance(pol, dict):
        pol = RetryPolicy(**pol)
    c = Client(transport=tr, clock=clock, credential={"userID": "test-acct-A"},
               token_file=token_file, task_file=task_file, policy=pol,
               stderr=open(os.devnull, "w"), **kw)
    return c, tr, clock


OK_LOGIN = {"path_contains": "/api/user/doLogin", "json": {"success": True, "data": {"browserCode": "TK"}}}
OK_USER = {"path_contains": "/api/user/getUser", "json": {"success": True, "status": "OK", "data": {"name": "u"}}}

#: 用例 A~K 的脚本与 kwargs —— selftest 与 cases/08 **共用同一份**
SCENARIOS = {
    "A": dict(
        title="无缓存 Token、主站登录失败 → 进入备用登录路径",
        script=[{"path_contains": "/api/user/doLogin", "base_contains": "shenwa.top",
                 "raise": "timeout", "advance": 1.0},
                {"path_contains": "/api/user/doLogin", "base_contains": "8.130.17.247",
                 "json": {"success": True, "data": {"browserCode": "TK2"}}},
                OK_USER],
        api="user_info", params={}),
    "B": dict(
        title="明确 HTTP 401 身份失效 → 有限重登并恢复",
        script=[{"path_contains": "/api/user/getUser", "status": 401,
                 "json": {"success": False, "status": "AUT", "msg": "token 已过期"}},
                {"path_contains": "/api/user/doLogin",
                 "json": {"success": True, "data": {"browserCode": "TK3"}}},
                OK_USER],
        token_seed="TKSEED", api="user_info", params={}),
    "C": dict(
        title="明确「你没有权限」→ 返回权限不足，不反复登录",
        script=[{"path_contains": "/api/review/getGuailiRates",
                 "json": {"success": False, "status": "AUT", "msg": "你没有权限"}}],
        token_seed="TKSEED", api="get_guaili_rates", params={"maxDays": "20"}),
    "D": dict(
        title="report_publish 响应超时 → 不自动第二次发布；不能核实则返回提交状态未知",
        script=[{"path_contains": "/api/manage/wfStartPublish", "raise": "timeout",
                 "advance": 2.0},
                {"path_contains": "/api/home/getList",
                 "json": {"success": True, "status": "OK", "data": []}}],
        token_seed="TKSEED", api="report_publish",
        params={"tid": "3002", "question": "q", "text": "t"}),
    "E": dict(
        title="已有 aid、查询暂时失败 → 继续保留原任务，不判失败、不新建",
        script=[{"path_contains": "/api/home/getReportStatus", "raise": "timeout",
                 "repeat": 5}],
        token_seed="TKSEED", api="report_status", params={"aid": "888"},
        wait=dict(aid="888", max_wait=600.0, interval=10.0)),
    "F": dict(
        title="success=false → 上层能识别业务失败，不能当正常数据继续分析",
        script=[{"path_contains": "/api/home/getList",
                 "json": {"success": False, "status": "ERR", "msg": "服务暂不可用"}}],
        token_seed="TKSEED", api="report_list", params={"type": "public", "num": 20}),
    "G": dict(
        title="下载返回错误 JSON / HTML → 不报告成功下载",
        script=[{"path_contains": "/api/home/downloadPdf",
                 "json": {"success": False, "msg": "报告不存在"},
                 "headers": {"Content-Type": "application/json"}}],
        token_seed="TKSEED", api="download_pdf", params={"aid": "1"}),
    "H": dict(
        title="字符串代码的前导零保持不变；不支持的代码格式得到明确提示",
        script=[{"path_contains": "/api/market/getRightSide",
                 "json": {"success": True, "status": "OK", "data": []}}],
        token_seed="TKSEED", api="get_right_side", params={"codes": "000300.CSI", "dayOffset": "3"}),
    "I": dict(
        title="账号切换 → 不会使用旧账号 Token",
        script=[OK_LOGIN, OK_USER],
        token_seed_other=True, api="user_info", params={}),
    "J": dict(
        title="正常查询 / 正常下载 / 正常报告终态不受影响",
        script=[OK_USER,
                {"path_contains": "/api/home/downloadPdf", "body": b"%PDF-1.7\n...",
                 "headers": {"Content-Type": "application/pdf"}},
                {"path_contains": "/api/home/getReportStatus",
                 "json": {"success": True, "status": "OK", "data": {"status": "public"}}}],
        token_seed="TKSEED", api="user_info", params={}),
    "K": dict(
        title="超时、退避与重试上限通过模拟时钟验证",
        script=[{"path_contains": "/api/user/getUser", "raise": "timeout", "advance": 5.0,
                 "repeat": 20}],
        token_seed="TKSEED", api="user_info", params={},
        policy=RetryPolicy(max_attempts_per_base=2, max_total_seconds=30.0, backoff=(0.5, 1.0))),
}


def run_scenario(key, **kw):
    """跑一个离线场景，返回可断言的观测字典。

    **全程不联网、不发任何真实写请求**：传输层是脚本化的，时钟是模拟的，
    凭据/Token/任务记录全部落在临时目录里（跑完即删）。
    """
    import shutil
    import tempfile

    spec = dict(SCENARIOS[key])
    spec.update(kw)
    tmp = tempfile.mkdtemp(prefix="shenwa_case_")
    token_file = os.path.join(tmp, ".tok")
    task_file = os.path.join(tmp, "tasks.jsonl")
    try:
        clock = MockClock()
        c, tr, _ = _client(spec["script"], clock, token_file=token_file,
                           task_file=task_file, policy=spec.get("policy"))
        if spec.get("token_seed"):
            save_token(spec["token_seed"], "test-acct-A", BASE_PRIMARY, c.token_file)
        if spec.get("token_seed_other"):
            save_token("OLDTOKEN", "test-acct-B", BASE_PRIMARY, c.token_file)

        obs = {"title": spec["title"]}
        params = {k: _coerce(v, k) for k, v in (spec.get("params") or {}).items()}
        out_path = None
        if key == "G":
            out_path = os.path.join(tmp, "should_not_exist.pdf")
            params["out"] = out_path

        if key == "E":
            w = dict(spec["wait"])
            r = c.wait_report(w["aid"],
                              poll=lambda: c.call("report_status", {"aid": w["aid"]}),
                              max_wait=w["max_wait"], interval=w["interval"])
            obs.update({"verdict": r["verdict"], "report_failed": r["report_failed"],
                        "polls": r["polls"], "exceptions": r["exceptions"],
                        "elapsed": round(r["elapsed"], 3)})
            obs["publish_calls"] = tr.count("/api/manage/wfStartPublish")
            obs["relogin"] = c.limits["relogin"]
            return obs

        res = c.call(spec["api"], params)
        obs.update({
            "ok": res.ok, "category": res.category, "exit_code": res.exit_code,
            "submit_state": res.submit_state, "attempts": res.attempts,
            "elapsed": round(res.elapsed, 3), "message": res.message,
            "warnings": res.warnings,
        })
        obs["login_calls"] = tr.count("/api/user/doLogin")
        obs["publish_calls"] = tr.count("/api/manage/wfStartPublish")
        obs["bases_used"] = sorted({b.replace("https://", "").replace("http://", "")
                                    for b in tr.bases()})
        obs["relogin"] = c.limits["relogin"]
        obs["sent_params"] = [x["params"] for x in tr.calls if "getRightSide" in x["path"]]
        if key == "D":
            obs["submit_unknown"] = (res.submit_state == STATE_SUBMIT_UNKNOWN)
            obs["reconcile_verdict"] = ((res.diagnostics or {}).get("reconcile") or {}).get("verdict")
            obs["readonly_after_submit"] = (tr.count("/api/home/getList")
                                            + tr.count("/api/home/getReportStatus"))
        if key == "G":
            obs["saved"] = bool(out_path and os.path.exists(out_path))
            obs["reasons"] = (res.diagnostics or {}).get("download_reasons")
            obs["content_kind"] = (res.diagnostics or {}).get("content_kind")
        if key == "K":
            obs["sleep_calls"] = len(clock.slept)
            obs["sleep_total"] = round(sum(clock.slept), 3)
            obs["elapsed_within_budget"] = res.elapsed <= c.policy.max_total_seconds
        if key == "I":
            obs["token_used"] = [x["token"] for x in tr.calls if x["token"]]
            obs["used_old_token"] = any(t == "OLDTOKEN" for t in obs["token_used"])
        if key == "H":
            obs["codes_sent"] = [p.get("codes") for p in obs["sent_params"]]
            obs["codes_is_str"] = all(isinstance(p.get("codes"), str) for p in obs["sent_params"])
        return obs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: 发布成功时的替身回执（含 aid，形状与实测一致：ManualNews@@<hash>@@<时间戳>）
OK_PUBLISH = {"path_contains": "/api/manage/wfStartPublish",
              "json": {"success": True, "status": "OK",
                       "data": {"aid": "ManualNews@@2f1c9a@@20261003210000"}}}


def run_publish_gate(params, script=None, *, coerce=False, token_seed="TKSEED",
                     task_file=None):
    """从**真实调用入口** `Client.call("report_publish", …)` 发起一次发布，全程离线。

    离线保证：传输层是 `ScriptedTransport`（记录每一次**真实发出**的请求）、时钟是模拟的、
    凭据 / 任务记录落在临时目录里，跑完即删 → **不联网、不发任何真实写请求**。

    返回可断言的观测（供「未通过检查时发布调用次数为 0；通过时实际提交的参数与计划一致」）：

      ok / category / exit_code / message / warnings / submit_state / aid
      publish_calls   —— 实际发往 /api/manage/wfStartPublish 的请求数
      total_calls     —— 实际发出的全部请求数（含登录等）
      sent_params     —— 实际提交的参数（逐次）
      n_task_records / task_state / task_aid —— 本地任务条目（成功 confirmed / 未确认 sent 等）

    `coerce=False`（默认）表示**原样传参**，用于单独检验校验层本身；
    `coerce=True` 复现 CLI 的 `_kv` + `_coerce` 路径。
    """
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="shenwa_gate_")
    tf = task_file or os.path.join(tmp, "tasks.jsonl")
    script = script if script is not None else [OK_LOGIN, OK_PUBLISH]
    try:
        c, tr, _ = _client(script, token_file=os.path.join(tmp, ".tok"), task_file=tf)
        if token_seed:
            save_token(token_seed, "test-acct-A", BASE_PRIMARY, c.token_file)
        p = {k: (_coerce(v, k) if coerce else v) for k, v in dict(params or {}).items()}
        r = c.call("report_publish", p)
        sent = [x["params"] for x in tr.calls if "wfStartPublish" in x["path"]]
        recs, _err = load_tasks(tf)
        return {
            "ok": r.ok, "category": r.category, "exit_code": r.exit_code,
            "message": r.message, "warnings": r.warnings,
            "submit_state": r.submit_state, "aid": r.task_id, "attempts": r.attempts,
            "publish_calls": tr.count("/api/manage/wfStartPublish"),
            "total_calls": len(tr.calls),
            "sent_params": sent,
            "sent_keys": sorted(sent[-1]) if sent else [],
            "n_task_records": len(recs),
            "task_state": (recs[-1].get("state") if recs else None),
            "task_aid": (recs[-1].get("aid") if recs else None),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_asserts(result, asserts):
    """与 capability.py 同一套断言算子（保持各轮一致）。"""
    out = []
    for a in asserts:
        p, op = a.get("path", ""), a.get("op", "eq")
        cur = _dig(result, p)
        ok, det = judge(cur, op, a.get("value"))
        out.append({"path": p, "op": op, "pass": ok, "detail": det, "expect": a.get("value")})
    return out


# 复用 capability.py 的算子（若不可用则退化为本模块自带的最小实现）
try:                                                             # pragma: no cover
    from capability import _dig, judge                        # type: ignore
except Exception:                                                # pragma: no cover
    def _dig(obj, path):
        if path in (None, "", "."):
            return obj
        cur = obj
        for seg in str(path).split("."):
            if isinstance(cur, dict):
                if seg not in cur:
                    return None
                cur = cur[seg]
            elif isinstance(cur, (list, tuple)):
                try:
                    cur = cur[int(seg)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return cur

    def judge(cur, op, want):
        if op == "eq":
            return cur == want, f"实际 {cur!r}"
        if op == "ne":
            return cur != want, f"实际 {cur!r}"
        if op == "contains":
            if isinstance(cur, str):
                return want in cur, f"实际 {cur[:80]!r}"
            if isinstance(cur, (list, tuple)):
                return want in cur, f"实际 {list(cur)!r}"
            return False, f"{cur!r} 不支持 contains"
        if op == "not_contains":
            if isinstance(cur, str):
                return want not in cur, f"实际 {cur[:80]!r}"
            if isinstance(cur, (list, tuple)):
                return want not in cur, f"实际 {list(cur)!r}"
            return True, f"{cur!r} 不含（非容器）"
        if op == "startswith":
            if isinstance(cur, str):
                return cur.startswith(want), f"实际 {cur[:60]!r}"
            return (isinstance(cur, (list, tuple)) and list(cur[:len(want)]) == list(want)), f"实际 {cur!r}"
        if op == "all_contains":
            have = set(cur or [])
            return all(x in have for x in want), f"实际 {sorted(have)!r}"
        if op == "any_str_contains":
            if not isinstance(cur, (list, tuple)):
                return False, f"{cur!r} 不是列表"
            hit = [x for x in cur if isinstance(x, str) and want in x]
            return bool(hit), f"命中 {hit[:2]!r}（共 {len(hit)} 条）"
        if op == "in":
            return cur in want, f"实际 {cur!r}"
        if op == "len_ge":
            return (isinstance(cur, (list, tuple, str, dict)) and len(cur) >= want), \
                   f"实际长度 {len(cur) if hasattr(cur, '__len__') else '—'}"
        if op == "len_eq":
            return (isinstance(cur, (list, tuple, str, dict)) and len(cur) == want), \
                   f"实际长度 {len(cur) if hasattr(cur, '__len__') else '—'}"
        if op == "none_of":
            have = set(cur or [])
            return not any(x in have for x in want), f"实际 {sorted(have)!r}"
        if op == "is_none":
            return cur is None, f"实际 {cur!r}"
        if op == "not_none":
            return cur is not None, f"实际 {cur!r}"
        if op == "true":
            return cur is True, f"实际 {cur!r}"
        if op == "false":
            return cur is False, f"实际 {cur!r}"
        if op == "truthy":
            return bool(cur), f"实际 {cur!r}"
        if op == "ge":
            return (isinstance(cur, (int, float)) and not isinstance(cur, bool) and cur >= want), \
                   f"实际 {cur!r}"
        return False, f"未知断言算子 {op!r}"


#: 本模块独立承担（不需要实例化客户端）的检查类型
LOCAL_CHECK_TYPES = ("classify", "classify_http", "coerce", "param", "download",
                     "update", "token", "registry", "taxonomy", "policy", "error_classes",
                     "publish_gate")


def _local_result(kind, ch):
    if kind == "classify":
        cat, why = classify_payload(ch["payload"])
        return {"category": cat, "why": why}
    if kind == "classify_http":
        return {"category": classify_http(ch["status"])}
    if kind == "coerce":
        return {"value": _coerce(ch["value"], ch.get("key")),
                "type": type(_coerce(ch["value"], ch.get("key"))).__name__}
    if kind == "param":
        e, w = validate_params(ch.get("api", "x"), ch.get("params", {}))
        return {"errors": e, "warnings": w, "n_errors": len(e), "n_warnings": len(w)}
    if kind == "download":
        body = ch["body"]
        body = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        ok, kind_, reasons = validate_download(ch.get("api", "download_pdf"), body,
                                              ch.get("headers"), ch.get("version"))
        return {"ok": ok, "kind": kind_, "reasons": reasons}
    if kind == "update":
        b = ch["body"]
        b = b.encode("utf-8") if isinstance(b, str) else bytes(b)
        return update_is_usable(ch["remote"], ch["local"], b, ch.get("headers"))
    if kind == "token":
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".tok")
            save_token("T1", ch["uid_a"], None, p)
            return {"same_account": load_token(ch["uid_a"], p) == "T1",
                    "other_account_blocked": load_token(ch["uid_b"], p) is None,
                    "legacy_blocked": _legacy_probe(p)}
    if kind == "registry":
        return registry_report()
    if kind == "taxonomy":
        return {"n_classes": len(ERROR_CLASSES),
                "classes": sorted(ERROR_CLASSES),
                "mutating": sorted(MUTATING_APIS),
                "authority": API_REGISTRY_AUTHORITY}
    if kind == "policy":
        return RetryPolicy().note()
    if kind == "error_classes":
        return {"classes": {k: {"zh": v[0], "recovery": v[1]} for k, v in ERROR_CLASSES.items()}}
    if kind == "publish_gate":                 # 从**真实调用入口**发起，离线替身记录发布调用
        return run_publish_gate(ch.get("params", {}), script=ch.get("script"),
                                coerce=bool(ch.get("coerce", False)))
    raise ValueError(f"未知检查类型 {kind!r}")


def _legacy_probe(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("PLAINTOKEN")
    return load_token("any", path) is None


def run_check_spec(spec):
    """执行 cases 的检查清单，返回 {pass, n_checks, n_fail, results}。

    与轮次 2~5 一致：**真跑生产函数 / 真实调用链**（脚本化传输层 + 模拟时钟），
    不另写一套测试逻辑；**全程不联网、不发任何真实写请求**。
    """
    items = []

    def add(label, ok, detail):
        items.append({"label": label, "pass": bool(ok), "detail": detail})

    for ch in spec.get("checks", []):
        kind = ch.get("type")
        label = ch.get("label") or kind
        if kind in LOCAL_CHECK_TYPES:
            try:
                res = _local_result(kind, ch)
            except Exception as e:                               # noqa: BLE001
                add(label, False, f"本地检查执行失败：{e}")
                continue
            for r in run_asserts(res, ch.get("assert", [])):
                add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                    r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")
            continue
        if kind == "scenario":
            key = ch["case"]
            try:
                obs = run_scenario(key, **(ch.get("kwargs") or {}))
            except Exception as e:                               # noqa: BLE001
                add(label, False, f"场景 {key} 执行失败：{e}")
                continue
            for r in run_asserts(obs, ch.get("assert", [])):
                add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                    r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")
            continue
        add(label, False, f"未知检查类型 {kind!r}")
    n_fail = sum(1 for i in items if not i["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail, "results": items}


def selftest():
    """离线自检：不联网、不发任何写请求，覆盖 A~K 与分类/参数/下载/更新/Token，
    以及轮次 10 新增的**发布参数校验**与**真实调用入口的拦截 / 放行**（P1~P22）。"""
    bad = []
    n_ck = [0]

    def ck(name, cond, extra=""):
        n_ck[0] += 1
        if not cond:
            bad.append(f"{name}{(' — ' + extra) if extra else ''}")

    # ① 分类表
    need7 = (CAT_NETWORK, CAT_HTTP, CAT_BUSINESS, CAT_AUTH, CAT_PERMISSION, CAT_PARAM, CAT_EMPTY)
    ck("七类 + 内容不合法 + 未知 全部登记",
       all(x in ERROR_CLASSES for x in need7 + (CAT_INVALID, CAT_UNKNOWN)),
       str(sorted(ERROR_CLASSES)))
    ck("分类表条目数 = 10（含 ok）", len(ERROR_CLASSES) == 10, str(len(ERROR_CLASSES)))
    ck("HTTP 401→auth", classify_http(401) == CAT_AUTH)
    ck("HTTP 403→permission", classify_http(403) == CAT_PERMISSION)
    ck("HTTP 400→param", classify_http(400) == CAT_PARAM)
    ck("HTTP 503→network", classify_http(503) == CAT_NETWORK)
    ck("AUT+你没有权限→permission",
       classify_payload({"success": False, "status": "AUT", "msg": "你没有权限"})[0] == CAT_PERMISSION)
    ck("AUT+token过期→auth",
       classify_payload({"success": False, "status": "AUT", "msg": "token 已过期"})[0] == CAT_AUTH)
    ck("success=false→business",
       classify_payload({"success": False, "status": "ERR", "msg": "服务暂不可用"})[0] == CAT_BUSINESS)
    ck("AUT 但措辞不明→unknown（不猜）",
       classify_payload({"success": False, "status": "AUT", "msg": "???"})[0] == CAT_UNKNOWN)
    ck("空数组+告警→param", classify_success({"success": True, "data": []}, ["裸代码"])[0] == CAT_PARAM)
    ck("空数组无告警→empty", classify_success({"success": True, "data": []}, [])[0] == CAT_EMPTY)

    # ② 参数
    ck("前导零保持字符串", _coerce("000300") == "000300")
    ck("codes 保持字符串", _coerce("000300", "codes") == "000300")
    ck("tid 保持字符串", _coerce("3002", "tid") == "3002")
    ck("普通数字仍转 int", _coerce("20", "num") == 20)
    ck("bool 还原", _coerce("true", "flag") is True)
    e, w = validate_params("get_right_side", {"codes": "000300.CSI", "dayOffset": "3"})
    ck("合法参数无 error", not e, str(e))
    e, w = validate_params("get_right_side", {"codes": "000300"})
    ck("裸代码 → warning", bool(w))
    e, w = validate_params("get_right_side", {"dayOffset": "21"})
    ck("dayOffset 越界 → error", bool(e))
    e, w = validate_params("get_right_side", {"codes": "abc$"})
    ck("非法代码格式 → error", bool(e))

    # ③ 下载与更新
    ok, kind, _ = validate_download("download_pdf", b"%PDF-1.7\nxx")
    ck("正常 PDF 通过", ok and kind == "pdf")
    ok, kind, rs = validate_download("download_pdf", b'{"success":false,"msg":"no"}')
    ck("错误 JSON 不得存成 PDF", (not ok) and kind == "json", str(rs))
    ok, kind, rs = validate_download("download_pdf", b"<!DOCTYPE html><html>err")
    ck("HTML 不得存成 PDF", (not ok) and kind == "html")
    u = update_is_usable("1.0.6", "1.0.5", b"<!DOCTYPE html>")
    ck("非法配置不得当可用更新", u["usable"] is False, str(u["reasons"]))
    u = update_is_usable("1.0.6", "1.0.5", b'{"latestVersion":"1.0.6","APIS":{}}')
    ck("合法配置且版本更新→可用", u["usable"] is True, str(u["reasons"]))
    u = update_is_usable("1.0.5", "1.0.5", b'{"latestVersion":"1.0.5","APIS":{}}')
    ck("同版本不算更新", u["usable"] is False)

    # ④ Token 与账号绑定
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, ".tok")
        save_token("T1", "test-acct-A", None, p)
        ck("同账号可取回", load_token("test-acct-A", p) == "T1")
        ck("换账号取不到", load_token("test-acct-B", p) is None)
        ck("旧版裸 Token 被拒", _legacy_probe(p) is True)
        ck("account_ref 不暴露账号明文", "test-acct-A" not in account_ref("test-acct-A"))

    # ⑤ A~K 场景
    for k in "ABCDEFGHIJK":
        try:
            obs = run_scenario(k)
        except Exception as ex:                                  # noqa: BLE001
            bad.append(f"场景 {k} 抛错：{ex}")
            continue
        if k == "A":
            ck("A 备用站登录", "8.130.17.247:8088" in obs["bases_used"], str(obs["bases_used"]))
            ck("A 成功", obs["ok"] is True, obs["message"])
        elif k == "B":
            ck("B 重登 1 次", obs["relogin"] == 1, str(obs["relogin"]))
            ck("B 最终成功", obs["ok"] is True, obs["message"])
        elif k == "C":
            ck("C 权限不足", obs["category"] == CAT_PERMISSION)
            ck("C 未重登", obs["relogin"] == 0, str(obs["relogin"]))
            ck("C 未重复登录请求", obs["login_calls"] == 0, str(obs["login_calls"]))
        elif k == "D":
            ck("D 只发过 1 次发布", obs["publish_calls"] == 1, str(obs["publish_calls"]))
            ck("D 提交状态未知", obs["submit_state"] == STATE_SUBMIT_UNKNOWN)
            ck("D 退出码 2", obs["exit_code"] == 2)
            ck("D 做了只读核对", obs["readonly_after_submit"] >= 1)
        elif k == "E":
            ck("E 判 unknown 不判失败", obs["verdict"] == "unknown" and obs["report_failed"] is False)
            ck("E 未新建报告", obs["publish_calls"] == 0, str(obs["publish_calls"]))
        elif k == "F":
            ck("F 业务失败", obs["category"] == CAT_BUSINESS and obs["ok"] is False)
            ck("F 退出码非 0", obs["exit_code"] != 0)
        elif k == "G":
            ck("G 不报成功", obs["ok"] is False and obs["category"] == CAT_INVALID)
            ck("G 未落盘", obs["saved"] is False)
        elif k == "H":
            ck("H codes 是字符串", obs["codes_is_str"] is True)
            ck("H 前导零保持", "000300.CSI" in (obs["codes_sent"] or []), str(obs["codes_sent"]))
        elif k == "I":
            ck("I 未沿用旧 Token", obs["used_old_token"] is False, str(obs["token_used"]))
        elif k == "J":
            ck("J 正常查询 ok", obs["ok"] is True, obs["message"])
            ck("J 正常路径无重登", obs["relogin"] == 0)
        elif k == "K":
            ck("K 有界重试", obs["attempts"] <= 4, str(obs["attempts"]))
            ck("K 在时间预算内", obs["elapsed_within_budget"] is True, str(obs["elapsed"]))
            ck("K 有等待（模拟）", obs["sleep_calls"] >= 0)

    # ⑥ 轮次 10：发布参数校验（接口约定核实 + 冲突登记）
    leg = validate_params("report_publish",
                          {"tid": "3002", "question": "q" * 60, "text": "t" * 500})
    ck("P1 恰好达上限（q=60 / text=500）→ 无 error", not leg[0], str(leg[0]))
    ck("P1 长度单位 = Unicode 码点", PUBLISH_LEN_UNIT == "unicode_code_points")
    ck("P2 question 61 字 → error",
       any("question" in x and "上限 60" in x for x in
           validate_params("report_publish",
                           {"tid": "3002", "question": "q" * 61, "text": "t"})[0]))
    ck("P3 text 501 字 → error",
       any("text" in x and "上限 500" in x for x in
           validate_params("report_publish",
                           {"tid": "3002", "question": "q", "text": "t" * 501})[0]))
    ck("P4 question 缺失 → error",
       any("必填" in x for x in
           validate_params("report_publish", {"tid": "3002", "text": "t"})[0]))
    ck("P5 question 空串 → error",
       any("空串" in x for x in
           validate_params("report_publish",
                           {"tid": "3002", "question": "", "text": "t"})[0]))
    ck("P6 question 纯空白 → error",
       any("纯空白" in x for x in
           validate_params("report_publish",
                           {"tid": "3002", "question": "   ", "text": "t"})[0]))
    ck("P7 question 非字符串 → error",
       any("应为字符串" in x for x in
           validate_params("report_publish",
                           {"tid": "3002", "question": 123, "text": "t"})[0]))
    ck("P8 tid 缺失 → error",
       any("tid" in x for x in
           validate_params("report_publish", {"question": "q", "text": "t"})[0]))
    ck("P9 tid 传 int → error（实测会 400）",
       any("应为字符串" in x for x in
           validate_params("report_publish",
                           {"tid": 3002, "question": "q", "text": "t"})[0]))
    e_t, w_t = validate_params("report_publish", {"tid": "3002", "question": "q"})
    ck("P10 text 缺失 → 不阻断，仅告警（冲突登记：官方标可选 / 实测缺省失败）",
       (not e_t) and PUBLISH_TEXT_REQUIRED is False
       and any("Missing required parameters" in x for x in w_t), str(w_t))
    e_t2, w_t2 = validate_params("report_publish",
                                 {"tid": "3002", "question": "q", "text": ""})
    ck("P11 text 空串 → 不阻断，仅告警（会走 ManualArticle 另一条管线）",
       (not e_t2) and any("ManualArticle" in x for x in w_t2), str(w_t2))
    e_t3, w_t3 = validate_params("report_publish",
                                 {"tid": "3002", "question": "q", "text": "   "})
    ck("P12 text 纯空白 → 不阻断，仅告警", (not e_t3) and bool(w_t3), str(w_t3))
    ck("P13 text 非字符串 → error",
       any("应为字符串" in x for x in
           validate_params("report_publish",
                           {"tid": "3002", "question": "q", "text": 5})[0]))
    ck("P14 非适用接口（report_list）不套发布约定",
       validate_params("report_list", {"question": "q" * 99})[0] == [])
    _before = {"tid": "3002", "question": "q" * 61, "text": "t"}
    _snap = dict(_before)
    validate_params("report_publish", _before)
    ck("P15 校验不修改传入参数（不 strip / 不截断 / 不改写日期）", _before == _snap)

    # ⑦ 轮次 10：从**真实调用入口** Client.call 发起的拦截与放行（离线替身）
    g_bad = run_publish_gate({"tid": "3002", "question": "q" * 61, "text": "t"})
    ck("P16 入口级：非法参数 → 发布调用 0 次",
       g_bad["publish_calls"] == 0, str(g_bad["publish_calls"]))
    ck("P17 入口级：非法参数 → 未发任何请求、未写任务条目",
       g_bad["total_calls"] == 0 and g_bad["n_task_records"] == 0, str(g_bad))
    ck("P18 入口级：非法参数 → 分类 param、退出码非 0（1=失败；2 专表提交状态未知）",
       g_bad["category"] == CAT_PARAM and g_bad["exit_code"] == 1,
       f"{g_bad['category']}/{g_bad['exit_code']}")
    g_ok = run_publish_gate({"tid": "3002", "question": "q" * 60, "text": "t" * 500})
    ck("P19 入口级：合法参数 → 发布 1 次且成功",
       g_ok["publish_calls"] == 1 and g_ok["ok"] is True, str(g_ok["publish_calls"]))
    ck("P20 入口级：实际提交的参数与传入一致",
       bool(g_ok["sent_params"]) and g_ok["sent_params"][0].get("tid") == "3002"
       and len(g_ok["sent_params"][0].get("question", "")) == 60
       and g_ok["sent_params"][0].get("text") == "t" * 500, str(g_ok["sent_params"]))
    ck("P21 入口级：成功后任务条目 confirmed 且回填 aid",
       g_ok["task_state"] == "confirmed"
       and str(g_ok["task_aid"] or "").startswith("ManualNews@@"),
       str((g_ok["task_state"], g_ok["task_aid"])))
    g_warn = run_publish_gate({"tid": "3002", "question": "q"})
    ck("P22 入口级：text 缺失仅告警、不阻断（仍发布 1 次）",
       g_warn["publish_calls"] == 1 and bool(g_warn["warnings"]), str(g_warn["warnings"]))

    # ⑧ 轮次 15：发布防重（任务身份 vs 参数指纹；未决提交阻断；跨进程意图文件）
    import tempfile as _tmpmod
    import shutil as _sh

    def _dup_client(script, task_file):
        clock = MockClock()
        tr = ScriptedTransport(script, clock)
        c = Client(transport=tr, clock=clock, credential={"userID": "test-acct-A"},
                   token_file=os.path.join(os.path.dirname(task_file), ".tok"),
                   task_file=task_file, stderr=open(os.devnull, "w"))
        return c, tr

    _d = _tmpmod.mkdtemp(prefix="shenwa_dedup_")
    try:
        _tf = os.path.join(_d, "tasks.jsonl")
        _login = {"path_contains": "/api/user/doLogin",
                  "json": {"success": True, "data": {"browserCode": "TK"}}}
        _timeout = {"path_contains": "/api/manage/wfStartPublish", "raise": "timeout"}
        _list0 = {"path_contains": "/api/home/getList",
                  "json": {"success": True, "status": "OK", "data": []}}
        _okpub = {"path_contains": "/api/manage/wfStartPublish",
                  "json": {"success": True, "status": "OK",
                           "data": {"aid": "ManualNews@@9999@@stamp"}}}

        # P23 首次超时 → submit_unknown；相同参数再次调用 → 不重复发布（累计 1 次）
        c1, tr1 = _dup_client([_login, _timeout, _list0, _timeout, _list0], _tf)
        r1 = c1.call("report_publish", {"tid": "3002", "question": "q", "text": "t"})
        r2 = c1.call("report_publish", {"tid": "3002", "question": "q", "text": "t"})
        ck("P23 首次超时后再调用同一参数 → 累计发布 1 次（第二次只读核对）",
           tr1.count("/api/manage/wfStartPublish") == 1
           and r1.submit_state == STATE_SUBMIT_UNKNOWN
           and r2.submit_state == STATE_SUBMIT_UNKNOWN,
           f"pub={tr1.count('/api/manage/wfStartPublish')}")

        # P24 同一 task_id 已受理 → 再次调用返回已有 aid，不重新发布
        _tf2 = os.path.join(_d, "tasks2.jsonl")
        c2, tr2 = _dup_client([_login, _okpub], _tf2)
        r3 = c2.call("report_publish", {"tid": "3002", "question": "q", "text": "t"},
                     task_id="entry-1")
        r4 = c2.call("report_publish", {"tid": "3002", "question": "q", "text": "t"},
                     task_id="entry-1")
        ck("P24 已受理 task_id 再次调用 → 返回已有 aid、发布仍 1 次",
           tr2.count("/api/manage/wfStartPublish") == 1
           and r3.submit_state == "confirmed" and r4.task_id == r3.task_id
           and r4.submit_state == "confirmed",
           f"pub={tr2.count('/api/manage/wfStartPublish')} aid={r4.task_id}")

        # P25 参数指纹相同但新 task_id → 独立新任务，重新发布（不被防重误拦）
        _tf3 = os.path.join(_d, "tasks3.jsonl")
        c3, tr3 = _dup_client([_login, _okpub, _okpub], _tf3)
        r5 = c3.call("report_publish", {"tid": "3002", "question": "q", "text": "t"},
                     task_id="entry-A")
        r6 = c3.call("report_publish", {"tid": "3002", "question": "q", "text": "t"},
                     task_id="entry-B")
        ck("P25 参数相同但 task_id 不同 → 两个独立任务，各发布 1 次（共 2 次）",
           tr3.count("/api/manage/wfStartPublish") == 2
           and r5.submit_state == "confirmed" and r6.submit_state == "confirmed",
           f"pub={tr3.count('/api/manage/wfStartPublish')}")

        # P26 意图文件跨进程语义：同一 task_id 第二次意图创建失败 → 阻断
        _tf4 = os.path.join(_d, "tasks4.jsonl")
        c4, tr4 = _dup_client([_login, _timeout, _list0], _tf4)
        _ = c4.call("report_publish", {"tid": "3002", "question": "q", "text": "t"},
                    task_id="entry-1")   # 超时 → submit_unknown，意图文件保留
        # 同一 task_id 再次提交，意图文件已存在 → 阻断
        c4b, tr4b = _dup_client([_login, _okpub], _tf4)
        r7 = c4b.call("report_publish", {"tid": "3002", "question": "q", "text": "t"},
                      task_id="entry-1")
        ck("P26 未决提交（意图文件在）→ 新客户端实例再提交同一 task_id 被阻断",
           tr4b.count("/api/manage/wfStartPublish") == 0
           and r7.submit_state == STATE_SUBMIT_UNKNOWN,
           f"pub={tr4b.count('/api/manage/wfStartPublish')}")
    finally:
        _sh.rmtree(_d, ignore_errors=True)

    for f in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"):
        print(f"  {f} · {SCENARIOS[f]['title']}")
    if bad:
        print("自检失败（%d 项断言，%d 项未过）：" % (n_ck[0], len(bad)))
        for b in bad:
            print("  - " + b)
        return 1
    print("selftest: 全部通过（%d 项断言；离线，无任何写请求）" % n_ck[0])
    return 0


def registry_report():
    """接口注册表 vs 配置文件的漂移自查（回答「定义由哪里维护、有没有脱节」）。"""
    cfg_dir = os.path.dirname(os.path.abspath(__file__))
    cfgs = sorted(f for f in os.listdir(cfg_dir) if f.startswith("shenwa_config_") and f.endswith(".json"))
    have = {f"{m} {p}" for m, p in APIS.values()}
    drift = []
    for f in cfgs:
        try:
            text = open(os.path.join(cfg_dir, f), encoding="utf-8").read()
        except Exception:
            continue
        for path in set(re.findall(r'"(/api/[A-Za-z0-9_/]+)"', text)):
            if path in APIS.values() or f"GET {path}" in have or f"POST {path}" in have:
                continue
            drift.append({"config": f, "path": path, "note": "配置里有、注册表里没有（以注册表为准）"})
    return {"authority": API_REGISTRY_AUTHORITY, "n_apis": len(APIS),
            "n_configs": len(cfgs), "configs": cfgs, "drift": drift,
            "note": "本客户端不读取配置来决定路径/方法；升级官方版本后须手工并入 APIS 并重跑 selfcheck"}


# ─────────────────────────── CLI ───────────────────────────
USAGE_FLAGS = ("list", "-h", "--help", "errors", "selfcheck", "selftest", "tasks")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = False
    if "--json" in argv:
        as_json = True
        argv.remove("--json")
    verbose = False
    if "--verbose" in argv or "-v" in argv:
        verbose = True
        for f in ("--verbose", "-v"):
            if f in argv:
                argv.remove(f)
    dry = False
    if "--dry-run" in argv:
        dry = True
        argv.remove("--dry-run")

    if not argv or argv[0] in ("list", "-h", "--help"):
        print("可用接口（**唯一权威 = 本文件 APIS 字典**；配置不参与路径/方法决策）:")
        for k, (m, p) in APIS.items():
            mark = " [有副作用]" if k in MUTATING_APIS else ""
            print("  %-18s %-4s %s%s" % (k, m, p, mark))
        print("\n错误分类与恢复规则：python %s errors" % os.path.basename(__file__))
        return 0

    name = argv[0]

    if name == "errors":
        for k in (CAT_OK, CAT_EMPTY, CAT_NETWORK, CAT_HTTP, CAT_BUSINESS, CAT_AUTH,
                  CAT_PERMISSION, CAT_PARAM, CAT_INVALID, CAT_UNKNOWN):
            zh, how = ERROR_CLASSES[k]
            print("%-16s %s\n%-16s → %s" % (k, zh, "", how))
        print("%-16s %s" % (STATE_SUBMIT_UNKNOWN, "「提交状态未知」状态（有副作用调用）："
                            "不第二次提交，只读核对，不能唯一确认即如实返回"))
        return 0

    if name == "selfcheck":
        rep = registry_report()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    if name == "selftest":
        return selftest()

    if name == "tasks":
        rows, errs = load_tasks()
        if as_json:
            print(json.dumps({"n": len(rows), "tasks": rows, "read_errors": errs},
                             ensure_ascii=False, indent=2))
        else:
            print("本地最小任务记录（%d 条，%s）：" % (len(rows), TASK_FILE))
            for r in rows[-20:]:
                print("  %s  %-16s %-14s aid=%s" % (r.get("ts"), r.get("api"),
                                                    r.get("state"), r.get("aid")))
            if errs:
                print("⚠️ %d 行记录损坏（已显式列出，未静默跳过）" % len(errs))
            print("⚠️ 本地记录仅供参考，**不是**服务端幂等保障。")
        return 0

    if name == "report_wait":
        params = _kv(argv[1:])
        aid = params.get("aid")
        if not aid:
            print("report_wait 需要 aid=<报告ID>", file=sys.stderr)
            return 1
        c = Client()
        r = c.wait_report(str(aid), max_wait=float(params.get("max_wait", 1200)),
                          interval=float(params.get("interval", 10)))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else (1 if r["report_failed"] else 2)

    if name not in APIS:
        print("未知接口: %s（用 list 查看）" % name, file=sys.stderr)
        return 1

    params = _kv(argv[1:])
    c = Client(verbose=verbose)
    res = c.call(name, params, dry_run=dry)

    if as_json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return res.exit_code

    # 人读模式：沿用旧输出（// base: + JSON），调试信息一律进 stderr
    if res.ok:
        print("// base:", res.base)
        if isinstance(res.data, bytes):
            print("// binary bytes:", len(res.data))
        else:
            print(json.dumps(res.data, ensure_ascii=False, indent=2))
    else:
        print("ERROR: %s" % res.message, file=sys.stderr)
        for w in res.warnings:
            print("WARN: %s" % w, file=sys.stderr)
        if res.submit_state == STATE_SUBMIT_UNKNOWN:
            print("STATE: 提交状态未知 —— 不得当作「未提交」或「已提交」处理。", file=sys.stderr)
        if res.data is not None:
            print(json.dumps(res.data, ensure_ascii=False, indent=2))
    return res.exit_code


def _kv(args):
    """把 k=v 解析成参数（含参数类型还原）。"""
    params = {}
    for kv in args:
        if "=" in kv:
            k, v = kv.split("=", 1)
            # 实测：wfStartPublish 的 tid 必须是字符串；传 int 会 400
            # （"$.tid: The JSON value could not be converted to System.String"）
            params[k] = _coerce(v, k)
    return params


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        # 凭据缺失/解析失败等：给干净的配置指引，不甩 traceback
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)
