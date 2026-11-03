# -*- coding: utf-8 -*-
"""
本地模拟门户端到端测试
在 127.0.0.1 上模拟一个"深澜 ePortal"风格和一个"通用表单"风格的校园网门户,
完整验证 campus_net 的: 探测 -> 解析表单 -> 构造登录请求 -> 登录 -> 验证联网。
不会访问真实校园网。
"""
import base64
import http.server
import os
import sys
import tempfile
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import campus_net as cn

PORT = 18899
BASE = "http://127.0.0.1:%d" % PORT

LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>校园网登录</title></head>
<body><h2>欢迎使用校园网</h2>
<form action="/eportal/?c=Portal&amp;a=login" method="post">
<input type="hidden" name="wlan_user_ip" value="10.1.2.3">
<input type="hidden" name="wlan_ac_name" value="Campus-AC">
<input type="text" name="login_user" value="">
<input type="password" name="login_password" value="">
<input type="checkbox" name="save_user" value="1">
<input type="submit" name="btn" value="登录">
</form></body></html>"""

GENERIC_PAGE = """<html><body><h3>统一身份认证</h3>
<form action="/login.do" method="post">
<input type="hidden" name="lt" value="LT-abc123">
<input type="text" name="username">
<input type="password" name="password">
<input type="text" name="captcha" value="">
<button type="submit">login</button>
</form></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _session(self):
        ck = self.headers.get("Cookie") or ""
        for part in ck.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return part[len("session="):]
        return None

    def do_GET(self):
        p = self.path
        if p.startswith("/probe2"):
            if self._session() == "ok":
                self._send(204)
            else:
                self._send(302, b"", headers={"Location": BASE + "/generic/login.html"})
        elif p.startswith("/generic/login.html"):
            self._send(200, GENERIC_PAGE.encode("utf-8"))
        elif p.startswith("/probe"):
            if self._session() == "ok":
                self._send(204)
            else:
                self._send(302, b"", headers={
                    "Location": BASE + "/eportal/?c=Goto&a=login&wlan_user_ip=10.1.2.3&wlan_ac_name=Campus-AC"})
        elif p.startswith("/eportal"):
            self._send(200, LOGIN_PAGE.encode("utf-8"))
        else:
            self._send(404, b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = dict(urllib.parse.parse_qsl(raw))
        p = self.path
        if p.startswith("/eportal"):
            user = form.get("login_user", "")
            try:
                pwd = base64.b64decode(form.get("login_password", "")).decode("utf-8", "replace")
            except Exception:
                pwd = ""
            if user == "testuser" and pwd == "testpass123":
                self._send(200, b'{"ret_code":1,"msg":"login ok"}',
                           headers={"Set-Cookie": "session=ok; Path=/"})
            else:
                self._send(200, b'{"ret_code":0,"msg":"bad user or password"}',
                           headers={"Content-Type": "application/json"})
        elif p.startswith("/login.do"):
            if form.get("username") == "testuser" and form.get("password") == "testpass123":
                self._send(200, "<html>login success</html>".encode("utf-8"),
                           headers={"Set-Cookie": "session=ok; Path=/"})
            else:
                self._send(200, "<html>login failed</html>".encode("utf-8"))
        else:
            self._send(404, b"not found")


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s - %s %s" % ("PASS" if cond else "FAIL", name, detail))


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    probes = [BASE + "/probe"]
    probes2 = [BASE + "/probe2"]

    def make_cfg(**kw):
        c = cn.Config()
        c.campus_ssid = "TEST-CAMPUS"
        c.username = "testuser"
        c.password = "testpass123"
        c.probe_urls = ",".join(kw.pop("probes", probes))
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    # ---------- 1. 基础解析 ----------
    form = cn.parse_form(LOGIN_PAGE, BASE + "/eportal/")
    check("parse_form 解析登录页", form is not None and form["method"] == "post"
          and any(i["name"] == "login_user" for i in form["inputs"])
          and any(i["name"] == "login_password" for i in form["inputs"]))
    hidden = [i for i in form["inputs"] if i["type"] == "hidden"]
    check("parse_form 保留隐藏字段", len(hidden) == 2, str([h["name"] for h in hidden]))

    # ---------- 2. 网络检测 / 门户探测 ----------
    client = cn.NetClient()
    online, redir = cn.check_internet(client, probe_urls=probes)
    check("check_internet 未登录时判定离线", online is False and redir and "eportal" in redir, str(redir))
    info = cn.detect_portal(client, probes)
    check("detect_portal 探测到门户", info is not None)
    check("detect_portal 识别为深澜(srun)", info.get("type") == "srun")
    check("detect_portal 解析到表单", info.get("form") is not None)

    # ---------- 3. 深澜模式登录 ----------
    cfg = make_cfg()
    url, data, method = cn.build_login_request(cfg, info)
    check("srun 登录 URL 正确", "c=Portal" in url and "a=login" in url, url)
    check("srun 用户名字段", data.get("login_user") == "testuser")
    try:
        decoded = base64.b64decode(data.get("login_password", "")).decode()
    except Exception:
        decoded = "?"
    check("srun 密码 base64 编码", decoded == "testpass123", decoded)

    cfg_bad = make_cfg(password="wrongpass")
    ok_bad = cn.try_portal_login(client, cfg_bad, info)
    check("错误密码登录失败", ok_bad is False)

    ok = cn.try_portal_login(client, cfg, info)
    check("正确账号密码登录成功", ok is True)
    online2, _ = cn.check_internet(client, probe_urls=probes)
    check("登录后探测返回联网", online2 is True)

    # ---------- 4. 通用表单模式 ----------
    client2 = cn.NetClient()
    cfg2 = make_cfg(portal_type="generic", pass_encode="raw", probes=probes2)
    info2 = cn.detect_portal(client2, probes2)
    check("generic 探测到通用门户", info2 is not None and info2.get("type") == "generic", str(info2 and info2.get("type")))
    check("generic 表单包含 username/password",
          info2.get("form") and any(i["name"] == "username" for i in info2["form"]["inputs"]))
    url2, data2, m2 = cn.build_login_request(cfg2, info2)
    check("generic 登录地址", url2.endswith("/login.do"), url2)
    check("generic 用户名/密码回填", data2.get("username") == "testuser")
    check("generic 隐藏字段透传", data2.get("lt") == "LT-abc123")
    ok2 = cn.try_portal_login(client2, cfg2, info2)
    check("generic 模式登录成功", ok2 is True)

    # ---------- 5. 自定义模式 ----------
    client3 = cn.NetClient()
    cfg3 = make_cfg(portal_type="custom", login_url=BASE + "/login.do",
                    user_field="username", pass_field="password", pass_encode="raw")
    info3 = {"login_url": BASE + "/login.do", "type": "generic", "form": None, "page_url": BASE + "/login.do"}
    url3, data3, m3 = cn.build_login_request(cfg3, info3)
    check("custom 模式请求构造", url3 == BASE + "/login.do"
          and data3.get("username") == "testuser" and data3.get("password") == "testpass123")
    ok3 = cn.try_portal_login(client3, cfg3, info3)
    check("custom 模式登录成功", ok3 is True)

    # ---------- 6. 引擎完整链路(含冷却逻辑) ----------
    eng = cn.Engine(make_cfg())
    eng.last_login_try = 0.0
    eng._ensure_login("引擎测试")
    check("Engine._ensure_login 自动登录成功", eng.login_fails == 0)

    # ---------- 7. 配置读写 + DPAPI 加密 ----------
    tmp = os.path.join(tempfile.gettempdir(), "campusnet_test_config.ini")
    c = make_cfg()
    c.save(tmp)
    raw = open(tmp, encoding="utf-8-sig").read()
    check("密码以 dpapi 加密写入", "dpapi:" in raw and "testpass123" not in raw)
    c2 = cn.Config.load(tmp)
    check("配置读回密码正确", c2.password == "testpass123")
    check("配置读回 SSID 正确", c2.campus_ssid == "TEST-CAMPUS")
    os.remove(tmp)

    # ---------- 8. WiFi 配置 XML ----------
    xml = cn.build_profile_xml("TEST-CAMPUS", "wpa2psk", "wifipass")
    check("WiFi 配置 XML 生成", "WPA2PSK" in xml and "wifipass" in xml)
    xml2 = cn.build_profile_xml("TEST-CAMPUS", "open", "")
    check("开放网络 XML 生成", "open" in xml2 and "sharedKey" not in xml2)

    # ---------- 9. 引擎校外路径不崩溃 ----------
    try:
        cn.Engine(make_cfg(campus_ssid="__NO_SUCH_SSID__")).tick()
        check("Engine 校外路径无异常", True)
    except Exception as e:
        check("Engine 校外路径无异常", False, str(e))

    srv.shutdown()
    print("-" * 50)
    failed = [n for n, ok in RESULTS if not ok]
    print("总计 %d 项, 失败 %d 项" % (len(RESULTS), len(failed)))
    if failed:
        print("失败项:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    cn.init_logging(os.path.join(tempfile.gettempdir(), "campusnet_test.log"), True)
    sys.exit(main())
