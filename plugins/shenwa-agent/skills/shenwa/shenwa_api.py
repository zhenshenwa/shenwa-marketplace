#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
深挖（Shenwa）API 命令行助手
用法:
  python shenwa_api.py <api_name> [key=value ...]
  python shenwa_api.py list            # 打印所有可用接口
示例:
  python shenwa_api.py templates
  python shenwa_api.py report_list type=public num=20
  python shenwa_api.py report_detail aid=123
  python shenwa_api.py report_publish tid=3002 question="..." text="..."
  python shenwa_api.py report_status aid=123
  python shenwa_api.py download_pdf aid=123 out=/path/to.pdf
  python shenwa_api.py knowledge_search keyword="光模块" knowledgeBase=report topK=5
  python shenwa_api.py get_guaili_rates maxDays=20
  python shenwa_api.py user_info
  python shenwa_api.py latest_version

凭据配置（本脚本不内置任何账号信息，每人用自己的账号）:
  方式1 环境变量：SHENWA_USER_ID=注册手机号  SHENWA_INVITE_CODE=邀请码
  方式2 本地文件：~/.workbuddy/shenwa_credentials.json
                  {"userID": "注册手机号", "inviteCode": "邀请码"}
  也可用 SHENWA_CRED_FILE 指定其它凭据文件路径。
  凭据文件位于包外，不会被随包分发。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_PRIMARY = "https://www.shenwa.top"
BASE_FALLBACK = "http://8.130.17.247:8088"

# 凭据一律从包外读取，严禁写回本文件（本文件会随专家包一起分发）
CRED_ENV_USER = "SHENWA_USER_ID"
CRED_ENV_CODE = "SHENWA_INVITE_CODE"
CRED_FILE_ENV = "SHENWA_CRED_FILE"
CRED_FILE_DEFAULT = os.path.join(os.path.expanduser("~"), ".workbuddy", "shenwa_credentials.json")

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".shenwa_token")

# name -> (method, path)
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
    "knowledge_search": ("POST", "/api/data/knowledgeSearch"),
    "latest_version": ("GET", "/api/manage/getLatestVersion"),
}

# 实测：这些参数服务端要求 String，不能被 _coerce 转成 int（否则 400）
STRING_PARAMS = {"tid"}

# 实测：knowledge_search 的参数走 query string，不发 JSON body（发 body 会 400 keyword required）
QUERY_PARAM_APIS = {"knowledge_search"}


def load_cred():
    """解析深挖账号凭据，优先级：环境变量 > 包外本地配置文件。

    包内不含任何账号信息，每位使用者需自行提供凭据；缺少时抛出带
    配置指引的错误，而不是回退到某个内置账号。
    """
    uid = os.environ.get(CRED_ENV_USER, "").strip()
    code = os.environ.get(CRED_ENV_CODE, "").strip()
    if uid and code:
        return {"userID": uid, "inviteCode": code}

    path = os.environ.get(CRED_FILE_ENV, "").strip() or CRED_FILE_DEFAULT
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            u = str(d.get("userID", "")).strip()
            c = str(d.get("inviteCode", "")).strip()
            if u and c:
                return {"userID": u, "inviteCode": c}
        except Exception as e:
            raise RuntimeError("凭据文件解析失败 (%s): %s" % (path, e))

    raise RuntimeError(
        "未配置深挖账号凭据。任选一种方式后重试：\n"
        "  1) 设置环境变量 %s（注册手机号）与 %s（邀请码）\n"
        "  2) 创建文件 %s，内容为 {\"userID\": \"注册手机号\", \"inviteCode\": \"邀请码\"}\n"
        "  也可用 %s 指定其它凭据文件路径。"
        % (CRED_ENV_USER, CRED_ENV_CODE, CRED_FILE_DEFAULT, CRED_FILE_ENV)
    )


def _request(base, method, path, params=None, token=None, raw=False, timeout=60):
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return body if raw else json.loads(body.decode("utf-8"))


def load_token():
    if os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE, encoding="utf-8").read().strip()
        return t or None
    return None


def save_token(t):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(t)


def login(base):
    r = _request(base, "POST", "/api/user/doLogin", load_cred())
    if r.get("success") and r.get("data"):
        save_token(r["data"]["browserCode"])
        return r["data"]["browserCode"]
    raise RuntimeError("登录失败: %s" % r.get("msg"))


def call(name, params=None, timeout=60):
    """自动带站点回退 + 401 重登。返回 (结果, 实际base)。download_pdf 返回 bytes。"""
    method, path = APIS[name]
    raw = name == "download_pdf"
    params = params or {}
    out_path = params.pop("out", None) if raw else None

    token = load_token() or login(BASE_PRIMARY)
    last_err = None
    for base in (BASE_PRIMARY, BASE_FALLBACK):
        try:
            r = _request(base, method, path, params, token, raw=raw, timeout=timeout)
            if raw:
                if out_path:
                    open(out_path, "wb").write(r)
                    return {"saved": out_path, "bytes": len(r)}, base
                return r, base
            if r.get("status") == "AUT":  # Token 失效 -> 重登后重试
                token = login(base)
                r = _request(base, method, path, params, token, timeout=timeout)
            return r, base
        except urllib.error.HTTPError as e:
            # 保留服务端校验详情，便于定位参数问题（如 tid 类型错误）
            try:
                detail = e.read().decode("utf-8", "ignore")[:400]
            except Exception:
                detail = ""
            last_err = RuntimeError("HTTP %s %s | body=%s" % (e.code, e.reason, detail))
            continue
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError("全部站点均失败: %s" % last_err)


def _coerce(v):
    """CLI 传入的都是字符串，需还原成 bool/int/float，否则服务端可能返回 400。
    注意：STRING_PARAMS 中的键名不做转换（服务端要求 String）。"""
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


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("list", "-h", "--help"):
        print("可用接口:")
        for k, (m, p) in APIS.items():
            print("  %-18s %-4s %s" % (k, m, p))
        return 0
    name = sys.argv[1]
    if name not in APIS:
        print("未知接口: %s（用 list 查看）" % name)
        return 1
    params = {}
    for kv in sys.argv[2:]:
        if "=" in kv:
            k, v = kv.split("=", 1)
            # 实测：wfStartPublish 的 tid 必须是字符串；传 int 会 400
            # （"$.tid: The JSON value could not be converted to System.String"）
            params[k] = v if k in STRING_PARAMS else _coerce(v)

    try:
        res, base = call(name, params)
    except RuntimeError as e:
        print("ERROR: %s" % e)
        return 1
    print("// base:", base)
    if isinstance(res, bytes):
        print("// binary bytes:", len(res))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
