#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校园网自动连接助手 (CampusNet Auto-Login)

零第三方依赖(仅 Python 标准库)的 Windows 校园网自动登录工具。

功能:
  * 开机自动运行(通过 Windows 计划任务安装, 无需管理员权限)
  * 自动判断当前是否处于校园网环境
  * 在校园网内: 自动连接校园 WiFi, 并自动完成网页门户(Portal)登录
  * 不在校园网: 不干预任何网络, 由 Windows 默认策略自动连接

认证方式(三种, 可单独或组合使用):
  1. Web 门户登录 —— 城市热点 Dr.COM(哆点)/深澜(Srun)/锐捷/H3C 等网页认证,
                     支持 自动探测 / Dr.COM / 深澜 / 通用表单 / 自定义 五种模式
  2. PPPoE 拨号   —— 宿舍宽带场景, 通过 rasdial 自动拨号
  3. 仅连接 WiFi  —— WPA2/WPA3/开放网络, 可自动写入 WiFi 配置文件后连接

命令行:
  CampusNet.exe --setup       打开图形化配置界面
  CampusNet.exe --status      查看当前网络/门户/自启/后台实例状态
  CampusNet.exe --run         前台守护运行(控制台可见日志)
  CampusNet.exe --daemon      后台静默守护运行(无托盘图标)
  CampusNet.exe --tray        后台驻留系统托盘运行(带图标/右键菜单, 开机自启默认)
  CampusNet.exe --once        立即执行一次"检测+连接+登录"
  CampusNet.exe --install     安装开机自启(任务计划, 失败自动改开机启动文件夹)
  CampusNet.exe --uninstall   卸载开机自启(两种方式都会清除)
  CampusNet.exe --stop-all    停止所有正在运行的实例(安装/卸载程序内部调用)
  CampusNet.exe --test-login  测试一次门户登录, 打印详细信息(排障用)
  CampusNet.exe --verbose     配合以上参数输出详细日志

说明: 双击exe(无参数)永远打开配置界面; 关闭窗口时(配置完整)默认最小化到系统托盘继续运行,
      右键托盘图标可随时打开主界面或彻底退出; 开机自启和[后台运行]按钮均以托盘模式启动。
"""

from __future__ import annotations

import argparse
import base64
import configparser
import ctypes
import datetime
import gzip
import html
import http.cookiejar
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

APP_NAME = "CampusNet"
APP_TITLE = "校园网自动连接助手"
VERSION = "1.2.3"
TASK_NAME = "CampusNetAutoLogin"
# 安装包(NSIS)写入的"应用和功能"卸载项, 用于判断本程序是否已被正式安装
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CampusNet"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#  captive portal 探测地址: 已联网时返回 200/204; 被门户劫持时返回 302 到登录页
CAPTIVE_PROBES = [
    "http://wifi.vivo.com.cn/generate_204",
    "http://www.msftconnecttest.com/redirect",
    "http://captive.apple.com/hotspot-detect.html",
    "http://detectportal.firefox.com/success.txt",
]

LOGIN_PAGE_HINTS = ("eportal", "drcom", "登录", "登陆", "认证", "上网欢迎", "login", "password", "密码", "账号")

# 城市热点 Dr.COM(哆点) 门户特征: 登录页常为 a79.htm?wlanuserip=...&wlanacip=...
# 注意 Dr.COM 也用 /eportal/ 路径, 必须靠这些标记与"深澜 Srun"区分开
DRCOM_MARKERS = (
    "dr.com", "drcom", "doctorcom", "哆点", "dodot", "webloginid",
    "authloginpath", "acsetting", "authuserfield",
)
# Dr.COM 登录返回 ret_code 含义(见门户 JS): 0成功 1账号密码错 2IP已在线 3系统忙 4未知
# 5~11 为 RADIUS 侧错误(超时/失败类), 可按"稍后重试"处理
DRCOM_RET_TEXT = {
    0: "成功", 1: "账号或密码不对", 2: "该 IP 已经在线", 3: "系统忙",
    4: "未知错误", 5: "REQ_CHALLENGE 失败", 6: "REQ_CHALLENGE 超时",
    7: "认证失败", 8: "认证超时", 9: "下线失败", 10: "下线超时", 11: "其他错误",
}

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)


# ---------------------------------------------------------------------------
# 子进程环境隔离 (单文件打包的关键修复)
#
# PyInstaller 单文件模式启动后, 会把自己的解压目录通过环境变量
# (_PYI_APPLICATION_HOME_DIR / _PYI_ARCHIVE_FILE / _PYI_PARENT_PROCESS_LEVEL)
# 传给子进程, 子进程于是"借用"父进程的 _MEIxxxxx 临时目录, 不再自己解压。
#
# 本程序的两条链路都会踩到这个坑:
#   [后台运行] 配置界面 --> 托盘守护 (界面随即退出, 守护仍在跑)
#   托盘双击   托盘守护 --> 配置界面
# 父进程退出时 bootloader 会删除那个解压目录, 而借用的子进程还在用里面的
# python312.dll 等文件 -> 删不掉 -> 弹出
#   "Failed to remove temporary directory: ...\Temp\_MEIxxxxx"
# 并且留下半删状态的 _MEI 残骸(被占用的文件留下, 其余已被删), 子进程后续
# 一旦需要加载别的模块就会出错。
#
# 处理办法: 拉起子进程时清掉这些变量, 让子进程自己解压到独立的目录,
# 各用各的, 谁退出谁清理自己的。
# ---------------------------------------------------------------------------

_PYI_ENV_PREFIXES = ("_PYI_", "_MEIPASS", "_MEI")


def child_env(base=None) -> dict:
    """返回一份去掉了 PyInstaller 嵌套运行标记的环境变量副本"""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        upper = key.upper()
        if any(upper.startswith(p) for p in _PYI_ENV_PREFIXES):
            env.pop(key, None)
    return env


def _mei_dirs_in_use() -> set:
    """枚举其它进程正在使用的 _MEIxxxxx 解压目录(只读查询; 查不到的一律当作占用)"""
    used = set()
    try:
        k = ctypes.windll.kernel32
        ps = ctypes.WinDLL("psapi")
        ps.EnumProcesses.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
                                     ctypes.POINTER(ctypes.c_ulong)]
        ps.EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        ps.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_wchar_p, ctypes.c_ulong]
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        arr = (ctypes.c_ulong * 4096)()
        need = ctypes.c_ulong()
        if not ps.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
            return used
        for i in range(need.value // ctypes.sizeof(ctypes.c_ulong)):
            pid = arr[i]
            if not pid:
                continue
            h = k.OpenProcess(0x0410, False, pid)   # QUERY_INFORMATION | VM_READ
            if not h:
                continue
            try:
                mods = (ctypes.c_void_p * 1024)()
                cb = ctypes.c_ulong()
                if not ps.EnumProcessModules(h, mods, ctypes.sizeof(mods), ctypes.byref(cb)):
                    continue
                count = min(cb.value // ctypes.sizeof(ctypes.c_void_p), 1024)
                for j in range(count):
                    buf = ctypes.create_unicode_buffer(600)
                    if not ps.GetModuleFileNameExW(h, mods[j], buf, 600):
                        continue
                    parts = buf.value.split("\\")
                    for idx, part in enumerate(parts):
                        if part.upper().startswith("_MEI"):
                            used.add(os.path.normcase("\\".join(parts[:idx + 1])))
                            break
            finally:
                k.CloseHandle(h)
    except Exception:
        pass
    return used


def sweep_stale_temp_dirs(max_age_hours: int = 24) -> int:
    """清理历史遗留的 _MEIxxxxx 解压目录(只清 24 小时前、且确认没人占用的)"""
    if not getattr(sys, "frozen", False):
        return 0
    removed = 0
    try:
        base = tempfile.gettempdir()
        own = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", "") or ""))
        in_use = _mei_dirs_in_use()
        deadline = time.time() - max_age_hours * 3600
        for name in os.listdir(base):
            if not name.upper().startswith("_MEI"):
                continue
            path = os.path.join(base, name)
            try:
                key = os.path.normcase(os.path.abspath(path))
                if not os.path.isdir(path) or key == own or key in in_use:
                    continue
                if os.path.getmtime(path) > deadline:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                if not os.path.isdir(path):
                    removed += 1
            except Exception:
                continue
    except Exception:
        pass
    if removed:
        log("已清理 %d 个历史临时解压目录" % removed)
    return removed


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()
_LOG_PATH = None
_LOG_VERBOSE = False
_LOG_SINK = None  # GUI 回调


def init_logging(path: str, verbose: bool = False):
    global _LOG_PATH, _LOG_VERBOSE
    _LOG_PATH = path
    _LOG_VERBOSE = verbose
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except Exception:
        pass


def set_log_sink(fn):
    global _LOG_SINK
    _LOG_SINK = fn


def log(msg, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] [%s] %s" % (ts, level, msg)
    with _LOG_LOCK:
        if _LOG_PATH:
            try:
                with open(_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
        if _LOG_VERBOSE or level in ("ERROR", "WARN"):
            try:
                print(line)
            except Exception:
                pass
        if _LOG_SINK:
            try:
                _LOG_SINK(line)
            except Exception:
                pass


def redact(text: str, *secrets) -> str:
    """把敏感信息从日志文本中抹掉"""
    if not text:
        return text
    for s in secrets:
        if s and len(str(s)) >= 2:
            text = text.replace(str(s), "***")
    return text


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path() -> str:
    return os.path.join(app_dir(), "config.ini")


def installed_uninstaller() -> str:
    """如果本程序是由安装包装到标准位置的, 返回卸载程序路径; 否则返回空串。
    用于让界面上的[删除此程序]在已安装时走系统卸载流程(顺带清理快捷方式/注册表)。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            loc = str(winreg.QueryValueEx(k, "InstallLocation")[0])
    except Exception:
        return ""
    try:
        if os.path.normcase(os.path.abspath(loc)) != os.path.normcase(app_dir()):
            return ""
    except Exception:
        return ""
    exe = os.path.join(loc, "Uninstall.exe")
    return exe if os.path.exists(exe) else ""


def get_log_path() -> str:
    p = os.path.join(app_dir(), "campus_net.log")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8"):
            pass
        return p
    except Exception:
        base = os.path.join(os.environ.get("APPDATA", tempfile.gettempdir()), APP_NAME)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "campus_net.log")


# ---------------------------------------------------------------------------
# Windows DPAPI: 用当前用户密钥加密配置里的密码(无法被其它用户解密)
# ---------------------------------------------------------------------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _setup_crypt():
    """显式声明 CryptProtectData/CryptUnprotectData 的函数签名, 避免参数错位导致崩溃"""
    if os.name != "nt":
        return
    try:
        proto = [ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.POINTER(_DATA_BLOB),
                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DATA_BLOB)]
        for fn in ("CryptProtectData", "CryptUnprotectData"):
            f = getattr(ctypes.windll.crypt32, fn)
            f.argtypes = proto
            f.restype = ctypes.c_int
    except Exception:
        pass


_setup_crypt()


def _setup_kernel32():
    """声明后续用到的 kernel32/user32/psapi 函数签名(避免 64 位句柄被截断)"""
    global _PSAPI
    if os.name != "nt":
        return
    try:
        k = ctypes.windll.kernel32
        DWORD, BOOL, HANDLE = ctypes.c_ulong, ctypes.c_int, ctypes.c_void_p
        k.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
        k.OpenProcess.restype = HANDLE
        k.TerminateProcess.argtypes = [HANDLE, ctypes.c_uint]
        k.TerminateProcess.restype = BOOL
        k.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        k.WaitForSingleObject.restype = DWORD
        k.CloseHandle.argtypes = [HANDLE]
        k.CloseHandle.restype = BOOL
        k.LocalFree.argtypes = [HANDLE]
        k.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, ctypes.c_wchar_p]
        k.CreateMutexW.restype = HANDLE
        k.GetConsoleWindow.restype = HANDLE
        k.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, ctypes.c_wchar_p,
                                                 ctypes.POINTER(DWORD)]
        k.QueryFullProcessImageNameW.restype = BOOL
        u = ctypes.windll.user32
        u.ShowWindow.argtypes = [HANDLE, ctypes.c_int]
        u.ShowWindow.restype = BOOL
        _PSAPI = ctypes.WinDLL("psapi")
        _PSAPI.EnumProcesses.argtypes = [ctypes.POINTER(DWORD), DWORD, ctypes.POINTER(DWORD)]
        _PSAPI.EnumProcesses.restype = BOOL
    except Exception:
        pass


_setup_kernel32()


