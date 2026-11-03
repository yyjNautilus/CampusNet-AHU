#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dr.COM(城市热点/哆点) 门户登录链路测试

用本地 mock 门户模拟真实 a79.htm 页面 + /eportal/?c=Portal&a=login 接口,
覆盖: 门户类型识别(不能被误判成深澜) / 参数解析 / 账号后缀 / 登录成功 / 密码错 / IP已在线。
不接触真实校园网。

运行: python tests/test_drcom_portal.py
"""

import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import campus_net as cn

PASS = FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS - %s %s" % (name, detail))
    else:
        FAIL += 1
        FAILED.append(name)
        print("FAIL - %s %s" % (name, detail))


# 真实门户 a79.htm 的关键片段(取自 172.16.253.3)
PAGE = """<!DOCTYPE html>
<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312">
<title>上网登录页</title>
<!--Dr.COMWebLoginID_0.htm-->
<script type="text/javascript">
sv=0;v4serip='127.0.0.1';m46=0;v46ip='10.9.8.7';ipm="ac10fd03";ss1="0c73eb9072d2";
ss2="0000";ss3="ac1a2cc2";ss4="aabbccddeeff";ss5="10.9.8.7";ss6="10.0.0.1";timet=1792327004;
</script>
<script type="text/javascript">
authtype=1;
authloginport={port};
authloginpath='/eportal/?c=ACSetting&a=Login';
authuserfield='DDDDD';
authpassfield='upass';
querypath='/eportal/?c=ACSetting&a=Query';
authsuccess='Dr.COMWebLoginID_3.htm';
carrier='{{"yys":{{"title":"服务类型","mode":"radiobutton","data":[{{"id":"1","name":"校园用户","suffix":"@xyw"}},{{"id":"2","name":"校园电信","suffix":"@dx"}}],"defaultID":"1"}}}}';
</script>
</head><body></body></html>
"""

SRUN_PAGE = """<html><head><title>登录</title></head><body>
<form action="/eportal/?c=Portal&a=login" method="post">
<input name="login_user"><input type="password" name="login_password">
</form></body></html>"""

STATE = {"password": "goodpass", "mode": "ok", "hits": []}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _send(self, body, code=200, ctype="text/html; charset=gb2312"):
        b = body.encode("gb2312", "replace") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        port = self.server.server_address[1]
        if u.path in ("/a79.htm", "/"):
            return self._send(PAGE.format(port=port))
        if u.path == "/a79_badport.htm":
            # 模拟"换个教学楼后端口变了/页面写的端口已失效"
            return self._send(PAGE.format(port=port + 7))
        if u.path == "/srun.htm":
            return self._send(SRUN_PAGE)
        if u.path == "/eportal/":
            STATE["hits"].append(q)
            if q.get("c") == "Portal" and q.get("a") == "login":
                if STATE["mode"] == "online":
                    return self._send('({"result":"0","msg":"","ret_code":2})')
                if q.get("user_password") != STATE["password"]:
                    return self._send('({"result":"0","msg":"","ret_code":1})')
                if not q.get("user_account", "").startswith("E1234"):
                    return self._send('({"result":"0","msg":"","ret_code":1})')
                return self._send('({"result":"1","msg":"\\u767b\\u5f55\\u6210\\u529f","ret_code":0})')
            return self._send('({"result":"0","msg":"unknown"})')
        if u.path == "/204":
            return self._send(b"", 204)
        return self._send("not found", 404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    login_url = base + "/a79.htm?wlanuserip=10.9.8.7&wlanacname=&wlanacip=10.0.0.1"

    client = cn.NetClient(5)
    cfg = cn.Config()
    cfg.username = "E12345678"
    cfg.password = "goodpass"

    # ---------- 1. 识别: Dr.COM 不能被误判成深澜 ----------
    info = cn.analyze_portal(client, login_url)
    check("门户识别为 drcom", info.get("type") == "drcom", info.get("type"))
    d = info.get("drcom") or {}
    check("解析出用户 IP", d.get("user_ip") == "10.9.8.7", d.get("user_ip"))
    check("解析出 AC IP", d.get("ac_ip") == "10.0.0.1", d.get("ac_ip"))
    check("解析出 MAC", d.get("mac") == "aabbccddeeff", d.get("mac"))
    check("解析出账号后缀 @xyw", d.get("suffix") == "@xyw", d.get("suffix"))
    check("接口基址带端口", d.get("base") == "http://127.0.0.1:%d/eportal/" % port, d.get("base"))
    check("候选账号先纯账号后后缀",
          cn.drcom_accounts(cfg, d) == ["E12345678", "E12345678@xyw"],
          str(cn.drcom_accounts(cfg, d)))

    # 普通 eportal 页面仍判为 srun(回归: 修 drcom 不能把 srun 弄坏)
    info2 = cn.analyze_portal(client, base + "/srun.htm")
    check("深澜页面仍识别为 srun", info2.get("type") == "srun", info2.get("type"))

    # ---------- 2. 登录成功 ----------
    STATE["hits"] = []
    ok = cn.drcom_login(client, cfg, info)
    check("Dr.COM 登录成功", ok is True)
    sent = STATE["hits"][-1] if STATE["hits"] else {}
    check("提交了 login_method", sent.get("login_method") == "1", sent.get("login_method"))
    check("首个候选用纯账号", sent.get("user_account") == "E12345678", sent.get("user_account"))
    check("密码为明文提交", sent.get("user_password") == "goodpass")
    check("未误报致命错误", not info.get("fatal"), info.get("fatal"))

    # ---------- 3. 密码错: 尝试两个候选后判定为致命 ----------
    cfg_bad = cn.Config()
    cfg_bad.username = "E12345678"
    cfg_bad.password = "wrongpass"
    info3 = cn.analyze_portal(client, login_url)
    STATE["hits"] = []
    ok = cn.drcom_login(client, cfg_bad, info3)
    check("密码错误返回 False", ok is False)
    check("密码错误标记 fatal", info3.get("fatal") == "账号或密码不对", str(info3.get("fatal")))
    check("两次候选账号都试过", len(STATE["hits"]) == 2, str(len(STATE["hits"])))

    # ---------- 4. IP 已在线(ret_code=2) 视为成功 ----------
    STATE["mode"] = "online"
    info4 = cn.analyze_portal(client, login_url)
    ok = cn.drcom_login(client, cfg, info4)
    check("ret_code=2 视为已登录", ok is True)
    STATE["mode"] = "ok"

    # ---------- 5. 手动指定后缀时优先使用 ----------
    cfg5 = cn.Config()
    cfg5.username = "E12345678"
    cfg5.password = "goodpass"
    cfg5.account_suffix = "@dx"
    info5 = cn.analyze_portal(client, login_url)
    STATE["hits"] = []
    cn.drcom_login(client, cfg5, info5)
    check("配置的后缀优先",
          (STATE["hits"][-1] if STATE["hits"] else {}).get("user_account") == "E12345678@dx",
          (STATE["hits"][-1] if STATE["hits"] else {}).get("user_account"))

    # ---------- 6. 端口/地址随接入位置变化: 声明端口失效时自动回退 ----------
    info6 = cn.analyze_portal(client, base + "/a79_badport.htm?wlanuserip=10.9.8.7&wlanacip=10.0.0.1")
    d6 = info6.get("drcom") or {}
    check("页面声明的端口作为首选基址",
          (d6.get("bases") or [""])[0].endswith(":%d/eportal/" % (port + 7)), str(d6.get("bases")))
    check("登录页自身地址在候选基址中",
          ("http://127.0.0.1:%d/eportal/" % port) in (d6.get("bases") or []), str(d6.get("bases")))
    STATE["hits"] = []
    ok6 = cn.drcom_login(client, cfg, info6)
    check("端口失效时自动换地址并登录成功", ok6 is True)

    # ---------- 7. 绝对地址的登录路径(authloginpath 写全 URL) ----------
    page_abs = PAGE.format(port=port).replace(
        "authloginpath='/eportal/?c=ACSetting&a=Login'",
        "authloginpath='http://127.0.0.1:%d/eportal/?c=ACSetting&a=Login'" % port)
    d7 = cn.parse_drcom_page(page_abs, login_url)
    check("绝对 authloginpath 被接受", d7["acsetting_path"].startswith("http://"), d7["acsetting_path"])

    srv.shutdown()
    print("-" * 50)
    print("总计 %d 项, 失败 %d 项" % (PASS + FAIL, FAIL))
    if FAILED:
        print("失败项: %s" % FAILED)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
