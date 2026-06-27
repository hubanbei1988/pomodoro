#!/usr/bin/env python3
"""
桌面番茄钟 - Desktop Pomodoro Timer
Python + tkinter 实现，零外部依赖，双击即可运行

功能：
  - 三种模式：专注(25min) / 短休息(5min) / 长休息(15min)
  - Canvas 圆形进度环 + 数字倒计时
  - 番茄周期自动切换（每 4 个番茄进入长休息）
  - 窗口置顶、系统托盘
  - 计时结束提示音 + Windows 原生通知
  - 状态自动保存，重启恢复
  - 键盘快捷键：空格 开始/暂停，R 重置，S 跳过，1/2/3 切换模式
"""

import tkinter as tk
from tkinter import messagebox, Menu
import json
import os
import sys
import math
import subprocess
import winsound
import ctypes
import struct
import threading
from pathlib import Path

# ═══════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════

APP_NAME = "番茄时钟"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "pomodoro-timer"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 默认模式配置（分钟数）
DEFAULT_MODES = {
    "work":       {"label": "专注工作", "short": "🍅 专注", "minutes": 25, "color": "#e94560", "icon": "🍅"},
    "shortBreak": {"label": "短休息",   "short": "☕ 短休息", "minutes": 5,  "color": "#2ecc71", "icon": "☕"},
    "longBreak":  {"label": "长休息",   "short": "🌿 长休息", "minutes": 15, "color": "#3498db", "icon": "🌿"},
}

# 配色方案（暗色主题）
C = {
    "bg":         "#1a1a2e",  # 窗口背景
    "card":       "#16213e",  # 卡片/面板背景
    "accent":     "#e94560",  # 强调色（红）
    "accent2":    "#ff6b81",  # 强调色浅版
    "text":       "#eeeeee",  # 主文字
    "text_muted": "#999999",  # 次要文字
    "ring_bg":    "#222244",  # 进度环底色
    "green":      "#2ecc71",  # 短休息环色
    "blue":       "#3498db",  # 长休息环色
    "orange":     "#f39c12",  # 暂停状态色
    "orange_hover":"#f5b042",  # 暂停悬停色
    "btn_bg":     "#252545",  # 次要按钮背景
    "btn_hover":  "#333366",  # 按钮悬停
    "dot_pending":"#444444",  # 未完成圆点
    "white":      "#ffffff",
}

FONT = "Microsoft YaHei"  # Windows 默认中文字体

# WM 常量
WM_TRAY_CALLBACK = 0x8001  # WM_APP + 1

# ═══════════════════════════════════════════════════
# NOTIFYICONDATA 结构体（统一定义，避免重复）
# ═══════════════════════════════════════════════════

class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint32),
        ("uFlags", ctypes.c_uint32),
        ("uCallbackMessage", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint32),
        ("dwStateMask", ctypes.c_uint32),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeoutOrVersion", ctypes.c_uint32),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint32),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


# ═══════════════════════════════════════════════════
# Windows 通知辅助（通过 ctypes 调用系统 API）
# ═══════════════════════════════════════════════════

def show_windows_toast(title: str, message: str):
    """
    尝试弹出 Windows 原生通知。
    通过 PowerShell 调用，兼容 Windows 10/11。
    失败时静默降级（应用内已有 toast 兜底）。
    """
    try:
        ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName("text")