def dpapi_protect(plain: bytes) -> bytes:
    if os.name != "nt" or not plain:
        return b"raw:" + base64.b64encode(plain)
    buf = ctypes.create_string_buffer(plain, len(plain))
    blob_in = _DATA_BLOB(len(plain), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if ok and blob_out.pbData:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return b"dpapi:" + base64.b64encode(raw)
    return b"raw:" + base64.b64encode(plain)


def dpapi_unprotect(stored: bytes) -> bytes:
    if not stored:
        return b""
    if stored.startswith(b"raw:"):
        return base64.b64decode(stored[4:])
    if not stored.startswith(b"dpapi:"):
        return stored  # 明文
    raw = base64.b64decode(stored[6:])
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if ok and blob_out.pbData:
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out
    return b""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class Config:
    def __init__(self):
        # general
        self.mode = "portal"            # portal | pppoe | wifi_only
        self.campus_ssid = ""           # 校园网 WiFi 名称
        self.poll_interval = 30         # 检测间隔(秒)
        self.start_delay = 20           # 开机后首次检测的延迟(秒)
        # wifi
        self.wifi_security = "wpa2psk"  # open | wpa2psk | wpa3psk | wpapsk
        self.wifi_key = ""              # WiFi 密码(用于自动写入配置并连接)
        # portal
        self.portal_type = "auto"       # auto | srun | drcom | generic | custom
        self.probe_urls = ",".join(CAPTIVE_PROBES)
        self.login_url = ""             # 登录页地址(自动探测或手动填写)
        self.user_field = ""            # 用户名字段名(留空自动识别)
        self.pass_field = ""            # 密码字段名(留空自动识别)
        self.pass_encode = "base64"     # raw | base64 | urlencode (深澜通常 base64)
        self.account_suffix = ""        # 账号后缀(Dr.COM 服务类型, 如 @xyw; 留空=按门户默认)
        self.isp = "校园网"
        self.extra_fields = ""          # 额外表单参数, 形如 k=v,k2=v2
        # auth
        self.username = ""
        self.password = ""              # 内存中为明文; 存盘时 DPAPI 加密
        self.save_password = True
        # pppoe
        self.pppoe_enabled = False
        self.pppoe_entry = "宽带连接"
        self.pppoe_username = ""
        self.pppoe_password = ""
        # off-campus
        self.offcampus_strategy = "ignore"  # ignore | connect
        self.fallback_ssid = ""

    # ---- 读写 ----
    @classmethod
    def load(cls, path: str) -> "Config":
        c = cls()
        if not os.path.exists(path):
            return c
        cp = configparser.RawConfigParser()
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8-sig")
        except Exception as e:
            log("读取配置失败: %s" % e, "ERROR")
            return c

        def g(sec, key, default):
            try:
                return cp.get(sec, key, fallback=default)
            except Exception:
                return default

        c.mode = g("general", "mode", c.mode)
        c.campus_ssid = g("general", "campus_ssid", c.campus_ssid).strip()
        try:
            c.poll_interval = max(5, int(g("general", "poll_interval", str(c.poll_interval))))
        except Exception:
            pass
        try:
            c.start_delay = max(0, int(g("general", "start_delay", str(c.start_delay))))
        except Exception:
            pass
        c.wifi_security = g("wifi", "security", c.wifi_security)
        c.wifi_key = g("wifi", "key", c.wifi_key)
        c.portal_type = g("portal", "type", c.portal_type)
        c.probe_urls = g("portal", "probe_urls", c.probe_urls)
        c.login_url = g("portal", "login_url", c.login_url).strip()
        c.user_field = g("portal", "user_field", c.user_field).strip()
        c.pass_field = g("portal", "pass_field", c.pass_field).strip()
        c.pass_encode = g("portal", "pass_encode", c.pass_encode)
        c.account_suffix = g("portal", "account_suffix", c.account_suffix).strip()
        c.isp = g("portal", "isp", c.isp)
        c.extra_fields = g("portal", "extra_fields", c.extra_fields)
        c.username = g("auth", "username", c.username)
        c.save_password = g("auth", "save_password", "true").lower() in ("1", "true", "yes", "on")
        c.pppoe_enabled = g("pppoe", "enabled", "false").lower() in ("1", "true", "yes", "on")
        c.pppoe_entry = g("pppoe", "entry", c.pppoe_entry)
        c.pppoe_username = g("pppoe", "username", c.pppoe_username)
        c.offcampus_strategy = g("offcampus", "strategy", c.offcampus_strategy)
        c.fallback_ssid = g("offcampus", "fallback_ssid", c.fallback_ssid).strip()

        # 密码(兼容 dpapi: 前缀 / raw: 前缀 / 明文)
        pw_raw = g("auth", "password", "")
        c.password = _decode_secret(pw_raw)
        ppp_raw = g("pppoe", "password", "")
        c.pppoe_password = _decode_secret(ppp_raw)
        if pw_raw and not pw_raw.startswith(("dpapi:", "raw:")):
            log("配置中的密码为明文, 建议使用 --setup 重新保存以加密存储", "WARN")
        return c

    def save(self, path: str):
        cp = configparser.RawConfigParser()
        cp.optionxform = str
        cp["general"] = {
            "mode": self.mode,
            "campus_ssid": self.campus_ssid,
            "poll_interval": str(self.poll_interval),
            "start_delay": str(self.start_delay),
        }
        cp["wifi"] = {
            "security": self.wifi_security,
            "key": self.wifi_key,
        }
        cp["portal"] = {
            "type": self.portal_type,
            "probe_urls": self.probe_urls,
            "login_url": self.login_url,
            "user_field": self.user_field,
            "pass_field": self.pass_field,
            "pass_encode": self.pass_encode,
            "account_suffix": self.account_suffix,
            "isp": self.isp,
            "extra_fields": self.extra_fields,
        }
        cp["auth"] = {
            "username": self.username,
            "password": _encode_secret(self.password) if self.password else "",
            "save_password": "true" if self.save_password else "false",
        }
        cp["pppoe"] = {
            "enabled": "true" if self.pppoe_enabled else "false",
            "entry": self.pppoe_entry,
            "username": self.pppoe_username,
            "password": _encode_secret(self.pppoe_password) if self.pppoe_password else "",
        }
        cp["offcampus"] = {
            "strategy": self.offcampus_strategy,
            "fallback_ssid": self.fallback_ssid,
        }
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        except Exception:
            pass
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("# %s 配置文件 (v%s)\n" % (APP_TITLE, VERSION))
            f.write("# 建议使用 \"--setup\" 图形界面编辑; 各选项含义见 README.md\n\n")
            cp.write(f)
        log("配置已保存: %s" % path)

    @property
    def probe_list(self):
        return [u.strip() for u in (self.probe_urls or "").split(",") if u.strip()] or CAPTIVE_PROBES

    @property
    def complete(self) -> bool:
        """是否已具备自动运行的最低配置"""
        if not self.campus_ssid:
            return False
        if self.mode == "wifi_only":
            return True
        if self.mode == "pppoe":
            return bool(self.pppoe_username and self.pppoe_password)
        return bool(self.username and self.password)

    def summary(self) -> str:
        parts = [
            "模式=%s" % self.mode,
            "SSID=%s" % (self.campus_ssid or "未配置"),
            "门户=%s" % self.portal_type,
            "账号=%s" % (self.username or "未配置"),
            "校外策略=%s" % self.offcampus_strategy,
        ]
        return ", ".join(parts)


def _encode_secret(plain: str) -> str:
    if not plain:
        return ""
    # dpapi_protect 已自带 dpapi:/raw: 前缀, 直接使用即可
    return dpapi_protect(plain.encode("utf-8")).decode("ascii")


def _decode_secret(stored: str) -> str:
    if not stored:
        return ""
    try:
        return dpapi_unprotect(stored.encode("utf-8")).decode("utf-8", "replace")
    except Exception as e:
        log("密码解密失败: %s" % e, "ERROR")
        return ""


# ---------------------------------------------------------------------------
# 外部命令
# ---------------------------------------------------------------------------

def _run(cmd, timeout=20) -> str:
    """执行外部命令并返回解码后的输出(不抛异常)"""
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return _dec(p.stdout) + _dec(p.stderr)
    except Exception as e:
        return "ERR:%s" % e


def _dec(b) -> str:
    if not b:
        return ""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "mbcs"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# WiFi (netsh)
# ---------------------------------------------------------------------------

def wifi_current_ssid():
    """当前已连接的 SSID, 未连接返回 None"""
    out = _run(["netsh", "wlan", "show", "interfaces"])
    for line in out.splitlines():
        m = re.match(r"\s*SSID\s*(?:名称\s*)?[:：]\s*(\S.*?)\s*$", line)
        if m:
            return m.group(1)
    return None


def wifi_visible_ssids():
    """当前可见的 SSID 列表"""
    out = _run(["netsh", "wlan", "show", "networks"])
    res = []
    for line in out.splitlines():
        m = re.match(r"\s*SSID\s+\d+\s*[:：]\s*(\S.*?)\s*$", line)
        if m and m.group(1) not in res:
            res.append(m.group(1))
    return res


def wifi_profiles():
    """系统已保存的 WiFi 配置( profile 名)列表"""
    out = _run(["netsh", "wlan", "show", "profiles"])
    res = []
    for line in out.splitlines():
        m = re.match(r"\s*(?:所有用户配置文件|All User Profile)\s*[:：]\s*(\S.*?)\s*$", line)
        if m and m.group(1) not in res:
            res.append(m.group(1))
    return res


def wifi_connect(ssid: str) -> bool:
    """连接指定 SSID(profile 名默认与 SSID 相同)"""
    out = _run(["netsh", "wlan", "connect", "ssid=%s" % ssid, "name=%s" % ssid], timeout=25)
    if ("完成" in out) or ("completed" in out.lower()):
        return True
    out2 = _run(["netsh", "wlan", "connect", "name=%s" % ssid], timeout=25)
    if ("完成" in out2) or ("completed" in out2.lower()):
        return True
    log("连接 WiFi 失败: %s" % redact((out + out2).strip()[:300]), "WARN")
    return False


def build_profile_xml(ssid: str, security: str, key: str) -> str:
    sec = (security or "wpa2psk").lower()
    if sec == "open":
        auth = ("<authEncryption><authentication>open</authentication>"
                "<encryption>none</encryption><useOneX>false</useOneX></authEncryption>")
    else:
        amap = {
            "wpa2psk": ("WPA2PSK", "AES"),
            "wpa3psk": ("WPA3SAE", "AES"),
            "wpapsk": ("WPAPSK", "TKIP"),
        }
        authn, enc = amap.get(sec, ("WPA2PSK", "AES"))
        auth = ("<authEncryption><authentication>%s</authentication>"
                "<encryption>%s</encryption><useOneX>false</useOneX></authEncryption>"
                "<sharedKey><keyType>passPhrase</keyType><protected>false</protected>"
                "<keyMaterial>%s</keyMaterial></sharedKey>") % (authn, enc, xml_escape(key or ""))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
        "    <name>%s</name>\n"
        "    <SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>\n"
        "    <connectionType>ESS</connectionType>\n"
        "    <connectionMode>auto</connectionMode>\n"
        "    <MSM><security>%s</security></MSM>\n"
        "</WLANProfile>\n"
    ) % (xml_escape(ssid), xml_escape(ssid), auth)


def wifi_ensure_profile(cfg: Config) -> bool:
    """确保系统里存在校园网 SSID 的 WiFi 配置; 必要时按配置自动写入"""
    ssid = cfg.campus_ssid
    if not ssid:
        return False
    if ssid in wifi_profiles():
        return True
    if not cfg.wifi_key and cfg.wifi_security != "open":
        log("系统未保存 WiFi 配置[%s]。请先手动连接一次校园网并勾选[自动连接], "
            "或在配置中填写 WiFi 密码让本工具自动写入。" % ssid, "WARN")
        return False
    xml = build_profile_xml(ssid, cfg.wifi_security, cfg.wifi_key)
    tmp = os.path.join(tempfile.gettempdir(), "campusnet_profile_%d.xml" % abs(hash(ssid)))
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(xml)
    except Exception as e:
        log("写入临时配置失败: %s" % e, "ERROR")
        return False
    out = _run(["netsh", "wlan", "add", "profile", "filename=%s" % tmp], timeout=25)
    ok = ("添加" in out) or ("added" in out.lower())
    if ok:
        log("已自动写入 WiFi 配置: %s (%s)" % (ssid, cfg.wifi_security))
    else:
        log("写入 WiFi 配置失败: %s" % redact(out.strip()[:300], cfg.wifi_key), "WARN")
    try:
        os.remove(tmp)
    except Exception:
        pass
    return ok


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

Resp = namedtuple("Resp", ["code", "url", "body", "headers"])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向, 直接把 30x 抛成 HTTPError 以便读取 Location"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, msg, headers, fp)


class NetClient:
    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler,
        )
        self.nr_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            _NoRedirect(),
        )

    def _headers(self, referer=None):
        h = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
        }
        if referer:
            h["Referer"] = referer
        return h

    def get(self, url: str, follow: bool = True, timeout: int = None, referer: str = None) -> Resp:
        req = urllib.request.Request(url, headers=self._headers(referer))
        opener = self.opener if follow else self.nr_opener
        try:
            with opener.open(req, timeout=timeout or self.timeout) as r:
                return Resp(r.status, r.geturl(), r.read(), r.headers)
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            url_final = getattr(e, "url", None) or url
            return Resp(e.code, url_final, body, e.headers)

    def post(self, url: str, data: dict, timeout: int = 12, referer: str = None) -> Resp:
        body = urllib.parse.urlencode(data).encode("utf-8")
        h = self._headers(referer)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=h)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return Resp(r.status, r.geturl(), r.read(), r.headers)
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read()
            except Exception:
                pass
            url_final = getattr(e, "url", None) or url
            return Resp(e.code, url_final, raw, e.headers)


def decode_text(b) -> str:
    if not b:
        return ""
    if isinstance(b, str):
        return b
    # 有的门户服务器无视 Accept-Encoding 直接返回 gzip 内容, 这里兜底解压
    if b[:2] == b"\x1f\x8b":
        try:
            b = gzip.decompress(b)
        except Exception:
            pass
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


def looks_like_login_page(text: str) -> bool:
    t = (text or "").lower()
    if "eportal" in t or "drcom" in t or "dr.com" in t or "认证成功页" in t:
        return True
    hits = sum(1 for k in ("登录", "登陆", "认证", "login", "password", "密码", "账号") if k.lower() in t)
    return hits >= 2


# ---------------------------------------------------------------------------
# 网络状态检测 / 门户探测
# ---------------------------------------------------------------------------

def check_internet(client: NetClient, timeout: int = 6, probe_urls=None):
    """返回 (online: bool, redirect: str|None)"""
    for url in (probe_urls or CAPTIVE_PROBES):
        try:
            r = client.get(url, follow=False, timeout=timeout)
        except Exception:
            continue
        if r.code in (301, 302, 303, 307, 308):
            loc = ""
            try:
                loc = r.headers.get("Location") or ""
            except Exception:
                pass
            if loc:
                return False, loc
            continue
        if r.code == 204:
            return True, None
        if r.code == 200:
            body = decode_text(r.body)
            if looks_like_login_page(body):
                return False, r.url
            return True, None
    return False, None


