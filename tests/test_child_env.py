#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试: 单文件打包下子进程不能继承 PyInstaller 的 _MEI 目录标记

背景: 配置界面点[后台运行]后自身会退出, 若托盘守护沿用了父进程的解压目录,
父进程退出时删不掉该目录 -> 弹 "Failed to remove temporary directory"。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import campus_net as cn  # noqa: E402


BASE = {
    "PATH": r"C:\Windows",
    "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI000051202",
    "_PYI_ARCHIVE_FILE": r"C:\Temp\_MEI000051202\base_library.zip",
    "_PYI_PARENT_PROCESS_LEVEL": "1",
    "_PYI_SPLASH_IPC": "1234",
    "_MEIPASS2": r"C:\Temp\_MEI000051202",
    "USERNAME": "nautilus",
}


def test_child_env_strips_pyinstaller_markers():
    env = cn.child_env(BASE)
    for k in ("_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
              "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC", "_MEIPASS2"):
        assert k not in env, "%s 应被清除, 否则子进程会借用父进程的 _MEI 目录" % k
    assert env["PATH"] == BASE["PATH"]
    assert env["USERNAME"] == "nautilus"
    # 原字典不能被就地改写
    assert "_PYI_APPLICATION_HOME_DIR" in BASE


def test_spawn_uses_isolated_env():
    """start_daemon_process / _open_gui_process 必须把隔离后的 env 传给 Popen"""
    captured = []
    real_popen = cn.subprocess.Popen
    real_frozen = getattr(sys, "frozen", None)
    real_exe = sys.executable
    os.environ["_PYI_APPLICATION_HOME_DIR"] = r"C:\Temp\_MEI000099999"
    os.environ["_PYI_PARENT_PROCESS_LEVEL"] = "1"
    try:
        cn.sys.frozen = True
        cn.sys.executable = os.path.join(tempfile.gettempdir(), "CampusNet.exe")

        def fake_popen(cmd, **kw):
            captured.append(kw.get("env"))
            return None

        cn.subprocess.Popen = fake_popen
        cn.start_daemon_process()
        cn._open_gui_process()
    finally:
        cn.subprocess.Popen = real_popen
        sys.executable = real_exe
        os.environ.pop("_PYI_APPLICATION_HOME_DIR", None)
        os.environ.pop("_PYI_PARENT_PROCESS_LEVEL", None)
        if real_frozen is None:
            del cn.sys.frozen
        else:
            cn.sys.frozen = real_frozen

    assert len(captured) == 2, "两处拉起子进程都应传 env"
    for env in captured:
        assert env is not None
        assert "_PYI_APPLICATION_HOME_DIR" not in env
        assert "_PYI_PARENT_PROCESS_LEVEL" not in env


def test_mei_dirs_in_use_detects_loaded_dir():
    """别的进程正从 _MEIxxxx 目录加载 dll 时, 必须能被识别出来(不能去删它)"""
    import shutil
    import subprocess

    d = os.path.join(tempfile.gettempdir(), "_MEItestload")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    src = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "ucrtbase.dll")
    dst = os.path.join(d, "ucrtbase.dll")
    shutil.copy2(src, dst)

    code = ("import ctypes,time;ctypes.WinDLL(r'%s');time.sleep(6)" % dst)
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        import time as _t
        _t.sleep(1.5)
        used = cn._mei_dirs_in_use()
        assert os.path.normcase(os.path.abspath(d)) in used, \
            "已加载的目录未识别为占用: %s" % sorted(used)
        assert os.path.normcase(os.path.abspath(tempfile.gettempdir())) not in used
    finally:
        child.kill()
        child.wait()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_child_env_strips_pyinstaller_markers()
    test_spawn_uses_isolated_env()
    test_mei_dirs_in_use_detects_loaded_dir()
    print("全部通过 (3 项)")
