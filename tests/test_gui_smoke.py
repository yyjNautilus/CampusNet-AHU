# -*- coding: utf-8 -*-
"""
GUI 冒烟测试(新版精简界面): 不弹出可见窗口, 通过组件树驱动配置界面,
验证: 界面构造、主表单3项、保存、高级设置折叠展开、日志折叠、状态标签。
不触碰真实网络/WiFi/后台进程。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import campus_net as cn

import tkinter as tk
from tkinter import ttk, messagebox

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s - %s %s" % ("PASS" if cond else "FAIL", name, detail))


class HiddenTk(tk.Tk):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.withdraw()


def find_buttons(widget, into):
    for c in widget.winfo_children():
        if isinstance(c, ttk.Button):
            into.append(c)
        find_buttons(c, into)


def find_entries(widget, into):
    for c in widget.winfo_children():
        if isinstance(c, ttk.Entry) and not isinstance(c, ttk.Combobox):
            into.append(c)
        find_entries(c, into)


def find_labels(widget, into):
    for c in widget.winfo_children():
        if isinstance(c, ttk.Label):
            into.append(c)
        find_labels(c, into)


def main():
    # 打补丁: 隐藏窗口、阻止 mainloop 阻塞、拦截消息框
    tk.Tk = HiddenTk
    tk.Misc.mainloop = lambda self, *a, **kw: None
    msgs = []
    messagebox.showinfo = lambda *a, **kw: msgs.append(("info", a))
    messagebox.showwarning = lambda *a, **kw: msgs.append(("warning", a))
    messagebox.showerror = lambda *a, **kw: msgs.append(("error", a))
    messagebox.askyesno = lambda *a, **kw: True

    tmp_cfg = os.path.join(tempfile.gettempdir(), "campusnet_gui_test2.ini")
    if os.path.exists(tmp_cfg):
        os.remove(tmp_cfg)
    cn.init_logging(os.path.join(tempfile.gettempdir(), "campusnet_gui_test2.log"), True)

    cfg = cn.Config()
    cfg.campus_ssid = "OLD-SSID"
    cn.run_setup_gui(cfg, tmp_cfg)

    root = tk._default_root
    # 新结构: root -> container -> (Scrollbar, Canvas) -> frm(内容)
    container = [c for c in root.winfo_children() if isinstance(c, ttk.Frame)][0]
    canvases = [c for c in container.winfo_children() if isinstance(c, tk.Canvas)]
    check("主内容区有画布滚动容器", len(canvases) == 1, str(len(canvases)))
    canvas = canvases[0]
    frm = canvas.winfo_children()[0]
    sbars = [c for c in container.winfo_children() if isinstance(c, ttk.Scrollbar)]
    check("配有垂直滚动条", len(sbars) == 1, str(len(sbars)))

    # ---------- 1. 主按钮齐全 ----------
    buttons = []
    find_buttons(frm, buttons)
    names = [b.cget("text") for b in buttons]
    for want in ["保存", "测试登录", "立即执行一次", "后台运行", "删除此程序"]:
        check("主按钮[%s]存在" % want, want in names, str(names))
    check("折叠按钮存在",
          any(n.startswith("更多设置") for n in names) and any(n.startswith("运行日志") for n in names))

    # ---------- 2. 主表单只有 3 个输入框(精简验证) ----------
    def top_parent(w):
        while getattr(w, "master", None) is not None and w.master is not frm:
            w = w.master
        return w

    all_entries = []
    find_entries(frm, all_entries)
    adv_frm = None
    for e in all_entries:
        tp = top_parent(e)
        if (isinstance(tp, ttk.Frame) and not isinstance(tp, ttk.Labelframe)
                and tp.master is frm):
            adv_frm = tp      # 高级区外层容器(普通 Frame, 区别于主卡 Labelframe)
            break
    check("定位到高级区容器", adv_frm is not None)
    main_entries = [e for e in all_entries if top_parent(e) is not adv_frm]
    adv_entries = [e for e in all_entries if top_parent(e) is adv_frm]
    check("主界面输入框只有 3 个(精简)", len(main_entries) == 3, str(len(main_entries)))
    cards = [c for c in frm.winfo_children() if isinstance(c, ttk.Labelframe)]
    check("必填项为卡片式分组", len(cards) >= 1, str(len(cards)))

    def set_entry(entry, value):
        name = entry.cget("textvariable")
        if name:
            root.setvar(name, value)

    for e in main_entries:
        set_entry(e, "")
    set_entry(main_entries[0], "testuser")     # 账号
    set_entry(main_entries[1], "portalpass")   # 密码
    set_entry(main_entries[2], "TEST-SSID")    # WiFi

    save_btn = [b for b in buttons if b.cget("text") == "保存"][0]
    check("保存按钮使用主色(Primary)样式", "Primary" in str(save_btn.cget("style")),
          str(save_btn.cget("style")))
    danger_btn = [b for b in buttons if b.cget("text") == "删除此程序"]
    check("删除按钮使用危险(Danger)样式",
          bool(danger_btn) and "Danger" in str(danger_btn[0].cget("style")),
          str(danger_btn[0].cget("style")) if danger_btn else "无")
    save_btn.invoke()
    time.sleep(0.4)
    check("保存后配置文件生成", os.path.exists(tmp_cfg))
    if os.path.exists(tmp_cfg):
        loaded = cn.Config.load(tmp_cfg)
        check("账号读回正确", loaded.username == "testuser", loaded.username)
        check("密码读回正确(DPAPI)", loaded.password == "portalpass", repr(loaded.password))
        check("SSID 读回正确", loaded.campus_ssid == "TEST-SSID", loaded.campus_ssid)

    # ---------- 3. 高级设置折叠: 默认隐藏, 展开后功能齐全 ----------
    # 注意: 根窗口被 withdraw, ismapped 全为 0; 用容器 frame 的 grid manager 判断
    adv_btn = [b for b in buttons if b.cget("text").startswith("更多设置")][0]
    check("高级输入框共 12 个", len(adv_entries) == 12, str(len(adv_entries)))
    check("高级选项默认折叠", adv_frm.winfo_manager() == "", repr(adv_frm.winfo_manager()))
    adv_btn.invoke()
    time.sleep(0.3)
    check("展开后按钮文案变化", adv_btn.cget("text").startswith("收起"))
    check("展开后高级区可见", adv_frm.winfo_manager() == "grid")
    # 填两个高级项(检测间隔/开机延迟是最后两个)并保存
    for e in adv_entries:
        set_entry(e, "")
    set_entry(adv_entries[-2], "15")   # 检测间隔
    set_entry(adv_entries[-1], "5")    # 开机延迟
    save_btn.invoke()
    time.sleep(0.4)
    loaded = cn.Config.load(tmp_cfg)
    check("高级项(检测间隔)保存生效", loaded.poll_interval == 15, str(loaded.poll_interval))
    check("高级项(开机延迟)保存生效", loaded.start_delay == 5, str(loaded.start_delay))

    # ---------- 3b. 展开高级区后内容超高, 可滚动查看 ----------
    root.update()                      # 让 <Configure> 结算 scrollregion
    sr = [float(x) for x in canvas.cget("scrollregion").split()]
    check("滚动范围已计算(内容高度>0)", sr[3] > 0, str(sr))
    frac = canvas.yview()
    check("高级区展开后内容超出窗口(需要滚动)", frac[1] - frac[0] < 0.99, str(frac))
    # 滚轮事件 -> handler -> canvas.yview_scroll 链路(mock 验证, 不受 1px 测试窗口影响)
    calls = []
    orig = canvas.yview_scroll
    canvas.yview_scroll = lambda *a: calls.append(a)
    frm.event_generate("<MouseWheel>", delta=-240)   # 滚轮向下
    check("鼠标滚轮事件触发内容滚动(向下)",
          len(calls) == 1 and calls[0][0] > 0 and calls[0][1] == "units", str(calls))
    calls.clear()
    frm.event_generate("<MouseWheel>", delta=240)    # 滚轮向上
    check("鼠标滚轮事件触发内容滚动(向上)",
          len(calls) == 1 and calls[0][0] < 0, str(calls))
    canvas.yview_scroll = orig
    canvas.yview_moveto(0)
    root.update()

    adv_btn.invoke()  # 收起
    time.sleep(0.2)
    check("再次点击可收起", adv_btn.cget("text").startswith("更多设置"))
    check("收起后高级区隐藏", adv_frm.winfo_manager() == "")

    # ---------- 4. 日志折叠 ----------
    log_btn = [b for b in buttons if b.cget("text").startswith("运行日志")][0]
    log_btn.invoke()
    time.sleep(0.2)
    check("日志区可展开", log_btn.cget("text").startswith("收起"))

    # ---------- 5. 状态标签已刷新 ----------
    all_labels = []
    find_labels(frm, all_labels)
    status_var = None
    for c in all_labels:
        tv = c.cget("textvariable")
        if tv:
            status_var = root.getvar(tv)
    check("状态标签有内容", bool(status_var), repr(status_var))
    title_labels = [c for c in all_labels if "Title" in str(c.cget("style"))]
    check("界面有标题栏(Title 样式)", len(title_labels) == 1, str(len(title_labels)))

    # ---------- 6. 关闭窗口 -> 最小化到托盘(配置完整时) ----------
    started = []
    cn.start_daemon_process = lambda: (started.append(1), True)[1]
    cn.daemon_running = lambda: False
    cb = root.wm_protocol("WM_DELETE_WINDOW")
    check("关闭窗口回调已注册", bool(cb))
    if cb:
        root.tk.call(cb)   # 通过 Tcl 命令名触发回调
        time.sleep(0.4)
        check("关闭窗口时启动了托盘实例", len(started) == 1, str(len(started)))

    root.destroy()
    print("-" * 50)
    failed = [n for n, ok in RESULTS if not ok]
    print("总计 %d 项, 失败 %d 项" % (len(RESULTS), len(failed)))
    if failed:
        print("失败项:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