def detect_portal(client: NetClient, probe_urls=None):
    """探测校园网登录门户; 返回信息 dict 或 None(无需登录/已在线)"""
    for url in (probe_urls or CAPTIVE_PROBES):
        try:
            r = client.get(url, follow=False, timeout=6)
        except Exception:
            continue
        if r.code in (301, 302, 303, 307, 308):
            loc = ""
            try:
                loc = r.headers.get("Location") or ""
            except Exception:
                pass
            if loc:
                return analyze_portal(client, loc)
        elif r.code == 200:
            body = decode_text(r.body)
            if looks_like_login_page(body):
                return analyze_portal(client, r.url)
            return None  # 能直接上网
        elif r.code == 204:
            return None
    return None


def analyze_portal(client: NetClient, login_url: str) -> dict:
    info = {"login_url": login_url, "type": "generic", "form": None, "page_url": login_url}
    page = ""
    try:
        r = client.get(login_url, timeout=10)
        page = decode_text(r.body)
        info["page_url"] = r.url
    except Exception as e:
        info["error"] = str(e)
    low = (login_url + " " + page).lower()
    # 城市热点 Dr.COM 同样使用 /eportal/ 路径, 必须先判定, 否则会被误当成深澜
    if any(m in low for m in DRCOM_MARKERS):
        info["type"] = "drcom"
        info["drcom"] = parse_drcom_page(page, info["page_url"] or login_url)
    elif "eportal" in low:
        info["type"] = "srun"      # 深澜/锐捷 ePortal
    elif "drcom" in low:
        info["type"] = "drcom"
        info["drcom"] = parse_drcom_page(page, info["page_url"] or login_url)
    info["form"] = parse_form(page, info["page_url"])
    return info


def _js_var(text: str, name: str, default: str = "") -> str:
    """取页面脚本里的 JS 变量值:  name='v';  / name=123;"""
    m = re.search(r"\b%s\s*=\s*['\"]?([^'\"\s;]*)['\"]?\s*;" % re.escape(name), text or "", re.I)
    return m.group(1) if m else default


def parse_drcom_page(page_html: str, page_url: str) -> dict:
    """从 Dr.COM(哆点) 门户页面解析登录所需参数

    页面里这套 哆点参数 是权威来源:
        authloginpath='/eportal/?c=ACSetting&a=Login'
        authloginport=801            authuserfield='DDDDD'  authpassfield='upass'
        v4serip='172.16.253.3'       ss4='mac'  ss5='用户IP'  ss6='AC IP'
        carrier='{"yys":{...,"defaultID":"1"}}'   -> 账号后缀(如 @xyw)
    真正提交的接口(见门户 a42.js login_portal)是 http://<域名>:801/eportal/?c=Portal&a=login
    """
    p = urllib.parse.urlparse(page_url or "")
    q = {k.lower(): v for k, v in urllib.parse.parse_qsl(p.query)}
    host = p.hostname or ""
    # 服务器地址: authloginIP(部分站点用它覆盖) > v4serip > 登录页自己的域名
    server = (_js_var(page_html, "authloginIP") or _js_var(page_html, "v4serip") or host).strip()
    # 端口: 页面里 authloginport 是权威值; 缺失/为 0 时退回登录页端口, 再退回 801
    port = (_js_var(page_html, "authloginport") or "").strip()
    if port in ("", "0"):
        port = str(p.port or "") or "801"
    scheme = "https" if str(_js_var(page_html, "enHttps") or "0").strip() == "1" else "http"
    if server.startswith("http://") or server.startswith("https://"):
        sp = urllib.parse.urlparse(server)
        scheme, server = sp.scheme or scheme, (sp.hostname or host)
        port = port if port not in ("", "0") else (str(sp.port or "") or "801")

    # 端口/地址可能随接入位置变化: 首选用页面声明的, 再退回登录页自身, 最后常见端口兜底
    bases = []

    def _add_base(h, pt):
        if not h:
            return
        b = "%s://%s:%s/eportal/" % (scheme, h, pt)
        if b not in bases:
            bases.append(b)

    _add_base(server, port)
    # 登录页自身所在的地址/端口(页面里 authloginport 写错时靠它兜底)
    _add_base(host, str(p.port or "") or "80")
    _add_base(server, "801")
    _add_base(server, "80")

    user_ip = (q.get("wlanuserip") or q.get("userip") or q.get("wlan_user_ip")
               or _js_var(page_html, "v46ip") or _js_var(page_html, "ss5") or "").strip()
    ac_ip = (q.get("wlanacip") or q.get("wlan_ac_ip") or _js_var(page_html, "ss6") or "").strip()
    ac_name = (q.get("wlanacname") or q.get("wlan_ac_name") or "").strip()
    mac = (_js_var(page_html, "ss4") or _js_var(page_html, "usermac") or "").strip()
    mac = re.sub(r"[^0-9a-fA-F]", "", mac) or "000000000000"

    # 服务类型(运营商)默认项的后缀: 校园用户@xyw / 电信@dx / 联通@lt ...
    suffix = ""
    m = re.search(r"carrier\s*=\s*'(\{.*?\})'\s*;", page_html or "", re.S)
    if m:
        try:
            yys = (json.loads(m.group(1)) or {}).get("yys") or {}
            default_id = str(yys.get("defaultID") or "1")
            for it in (yys.get("data") or []):
                if str(it.get("id")) == default_id:
                    suffix = (it.get("suffix") or "").strip()
                    break
        except Exception:
            pass
    if not suffix:
        suffix = _js_var(page_html, "accountSuffix")

    return {
        "server": server,
        "port": port,
        "scheme": scheme,
        "base": bases[0] if bases else "http://%s:801/eportal/" % server,
        "bases": bases,
        "acsetting_path": _js_var(page_html, "authloginpath") or "/eportal/?c=ACSetting&a=Login",
        "user_field": _js_var(page_html, "authuserfield") or "DDDDD",
        "pass_field": _js_var(page_html, "authpassfield") or "upass",
        "login_method": _js_var(page_html, "loginMethod") or "1",
        "user_ip": user_ip,
        "ac_ip": ac_ip,
        "ac_name": ac_name,
        "mac": mac,
        "suffix": suffix,
        "js_version": _js_var(page_html, "jsVersion") or "3.3.2",
    }