$texts[0].AppendChild($template.CreateTextNode("{title}")) > $null
$texts[1].AppendChild($template.CreateTextNode("{message}")) > $null
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{APP_NAME}")
$notifier.Show($template)
'''
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        pass  # 静默降级，应用内 toast 已足够


# ═══════════════════════════════════════════════════
# 托盘图标生成（运行时生成 .ico 写入临时文件）
# ═══════════════════════════════════════════════════

def generate_tray_icon() -> Path:
    """
    生成一个简单的 32x32 红色圆形 .ico 文件（番茄色），
    返回文件路径。用于系统托盘图标。
    """
    icon_path = CONFIG_DIR / "tray.ico"
    if icon_path.exists():
        return icon_path

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 32x32 RGBA 像素：红色圆形 + 绿色小叶子在顶部
    # 简化版：纯红色圆形
    size = 32
    # ICO 文件头
    # BITMAPINFOHEADER + RGBA pixel data
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            dx = x - size / 2 + 0.5
            dy = y - size / 2 + 0.5
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= size / 2 - 2:
                # 主体红色（番茄）
                r, g, b, a = 0xE9, 0x45, 0x60, 0xFF
                # 顶部小叶子
                if y < 10 and abs(dx) < 6 and dy < -8:
                    r, g, b, a = 0x2E, 0xCC, 0x71, 0xFF
            else:
                r, g, b, a = 0, 0, 0, 0  # 透明
            pixels.extend([b, g, r, a])  # BGRA 顺序

    # 构建 ICO 文件
    # ICO header: reserved(2) + type(2) + count(2)
    ico = bytearray()
    ico += struct.pack("<HHH", 0, 1, 1)  # reserved=0, type=1(ICO), count=1

    # ICONDIRENTRY: bWidth(1) + bHeight(1) + bColorCount(1) + bReserved(1)
    #             + wPlanes(2) + wBitCount(2) + dwBytesInRes(4) + dwImageOffset(4)
    #             = 16 bytes
    data_offset = 6 + 16  # header(6) + one entry(16)
    img_data_size = 40 + len(pixels)  # BITMAPINFOHEADER(40) + pixels
    ico += struct.pack("<BBBBHHII",
        size, size,          # width, height
        0,                   # color palette (0 = none)
        0,                   # reserved
        1,                   # color planes
        32,                  # bits per pixel
        img_data_size,       # size of image data
        data_offset,         # offset to image data
    )

    # BITMAPINFOHEADER (40 bytes)
    # Note: ICO uses double height (once for AND mask)
    ico += struct.pack("<IiiHHIIiiII",
        40,            # biSize
        size,          # biWidth
        size * 2,      # biHeight (double for ICO: XOR mask + AND mask)
        1,             # biPlanes
        32,            # biBitCount
        0,             # biCompression (BI_RGB)
        len(pixels),   # biSizeImage
        0,             # biXPelsPerMeter
        0,             # biYPelsPerMeter
        0,             # biClrUsed
        0,             # biClrImportant
    )
    ico += pixels

    icon_path.write_bytes(ico)
    return icon_path


def remove_tray_icon(hwnd: int, uid: int = 1):
    """从系统托盘移除图标。"""
    try:
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = ctypes.c_void_p(hwnd)
        nid.uID = uid
        ctypes.windll.shell32.Shell_NotifyIconW(2, ctypes.byref(nid))  # NIM_DELETE = 2
    except Exception:
        pass


def add_tray_icon(hwnd: int, tip: str, uid: int = 1) -> bool:
    """向系统托盘添加图标。成功返回 True。"""
    try:
        icon_path = generate_tray_icon()
        # 加载图标
        hicon = ctypes.windll.user32.LoadImageW(
            0, str(icon_path), 1,  # IMAGE_ICON = 1
            0, 0, 0x00000010 | 0x00000040  # LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        if not hicon:
            return False

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = ctypes.c_void_p(hwnd)
        nid.uID = uid
        nid.uFlags = 0x00000001 | 0x00000002 | 0x00000004  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = hicon
        nid.szTip = tip[:127]

        ctypes.windll.shell32.Shell_NotifyIconW(0, ctypes.byref(nid))  # NIM_ADD = 0
        return True
    except Exception:
        return False


def update_tray_tip(hwnd: int, tip: str, uid: int = 1):
    """更新托盘提示文字。"""
    try:
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = ctypes.c_void_p(hwnd)
        nid.uID = uid
        nid.uFlags = 0x00000004  # NIF_TIP
        nid.szTip = tip[:127]
        ctypes.windll.shell32.Shell_NotifyIconW(1, ctypes.byref(nid))  # NIM_MODIFY = 1
    except Exception:
        pass


# ═══════════════════════════════════════════════════
# 主应用类
# ═══════════════════════════════════════════════════

class PomodoroApp:
    def __init__(self):
        # ── 窗口初始化 ──
        self.root = tk.Tk()
        self.root.title(f"🍅 {APP_NAME}")
        self.root.geometry("420x620")
        self.root.minsize(380, 560)
        self.root.configure(bg=C["bg"])
        self.root.resizable(True, True)

        # ── 状态变量 ──
        self.current_mode: str = "work"
        self.time_left: int = DEFAULT_MODES["work"]["minutes"] * 60
        self.total_time: int = self.time_left
        self.is_running: bool = False
        self.timer_id: str | None = None
        self.session_count: int = 0
        self.pomodoros_in_set: int = 0  # 0-3，满 4 进入长休息

        # 可自定义的时长（分钟）
        self.custom_minutes: dict[str, int] = {
            k: v["minutes"] for k, v in DEFAULT_MODES.items()
        }

        # 窗口置顶
        self.always_on_top: bool = False

        # 系统托盘
        self.tray_enabled: bool = False
        self._tray_uid: int = 1

        # ── 加载持久化配置 ──
        self.load_config()

        # ── 构建 UI ──
        self.build_ui()

        # ── 事件绑定 ──
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<space>", lambda e: self.toggle_timer())
        self.root.bind("<Key-r>", lambda e: self.reset_timer())
        self.root.bind("<Key-s>", lambda e: self.skip_timer())
        self.root.bind("<Key-1>", lambda e: self.switch_mode("work"))
        self.root.bind("<Key-2>", lambda e: self.switch_mode("shortBreak"))
        self.root.bind("<Key-3>", lambda e: self.switch_mode("longBreak"))

        # 右键菜单
        self.build_context_menu()

        # ── 托盘消息处理 ──
        self.root.bind("<<TrayMessage>>", self._on_tray_message)

        # ── 窗口关闭时保存 ──
        self.root.bind("<Destroy>", lambda e: self._on_destroy())

        # ── 刷新显示 ──
        self.update_display()
        self.update_dots()
        self._update_ring_arc()

        # ── 延迟设置托盘（等窗口创建完毕）──
        self.root.after(500, self._init_tray)

    # ═══════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════

    def build_ui(self):
        """构建全部界面组件。"""
        # 主容器（自适应居中）
        main = tk.Frame(self.root, bg=C["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=30, pady=(20, 10))
        main.grid_columnconfigure(0, weight=1)

        row = 0

        # ── 标题 ──
        title_lbl = tk.Label(
            main, text="🍅 番茄时钟", font=(FONT, 20, "bold"),
            bg=C["bg"], fg=C["text"],
        )
        title_lbl.grid(row=row, column=0, pady=(0, 24))
        row += 1

        # ── 模式切换标签 ──
        tab_frame = tk.Frame(main, bg=C["card"], bd=0, highlightthickness=0)
        tab_frame.grid(row=row, column=0, sticky="ew", pady=(0, 24))
        tab_frame.grid_columnconfigure(0, weight=1)
        tab_frame.grid_columnconfigure(1, weight=1)
        tab_frame.grid_columnconfigure(2, weight=1)

        # 用 Canvas 画圆角矩形背景
        self.tab_btns: dict[str, tk.Label] = {}
        for i, (mode, cfg) in enumerate(DEFAULT_MODES.items()):
            btn = tk.Label(
                tab_frame,
                text=cfg.get("short", f"{cfg['icon']} {cfg['label']}"),
                font=(FONT, 10),
                bg=C["card"], fg=C["text_muted"],
                padx=12, pady=8,
                cursor="hand2",
            )
            btn.grid(row=0, column=i, sticky="ew", padx=3, pady=3)
            btn.bind("<Button-1>", lambda e, m=mode: self._on_tab_click(m))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(fg=C["white"]))
            btn.bind("<Leave>", lambda e, b=btn, m=mode:
                      b.configure(fg=C["white"] if m == self.current_mode else C["text_muted"]))
            self.tab_btns[mode] = btn
            tab_frame.grid_columnconfigure(i, weight=1)

        self._style_tabs()
        row += 1

        # ── 进度环 + 时间显示 (Canvas) ──
        ring_size = 280
        self.canvas = tk.Canvas(
            main, width=ring_size, height=ring_size,
            bg=C["bg"], highlightthickness=0,
        )
        self.canvas.grid(row=row, column=0, pady=(0, 20))
        self.canvas_size = ring_size
        self.center = ring_size / 2
        self.ring_radius = 126
        self.ring_width = 9
        self._draw_ring()
        row += 1

        # ── 控制按钮 ──
        ctrl_frame = tk.Frame(main, bg=C["bg"])
        ctrl_frame.grid(row=row, column=0, pady=(0, 20))

        # 重置按钮
        self.btn_reset = tk.Label(
            ctrl_frame, text="↺", font=(FONT, 16),
            bg=C["btn_bg"], fg=C["text"],
            width=3, height=1, cursor="hand2",
        )
        self.btn_reset.pack(side=tk.LEFT, padx=10)
        self.btn_reset.bind("<Button-1>", lambda e: self.reset_timer())
        self._bind_hover(self.btn_reset, C["btn_bg"], C["btn_hover"])

        # 开始/暂停按钮（大的圆形）
        self.btn_main = tk.Label(
            ctrl_frame, text="▶", font=(FONT, 22, "bold"),
            bg=C["accent"], fg=C["white"],
            width=4, height=2, cursor="hand2",
        )
        self.btn_main.pack(side=tk.LEFT, padx=10)
        self.btn_main.bind("<Button-1>", lambda e: self.toggle_timer())
        self._bind_hover(self.btn_main, C["accent"], C["accent2"])

        # 跳过按钮
        self.btn_skip = tk.Label(
            ctrl_frame, text="⏭", font=(FONT, 16),
            bg=C["btn_bg"], fg=C["text"],
            width=3, height=1, cursor="hand2",
        )
        self.btn_skip.pack(side=tk.LEFT, padx=10)
        self.btn_skip.bind("<Button-1>", lambda e: self.skip_timer())
        self._bind_hover(self.btn_skip, C["btn_bg"], C["btn_hover"])
        row += 1

        # ── 会话信息 ──
        info_frame = tk.Frame(main, bg=C["bg"])
        info_frame.grid(row=row, column=0, pady=(0, 10))

        self.session_label = tk.Label(
            info_frame, text="🍅 完成 0 轮",
            font=(FONT, 10), bg=C["bg"], fg=C["text_muted"],
        )
        self.session_label.pack(side=tk.LEFT, padx=(0, 12))

        # 4 个指示灯
        self.dot_labels: list[tk.Label] = []
        for i in range(4):
            dot = tk.Label(
                info_frame, text="●", font=(FONT, 14),
                bg=C["bg"], fg=C["dot_pending"],
            )
            dot.pack(side=tk.LEFT, padx=3)
            self.dot_labels.append(dot)

    def _draw_ring(self):
        """在 Canvas 上绘制背景环和进度弧。"""
        self.canvas.delete("ring")
        x1 = self.center - self.ring_radius
        y1 = self.center - self.ring_radius
        x2 = self.center + self.ring_radius
        y2 = self.center + self.ring_radius

        # 背景环
        self.canvas.create_arc(
            x1, y1, x2, y2,
            start=0, extent=359.9,
            style="arc", outline=C["ring_bg"],
            width=self.ring_width,
            tags="ring",
        )

        # 进度弧（初始为空，后续通过 itemconfigure 更新）
        self.canvas.create_arc(
            x1, y1, x2, y2,
            start=90, extent=0,
            style="arc", outline=C["accent"],
            width=self.ring_width,
            tags="progress",
        )

        # 时间文字（Canvas 中央）
        self.canvas.create_text(
            self.center, self.center - 10,
            text="25:00",
            font=(FONT, 42, "bold"),
            fill=C["white"],
            tags="ring time_text",
        )
        self.canvas.create_text(
            self.center, self.center + 28,
            text="专注工作",
            font=(FONT, 10),
            fill=C["text_muted"],
            tags="ring label_text",
        )

    def _style_tabs(self):
        """重置所有标签样式为当前模式对应的状态。"""
        for mode, btn in self.tab_btns.items():
            is_active = mode == self.current_mode
            btn.configure(
                bg=C["accent"] if is_active else C["card"],
                fg=C["white"] if is_active else C["text_muted"],
            )

    @staticmethod
    def _bind_hover(widget: tk.Label, normal_bg: str, hover_bg: str):
        """为 Label 按钮绑定悬停效果（仅绑定一次，颜色存储在 widget 属性上）。"""
        widget._normal_bg = normal_bg
        widget._hover_bg = hover_bg
        widget.bind("<Enter>", lambda e: e.widget.configure(bg=e.widget._hover_bg))
        widget.bind("<Leave>", lambda e: e.widget.configure(bg=e.widget._normal_bg))

    def build_context_menu(self):
        """右键菜单。"""
        self.ctx_menu = Menu(self.root, tearoff=0, bg=C["card"], fg=C["text"],
                             activebackground=C["accent"], activeforeground=C["white"],
                             font=(FONT, 9))
        self.ctx_menu.add_command(label="⚙ 设置时长...", command=self.open_settings)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="📌 窗口置顶", command=self.toggle_always_on_top)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="❌ 退出", command=self.quit_app)

        self.root.bind("<Button-3>", self._show_context_menu)
        # Canvas 也绑定右键
        self.canvas.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event):
        self.ctx_menu.tk_popup(event.x_root, event.y_root)

    # ═══════════════════════════════════════
    # 计时引擎
    # ═══════════════════════════════════════

    def start_timer(self):
        """开始计时。"""
        if self.is_running:
            return
        self.is_running = True
        self._update_main_btn()
        self._run_tick()

    def pause_timer(self):
        """暂停计时。"""
        if not self.is_running:
            return
        self.is_running = False
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self._update_main_btn()

    def toggle_timer(self):
        """切换 开始/暂停。"""
        if self.is_running:
            self.pause_timer()
        else:
            self.start_timer()

    def reset_timer(self):
        """重置当前模式计时器。"""
        self.pause_timer()
        self.time_left = self.custom_minutes[self.current_mode] * 60
        self.total_time = self.time_left
        self.update_display()

    def skip_timer(self):
        """跳过当前阶段。"""
        result = messagebox.askyesno("跳过", f"确定要跳过当前阶段吗？")
        if result:
            self.pause_timer()
            self._complete_session()

    def _run_tick(self):
        """每秒执行一次。"""
        if not self.is_running:
            return

        self.time_left -= 1
        self.update_display()

        if self.time_left <= 0:
            self.pause_timer()
            self._complete_session()
        else:
            self.timer_id = self.root.after(1000, self._run_tick)

    def _complete_session(self):
        """当前阶段计时结束，自动切换。"""
        mode_cfg = DEFAULT_MODES[self.current_mode]

        # 播放提示音
        self._play_sound()

        # 弹出通知
        if self.current_mode == "work":
            self.pomodoros_in_set += 1
            self.session_count += 1
            self.session_label.configure(text=f"🍅 完成 {self.session_count} 轮")
            self.update_dots()

            if self.pomodoros_in_set >= 4:
                self._show_toast("🎉 已完成 4 个番茄！享受长休息吧~")
                self.switch_mode("longBreak")
            else:
                self._show_toast("✅ 番茄完成！休息一下~")
                self.switch_mode("shortBreak")
        else:
            if self.current_mode == "longBreak":
                self.pomodoros_in_set = 0
                self.update_dots()
            self._show_toast("🔔 休息结束，开始新的番茄！")
            self.switch_mode("work")

        self.save_config()

    def switch_mode(self, mode: str):
        """切换到指定模式。"""
        if self.is_running:
            self.pause_timer()

        self.current_mode = mode
        self.time_left = self.custom_minutes[mode] * 60
        self.total_time = self.time_left

        self._style_tabs()
        self._update_ring_arc()
        self.update_display()
        self.update_tray()

    # ═══════════════════════════════════════
    # 显示更新
    # ═══════════════════════════════════════

    @staticmethod
    def _fmt_time(seconds: int) -> str:
        """将秒数格式化为 MM:SS 字符串。"""
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    def update_display(self):
        """更新倒计时文字和进度弧。"""
        time_str = self._fmt_time(self.time_left)

        # 更新 Canvas 上的时间文字
        self.canvas.itemconfigure("time_text", text=time_str)

        # 更新模式标签
        label = DEFAULT_MODES[self.current_mode]["label"]
        self.canvas.itemconfigure("label_text", text=label)

        # 更新窗口标题
        icon = DEFAULT_MODES[self.current_mode]["icon"]
        self.root.title(f"{time_str} - {icon} {APP_NAME}")

        # 更新进度弧
        self._update_ring_arc()

        # 更新托盘提示
        if self.is_running:
            mode_label = DEFAULT_MODES[self.current_mode]["label"]
            self.update_tray(f"{time_str} - {mode_label}")

    def _update_ring_arc(self):
        """更新进度弧的 extent 和颜色（通过 itemconfigure 原位更新）。"""
        progress = self.time_left / self.total_time if self.total_time > 0 else 0

        # extent 为负表示顺时针方向（从 12 点方向开始）
        extent = -progress * 360
        if extent > -1:
            extent = 0  # 剩余不足 1 度时不显示

        color = DEFAULT_MODES[self.current_mode]["color"]
        self.canvas.itemconfigure("progress", extent=extent, outline=color)

    def update_dots(self):
        """更新 4 个指示灯。"""
        for i, dot in enumerate(self.dot_labels):
            if i < self.pomodoros_in_set:
                dot.configure(fg=C["green"])
            else:
                dot.configure(fg=C["dot_pending"])

    def _update_main_btn(self):
        """更新主按钮的文本和颜色（自动更新 hover 色）。"""
        if self.is_running:
            self.btn_main._normal_bg = C["orange"]
            self.btn_main._hover_bg = C["orange_hover"]
            self.btn_main.configure(text="⏸", bg=C["orange"])
        else:
            self.btn_main._normal_bg = C["accent"]
            self.btn_main._hover_bg = C["accent2"]
            self.btn_main.configure(text="▶", bg=C["accent"])

    # ═══════════════════════════════════════
    # 提示与通知
    # ═══════════════════════════════════════

    def _show_toast(self, msg: str):
        """在应用内显示浮动提示（Toplevel 窗口）。"""
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=C["card"])

        lbl = tk.Label(
            toast, text=msg,
            font=(FONT, 11),
            bg=C["card"], fg=C["white"],
            padx=24, pady=12,
        )
        lbl.pack()

        # 定位：主窗口上方居中
        toast.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        tw = toast.winfo_reqwidth()
        th = toast.winfo_reqheight()
        x = rx + (rw - tw) // 2
        y = max(10, ry - th - 10)
        toast.geometry(f"+{x}+{y}")

        # 动画：从上方滑入
        toast.attributes("-alpha", 0.0)
        self._fade_in(toast)

        # 3 秒后渐隐
        toast.after(3000, lambda: self._fade_out(toast))

        # 同时也尝试 Windows 原生通知
        threading.Thread(target=show_windows_toast, args=(APP_NAME, msg), daemon=True).start()

    @staticmethod
    def _fade_in(window: tk.Toplevel, step: float = 0.0):
        try:
            if step <= 1.0:
                window.attributes("-alpha", step)
                window.after(20, lambda: PomodoroApp._fade_in(window, step + 0.1))
        except tk.TclError:
            pass  # 窗口已被销毁

    @staticmethod
    def _fade_out(window: tk.Toplevel, step: float = 1.0):
        try:
            if step >= 0.0:
                window.attributes("-alpha", step)
                window.after(20, lambda: PomodoroApp._fade_out(window, step - 0.1))
            else:
                window.destroy()
        except tk.TclError:
            pass  # 窗口已被销毁

    @staticmethod
    def _play_sound():
        """播放计时结束提示音。"""
        try:
            # 三连音：上升音阶或下降（根据模式）
            for freq in (523, 659, 784):
                winsound.Beep(freq, 180)
        except Exception:
            # Beep 在某些系统上不可用，降级为系统提示音
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass

    # ═══════════════════════════════════════
    # 窗口控制
    # ═══════════════════════════════════════

    def toggle_always_on_top(self):
        """切换窗口置顶状态。"""
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        state = "开" if self.always_on_top else "关"
        self._show_toast(f"📌 窗口置顶：{state}")

    def on_close(self):
        """关闭窗口 → 退出应用。"""
        self.quit_app()

    def quit_app(self):
        """完全退出应用。"""
        self.pause_timer()
        self.save_config()
        self._saved = True  # 防止 _on_destroy 重复保存
        self._remove_tray()
        self.root.destroy()
        sys.exit(0)

    def _on_destroy(self):
        """窗口销毁时的清理（仅非正常退出路径）。"""
        if getattr(self, '_saved', False):
            return
        try:
            self.save_config()
        except Exception:
            pass

    # ═══════════════════════════════════════
    # 系统托盘
    # ═══════════════════════════════════════

    def _init_tray(self):
        """初始化系统托盘图标。"""
        hwnd = self.root.winfo_id()
        tip = f"🍅 {APP_NAME}"
        self.tray_enabled = add_tray_icon(hwnd, tip, self._tray_uid)
        if self.tray_enabled:
            self.update_tray()
            # 监听托盘消息（仅过滤我们的回调消息，避免干扰 tkinter 事件循环）
            self._poll_tray()

    def _poll_tray(self):
        """轮询托盘消息 — 仅取出 WM_TRAY_CALLBACK 消息，不影响 tkinter。"""
        if not self.tray_enabled:
            return
        try:
            hwnd = self.root.winfo_id()
            while True:
                msg = ctypes.wintypes.MSG()
                # PM_REMOVE | 按 HWND + 消息范围过滤，只取我们的回调消息
                has_msg = ctypes.windll.user32.PeekMessageW(
                    ctypes.byref(msg),
                    hwnd,                    # 仅本窗口
                    WM_TRAY_CALLBACK,        # 消息范围下限
                    WM_TRAY_CALLBACK,        # 消息范围上限
                    1,                       # PM_REMOVE
                )
                if not has_msg:
                    break
                if msg.lParam == 0x0205:    # WM_RBUTTONUP
                    self.root.event_generate("<<TrayMessage>>")
                # 注意：不要 TranslateMessage/DispatchMessage — 我们已经自己处理了
        except Exception:
            pass
        self.root.after(200, self._poll_tray)

    def _on_tray_message(self, event=None):
        """托盘右键消息处理：弹出菜单。"""
        menu = Menu(self.root, tearoff=0, bg=C["card"], fg=C["text"],
                    activebackground=C["accent"], activeforeground=C["white"],
                    font=(FONT, 9))
        menu.add_command(label="显示窗口", command=self._restore_window)
        menu.add_command(label="📌 置顶" if not self.always_on_top else "📌 取消置顶",
                         command=self.toggle_always_on_top)
        menu.add_separator()
        menu.add_command(label="❌ 退出", command=self.quit_app)

        # 在鼠标位置弹出
        try:
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            menu.tk_popup(pt.x, pt.y)
        except Exception:
            menu.tk_popup(100, 100)

    def _restore_window(self):
        """从托盘恢复窗口。"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def update_tray(self, tip: str | None = None):
        """更新托盘提示文字。"""
        if not self.tray_enabled:
            return
        if tip is None:
            tip = f"{self._fmt_time(self.time_left)} - {DEFAULT_MODES[self.current_mode]['label']}"
        hwnd = self.root.winfo_id()
        update_tray_tip(hwnd, tip, self._tray_uid)

    def _remove_tray(self):
        """移除托盘图标。"""
        if self.tray_enabled:
            hwnd = self.root.winfo_id()
            remove_tray_icon(hwnd, self._tray_uid)
            self.tray_enabled = False

    # ═══════════════════════════════════════
    # 事件处理
    # ═══════════════════════════════════════

    def _on_tab_click(self, mode: str):
        """点击模式标签。"""
        if mode == self.current_mode:
            # 如果当前模式已被修改（正在运行或时间不是默认），重置
            default = self.custom_minutes[mode] * 60
            if self.is_running or self.time_left != default:
                self.reset_timer()
            return
        if self.is_running:
            if not messagebox.askyesno("切换模式", "切换模式会重置当前计时，确定吗？"):
                return
        self.switch_mode(mode)

    # ═══════════════════════════════════════
    # 设置窗口
    # ═══════════════════════════════════════

    def open_settings(self):
        """打开设置窗口。"""
        win = tk.Toplevel(self.root)
        win.title("⚙ 设置时长")
        win.geometry("320x280")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        # 居中
        win.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        ww = 320
        wh = 280
        x = rx + (rw - ww) // 2
        y = ry + (rh - wh) // 2
        win.geometry(f"+{x}+{y}")

        entries: dict[str, tk.Entry] = {}
        for i, (mode, cfg) in enumerate(DEFAULT_MODES.items()):
            frame = tk.Frame(win, bg=C["bg"])
            frame.pack(fill=tk.X, padx=30, pady=(16 if i == 0 else 8, 4))

            lbl = tk.Label(
                frame, text=f"{cfg['icon']} {cfg['label']}",
                font=(FONT, 11), bg=C["bg"], fg=C["text"],
            )
            lbl.pack(side=tk.LEFT)

            entry = tk.Entry(
                frame, font=(FONT, 11), bg=C["card"], fg=C["white"],
                insertbackground=C["white"], relief=tk.FLAT, width=5,
                justify=tk.CENTER,
            )
            entry.insert(0, str(self.custom_minutes[mode]))
            entry.pack(side=tk.RIGHT)

            unit = tk.Label(
                frame, text="分钟", font=(FONT, 10),
                bg=C["bg"], fg=C["text_muted"],
            )
            unit.pack(side=tk.RIGHT, padx=(0, 6))
            entries[mode] = entry

        def save():
            try:
                for mode, entry in entries.items():
                    val = int(entry.get())
                    if val < 1:
                        raise ValueError("至少 1 分钟")
                    if val > 120:
                        raise ValueError("最多 120 分钟")
                    self.custom_minutes[mode] = val
                # 如果当前没有在计时，立即更新
                if not self.is_running:
                    self.time_left = self.custom_minutes[self.current_mode] * 60
                    self.total_time = self.time_left
                    self.update_display()
                self.save_config()
                self._show_toast("✅ 设置已保存")
                win.destroy()
            except ValueError as e:
                messagebox.showerror("输入错误", str(e))

        btn_frame = tk.Frame(win, bg=C["bg"])
        btn_frame.pack(pady=20)

        save_btn = tk.Label(
            btn_frame, text="💾 保存", font=(FONT, 11),
            bg=C["accent"], fg=C["white"], padx=24, pady=6,
            cursor="hand2",
        )
        save_btn.pack(side=tk.LEFT, padx=6)
        save_btn.bind("<Button-1>", lambda e: save())
        self._bind_hover(save_btn, C["accent"], C["accent2"])

        cancel_btn = tk.Label(
            btn_frame, text="取消", font=(FONT, 11),
            bg=C["btn_bg"], fg=C["text"], padx=24, pady=6,
            cursor="hand2",
        )
        cancel_btn.pack(side=tk.LEFT, padx=6)
        cancel_btn.bind("<Button-1>", lambda e: win.destroy())
        self._bind_hover(cancel_btn, C["btn_bg"], C["btn_hover"])

    # ═══════════════════════════════════════
    # 配置持久化
    # ═══════════════════════════════════════

    def _config_dict(self) -> dict:
        """生成当前配置字典。"""
        return {
            "current_mode": self.current_mode,
            "time_left": self.time_left,
            "total_time": self.total_time,
            "session_count": self.session_count,
            "pomodoros_in_set": self.pomodoros_in_set,
            "always_on_top": self.always_on_top,
            "custom_minutes": self.custom_minutes,
            "window_geometry": self.root.geometry() if not self.root.winfo_viewable() else "",
        }

    def save_config(self):
        """保存配置到 JSON 文件。"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = self._config_dict()
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception:
            pass  # 静默失败，不影响使用

    def load_config(self):
        """从 JSON 文件加载配置。"""
        try:
            if not CONFIG_FILE.exists():
                return
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

            if "current_mode" in data and data["current_mode"] in DEFAULT_MODES:
                self.current_mode = data["current_mode"]
            if "time_left" in data and isinstance(data["time_left"], int):
                self.time_left = max(0, data["time_left"])
            if "total_time" in data and isinstance(data["total_time"], int):
                self.total_time = max(1, data["total_time"])
            if "session_count" in data:
                self.session_count = max(0, int(data["session_count"]))
            if "pomodoros_in_set" in data:
                self.pomodoros_in_set = max(0, min(4, int(data["pomodoros_in_set"])))
            if "always_on_top" in data:
                self.always_on_top = bool(data["always_on_top"])
            if "custom_minutes" in data and isinstance(data["custom_minutes"], dict):
                for mode in DEFAULT_MODES:
                    if mode in data["custom_minutes"]:
                        self.custom_minutes[mode] = max(1, min(120,
                            int(data["custom_minutes"][mode])))

            # 恢复置顶状态
            if self.always_on_top:
                self.root.attributes("-topmost", True)

            # 如果 time_left 超过当前 total_time（模式可能变了），重置
            if self.time_left > self.total_time:
                self.time_left = self.total_time

        except Exception:
            pass  # 配置损坏时使用默认值

    # ═══════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════

    def run(self):
        """启动主循环。"""
        self.root.mainloop()


# ═══════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    app = PomodoroApp()
    app.run()
