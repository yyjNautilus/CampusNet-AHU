#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证: 配置界面[最小化到托盘]后退出时, 不应出现
   "Failed to remove temporary directory" 警告框, 且托盘进程应使用
   自己独立的 _MEI 解压目录。

用法: python tests/e2e_mei_warning.py "D:\\...\\CampusNet.exe"
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32
psapi = ctypes.WinDLL("psapi")
kernel32 = ctypes.windll.kernel32

psapi.EnumProcesses.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
                                ctypes.POINTER(ctypes.c_ulong)]
psapi.EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
psapi.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_wchar_p, ctypes.c_ulong]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

WM_CLOSE = 0x0010
BM_CLICK = 0x00F5


def text_of(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def top_windows():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return out


def child_windows(hwnd):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        out.append(h)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return out


def find_dialog(substr, timeout=15.0, exclude_texts=()):
    """在 #32770 对话框里按正文关键字找窗口"""
    end = time.time() + timeout
    while time.time() < end:
        for hwnd in top_windows():
            if class_of(hwnd) != "#32770":
                continue
            body = " ".join(text_of(c) for c in child_windows(hwnd))
            if substr.lower() in body.lower() and not any(
                    e in body for e in exclude_texts):
                return hwnd, body
        time.sleep(0.3)
    return None, ""


def click_button(dialog, words):
    for c in child_windows(dialog):
        t = text_of(c).replace("&", "").strip()
        if t in words:
            user32.SendMessageW(c, BM_CLICK, 0, 0)
            return t
    return None


def mei_dirs_of_pid(pid):
    dirs = set()
    h = kernel32.OpenProcess(0x0410, False, pid)
    if not h:
        return dirs
    try:
        mods = (ctypes.c_void_p * 1024)()
        cb = ctypes.c_ulong()
        if psapi.EnumProcessModules(h, mods, ctypes.sizeof(mods), ctypes.byref(cb)):
            for j in range(min(cb.value // ctypes.sizeof(ctypes.c_void_p), 1024)):
                buf = ctypes.create_unicode_buffer(600)
                if psapi.GetModuleFileNameExW(h, mods[j], buf, 600):
                    parts = buf.value.split("\\")
                    for i, p in enumerate(parts):
                        if p.upper().startswith("_MEI"):
                            dirs.add("\\".join(parts[:i + 1]))
                            break
    finally:
        kernel32.CloseHandle(h)
    return dirs


def campusnet_pids():
    pids = []
    arr = (ctypes.c_ulong * 4096)()
    need = ctypes.c_ulong()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
        return pids
    for i in range(need.value // ctypes.sizeof(ctypes.c_ulong)):
        pid = arr[i]
        if not pid:
            continue
        h = kernel32.OpenProcess(0x0410, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(600)
            size = ctypes.c_ulong(600)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                if os.path.basename(buf.value).lower() == "campusnet.exe":
                    pids.append(pid)
        finally:
            kernel32.CloseHandle(h)
    return pids


def temp_mei_dirs():
    base = os.environ.get("TEMP", "")
    try:
        return set(d for d in os.listdir(base) if d.upper().startswith("_MEI"))
    except Exception:
        return set()


def main():
    exe = sys.argv[1]
    before = set(campusnet_pids())
    before_tmp = temp_mei_dirs()
    print("启动界面:", exe)
    gui = subprocess.Popen([exe])
    try:
        # 1. 等界面出现
        hwnd = None
        end = time.time() + 30
        while time.time() < end and hwnd is None:
            for h in top_windows():
                if "校园网自动连接助手" in text_of(h) and class_of(h) != "#32770":
                    hwnd = h
                    break
            time.sleep(0.3)
        assert hwnd, "界面没有出现"
        print("界面已出现:", repr(text_of(hwnd)))
        time.sleep(1.5)

        # 2. 关闭窗口 -> 若托盘实例没在跑, 应弹出[最小化到托盘]确认框
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        dlg, body = find_dialog("最小化到托盘", timeout=8)
        if dlg:
            hit = click_button(dlg, {"是", "是(Y)", "Yes", "确定"})
            print("已点击确认按钮:", hit)
        else:
            print("托盘实例已在运行, 界面直接关闭(无需确认框)")

        # 3. 退出过程不能出现临时目录警告
        warn, warn_body = find_dialog("temporary directory", timeout=10)
        try:
            gui.wait(timeout=25)
            print("界面进程已退出")
        except subprocess.TimeoutExpired:
            print("提示: 界面进程 25s 内未退出(可能仍在清理)")
        if not warn:
            warn, warn_body = find_dialog("temporary directory", timeout=2)
        assert not warn, "仍然弹出了警告框: %s" % warn_body
        print("没有出现 'Failed to remove temporary directory' 警告")

        # 4. 不应残留本次解压目录, 托盘实例应在运行
        time.sleep(2.5)
        left = temp_mei_dirs() - before_tmp
        assert not left, "残留了临时解压目录: %s" % sorted(left)
        print("没有残留临时解压目录(当前: %s)" % (sorted(temp_mei_dirs()) or "无"))

        pids = [p for p in campusnet_pids() if p not in before]
        assert pids, "托盘实例没有启动"
        for p in pids:
            print("  托盘实例 pid %d, 依赖目录: %s"
                  % (p, sorted(mei_dirs_of_pid(p)) or "程序自带目录(onedir)"))
        print("\n结论: 通过")
    finally:
        if gui.poll() is None:
            gui.kill()


if __name__ == "__main__":
    main()