def parse_form(page_html: str, page_url: str):
    """解析登录页表单, 返回 {action, method, inputs:[{name,type,value}]}"""
    if not page_html:
        return None
    forms = re.findall(r"<form\b([^>]*)>(.*?)</form>", page_html, re.I | re.S)
    if not forms:
        return None
    chosen = None
    for attrs, inner in forms:
        if re.search(r"<input[^>]+type=[\"']?password", inner, re.I):
            chosen = (attrs, inner)
            break
    if chosen is None:
        chosen = forms[0]
    attrs, inner = chosen
    am = re.search(r"action\s*=\s*[\"']?([^\"'\s>]+)", attrs, re.I)
    mm = re.search(r"method\s*=\s*[\"']?(\w+)", attrs, re.I)
    action = urllib.parse.urljoin(page_url, html.unescape(am.group(1))) if am else page_url
    method = mm.group(1).lower() if mm else "get"
    inputs = []
    for tag in re.findall(r"<input\b([^>]*)>", inner, re.I):
        nm = re.search(r"name\s*=\s*[\"']?([^\"'\s>]+)", tag, re.I)
        if not nm:
            continue
        tm = re.search(r"type\s*=\s*[\"']?(\w+)", tag, re.I)
        typ = tm.group(1).lower() if tm else "text"
        vm = re.search(r"value\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        if not vm:
            vm = re.search(r"value\s*=\s*([^\s>]+)", tag, re.I)
        val = vm.group(1) if vm else ""
        inputs.append({"name": html.unescape(nm.group(1)), "type": typ,
                       "value": html.unescape(val)})
    return {"action": action, "method": method, "inputs": inputs}


# ---------------------------------------------------------------------------
# 门户登录
# ---------------------------------------------------------------------------

def encode_password(pwd: str, mode: str) -> str:
    if not pwd:
        return ""
    if mode == "raw":
        return pwd
    if mode == "urlencode":
        return urllib.parse.quote(pwd)
    return base64.b64encode(pwd.encode("utf-8")).decode("ascii")


def parse_extra_fields(text: str) -> dict:
    out = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


SRUN_ECHO_PARAMS = (
    "user_ip", "user_mac", "wlan_user_ip", "wlan_user_mac", "wlan_ac_ip", "wlan_ac_name",
    "nas_ip", "nas_id", "success_url", "fail_url", "login_method", "mac", "ip",
)


def build_login_request(cfg: Config, info: dict):
    """根据门户类型构造登录请求, 返回 (url, data, method)"""
    extra = parse_extra_fields(cfg.extra_fields)
    ptype = cfg.portal_type

    # ---- 自定义模式: 直接 POST 到用户指定的 URL ----
    if ptype == "custom":
        url = cfg.login_url or (info.get("form") or {}).get("action") or info.get("page_url")
        if not url:
            raise RuntimeError("自定义模式需要填写登录页地址(login_url)")
        data = {}
        if cfg.user_field:
            data[cfg.user_field] = cfg.username
        if cfg.pass_field:
            data[cfg.pass_field] = encode_password(cfg.password, cfg.pass_encode)
        data.update(extra)
        return url, data, "POST"

    # ---- 深澜 / 锐捷 ePortal ----
    if ptype == "srun" or (ptype == "auto" and info.get("type") == "srun"):
        page_url = info.get("page_url") or info.get("login_url") or ""
        p = urllib.parse.urlparse(page_url)
        base = "%s://%s" % (p.scheme or "http", p.netloc)
        q = dict(urllib.parse.parse_qsl(p.query))
        echo = {k: v for k, v in q.items() if k in SRUN_ECHO_PARAMS}
        post_url = base + "/eportal/?c=Portal&a=login"
        if echo:
            post_url += "&" + urllib.parse.urlencode(echo)
        data = {
            "login_user": cfg.username,
            "login_password": encode_password(cfg.password, cfg.pass_encode),
            "save_user": "1" if cfg.save_password else "0",
            "is_auto_login": "0",
        }
        data.update(extra)
        return post_url, data, "POST"

    # ---- 通用表单模式 ----
    form = info.get("form")
    if not form:
        raise RuntimeError("登录页未解析到表单, 请检查 login_url 或改用自定义模式")
    u_re = re.compile(cfg.user_field or r"(user|login|account|uid|username|学号|账号|id)", re.I)
    p_re = re.compile(cfg.pass_field or r"(pass|pwd|password|密码)", re.I)
    data = {}
    for it in form["inputs"]:
        n, t = it["name"], it["type"]
        if t in ("submit", "button", "image", "reset", "file"):
            continue
        if t == "hidden":
            data[n] = it["value"]
        elif p_re.search(n):
            data[n] = encode_password(cfg.password, cfg.pass_encode)
        elif u_re.search(n):
            data[n] = cfg.username
        elif n in extra:
            data[n] = extra[n]
        else:
            data[n] = it["value"]
    data.update(extra)
    if form["method"] == "get":
        url = form["action"]
        sep = "&" if "?" in url else "?"
        return url + sep + urllib.parse.urlencode(data), {}, "GET"
    return form["action"], data, "POST"


def _extract_json(text: str):
    """从 ( {...} ) / callback({...}) / 裸 JSON 中取出 dict"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _verify_online(client: NetClient, cfg: Config, rounds: int = 3) -> bool:
    for i in range(max(1, rounds)):
        time.sleep(1.5)
        try:
            ok, _ = check_internet(client, probe_urls=cfg.probe_list)
        except Exception:
            ok = False
        if ok:
            return True
    return False


def _maybe_b64_text(s: str) -> str:
    """服务端有些 msg 是 base64(如 QXV0aGVudGljYXRpb24gZmFpbA== -> Authentication fail)"""
    s = (s or "").strip()
    if len(s) >= 8 and len(s) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/=]+", s):
        try:
            out = base64.b64decode(s).decode("utf-8")
            if out and all(31 < ord(ch) < 127 or ch.isprintable() for ch in out):
                return "%s (%s)" % (s, out)
        except Exception:
            pass
    return s


def drcom_accounts(cfg: Config, d: dict):
    """Dr.COM 候选账号

    顺序很重要: 多试一个错账号就多一次认证失败, 学校侧可能有失败次数限制。
    默认先试"纯账号"(多数学校如此, 如安大实测纯学号即可);
    只有在用户显式配置了「账号后缀」时才优先带后缀。
    """
    base = (cfg.username or "").strip()
    if not base:
        return []
    if "@" in base:
        return [base]
    conf_suffix = (cfg.account_suffix or "").strip()
    page_suffix = (d.get("suffix") or "").strip()
    out = []
    if conf_suffix:
        out.append(base + conf_suffix)
        out.append(base)
    else:
        out.append(base)
        if page_suffix:
            out.append(base + page_suffix)
    return out


def _drcom_acsetting_login(client: NetClient, cfg: Config, info: dict, d: dict,
                           account: str, base: str = "") -> bool:
    """兜底: 老式 ACSetting 表单登录(返回 HTML 而非 JSON 时使用)"""
    path = d.get("acsetting_path") or "/eportal/?c=ACSetting&a=Login"
    if path.startswith(("http://", "https://")):
        url = path
    else:
        if base:
            root = base.rstrip("/").rsplit("/eportal", 1)[0]
            if not root:
                root = "%s://%s:%s" % (d.get("scheme") or "http", d.get("server") or "",
                                       d.get("port") or "801")
        else:
            root = "%s://%s:%s" % (d.get("scheme") or "http", d.get("server") or "",
                                   d.get("port") or "801")
        if not path.startswith("/"):
            path = "/" + path
        url = root + path
    params = {
        "c": "ACSetting", "a": "Login", "protocol": "http:", "hostname": d.get("server") or "",
        "iTermType": "1", "wlanuserip": d.get("user_ip") or "", "wlanacip": d.get("ac_ip") or "",
        "wlanacname": d.get("ac_name") or "", "mac": d.get("mac") or "000000000000",
        "ip": d.get("user_ip") or "", "enAdvert": "0", "queryACIP": "0",
        "loginMethod": d.get("login_method") or "1",
    }
    sep = "&" if "?" in url else "?"
    url = url + sep + urllib.parse.urlencode(params)
    uf = d.get("user_field") or "DDDDD"
    pf = d.get("pass_field") or "upass"
    data = {uf: account, pf: cfg.password, "R1": "0", "R2": "", "R3": "0", "R6": "0",
            "para": "00", "0MKKey": "123456"}
    log("ACSetting 表单登录: POST %s" % url.split("?")[0])
    try:
        r = client.post(url, data, timeout=12, referer=info.get("page_url"))
    except Exception as e:
        log("登录请求异常: %s" % e, "ERROR")
        return False
    body = decode_text(r.body)
    if "Dr.COMWebLoginID_3" in body or "认证成功" in body or "登录成功" in body:
        log("门户登录成功 (Dr.COM/ACSetting)")
        return True
    js = _extract_json(body)
    if js and str(js.get("result")).lower() in ("1", "ok", "success"):
        log("门户登录成功 (Dr.COM/ACSetting)")
        return True
    log("ACSetting 登录响应: %s" % redact(body[:300], cfg.password), "WARN")
    return False


def drcom_login(client: NetClient, cfg: Config, info: dict) -> bool:
    """城市热点 Dr.COM(哆点) 门户登录

    真正接口(与浏览器 a42.js 一致):
      GET http://<门户>:<端口>/eportal/?c=Portal&a=login&login_method=1
          &user_account=<账号+后缀>&user_password=<明文密码>
          &wlan_user_ip=..&wlan_user_mac=..&wlan_ac_ip=..&wlan_ac_name=..&jsVersion=..
      应答: ({"result":"1"|"0","msg":"...","ret_code":0..11})

    不同教学楼/宿舍区的门户地址与端口可能不同, 所以这里按
    页面声明的地址 -> 登录页自身地址 -> 801/80 兜底 的顺序逐个试,
    只有"连不上"(连接异常)才换下一个; 一旦拿到 JSON 应答就认定该地址有效。
    """
    d = info.get("drcom") or {}
    extra = parse_extra_fields(cfg.extra_fields)
    bases = []
    if extra.get("drcom_base"):
        bases = [extra["drcom_base"]]
    else:
        bases = list(d.get("bases") or [])
        if not bases and d.get("base"):
            bases = [d["base"]]
        if not bases:
            p = urllib.parse.urlparse(info.get("page_url") or info.get("login_url") or "")
            if not p.hostname:
                log("无法确定 Dr.COM 门户地址", "ERROR")
                return False
            bases = ["http://%s:%s/eportal/" % (p.hostname, p.port or 801)]
    if not cfg.password:
        log("未配置密码, 无法登录 Dr.COM 门户", "ERROR")
        info["fatal"] = "未配置密码"
        return False

    accounts = drcom_accounts(cfg, d)
    if not accounts:
        log("未配置账号, 无法登录 Dr.COM 门户", "ERROR")
        info["fatal"] = "未配置账号"
        return False

    last_rc = None
    for base in bases:
        reachable = False
        for account in accounts:
            params = {
                "c": "Portal", "a": "login", "login_method": d.get("login_method") or "1",
                "user_account": account, "user_password": cfg.password,
                "wlan_user_ip": d.get("user_ip") or "", "wlan_user_ipv6": "",
                "wlan_user_mac": d.get("mac") or "000000000000",
                "wlan_ac_ip": d.get("ac_ip") or "", "wlan_ac_name": d.get("ac_name") or "",
                "jsVersion": d.get("js_version") or "3.3.2",
            }
            url = base.rstrip("/") + "/?" + urllib.parse.urlencode(params)
            log("门户登录(Dr.COM): 账号=%s 接口=%s" % (account, url.split("?")[0]))
            try:
                r = client.get(url, timeout=12, referer=info.get("page_url"))
            except Exception as e:
                log("该地址连不通(%s), 换下一个地址" % e, "WARN")
                break                 # 换基址, 不算认证失败
            reachable = True
            body = decode_text(r.body)
            js = _extract_json(body)
            if js is None:
                log("Dr.COM 接口未返回 JSON(HTTP %s), 改试 ACSetting 表单登录" % r.code, "WARN")
                return _drcom_acsetting_login(client, cfg, info, d, account, base)

            res = str(js.get("result", "")).lower()
            try:
                rc = int(js.get("ret_code"))
            except (TypeError, ValueError):
                rc = None
            last_rc = rc
            if res in ("1", "ok", "success") or rc == 0:
                log("门户登录成功 (Dr.COM, 账号 %s)" % account)
                return True
            if rc == 2:
                log("门户提示该 IP 已在线(视为已登录), 继续验证连通性")
                return True
            text = DRCOM_RET_TEXT.get(rc, "") if rc is not None else ""
            detail = _maybe_b64_text(js.get("msg") or "")
            log("Dr.COM 返回 ret_code=%s(%s)%s" % (rc, text, (" 消息: " + detail) if detail else ""), "WARN")
            if rc == 1:
                continue              # 凭据问题: 换下一个候选账号再试
            break                     # 系统忙/超时类: 本轮不再换账号, 交给下一轮
        if reachable:
            break                     # 该地址协议正常(拿到了应答), 不再试其它端口

    if last_rc == 1:
        info["fatal"] = "账号或密码不对"
        log("账号或密码不对。请核对账号/密码; 若学校要求服务类型, 在高级设置里填账号后缀(如 @xyw)", "ERROR")
    return False


def try_portal_login(client: NetClient, cfg: Config, info: dict) -> bool:
    """执行一次门户登录并验证结果"""
    if not info:
        log("未探测到门户信息, 跳过登录", "WARN")
        return False
    ptype = info.get("type")
    use_drcom = (cfg.portal_type == "drcom") or (cfg.portal_type == "auto" and ptype == "drcom")
    if use_drcom:
        ok = drcom_login(client, cfg, info)
        if ok:
            if _verify_online(client, cfg):
                log("门户登录成功, 网络已连通")
            else:
                log("门户已接受登录, 但暂时仍无法上网(稍后自动复核)", "WARN")
        return ok

    try:
        url, data, method = build_login_request(cfg, info)
    except Exception as e:
        log("构造登录请求失败: %s" % e, "ERROR")
        return False
    log("门户登录: %s %s (%s)" % (method, url, ptype))
    try:
        if method == "GET":
            r = client.get(url, timeout=15, referer=info.get("page_url"))
        else:
            r = client.post(url, data, timeout=15, referer=info.get("page_url"))
    except Exception as e:
        log("登录请求异常: %s" % e, "ERROR")
        return False
    body = decode_text(r.body)
    log("门户响应: HTTP %s -> %s" % (r.code, r.url))
    if _LOG_VERBOSE:
        log("响应片段: " + redact(body[:600], cfg.password))
    # 有些门户需要一两秒才生效, 验证 2 次
    if _verify_online(client, cfg, rounds=2):
        log("门户登录成功, 网络已连通")
        return True
    log("门户登录后仍无法上网, 请检查: 账号密码 / 门户类型 / pass_encode / 日志", "WARN")
    return False


# ---------------------------------------------------------------------------
# PPPoE
# ---------------------------------------------------------------------------

def pppoe_dial(cfg: Config) -> bool:
    entry = cfg.pppoe_entry or "宽带连接"
    out = _run(["rasdial", entry, cfg.pppoe_username, cfg.pppoe_password], timeout=45)
    out = redact(out, cfg.pppoe_password)
    low = out.lower()
    if ("已连接" in out) or ("connected" in low) or ("successfully" in low):
        log("PPPoE 拨号成功: %s" % entry)
        return True
    log("PPPoE 拨号未成功: %s" % out.strip()[:250], "WARN")
    return False


# ---------------------------------------------------------------------------
# 核心引擎
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, cfg: Config, stop_check=None):
        self.cfg = cfg
        self.stop_check = stop_check      # 返回 True 时中止当前检测(用于快速退出)
        self.client = NetClient(timeout=8)
        self.portal_info = None
        self.portal_probed_at = 0.0
        self.login_fails = 0
        self.fatal_reason = None          # 账号密码错等"重试无意义"的原因
        self.last_login_try = 0.0
        self.last_offcampus_log = 0.0
        self.last_state_log = 0.0
        self.last_dial = 0.0

    def _stopping(self):
        try:
            return bool(self.stop_check and self.stop_check())
        except Exception:
            return False

    # ---- 一次检测+动作 ----
    def tick(self):
        cfg = self.cfg
        cur = wifi_current_ssid()
        if self._stopping():
            return
        visible = wifi_visible_ssids()
        if self._stopping():
            return
        try:
            online, redirect = check_internet(self.client, probe_urls=cfg.probe_list)
        except Exception:
            online, redirect = False, None
        self._log_state(cur, visible, online, redirect)

        campus = cfg.campus_ssid
        if not campus:
            # 未配置 SSID: 仅做门户保活
            if not online and cfg.mode == "portal":
                self._ensure_login("未配置 SSID, 检测到网络未连通")
            return

        if cur == campus:
            if online:
                self.login_fails = 0
                return
            if cfg.mode == "pppoe":
                self._dial()
            else:
                self._ensure_login("已连接[%s]但无法访问互联网" % campus)
        elif campus in visible:
            if self._stopping():
                return
            log("发现校园网[%s], 正在连接..." % campus)
            wifi_ensure_profile(cfg)
            if wifi_connect(campus):
                log("已连接[%s], 等待获取 IP..." % campus)
                time.sleep(4)
                if not self._stopping() and not check_internet(self.client, probe_urls=cfg.probe_list)[0]:
                    if cfg.mode == "pppoe":
                        self._dial()
                    else:
                        self._ensure_login("连接校园网后需要认证")
            else:
                log("连接[%s]失败。若系统未保存过该 WiFi, 请手动连一次或填写 WiFi 密码" % campus, "WARN")
        else:
            self._offcampus(cur, visible)

    def _log_state(self, cur, visible, online, redirect):
        now = time.time()
        if now - self.last_state_log < 60:
            return
        self.last_state_log = now
        extra = ""
        if redirect:
            extra = " (被劫持到 %s)" % redirect
        log("状态: 连接=%s, 可见=%d个, 联网=%s%s" % (cur or "无", len(visible), "是" if online else "否", extra))

    def _ensure_login(self, reason: str):
        now = time.time()
        if self.fatal_reason:
            # 账号/密码错这类问题重试也没用, 拉长到 30 分钟一次, 避免把账号试锁
            cooldown = 1800
        elif self.login_fails == 0:
            cooldown = 20
        else:
            cooldown = min(30 * self.login_fails, 600)
        if now - self.last_login_try < cooldown:
            return
        self.last_login_try = now
        log("尝试校园网登录 (%s)" % reason)
        info = self.portal_info
        if (info is None) or (now - self.portal_probed_at > 300) or self.login_fails >= 2:
            info = detect_portal(self.client, self.cfg.probe_list)
            self.portal_info = info
            self.portal_probed_at = now
        if not info:
            log("未探测到登录门户(可能无需登录, 或网络本身不通)")
            return
        log("门户: %s -> %s" % (info.get("type"), info.get("page_url") or info.get("login_url")))
        ok = try_portal_login(self.client, self.cfg, info)
        if ok:
            self.login_fails = 0
            self.fatal_reason = None
        else:
            self.login_fails += 1
            self.fatal_reason = info.get("fatal")
            if self.fatal_reason:
                log("登录被拒绝(%s)。30 分钟内不再重试, 请修改配置后重新保存" % self.fatal_reason, "ERROR")

    def _dial(self):
        now = time.time()
        if now - self.last_dial < 60:
            return
        self.last_dial = now
        pppoe_dial(self.cfg)

    def _offcampus(self, cur, visible):
        cfg = self.cfg
        if cfg.offcampus_strategy == "connect" and cfg.fallback_ssid:
            if cfg.fallback_ssid in visible and cur != cfg.fallback_ssid:
                log("不在校园网, 按策略连接备用网络[%s]" % cfg.fallback_ssid)
                wifi_connect(cfg.fallback_ssid)
                return
        now = time.time()
        if now - self.last_offcampus_log > 300:
            self.last_offcampus_log = now
            log("当前不在校园网环境(已连接: %s), 网络交由系统自动管理" % (cur or "无"))


# ---------------------------------------------------------------------------
# 开机自启
# ---------------------------------------------------------------------------

def _launch_target():
    """返回 (可执行文件路径, 参数列表), 供自启安装使用"""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--tray"]
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    target = pyw if os.path.exists(pyw) else sys.executable
    return target, [os.path.abspath(__file__), "--tray"]


def startup_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


STARTUP_LNK_NAME = "校园网自动连接.lnk"
STARTUP_VBS_NAME = "校园网自动连接.vbs"


def _startup_files():
    d = startup_dir()
    return [os.path.join(d, STARTUP_LNK_NAME), os.path.join(d, STARTUP_VBS_NAME)]


def autostart_method():
    """返回已安装的自启方式: '任务计划' / '开机启动文件夹' / None"""
    out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if TASK_NAME in out:
        return "任务计划"
    for f in _startup_files():
        if os.path.exists(f):
            return "开机启动文件夹"
    return None


def autostart_status() -> bool:
    return autostart_method() is not None


def _install_startup_folder() -> bool:
    """方案B: 在"开机启动文件夹"放置快捷方式(无需管理员, 不依赖任务计划服务)"""
    target, args = _launch_target()
    d = startup_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        log("无法访问开机启动文件夹: %s" % e, "ERROR")
        return False
    lnk = os.path.join(d, STARTUP_LNK_NAME)
    vbs = os.path.join(d, STARTUP_VBS_NAME)
    # 先清掉另一种格式, 避免开机时重复启动
    for f in (lnk, vbs):
        try:
            if os.path.exists(f) and f != lnk:
                os.remove(f)
        except Exception:
            pass
    ps = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='%s';"
        "$s.WorkingDirectory='%s';$s.Description='%s';$s.Save()"
    ) % (lnk, target, " ".join(args), os.path.dirname(target), APP_TITLE)
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    if os.path.exists(lnk):
        log("开机自启已安装(开机启动文件夹方式): %s" % lnk)
        return True
    # 兜底: .vbs 隐藏窗口启动
    try:
        for f in (lnk,):
            if os.path.exists(f):
                os.remove(f)
        with open(vbs, "w", encoding="gbk", errors="replace") as f:
            f.write('Set ws = CreateObject("WScript.Shell")\r\n')
            f.write('ws.Run """%s"" %s", 0, False\r\n' % (target, " ".join(args)))
        if os.path.exists(vbs):
            log("开机自启已安装(开机启动文件夹方式/vbs): %s" % vbs)
            return True
    except Exception as e:
        log("写入开机启动文件夹失败: %s" % e, "ERROR")
    return False


def install_autostart() -> bool:
    # 方案A: 任务计划(登录时触发, 无需管理员)
    target, args = _launch_target()
    tr = " ".join(['"%s"' % target] + ['"%s"' % a for a in args])
    out = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON",
                "/RL", "LIMITED", "/TR", tr, "/F"])
    if ("成功" in out) or ("SUCCESS" in out.upper()):
        log("开机自启已安装(任务计划, 登录时触发): %s" % tr)
        return True
    log("任务计划方式不可用(%s), 自动改用开机启动文件夹方式" % (out.strip()[:120] or "无输出"), "WARN")
    # 方案B: 开机启动文件夹
    return _install_startup_folder()


def uninstall_autostart() -> bool:
    ok = False
    out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if ("成功" in out) or ("SUCCESS" in out.upper()):
        log("已删除任务计划: %s" % TASK_NAME)
        ok = True
    for f in _startup_files():
        try:
            if os.path.exists(f):
                os.remove(f)
                log("已删除开机启动项: %s" % f)
                ok = True
        except Exception as e:
            log("删除开机启动项失败: %s (%s)" % (f, e), "ERROR")
    if not ok:
        log("未发现已安装的开机自启(无需卸载)")
        return True
    log("开机自启已卸载")
    return True


# ---------------------------------------------------------------------------
# 状态 / 测试命令
# ---------------------------------------------------------------------------

def cmd_status(cfg: Config, cfg_path: str):
    cur = wifi_current_ssid()
    visible = wifi_visible_ssids()
    client = NetClient(timeout=6)
    online, redirect = check_internet(client, probe_urls=cfg.probe_list)
    print("=" * 52)
    print("%s v%s  状态报告" % (APP_TITLE, VERSION))
    print("-" * 52)
    print("当前连接    : %s" % (cur or "未连接"))
    print("可见网络    : %s" % (", ".join(visible) if visible else "无"))
    line = "互联网连通  : %s" % ("是" if online else "否")
    if redirect:
        line += " (被劫持到 %s)" % redirect
    print(line)
    if cfg.campus_ssid:
        print("校园网 SSID : %s  (当前%s)" % (
            cfg.campus_ssid, "已连接" if cur == cfg.campus_ssid else
            ("在范围内" if cfg.campus_ssid in visible else "不在范围")))
    else:
        print("校园网 SSID : 未配置")
    if not online:
        info = detect_portal(client, cfg.probe_list)
        if info:
            print("登录门户    : %s -> %s" % (info.get("type"), info.get("page_url") or info.get("login_url")))
            if info.get("form"):
                print("表单字段    : %s" % ", ".join(
                    "%s(%s)" % (i["name"], i["type"]) for i in info["form"]["inputs"]))
        else:
            print("登录门户    : 未探测到")
    m = autostart_method()
    print("开机自启    : %s" % (("已安装(%s)" % m) if m else "未安装"))
    print("后台实例    : %s" % ("运行中" if daemon_running() else "未运行"))
    print("配置文件    : %s" % cfg_path)
    print("配置概要    : %s" % cfg.summary())
    print("=" * 52)


def cmd_test_login(cfg: Config):
    client = NetClient(timeout=8)
    online, redirect = check_internet(client, probe_urls=cfg.probe_list)
    print("当前联网: %s%s" % ("是" if online else "否", (" (-> %s)" % redirect) if redirect else ""))
    if online:
        print("已可访问互联网, 无需登录。")
        return
    info = detect_portal(client, cfg.probe_list)
    if not info:
        print("未探测到登录门户。请确认: 1) 已连接校园网 2) probe_urls 可访问")
        return
    print("门户类型: %s" % info.get("type"))
    print("登录页面: %s" % (info.get("page_url") or info.get("login_url")))
    if info.get("form"):
        print("表单字段: %s" % ", ".join("%s(%s)" % (i["name"], i["type"]) for i in info["form"]["inputs"]))
        print("提交地址: %s [%s]" % (info["form"]["action"], info["form"]["method"].upper()))
    ok = try_portal_login(client, cfg, info)
    print("登录结果: %s" % ("成功" if ok else "失败"))


# ---------------------------------------------------------------------------
# 系统托盘(纯 ctypes 实现 Shell_NotifyIcon, 无第三方依赖)
# ---------------------------------------------------------------------------

WM_APP = 0x8000
WM_TRAY = WM_APP + 1          # 托盘回调消息
WM_STOP_MSG = WM_APP + 2      # 自定义: 请求退出
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_COMMAND = 0x0111
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x0, 0x1, 0x2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
IDM_OPEN, IDM_EXIT = 1001, 1002
TRAY_CLASS_NAME = "CampusNetTrayWnd"
_TRAY_TIP = "校园网自动连接 - 运行中"

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.c_void_p, ctypes.c_void_p)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_ulong),
        ("uFlags", ctypes.c_ulong), ("uCallbackMessage", ctypes.c_ulong),
        ("hIcon", ctypes.c_void_p), ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_ulong), ("dwStateFlags", ctypes.c_ulong),
        ("szInfo", ctypes.c_wchar * 256), ("uVersion", ctypes.c_ulong),
        ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", ctypes.c_ulong),
        ("guidItem", ctypes.c_ubyte * 16), ("hBalloonIcon", ctypes.c_void_p),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_tray = {"hwnd": None, "icon": None, "added": False, "stop": False,
         "cfg": None, "wndproc": None}


def _setup_shell32():
    if os.name != "nt":
        return
    try:
        u = ctypes.windll.user32
        s = ctypes.windll.shell32
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        u.RegisterClassW.restype = ctypes.c_ushort
        u.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.CreateWindowExW.restype = ctypes.c_void_p
        u.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                     ctypes.c_void_p, ctypes.c_void_p]
        u.DefWindowProcW.restype = ctypes.c_long
        u.GetMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p,
                                  ctypes.c_uint, ctypes.c_uint]
        u.GetMessageW.restype = ctypes.c_int
        u.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.FindWindowW.restype = ctypes.c_void_p
        u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_void_p, ctypes.c_void_p]
        u.CreatePopupMenu.restype = ctypes.c_void_p
        u.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                  ctypes.c_void_p, ctypes.c_wchar_p]
        u.TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_void_p]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.DestroyMenu.argtypes = [ctypes.c_void_p]
        u.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.LoadImageW.restype = ctypes.c_void_p
        # 注意: ExtractIconExW 在 shell32, 不在 user32(写错会抛 AttributeError 并让下面的声明全部失效)
        s.ExtractIconExW.argtypes = [ctypes.c_wchar_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint]
        s.ExtractIconExW.restype = ctypes.c_uint
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.DestroyIcon.argtypes = [ctypes.c_void_p]
        s.Shell_NotifyIconW.argtypes = [ctypes.c_ulong, ctypes.POINTER(_NOTIFYICONDATAW)]
        s.Shell_NotifyIconW.restype = ctypes.c_int
    except Exception:
        pass


_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64)
_ICON_C1 = (76, 141, 255)        # 渐变顶部(Fluent 蓝)
_ICON_C2 = (22, 78, 208)         # 渐变底部


def _render_icon(size: int):
    """渲染 size×size 的 RGBA 图标(行优先, 上到下)

    与 build_tools/make_icon.py 同一套画法: 圆角方形蓝紫渐变底 + 白色 WiFi 弧,
    按尺寸分级简化(16~20px 一根粗弧 / 24~40px 两根 / ≥64px 三根), 4x 超采样抗锯齿。
    """
    ss = 4
    M = size * ss
    inv = 1.0 / M
    inset, radius = 0.045, 0.215
    lo, hi = inset, 1.0 - inset
    cx, cy = 0.5, 0.5
    half = (hi - lo) / 2.0
    apex_y = 0.735
    if size >= 64:
        dot_r, radii, widths, cone = 0.060, [0.135, 0.243, 0.351], [0.062, 0.068, 0.074], 55.0
    elif size >= 24:
        dot_r, radii, widths, cone = 0.068, [0.205, 0.392], [0.086, 0.096], 49.0
    else:
        dot_r, radii, widths, cone = 0.108, [0.318], [0.130], 47.0
    caps = [(cx + r * math.sin(math.radians(cone)), apex_y - r * math.cos(math.radians(cone)), w)
            for r, w in zip(radii, widths)]
    caps += [(cx - r * math.sin(math.radians(cone)), apex_y - r * math.cos(math.radians(cone)), w)
             for r, w in zip(radii, widths)]

    buf = [[0.0, 0.0, 0.0, 0.0] for _ in range(M * M)]
    for py in range(M):
        y = (py + 0.5) * inv
        for px in range(M):
            x = (px + 0.5) * inv
            qx = abs(x - cx) - (half - radius)
            qy = abs(y - cy) - (half - radius)
            dx, dy = max(qx, 0.0), max(qy, 0.0)
            if math.hypot(dx, dy) + min(max(qx, qy), 0.0) - radius > 0.0:
                continue                       # 圆角外: 全透明
            t = min(max(x * 0.45 + y * 0.85, 0.0), 1.0)
            r = _ICON_C1[0] + (_ICON_C2[0] - _ICON_C1[0]) * t
            g = _ICON_C1[1] + (_ICON_C2[1] - _ICON_C1[1]) * t
            b = _ICON_C1[2] + (_ICON_C2[2] - _ICON_C1[2]) * t
            glare = max(0.0, 1.0 - math.hypot(x - 0.22, y - 0.16) / 0.85)
            k = 0.16 * glare * glare
            r += (255 - r) * k
            g += (255 - g) * k
            b += (255 - b) * k
            wxy, wyy = x - cx, y - apex_y
            dist = math.hypot(wxy, wyy)
            white = 0.0
            if dist <= dot_r:
                white = 1.0
            elif wyy < 0.0:                    # 只画上半部分, 形成"向上张开的弧"
                if math.degrees(math.atan2(abs(wxy), -wyy)) <= cone:
                    for rr, ww in zip(radii, widths):
                        if abs(dist - rr) <= ww / 2.0:
                            white = 1.0
                            break
                if white == 0.0:               # 弧端半圆端帽, 避免一刀切
                    for ex, ey, ew in caps:
                        if (x - ex) ** 2 + (y - ey) ** 2 <= (ew / 2.0) ** 2:
                            white = 1.0
                            break
            if white > 0.0:
                r += (255 - r) * white
                g += (255 - g) * white
                b += (255 - b) * white
            buf[py * M + px] = [r, g, b, 255.0]

    out = []
    n = ss * ss
    for oy in range(size):
        for ox in range(size):
            sr = sg = sb = sa = 0.0
            for yy in range(ss):
                base = (oy * ss + yy) * M + ox * ss
                for xx in range(ss):
                    q = buf[base + xx]
                    sr += q[0]; sg += q[1]; sb += q[2]; sa += q[3]
            a = sa / n
            if a <= 0.5:
                out.append((0, 0, 0, 0))
            else:
                out.append((int(sr / n + 0.5), int(sg / n + 0.5), int(sb / n + 0.5), int(a + 0.5)))
    return out


def _make_icon_bytes(sizes=None) -> bytes:
    """生成 32bpp 多尺寸 ICO(纯标准库, 无需 PIL)

    格式要点(踩过两次坑, 别再改坏):
      1. DIB 头必须**正好 40 字节**, 多写字节会让像素整体错位 -> 图标只剩几个杂点;
      2. 像素顺序是 **BGRA**(小端), 按 RGB 写会把蓝色渲染成橙色;
      3. 每行自下而上, AND 掩码每行按 4 字节对齐。
    """
    sizes = tuple(sizes or _ICON_SIZES)
    entries = []
    for s in sizes:
        pixels = _render_icon(s)
        hdr = (b"\x28\x00\x00\x00"
               + s.to_bytes(4, "little") + (s * 2).to_bytes(4, "little")
               + b"\x01\x00" + b"\x20\x00" + b"\x00" * 24)
        body = bytearray()
        for y in range(s - 1, -1, -1):
            for x in range(s):
                r, g, b, a = pixels[y * s + x]
                body += bytes((b, g, r, a))
        body += bytes((((s + 31) // 32) * 4) * s)
        entries.append((s, hdr + bytes(body)))

    head = b"\x00\x00\x01\x00" + len(entries).to_bytes(2, "little")
    offset = 6 + 16 * len(entries)
    dirs = b""
    for s, data in entries:
        dirs += (bytes((s if s < 256 else 0, s if s < 256 else 0, 0, 0, 1, 0, 32, 0))
                 + len(data).to_bytes(4, "little") + offset.to_bytes(4, "little"))
        offset += len(data)
    return head + dirs + b"".join(d for _, d in entries)


def write_icon_file(path: str) -> str:
    """把内置图标写到文件(供 GUI / 托盘 / NSIS 使用)"""
    try:
        with open(path, "wb") as f:
            f.write(_make_icon_bytes())
    except Exception as e:
        log("写入图标文件失败: %s" % e, "WARN")
    return path


def _tray_icon_from_exe():
    """从 exe 内嵌资源抽小图标(与桌面快捷方式显示的一致)"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    try:
        s = ctypes.windll.shell32
        big = ctypes.c_void_p()
        small = ctypes.c_void_p()
        n = s.ExtractIconExW(sys.executable, 0, ctypes.byref(big),
                             ctypes.byref(small), 1)
        if n and n > 0:
            if small.value:
                return small.value
            if big.value:
                return big.value
    except Exception as e:
        log("抽取 exe 图标失败: %s" % e, "WARN")
    return None


def _load_tray_icon():
    """托盘图标: exe 内嵌图标 -> 程序目录 app_icon.ico -> 内置生成(版本化临时文件)"""
    if os.name != "nt":
        return None
    u = ctypes.windll.user32
    h = _tray_icon_from_exe()
    if h:
        return h
    try:
        ico = os.path.join(app_dir(), "app_icon.ico")
        if os.path.exists(ico):
            cx = u.GetSystemMetrics(49) or 16          # SM_CXSMICON
            h = u.LoadImageW(None, ico, 1, cx, cx, 0x00000010)   # IMAGE_ICON | LR_LOADFROMFILE
            if h:
                return h
    except Exception as e:
        log("加载 app_icon.ico 失败: %s" % e, "WARN")
    try:
        # 文件名带版本号: 避免命中旧的临时图标缓存(早期版本生成过坏图标)
        path = os.path.join(tempfile.gettempdir(), "campusnet_tray_%s.ico" % VERSION.replace(".", ""))
        cx = u.GetSystemMetrics(49) or 16
        h = u.LoadImageW(None, path, 1, cx, cx, 0x00000010)
        if h:
            return h
        with open(path, "wb") as f:
            f.write(_make_icon_bytes((16, 24, 32)))
        h = u.LoadImageW(None, path, 1, cx, cx, 0x00000010)
        return h or None
    except Exception as e:
        log("加载托盘图标失败: %s" % e, "WARN")
        return None


def _tray_notify(hwnd, hicon, tip, balloon=False):
    nid = _NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | (NIF_INFO if balloon else 0)
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = hicon
    nid.szTip = tip[:127]
    if balloon:
        nid.szInfo = "已启动并驻留托盘, 右键图标可打开主界面或退出"
        nid.szInfoTitle = APP_TITLE
        nid.dwInfoFlags = NIIF_INFO
    return ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))


def _tray_remove(hwnd):
    if not _tray["added"]:
        return
    nid = _NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd = hwnd
    nid.uID = 1
    try:
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    except Exception:
        pass
    _tray["added"] = False


def _open_gui_process():
    """拉起主界面进程"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = [pyw if os.path.exists(pyw) else sys.executable, os.path.abspath(__file__)]
    try:
        subprocess.Popen(cmd, cwd=app_dir(), close_fds=True,
                         env=child_env(),
                         creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
    except Exception as e:
        log("打开主界面失败: %s" % e, "ERROR")


def _tray_quit():
    _tray["stop"] = True
    ctypes.windll.user32.PostQuitMessage(0)


def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_TRAY:
        if lparam == WM_RBUTTONUP:
            _show_tray_menu(hwnd)
        elif lparam == WM_LBUTTONDBLCLK:
            _open_gui_process()
        return 0
    if msg == WM_STOP_MSG:
        _tray_quit()
        return 0
    if msg == WM_COMMAND:
        cmd = (wparam or 0) & 0xFFFF
        if cmd == IDM_OPEN:
            _open_gui_process()
        elif cmd == IDM_EXIT:
            _tray_quit()
        return 0
    return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _show_tray_menu(hwnd):
    u = ctypes.windll.user32
    hmenu = u.CreatePopupMenu()
    try:
        u.AppendMenuW(hmenu, MF_STRING, IDM_OPEN, "打开主界面")
        u.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        running = "暂停自动登录(退出托盘)" if _tray["cfg"] else "退出"
        u.AppendMenuW(hmenu, MF_STRING, IDM_EXIT, running)
        pt = _POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(hwnd)
        cmd = u.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                               pt.x, pt.y, 0, hwnd, None)
        if cmd == IDM_OPEN:
            _open_gui_process()
        elif cmd == IDM_EXIT:
            _tray_quit()
    finally:
        u.DestroyMenu(hmenu)


def _tray_message_loop():
    u = ctypes.windll.user32
    msg = _MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


def stop_tray_process(timeout: float = 25.0) -> bool:
    """优雅停止托盘实例: 先发窗口消息让其自行清理图标并退出(最多等 timeout 秒), 失败再按 pid 强杀"""
    _setup_shell32()
    stopped = False
    if os.name == "nt":
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(TRAY_CLASS_NAME, None)
        if hwnd:
            u.PostMessageW(hwnd, WM_STOP_MSG, None, None)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not daemon_running():
                    stopped = True
                    break
                time.sleep(0.2)
    if not stopped:
        stopped = stop_daemon()
    else:
        remove_daemon_pid()
        log("托盘实例已优雅退出")
    return stopped


def _tray_thread(cfg):
    """托盘线程: 窗口/图标/消息循环必须在同一线程(Win32 规则: 窗口消息只进创建线程的队列)"""
    u = ctypes.windll.user32
    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
    wc = _WNDCLASSW()
    _tray["wndproc"] = _WNDPROC(_wnd_proc)     # 防 GC
    wc.lpfnWndProc = ctypes.cast(_tray["wndproc"], ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = TRAY_CLASS_NAME
    try:
        u.RegisterClassW(ctypes.byref(wc))
    except Exception as e:
        log("注册托盘窗口类失败: %s" % e, "ERROR")
        _tray["stop"] = True
        return
    hwnd = u.CreateWindowExW(0, TRAY_CLASS_NAME, APP_TITLE, 0x80000000,  # WS_POPUP
                             0, 0, 0, 0, -3, None, hinst, None)          # HWND_MESSAGE
    if not hwnd:
        log("创建托盘窗口失败")
        _tray["stop"] = True
        return
    _tray["hwnd"] = hwnd
    hicon = _load_tray_icon()
    if hicon:
        _tray["icon"] = hicon
        _tray_notify(hwnd, hicon, _TRAY_TIP, balloon=True)
        _tray["added"] = True
        log("托盘图标已添加(右下角), 右键图标可打开主界面或退出")
    else:
        log("未能加载图标, 仅后台运行(无托盘图标)", "WARN")
    _tray_message_loop()      # 阻塞直到收到退出(PostQuitMessage)
    _tray["stop"] = True      # 通知主线程收尾


def run_tray(cfg: Config) -> int:
    """托盘驻留模式: 图标 + 引擎循环, 关掉主界面后由它接管"""
    if os.name != "nt":
        log("托盘模式仅支持 Windows")
        return 1
    _setup_shell32()
    if not acquire_daemon_mutex():
        log("已有后台实例在运行, 托盘不会重复启动")
        return 0
    write_daemon_pid()
    _tray["cfg"] = cfg

    threading.Thread(target=_tray_thread, args=(cfg,), daemon=True).start()
    log("进入托盘守护: %s; 每 %ds 检测一次" % (cfg.summary(), cfg.poll_interval))
    try:
        time.sleep(min(cfg.start_delay, 5))
        engine = Engine(cfg, stop_check=lambda: _tray["stop"])
        while not _tray["stop"]:
            try:
                engine.tick()
            except Exception:
                log(traceback.format_exc(), "ERROR")
            for _ in range(cfg.poll_interval):
                if _tray["stop"]:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _tray_remove(_tray["hwnd"])
        remove_daemon_pid()
        log("托盘已退出")
    return 0


_setup_shell32()  # 模块加载即声明签名, 供 GUI 进程的 stop_tray_process 使用


# ---------------------------------------------------------------------------
# 后台实例管理(单实例/pid文件/清理僵尸进程)
# ---------------------------------------------------------------------------

_MUTEX_HANDLE = None
_PSAPI = None


def daemon_pid_file() -> str:
    return os.path.join(app_dir(), "campus_net.pid")


def acquire_daemon_mutex() -> bool:
    """保证后台实例唯一; 返回 False 表示已有后台实例在运行"""
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    h = ctypes.windll.kernel32.CreateMutexW(None, False, "CampusNetDaemonMutex")
    if not h:
        return True  # 创建失败则不阻拦, 避免程序不可用
    if ctypes.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(h)
        return False
    _MUTEX_HANDLE = h  # 句柄保活, 进程退出才释放
    return True


def write_daemon_pid():
    try:
        with open(daemon_pid_file(), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def remove_daemon_pid():
    path = daemon_pid_file()
    for _ in range(3):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except Exception:
            time.sleep(0.3)  # 刚杀掉的进程可能还没释放文件句柄, 重试
    log("删除 pid 文件失败(不影响使用): %s" % path, "WARN")


def _pid_alive(pid: int) -> bool:
    """精确判断进程是否在运行(已退出未回收的僵尸进程会判为不在运行)"""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    SYNCHRONIZE = 0x00100000
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    r = ctypes.windll.kernel32.WaitForSingleObject(h, 0)  # 0=已退出, 0x102=仍在运行
    ctypes.windll.kernel32.CloseHandle(h)
    return r == 0x102


def daemon_running() -> bool:
    return _pid_alive(_read_daemon_pid())


def _read_daemon_pid():
    try:
        return int(open(daemon_pid_file(), encoding="utf-8").read().strip())
    except Exception:
        return 0


def stop_daemon() -> bool:
    """停止后台实例: 先按 pid 文件结束, 再兜底清理无记录的僵尸实例"""
    killed = False
    pid = _read_daemon_pid()
    if pid and _pid_alive(pid):
        # PROCESS_TERMINATE | PROCESS_SYNCHRONIZE
        h = ctypes.windll.kernel32.OpenProcess(0x00000001 | 0x00100000, False, pid)
        if h:
            ctypes.windll.kernel32.TerminateProcess(h, 0)
            ctypes.windll.kernel32.WaitForSingleObject(h, 3000)  # 等进程真正退出再删文件
            ctypes.windll.kernel32.CloseHandle(h)
            killed = True
            time.sleep(0.2)
    remove_daemon_pid()
    try:
        n = kill_other_campusnet()
        if n:
            killed = True
            log("清理了 %d 个残留实例" % n)
    except Exception as e:
        log("清理残留实例时出错: %s" % e, "ERROR")
    if killed:
        log("后台运行已停止")
    return killed


def kill_other_campusnet() -> int:
    """结束除自身外的所有 CampusNet.exe 进程(清理旧版僵尸后台实例)"""
    if os.name != "nt":
        return 0
    me = os.getpid()
    psapi = _PSAPI if _PSAPI is not None else ctypes.WinDLL("psapi")
    k = ctypes.windll.kernel32
    arr = (ctypes.c_ulong * 4096)()
    need = ctypes.c_ulong()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
        return 0
    killed = 0
    for i in range(need.value // ctypes.sizeof(ctypes.c_ulong)):
        pid = arr[i]
        if not pid or pid == me:
            continue
        h = k.OpenProcess(0x00000400, False, pid)  # PROCESS_QUERY_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                if os.path.basename(buf.value).lower() == "campusnet.exe":
                    th = k.OpenProcess(0x00000001, False, pid)  # PROCESS_TERMINATE
                    if th:
                        k.TerminateProcess(th, 0)
                        k.CloseHandle(th)
                        killed += 1
        finally:
            k.CloseHandle(h)
    return killed


def start_daemon_process() -> bool:
    """以脱离进程方式启动后台实例(托盘驻留模式)"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--tray"]
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        py = pyw if os.path.exists(pyw) else sys.executable
        cmd = [py, os.path.abspath(__file__), "--tray"]
    try:
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(cmd, cwd=app_dir(), close_fds=True,
                         env=child_env(),
                         creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
        return True
    except Exception as e:
        log("启动后台实例失败: %s" % e, "ERROR")
        return False


# ---------------------------------------------------------------------------
# 图形化配置界面
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 界面视觉风格(零依赖: clam 主题深度定制 + Win10/11 标题栏配色)
# ---------------------------------------------------------------------------

_UI = {
    "bg": "#f3f5f9",        # 页面底
    "card": "#ffffff",      # 卡片
    "primary": "#2563eb",   # 主蓝
    "text": "#1f2937",
    "muted": "#6b7280",
    "border": "#d7dce5",
    "font": ("Microsoft YaHei", 9),
    "font_s": ("Microsoft YaHei", 8),
}


def _apply_modern_style(root):
    """统一视觉风格: 主题/配色/字体/控件样式/Win11 标题栏"""
    try:
        from tkinter import ttk
        style = ttk.Style(root)
        avail = style.theme_names()
        for t in ("clam", "vista", "xpnative"):
            if t in avail:
                style.theme_use(t)
                break
        BG, CARD, PRIMARY = _UI["bg"], _UI["card"], _UI["primary"]
        TEXT, MUTED, BORDER = _UI["text"], _UI["muted"], _UI["border"]
        F, FS = _UI["font"], _UI["font_s"]
        root.configure(bg=BG)
        style.configure(".", font=F, background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT, font=F)
        style.configure("Title.TLabel", background=BG, foreground=PRIMARY,
                        font=("Microsoft YaHei", 12, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=FS)
        style.configure("Card.TLabel", background=CARD, foreground=MUTED, font=FS)
        style.configure("Section.TLabelframe", background=BG, bordercolor=BORDER,
                        relief="solid", borderwidth=1)
        style.configure("Section.TLabelframe.Label", background=BG, foreground=PRIMARY,
                        font=("Microsoft YaHei", 9, "bold"))
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        insertcolor=TEXT, padding=(4, 3), font=F)
        style.map("TEntry", bordercolor=[("focus", PRIMARY)],
                  fieldbackground=[("focus", CARD)])
        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, arrowcolor=MUTED, padding=(3, 2), font=F)
        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=F,
                        focuscolor=BG, indicatorcolor=CARD, indicatorrelief="flat")
        style.configure("TRadiobutton", background=BG, foreground=TEXT, font=F)
        style.configure("TButton", background=CARD, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=(12, 4),
                        font=F, focuscolor=PRIMARY, anchor="center")
        style.map("TButton",
                  background=[("active", "#eef2f8"), ("pressed", "#e2e8f2"),
                              ("disabled", BG)],
                  bordercolor=[("focus", PRIMARY)])
        style.configure("Primary.TButton", background=PRIMARY, foreground="#ffffff",
                        bordercolor=PRIMARY, lightcolor=PRIMARY, darkcolor=PRIMARY,
                        padding=(16, 5), font=("Microsoft YaHei", 9, "bold"))
        style.map("Primary.TButton",
                  background=[("active", "#1d4ed8"), ("pressed", "#1e40af"),
                              ("disabled", "#a8c0f2")],
                  foreground=[("disabled", "#eef3fd")])
        style.configure("Danger.TButton", background="#ffffff", foreground="#dc2626",
                        bordercolor="#f3c2c2", lightcolor="#f3c2c2",
                        darkcolor="#f3c2c2", padding=(12, 4), font=F)
        style.map("Danger.TButton",
                  background=[("active", "#fef2f2"), ("pressed", "#fee2e2")],
                  bordercolor=[("active", "#dc2626")])
        style.configure("Vertical.TScrollbar", background="#c3cbd8", troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED)
        style.configure("Horizontal.TScrollbar", background="#c3cbd8", troughcolor=BG)
        style.configure("TProgressbar", background=PRIMARY, troughcolor="#e5eaf3",
                        bordercolor=BG, lightcolor=PRIMARY, darkcolor=PRIMARY)
    except Exception:
        pass
    # Win10/11 标题栏跟随主色(失败静默, 不影响旧系统)
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        bg = int(_UI["bg"][5:7], 16), int(_UI["bg"][3:5], 16), int(_UI["bg"][1:3], 16)
        bd = int(_UI["border"][5:7], 16), int(_UI["border"][3:5], 16), int(_UI["border"][1:3], 16)
        cap = (bg[2] << 16) | (bg[1] << 8) | bg[0]     # COLORREF = 0x00BBGGRR
        brd = (bd[2] << 16) | (bd[1] << 8) | bd[0]
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ctypes.c_int(cap)), 4)  # CAPTION_COLOR
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ctypes.c_int(brd)), 4)  # BORDER_COLOR
    except Exception:
        pass


def _icon_path_for_gui() -> str:
    """把内置图标写临时文件供 iconbitmap 使用(文件名带版本, 避免拿到旧版本生成的坏图标)"""
    path = os.path.join(tempfile.gettempdir(), "campusnet_app_%s.ico" % VERSION.replace(".", ""))
    try:
        write_icon_file(path)
    except Exception:
        pass
    return path


def run_setup_gui(cfg: Config, cfg_path: str):
    """精简配置界面: 主界面只放 3 个必填项, 高级选项折叠隐藏"""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception as e:
        log("无法启动图形界面(需要 tkinter): %s" % e, "ERROR")
        log("请使用命令行参数, 或直接编辑配置文件: %s" % cfg_path)
        return

    root = tk.Tk()
    root.title("%s  v%s" % (APP_TITLE, VERSION))
    root.geometry("560x540")
    root.minsize(520, 500)
    _apply_modern_style(root)
    try:
        root.iconbitmap(_icon_path_for_gui())
    except Exception:
        pass

    # ================= 滚动容器(高级选项内容超高时可滚动查看) =================
    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)
    vsb = ttk.Scrollbar(container, orient="vertical")
    canvas = tk.Canvas(container, highlightthickness=0, borderwidth=0,
                       yscrollcommand=vsb.set)
    vsb.configure(command=canvas.yview)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    frm = ttk.Frame(canvas, padding=12)
    _cwin = canvas.create_window((0, 0), window=frm, anchor="nw")

    def _on_frm_configure(_e):
        # 内容高度变化时更新滚动范围
        canvas.configure(scrollregion=canvas.bbox("all"))
    frm.bind("<Configure>", _on_frm_configure)

    def _on_canvas_configure(e):
        # 窗口变宽时让内容跟随, 避免右侧留空白
        if e.width > 1:
            canvas.itemconfigure(_cwin, width=e.width)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _wheel(e, target=None):
        target = target or canvas
        target.yview_scroll(int(-1 * (e.delta // 120)), "units")
        return "break"

    def bind_wheel(w):
        # 日志文本框滚自己的文本, 其余区域统一滚动主内容
        if isinstance(w, tk.Text):
            w.bind("<MouseWheel>", lambda e, t=w: _wheel(e, t))
        else:
            w.bind("<MouseWheel>", _wheel)
        for c in w.winfo_children():
            bind_wheel(c)

    V = {}
    lines = []
    sink_lock = threading.Lock()

    def sink(line):
        with sink_lock:
            lines.append(line)
            if len(lines) > 500:
                del lines[:200]

    set_log_sink(sink)

    def poll_log():
        with sink_lock:
            new = lines[:]
            lines.clear()
        if new and log_box is not None:
            log_box.configure(state="normal")
            for ln in new:
                log_box.insert("end", ln + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(300, poll_log)

    row = [0]
    log_box = None
    cur = [frm]              # 当前内容容器(卡片)
    card_rows = {}           # 每个卡片内部的行计数器

    def _crow(parent):
        r = card_rows.get(parent, 0)
        card_rows[parent] = r + 1
        return r

    def section(text):
        """创建一张分组卡片, 后续控件放进卡片"""
        row[0] += 1
        card = ttk.Labelframe(frm, text=text, style="Section.TLabelframe",
                              padding=(10, 4))
        card.grid(column=0, row=row[0], columnspan=3, sticky="new",
                  padx=4, pady=(8, 4))
        card.columnconfigure(1, weight=1)
        cur[0] = card
        return card

    def add_row(label, key, width=30, show=None, combo=None, check=False):
        parent = cur[0]
        r = _crow(parent)
        ttk.Label(parent, text=label, width=12, anchor="e").grid(
            column=0, row=r, sticky="e", padx=4, pady=3)
        if check:
            var = tk.BooleanVar(value=bool(getattr(cfg, key, False)))
            ttk.Checkbutton(parent, variable=var).grid(column=1, row=r, sticky="w")
            V[key] = var
            return
        if combo:
            var = tk.StringVar(value=getattr(cfg, key, "") or "")
            ttk.Combobox(parent, textvariable=var, width=width - 4, state="readonly",
                         values=combo).grid(column=1, row=r, sticky="w")
            V[key] = var
            return
        var = tk.StringVar(value=getattr(cfg, key, "") or "")
        ttk.Entry(parent, textvariable=var, width=width, show=show or "").grid(
            column=1, row=r, sticky="w")
        V[key] = var

    def val(key):
        var = V[key]
        if isinstance(var, tk.BooleanVar):
            return var.get()
        return (var.get() or "").strip()

    def collect():
        c = Config()
        c.mode = val("mode") or "portal"
        c.campus_ssid = val("campus_ssid")
        c.wifi_security = val("wifi_security") or "wpa2psk"
        c.wifi_key = val("wifi_key")
        c.portal_type = val("portal_type") or "auto"
        c.probe_urls = ",".join(CAPTIVE_PROBES)
        c.login_url = val("login_url")
        c.user_field = val("user_field")
        c.pass_field = val("pass_field")
        c.pass_encode = val("pass_encode") or "base64"
        c.account_suffix = val("account_suffix")
        c.isp = val("isp")
        c.username = val("username")
        c.password = val("password")
        c.save_password = val("save_password")
        c.pppoe_enabled = val("pppoe_enabled")
        c.pppoe_entry = val("pppoe_entry") or "宽带连接"
        c.pppoe_username = val("pppoe_username")
        c.pppoe_password = val("pppoe_password")
        c.offcampus_strategy = val("offcampus_strategy") or "ignore"
        c.fallback_ssid = val("fallback_ssid")
        try:
            c.poll_interval = int(val("poll_interval") or 30)
        except Exception:
            c.poll_interval = 30
        try:
            c.start_delay = int(val("start_delay") or 20)
        except Exception:
            c.start_delay = 20
        return c

    def run_bg(fn, done=None):
        def w():
            try:
                r = fn()
            except Exception as e:
                log("操作失败: %s" % e, "ERROR")
                r = None
            if done:
                root.after(0, lambda: done(r))
        threading.Thread(target=w, daemon=True).start()

    # ================= 顶部标题 + 状态条 =================
    row[0] += 1
    ttk.Label(frm, text=APP_TITLE, style="Title.TLabel").grid(
        column=0, row=row[0], columnspan=3, sticky="w", padx=4, pady=(2, 0))
    row[0] += 1
    ttk.Label(frm, text="自动识别校内/校外网络, 在校内自动连接并登录", style="Sub.TLabel").grid(
        column=0, row=row[0], columnspan=3, sticky="w", padx=4, pady=(0, 6))
    status_var = tk.StringVar(value="正在检测...")
    sbar = ttk.Frame(frm, style="Card.TFrame", padding=(10, 5))
    sbar.grid(column=0, row=row[0] + 1, columnspan=3, sticky="ew", padx=4, pady=(0, 6))
    ttk.Label(sbar, textvariable=status_var, style="Card.TLabel").grid(
        column=0, row=0, sticky="w")
    row[0] += 1

    # ================= 主表单(3 项) =================
    card = section("必填项(只需填这三项)")
    add_row("校园网账号", "username", width=32)
    add_row("校园网密码", "password", width=32, show="*")
    r = _crow(card)
    ttk.Label(card, text="校园网 WiFi", width=12, anchor="e").grid(
        column=0, row=r, sticky="e", padx=4, pady=3)
    var_ssid = tk.StringVar(value=cfg.campus_ssid or "")
    ttk.Entry(card, textvariable=var_ssid, width=32).grid(column=1, row=r, sticky="w")
    V["campus_ssid"] = var_ssid

    def use_current_ssid():
        cur = wifi_current_ssid()
        if cur:
            var_ssid.set(cur)
            log("已填入当前网络: %s" % cur)
        else:
            log("当前未连接任何 WiFi", "WARN")
    ttk.Button(card, text="用当前的", command=use_current_ssid).grid(
        column=2, row=r, sticky="w", padx=6)

    # ================= 主按钮 =================
    row[0] += 1
    bfrm = ttk.Frame(frm)
    bfrm.grid(column=0, row=row[0], columnspan=3, pady=10, sticky="w")

    def do_save():
        try:
            c = collect()
            c.save(cfg_path)
            log("配置已保存")
            return True
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return False

    def do_test():
        if not do_save():
            return
        def work():
            c = collect()
            client = NetClient(8)
            online, _ = check_internet(client, probe_urls=c.probe_list)
            if online:
                log("测试: 当前已可上网, 无需登录")
                log("测试: 如需验证自动登录流程, 先在浏览器退出门户登录, 再点[测试登录]", "WARN")
                return True
            info = detect_portal(client, c.probe_list)
            if not info:
                log("测试: 未探测到门户(请先连接校园网再试)", "WARN")
                return False
            log("测试: 门户类型=%s 页面=%s" % (info.get("type"), info.get("page_url")))
            return try_portal_login(client, c, info)
        def done(r):
            if r:
                messagebox.showinfo("测试登录", "登录成功, 网络已连通!")
            elif r is False:
                messagebox.showwarning("测试登录", "登录未成功, 请展开[运行日志]查看详细原因。")
        run_bg(work, done)

    def do_once():
        if not do_save():
            return
        def work():
            c = collect()
            Engine(c).tick()
            log("手动执行完成")
        run_bg(work)

    def do_toggle_autostart():
        if not do_save():
            return
        c = collect()
        if not c.complete:
            messagebox.showwarning("配置不完整", "请先填写: 校园网账号、密码、WiFi 名称。")
            return
        if autostart_method():
            if messagebox.askyesno("开机自启", "确定取消开机自启吗?(程序保留, 以后可再开启)"):
                uninstall_autostart()
        else:
            if install_autostart():
                messagebox.showinfo("开机自启", "已安装。下次登录 Windows 时自动运行。")

    def do_toggle_daemon():
        if not do_save():
            return
        if daemon_running():
            def stop_work():
                stop_tray_process()
                root.after(0, refresh_labels)
            run_bg(stop_work)
        else:
            if start_daemon_process():
                log("后台实例已启动, 右下角托盘可见其图标")
                root.after(1200, refresh_labels)

    def do_delete_self():
        # 已通过安装包安装: 交给标准卸载程序(会一并清理快捷方式与注册表卸载项)
        uninst = installed_uninstaller()
        if uninst:
            if not messagebox.askyesno(
                    "卸载程序",
                    "本程序已正常安装到系统中。\n\n"
                    "将启动卸载程序, 从「设置 - 应用」中完整移除\n"
                    "(可自行选择是否保留已保存的账号密码)。\n\n继续吗?"):
                return
            try:
                subprocess.Popen([uninst], cwd=app_dir())
            except Exception as e:
                log("启动卸载程序失败: %s" % e, "ERROR")
                messagebox.showerror("卸载", "无法启动卸载程序:\n%s\n\n"
                                             "可到 设置 - 应用 - 已安装的应用 中卸载。" % e)
                return
            root.destroy()
            return
        if not messagebox.askyesno(
                "删除程序",
                "将执行:\n1. 取消开机自启\n2. 停止后台运行\n"
                "3. 删除整个程序文件夹(含配置/日志)\n\n此操作不可恢复, 确定吗?"):
            return
        log("正在卸载并准备删除...")
        uninstall_autostart()
        # 删除场景下 5 秒等不到优雅退出就强杀(程序即将删除, 图标残留一次可接受)
        stop_tray_process(timeout=5.0)
        target = app_dir()
        try:
            subprocess.Popen(
                ["cmd", "/c", "timeout /t 2 /nobreak >nul & rd /s /q \"%s\"" % target],
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            log("安排自删除失败, 请手动删除文件夹: %s (%s)" % (target, e), "ERROR")
            return
        root.destroy()

    # ---- 关闭窗口行为: 配置完整则最小化到托盘 ----
    def on_window_close():
        try:
            c = collect()
            complete = c.complete
        except Exception:
            complete = cfg.complete
        if not complete:
            if not messagebox.askyesno(
                    "退出", "配置还未填写完整, 关闭后将无法自动登录。\n确定退出吗?"):
                return
            root.destroy()
            return
        if daemon_running():
            log("后台实例正在运行(右下角托盘), 关闭窗口不会影响它")
            root.destroy()
            return
        if messagebox.askyesno(
                "最小化到托盘",
                "关闭窗口后程序将继续在后台运行, 并在右下角托盘显示图标。\n\n"
                "确定最小化到托盘吗?\n(选[否]则完全退出)"):
            if do_save():
                if start_daemon_process():
                    log("已最小化到托盘, 右键托盘图标可打开主界面或退出")
                    root.after(800, root.destroy)
                else:
                    root.destroy()
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_window_close)

    ttk.Button(bfrm, text="保存", command=do_save, width=10,
               style="Primary.TButton").pack(side="left", padx=3)
    ttk.Button(bfrm, text="测试登录", command=do_test, width=10).pack(side="left", padx=3)
    ttk.Button(bfrm, text="立即执行一次", command=do_once, width=12).pack(side="left", padx=3)
    bfrm2 = ttk.Frame(frm, style="TFrame")
    bfrm2.grid(column=0, row=row[0] + 1, columnspan=3, pady=(0, 8), sticky="w")
    btn_autostart = ttk.Button(bfrm2, text="开机自启", command=do_toggle_autostart, width=20)
    btn_autostart.pack(side="left", padx=3)
    btn_daemon = ttk.Button(bfrm2, text="后台运行", command=do_toggle_daemon, width=12)
    btn_daemon.pack(side="left", padx=3)
    ttk.Button(bfrm2, text="删除此程序", command=do_delete_self, width=12,
               style="Danger.TButton").pack(side="left", padx=3)
    row[0] += 1

    # ================= 折叠: 更多设置 =================
    adv_open = [False]

    def toggle_adv():
        adv_open[0] = not adv_open[0]
        if adv_open[0]:
            adv_frm.grid()
            btn_adv.configure(text="收起高级设置 ▲")
        else:
            adv_frm.grid_remove()
            btn_adv.configure(text="更多设置(高级选项) ▼")

    btn_adv = ttk.Button(frm, text="更多设置(高级选项) ▼", command=toggle_adv)
    btn_adv.grid(column=0, row=row[0] + 1, sticky="w", padx=4, pady=(4, 0))
    row[0] += 1

    adv_frm = ttk.Frame(frm, style="TFrame")
    acur = [adv_frm]         # 高级区当前分组卡片

    def a_section(text):
        """高级区内的一张分组卡片"""
        card = ttk.Labelframe(adv_frm, text=text, style="Section.TLabelframe",
                              padding=(8, 2))
        card.grid(column=0, row=len(adv_frm.grid_slaves()), columnspan=3,
                  sticky="new", padx=2, pady=(4, 2))
        card.columnconfigure(1, weight=1)
        acur[0] = card
        return card

    def a_add(label, key, width=26, show=None, combo=None, check=False):
        parent = acur[0]
        r = _crow(parent)
        ttk.Label(parent, text=label, width=12, anchor="e").grid(
            column=0, row=r, sticky="e", padx=4, pady=2)
        if check:
            var = tk.BooleanVar(value=bool(getattr(cfg, key, False)))
            ttk.Checkbutton(parent, variable=var).grid(column=1, row=r, sticky="w")
            V[key] = var
            return
        if combo:
            var = tk.StringVar(value=getattr(cfg, key, "") or "")
            ttk.Combobox(parent, textvariable=var, width=width - 4, state="readonly",
                         values=combo).grid(column=1, row=r, sticky="w")
            V[key] = var
            return
        var = tk.StringVar(value=getattr(cfg, key, "") or "")
        ttk.Entry(parent, textvariable=var, width=width, show=show or "").grid(
            column=1, row=r, sticky="w")
        V[key] = var

    a_section("门户登录")
    a_add("工作模式", "mode", combo=["portal", "pppoe", "wifi_only"])
    a_add("门户类型", "portal_type", combo=["auto", "srun", "drcom", "generic", "custom"])
    var_login = tk.StringVar(value=cfg.login_url or "")
    _lc = acur[0]
    _r = _crow(_lc)
    ttk.Label(_lc, text="登录页地址", width=12, anchor="e").grid(
        column=0, row=_r, sticky="e", padx=4, pady=2)
    ttk.Entry(_lc, textvariable=var_login, width=26).grid(column=1, row=_r, sticky="w")
    V["login_url"] = var_login
    ttk.Button(_lc, text="自动探测", command=lambda: run_bg(do_probe)).grid(
        column=2, row=_r, sticky="w", padx=4)
    a_add("账号后缀", "account_suffix")
    a_add("账号字段名", "user_field")
    a_add("密码字段名", "pass_field")
    a_add("密码编码", "pass_encode", combo=["base64", "raw", "urlencode"])
    a_add("运营商", "isp")
    a_add("记住密码", "save_password", check=True)
    a_section("WiFi 连接")
    a_add("加密方式", "wifi_security", combo=["wpa2psk", "wpa3psk", "wpapsk", "open"])
    a_add("WiFi 密码", "wifi_key", show="*")
    a_section("PPPoE 拨号(宿舍宽带)")
    a_add("启用 PPPoE", "pppoe_enabled", check=True)
    a_add("连接名称", "pppoe_entry")
    a_add("宽带账号", "pppoe_username")
    a_add("宽带密码", "pppoe_password", show="*")
    a_section("校外策略")
    a_add("校外策略", "offcampus_strategy", combo=["ignore", "connect"])
    a_add("备用 SSID", "fallback_ssid")
    a_section("高级")
    a_add("检测间隔(秒)", "poll_interval", width=8)
    a_add("开机延迟(秒)", "start_delay", width=8)

    def do_probe():
        client = NetClient(8)
        info = detect_portal(client, CAPTIVE_PROBES)
        if not info:
            log("探测: 未发现登录门户(当前可能已联网或不在校园网)")
            return
        def apply():
            var_login.set(info.get("page_url") or info.get("login_url") or "")
            if info.get("type") in ("srun", "drcom"):
                V["portal_type"].set(info.get("type"))
            d = info.get("drcom") or {}
            if d.get("suffix") and not (V["account_suffix"].get() or "").strip():
                V["account_suffix"].set(d["suffix"])
            log("探测完成: 类型=%s, 已填入登录页地址" % info.get("type"))
        root.after(0, apply)

    # ================= 折叠: 运行日志 =================
    log_open = [False]

    def toggle_log():
        log_open[0] = not log_open[0]
        if log_open[0]:
            log_frm.grid()
            btn_log.configure(text="收起运行日志 ▲")
        else:
            log_frm.grid_remove()
            btn_log.configure(text="运行日志 ▼")

    btn_log = ttk.Button(frm, text="运行日志 ▼", command=toggle_log)
    btn_log.grid(column=0, row=row[0] + 1, sticky="w", padx=4, pady=(4, 0))
    row[0] += 1

    log_frm = ttk.Labelframe(frm, text="运行日志", style="Section.TLabelframe",
                             padding=(6, 4))
    log_box = tk.Text(log_frm, height=8, wrap="word", state="disabled",
                      font=("Consolas", 9), bg="#fbfcfe", fg="#374151",
                      relief="flat", highlightthickness=1,
                      highlightbackground="#d7dce5", highlightcolor="#2563eb",
                      padx=6, pady=4)
    sb = ttk.Scrollbar(log_frm, command=log_box.yview)
    log_box.configure(yscrollcommand=sb.set)
    log_box.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    log_frm.grid(column=0, row=row[0] + 1, columnspan=3, sticky="nsew", padx=4, pady=4)
    log_frm.grid_remove()
    frm.rowconfigure(row[0] + 1, weight=1)
    frm.columnconfigure(1, weight=1)

    # ================= 状态刷新 =================
    def refresh_labels():
        running = daemon_running()
        btn_daemon.configure(text="停止后台运行" if running else "后台运行")
        m = autostart_method()
        btn_autostart.configure(
            text=("开机自启: 已安装(点此取消)" if m else "开机自启: 未安装(点此开启)"))
        try:
            cur = wifi_current_ssid() or "未连接"
            status_var.set("当前网络: %s    后台: %s    开机自启: %s" % (
                cur, "运行中(托盘)" if running else "未运行", m or "未安装"))
        except Exception:
            pass
        root.after(3000, refresh_labels)

    refresh_labels()
    poll_log()
    bind_wheel(frm)   # 内容全部建完后统一绑定鼠标滚轮(主区滚动/日志区滚文本)
    log("配置界面已打开, 配置文件: %s" % cfg_path)
    if cfg.complete:
        log("配置已就绪。点击[后台运行]立即开始自动登录; 开机自启已安装则下次开机自动运行。")
    root.mainloop()
    set_log_sink(None)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def hide_console():
    if os.name == "nt":
        try:
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME, description=APP_TITLE)
    ap.add_argument("--config", help="配置文件路径(默认: 程序目录下 config.ini)")
    ap.add_argument("--setup", action="store_true", help="打开图形化配置界面")
    ap.add_argument("--run", action="store_true", help="前台运行(显示控制台日志)")
    ap.add_argument("--daemon", action="store_true", help="后台静默守护运行(无托盘图标)")
    ap.add_argument("--tray", action="store_true", help="后台驻留系统托盘运行(带图标/右键菜单, 开机自启默认)")
    ap.add_argument("--once", action="store_true", help="立即执行一次检测/连接/登录")
    ap.add_argument("--status", action="store_true", help="查看当前状态")
    ap.add_argument("--install", action="store_true", help="安装开机自启")
    ap.add_argument("--uninstall", action="store_true", help="卸载开机自启")
    ap.add_argument("--test-login", action="store_true", help="测试门户登录(排障)")
    ap.add_argument("--stop-all", action="store_true",
                    help="停止所有运行中的实例(安装/卸载前由安装包调用)")
    ap.add_argument("--verbose", "-v", action="store_true", help="输出详细日志")
    ap.add_argument("--version", action="store_true", help="显示版本")
    args = ap.parse_args(argv)

    if args.version:
        print("%s v%s" % (APP_TITLE, VERSION))
        return 0

    cfg_path = args.config or default_config_path()
    cfg = Config.load(cfg_path) if os.path.exists(cfg_path) else Config()
    init_logging(get_log_path(), args.verbose)
    log("=== %s v%s 启动 (%s) ===" % (APP_TITLE, VERSION, "exe" if getattr(sys, "frozen", False) else "python"))
    try:
        sweep_stale_temp_dirs()
    except Exception:
        pass

    if args.install:
        return 0 if install_autostart() else 1
    if args.uninstall:
        return 0 if uninstall_autostart() else 1
    if args.status:
        cmd_status(cfg, cfg_path)
        return 0
    if args.test_login:
        cmd_test_login(cfg)
        return 0
    if args.stop_all:
        stopped = False
        try:
            stopped = stop_tray_process(timeout=3.0)
        except Exception:
            pass
        try:
            if not stopped:
                stopped = stop_daemon()
            n = kill_other_campusnet()
            if n:
                stopped = True
                log("清理了 %d 个残留实例" % n)
        except Exception:
            pass
        log("已停止运行实例: %s" % ("是" if stopped else "无实例在运行"))
        return 0
    if args.setup:
        run_setup_gui(cfg, cfg_path)
        return 0
    if args.once:
        Engine(cfg).tick()
        return 0

    if args.daemon or args.tray or args.run:
        # ---- 后台/前台守护模式 ----
        if args.daemon:
            if not acquire_daemon_mutex():
                log("已有后台实例在运行, 本次启动退出(守护模式只允许一个实例)")
                return 0
            hide_console()
            write_daemon_pid()
        if args.tray:
            return run_tray(cfg)
        if not cfg.complete:
            log("配置不完整, 无法进入守护模式。请运行 --setup 完成配置", "ERROR")
            remove_daemon_pid()
            return 2
        log("进入守护模式: %s; 每 %ds 检测一次; 开机延迟 %ds" % (cfg.summary(), cfg.poll_interval, cfg.start_delay))
        try:
            time.sleep(cfg.start_delay)
            engine = Engine(cfg)
            while True:
                try:
                    engine.tick()
                except KeyboardInterrupt:
                    break
                except Exception:
                    log(traceback.format_exc(), "ERROR")
                time.sleep(cfg.poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            remove_daemon_pid()
        log("已退出")
        return 0

    # ---- 默认(双击/无参数): 永远打开配置界面, 不再静默转后台 ----
    run_setup_gui(cfg, cfg_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        init_logging(get_log_path(), True)
        log(traceback.format_exc(), "ERROR")
        sys.exit(1)
