"""
smartcity_gui.py — Tkinter GUI (스크린샷 기반 재구성)
=====================================================
- 좌측: 예약 정보 입력 패널 (흰 경계선 카드)
- 우측 상단: 총실행 / 성공 / 실패 통계 카드 3개
- 우측 중단: 탭 없는 단일 로그 영역 (실행 결과 로그)
- 우측 하단: 예약현황 / 새로고침 / 지우기 / 자동정지 버튼 바
- 버튼: 자동화 예약 실행 (보라/파랑 그라데이션 느낌)
- playwright 미사용 — subprocess → smartcity_core.py
"""

import json, os, queue, subprocess, sys, threading, tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk

# ── .env ─────────────────────────────────────────────────────────
def _load_env() -> dict:
    r = {}
    for p in [Path(".env"), Path(__file__).parent / ".env"]:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                r[k.strip()] = v.strip().strip('"').strip("'")
            break
    return r

ENV  = _load_env()
CORE = Path(__file__).parent / "Digital_world_core.py"

TIMES  = [f"{h:02d}:{m:02d}" for h in range(7, 23) for m in (0, 30)]
# R5 MX 회의실 전용 기본값
# 실제 Digital World 화면 표기와 조금 달라도 core에서 공백/괄호를 제거해 유사 매칭합니다.
ROOMS  = [
    "A1", "B1", "C1", "C2", "C3", "D1", "D2", "D4", "Idea",
    "MX회의실", "회의실", "전체"
]
LOC1   = ["디지털시티", "수원", "모바일연구소(R5)"]
LOC2   = ["모바일연구소(R5)", "A타워", "B타워", "R5 사회상생회의 전용", "공용존"]
LOC3   = ["A타워/22층"] + [f"A타워/{i}층" for i in range(1, 29)] + ["공용존/지하1층", "공용존/1층", "전체"]
RTYPES = ["전체", "회의실", "교육실", "내방객실", "VIP회의실", "외부用 화상회의실", "모바일홀", "Test Room"]

# ── 색상 (스크린샷 기준) ─────────────────────────────────────────
BG        = "#0e1422"   # 전체 배경 (진한 네이비)
PANEL     = "#151c2e"   # 패널 배경
PANEL2    = "#1a2235"   # 입력 필드 배경
BORDER    = "#2a3555"   # 테두리
INK       = "#dde4f0"   # 기본 텍스트
SUB       = "#6b7a9a"   # 보조 텍스트
ACCENT    = "#4cc2ff"   # 포인트 (cyan)
OK        = "#3ddc97"   # 성공 (green)
WARN      = "#ffb454"   # 경고 (amber)
ERR       = "#ff6b6b"   # 오류 (red)
STEP      = "#c084fc"   # 단계 (purple)
SEP_C     = "#1e2a40"   # 구분선

# 섹션 레이블 색 (스크린샷: 파랑/보라 계열)
SEC_DATE  = "#4f7fff"   # 날짜/시간 — 파랑
SEC_ROOM  = "#4f7fff"   # 회의실/위치 — 파랑
SEC_TITLE = "#4f7fff"   # 회의 정보 — 파랑
SEC_OPT   = "#4f7fff"   # 실행 옵션 — 파랑

# 버튼
BTN_RUN   = "#5b3cf5"   # 예약 실행 (보라)
BTN_RUN_H = "#7055f7"
BTN_STATUS= "#1a6b4a"   # 예약현황 (초록)
BTN_REF   = "#1a4a6b"   # 새로고침 (파랑)
BTN_CLR   = "#6b2020"   # 지우기 (빨강)
BTN_AR    = "#252f45"   # 자동정지

# ── 로그 큐 ─────────────────────────────────────────────────────
log_q: queue.Queue = queue.Queue()

def q_log(msg: str, tag: str = "INFO"):
    log_q.put({"msg": msg, "tag": tag,
               "time": datetime.now().strftime("%H:%M:%S")})

# ── subprocess 호출 ──────────────────────────────────────────────
def _call_core(payload: dict, on_log, on_result):
    if not CORE.exists():
        on_log("ERR", f"smartcity_core.py 없음: {CORE}")
        on_result(False, []); return

    cmd     = [sys.executable, str(CORE)]
    stdin_b = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    
    # UTF-8 인코딩 강제 (Windows)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    
    try:
        proc = subprocess.Popen(cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(CORE.parent),
            env=env)
    except Exception as e:
        on_log("ERR", f"subprocess 실행 실패: {e}")
        on_result(False, []); return

    proc.stdin.write(stdin_b); proc.stdin.close()

    got_result = False
    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            on_log("INFO", line); continue
        if obj.get("type") == "log":
            on_log(obj.get("tag","INFO"), obj.get("msg",""))
        elif obj.get("type") == "result":
            on_result(obj.get("ok", False), obj.get("data", []))
            got_result = True

    stderr_out = proc.stderr.read().decode("utf-8", errors="replace").strip()
    if stderr_out:
        lines = stderr_out.splitlines()
        # playwright 미설치 오류 감지 → 자동 설치
        if any("playwright" in l and ("ModuleNotFound" in l or "No module" in l) for l in lines):
            on_log("WARN", "playwright 미설치 감지 — 자동 설치 중... (1~3분 소요)")
            try:
                import subprocess as _sp
                _sp.check_call([sys.executable, "-m", "pip", "install",
                                "--quiet", "--disable-pip-version-check", "playwright"])
                _sp.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
                on_log("OK", "playwright 설치 완료! 다시 실행해주세요.")
            except Exception as e:
                on_log("ERR", f"자동 설치 실패: {e}")
                on_log("ERR", "cmd 에서 수동 실행: pip install playwright && playwright install chromium")
        else:
            for line in lines:
                if line.strip(): on_log("ERR", f"[core] {line}")
    proc.wait()
    if not got_result: on_result(False, [])

def _run_core(payload, on_log, on_result):
    threading.Thread(target=_call_core,
                     args=(payload, on_log, on_result), daemon=True).start()

# ── ttk 스타일 전역 설정 ─────────────────────────────────────────
def _apply_ttk_style(root):
    s = ttk.Style(root)
    try: s.theme_use("clam")
    except Exception: pass

    # Combobox
    s.configure("SC.TCombobox",
        fieldbackground=PANEL2, background=PANEL2,
        foreground=INK, selectbackground=PANEL2,
        selectforeground=INK, bordercolor=BORDER,
        lightcolor=BORDER, darkcolor=BORDER,
        arrowcolor=SUB, insertcolor=INK)
    s.map("SC.TCombobox",
        fieldbackground=[("readonly", PANEL2), ("focus", PANEL2)],
        foreground=[("readonly", INK), ("focus", INK)],
        selectbackground=[("readonly", PANEL2)],
        bordercolor=[("focus", ACCENT)])

    # Scrollbar
    s.configure("SC.Vertical.TScrollbar",
        background=PANEL2, troughcolor=BG, arrowcolor=SUB,
        bordercolor=BORDER, darkcolor=PANEL2, lightcolor=PANEL2)
    s.map("SC.Vertical.TScrollbar",
        background=[("active", BORDER)])

    # Treeview
    s.configure("SC.Treeview",
        background=PANEL2, foreground=INK,
        fieldbackground=PANEL2, rowheight=28,
        font=("Malgun Gothic", 9), bordercolor=BORDER)
    s.configure("SC.Treeview.Heading",
        background=PANEL, foreground=ACCENT,
        font=("Malgun Gothic", 9, "bold"), relief="flat")
    s.map("SC.Treeview",
        background=[("selected", "#1e3a5f")],
        foreground=[("selected", INK)])

# ── GUI ──────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Digital World R5 MX회의실 예약 자동화")
        self.configure(bg=BG)
        self.geometry("1160x820")
        self.minsize(960, 700)

        self._stats       = {"total":0, "success":0, "fail":0}
        self._running     = False
        self._ar          = True
        self._logs: list  = []
        self._reservations: list = []
        self._status_win  = None   # 예약현황 Toplevel

        # 예약 진행 표시(REC) 상태
        self._rec_active   = False
        self._rec_blink    = False
        self._rec_step     = "대기"
        self._rec_percent  = 0

        _apply_ttk_style(self)
        self._build()
        self._poll()
        self._ar_tick()

    # ── 전체 레이아웃 ────────────────────────────────────────────
    def _build(self):
        self._topbar()
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(8,12))

        # 왼쪽 입력 패널
        lf = tk.Frame(body, bg=PANEL,
                      highlightthickness=1, highlightbackground=BORDER)
        lf.pack(side="left", fill="y", padx=(0,12))
        lf.pack_propagate(False); lf.configure(width=340)
        self._left_panel(lf)

        # 오른쪽 영역
        rf = tk.Frame(body, bg=BG)
        rf.pack(side="left", fill="both", expand=True)
        self._stat_row(rf)
        self._log_area(rf)
        self._bottom_bar(rf)

    # ── 탑바 ────────────────────────────────────────────────────
    def _topbar(self):
        bar = tk.Frame(self, bg="#0a1020", height=56)
        bar.pack(fill="x"); bar.pack_propagate(False)

        # SC 뱃지
        badge = tk.Frame(bar, bg="#5b3cf5", width=42, height=36)
        badge.pack(side="left", padx=(16,0), pady=10)
        badge.pack_propagate(False)
        tk.Label(badge, text="SC", bg="#5b3cf5", fg="white",
                 font=("Malgun Gothic",11,"bold")).place(relx=.5,rely=.5,anchor="center")

        tk.Label(bar, text="  Digital World   회의실 예약 자동화",
                 bg="#0a1020", fg=INK,
                 font=("Malgun Gothic",13,"bold")).pack(side="left")

        # 시계
        self._clk = tk.StringVar()
        tk.Label(bar, textvariable=self._clk,
                 bg="#0a1020", fg=SUB,
                 font=("Consolas",11)).pack(side="right", padx=20)
        self._tick_clock()

    def _tick_clock(self):
        self._clk.set(datetime.now().strftime("%Y-%m-%d   %H:%M:%S"))
        self.after(1000, self._tick_clock)

    # ── 왼쪽 입력 패널 ──────────────────────────────────────────
    def _left_panel(self, p):
        se = dict(bg=PANEL2, fg=INK, insertbackground=ACCENT,
                  relief="flat", font=("Malgun Gothic",10),
                  highlightthickness=1,
                  highlightbackground=BORDER,
                  highlightcolor=ACCENT)
        sc_kw = dict(style="SC.TCombobox", font=("Malgun Gothic",10))

        def sec(label, color=SEC_DATE):
            """섹션 헤더 — 색 점 + 텍스트"""
            f = tk.Frame(p, bg=PANEL); f.pack(fill="x", padx=16, pady=(14,4))
            tk.Label(f, text="●", bg=PANEL, fg=color,
                     font=("Malgun Gothic",8)).pack(side="left")
            tk.Label(f, text=f"  {label}", bg=PANEL, fg=color,
                     font=("Malgun Gothic",9,"bold")).pack(side="left")

        def field(label, widget_fn):
            f = tk.Frame(p, bg=PANEL); f.pack(fill="x", padx=16, pady=(0,5))
            tk.Label(f, text=label, bg=PANEL, fg=SUB,
                     font=("Malgun Gothic",9), width=8, anchor="w").pack(side="left")
            w = widget_fn(f); w.pack(side="left", fill="x", expand=True); return w

        # 헤더
        tk.Label(p, text="예약 정보 입력", bg=PANEL, fg=INK,
                 font=("Malgun Gothic",12,"bold")).pack(anchor="w", padx=16, pady=(16,4))

        # 날짜 / 시간
        sec("날짜 / 시간", SEC_DATE)
        self._vdate  = tk.StringVar(value=str(date.today()))
        self._vstart = tk.StringVar(value="14:00")
        self._vend   = tk.StringVar(value="15:00")
        field("날짜",  lambda f: tk.Entry(f, textvariable=self._vdate, **se))
        field("시작",  lambda f: ttk.Combobox(f, textvariable=self._vstart,
                                               values=TIMES, state="readonly", **sc_kw))
        field("종료",  lambda f: ttk.Combobox(f, textvariable=self._vend,
                                               values=TIMES, state="readonly", **sc_kw))

        # 회의실 / 위치
        sec("회의실 / 위치", SEC_ROOM)
        self._vroom  = tk.StringVar(value="A1")
        self._vrtype = tk.StringVar(value="전체")
        self._vl1    = tk.StringVar(value="디지털시티")
        self._vl2    = tk.StringVar(value="모바일연구소(R5)")
        self._vl3    = tk.StringVar(value="A타워/22층")
        field("회의실",   lambda f: ttk.Combobox(f, textvariable=self._vroom,
                                                  values=ROOMS, **sc_kw))
        field("타입",     lambda f: ttk.Combobox(f, textvariable=self._vrtype,
                                                  values=RTYPES, state="readonly", **sc_kw))
        field("캠퍼스",   lambda f: ttk.Combobox(f, textvariable=self._vl1,
                                                  values=LOC1, state="readonly", **sc_kw))
        field("연구소", lambda f: ttk.Combobox(f, textvariable=self._vl2,
                                                  values=LOC2, state="readonly", **sc_kw))
        field("구역/층",   lambda f: ttk.Combobox(f, textvariable=self._vl3,
                                                  values=LOC3, state="readonly", **sc_kw))

        # 회의 정보
        sec("회의 정보", SEC_TITLE)
        self._vtitle = tk.StringVar(value="AX팀 내부회의")
        field("회의명", lambda f: tk.Entry(f, textvariable=self._vtitle, **se))

        # 실행 옵션
        sec("실행 옵션", SEC_OPT)
        self._vhl = tk.BooleanVar(value=False)
        fh = tk.Frame(p, bg=PANEL); fh.pack(fill="x", padx=16, pady=(0,4))
        tk.Checkbutton(fh, text="헤드리스 모드 (브라우저 숨김)",
                       variable=self._vhl,
                       bg=PANEL, fg=SUB, activebackground=PANEL,
                       selectcolor=PANEL2, font=("Malgun Gothic",9)
                       ).pack(side="left")

        # 실행 버튼
        self._btn_run = tk.Button(
            p, text="▶  자동화 예약 실행",
            bg=BTN_RUN, fg="white",
            activebackground=BTN_RUN_H, activeforeground="white",
            font=("Malgun Gothic",11,"bold"),
            relief="flat", cursor="hand2",
            command=self._do_reserve, height=2,
            bd=0, highlightthickness=0)
        self._btn_run.pack(fill="x", padx=16, pady=(16,4))

        # 상태 텍스트
        self._sv_status = tk.StringVar(value="대기 중")
        self._sl_status = tk.Label(p, textvariable=self._sv_status,
                                   bg=PANEL, fg=SUB,
                                   font=("Malgun Gothic",9))
        self._sl_status.pack(pady=(0,6))

        # 예약 진행 중 시 녹화 아이콘처럼 깜빡이는 표시
        self._sv_rec = tk.StringVar(value="⚪ 대기 중")
        self._rec_label = tk.Label(
            p, textvariable=self._sv_rec,
            bg="#101828", fg=SUB,
            font=("Malgun Gothic", 10, "bold"),
            padx=10, pady=8,
            highlightthickness=1, highlightbackground=BORDER
        )
        self._rec_label.pack(fill="x", padx=16, pady=(0,16))

    # ── 통계 카드 3개 ───────────────────────────────────────────
    def _stat_row(self, p):
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", pady=(0,10))
        self._svars = {}
        items = [
            ("□ 총 실행",  "total",   ACCENT),
            ("☑ 성공",     "success", OK),
            ("✕ 실패",     "fail",    ERR),
        ]
        for label, key, color in items:
            card = tk.Frame(row, bg=PANEL,
                            highlightthickness=1, highlightbackground=BORDER)
            card.pack(side="left", fill="both", expand=True, padx=(0,10))
            tk.Label(card, text=label, bg=PANEL, fg=SUB,
                     font=("Malgun Gothic",9)).pack(pady=(12,2))
            v = tk.StringVar(value="0"); self._svars[key] = v
            tk.Label(card, textvariable=v, bg=PANEL, fg=color,
                     font=("Malgun Gothic",26,"bold")).pack(pady=(0,12))

    # ── 로그 영역 ───────────────────────────────────────────────
    def _log_area(self, p):
        # 헤더 바
        hdr = tk.Frame(p, bg=PANEL,
                       highlightthickness=1, highlightbackground=BORDER)
        hdr.pack(fill="x", pady=(0,3))
        tk.Label(hdr, text=" □  실행 결과 로그",
                 bg=PANEL, fg=INK,
                 font=("Malgun Gothic",10,"bold")).pack(side="left", padx=12, pady=8)
        self._ar_var = tk.StringVar(value="□ 자동새로고침: ON")
        tk.Label(hdr, textvariable=self._ar_var,
                 bg=PANEL, fg=OK,
                 font=("Malgun Gothic",9)).pack(side="right", padx=12)

        # 텍스트 창
        tf = tk.Frame(p, bg=BORDER, bd=1)
        tf.pack(fill="both", expand=True, pady=(0,6))

        self._txt = tk.Text(
            tf,
            bg="#070d18",   # 스크린샷의 거의 검정 배경
            fg=INK,
            font=("Consolas",10),
            relief="flat", bd=0,
            wrap="word",
            state="disabled",
            selectbackground="#1e3a5f",
            insertbackground=ACCENT,
        )
        sb = ttk.Scrollbar(tf, orient="vertical",
                           command=self._txt.yview,
                           style="SC.Vertical.TScrollbar")
        self._txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._txt.pack(fill="both", expand=True, padx=1, pady=1)

        for tag, fg in [
            ("OK",   OK),
            ("ERR",  ERR),
            ("WARN", WARN),
            ("STEP", STEP),
            ("INFO", SUB),
            ("TIME", "#3a4f70"),
            ("SEP",  SEP_C),
        ]:
            self._txt.tag_configure(tag, foreground=fg)

    # ── 하단 버튼 바 ────────────────────────────────────────────
    def _bottom_bar(self, p):
        bar = tk.Frame(p, bg=BG)
        bar.pack(fill="x", pady=(2,0))

        def _btn(parent, text, bg, cmd, side="left", padx=(0,8)):
            b = tk.Button(parent, text=text,
                          bg=bg, fg="white",
                          activebackground=bg, activeforeground="white",
                          font=("Malgun Gothic",9,"bold"),
                          relief="flat", cursor="hand2",
                          command=cmd, padx=14, pady=7, bd=0,
                          highlightthickness=0)
            b.pack(side=side, padx=padx)
            return b

        _btn(bar, "□ 예약현황",  BTN_STATUS, self._open_status_browser)
        _btn(bar, "□ 새로고침",  BTN_REF,    self._flush)
        _btn(bar, "□ 지우기",    "#3a3a3a",  self._clear_log)

        self._ar_btn = tk.Button(
            bar, text="□ 자동정지",
            bg=BTN_AR, fg=SUB,
            activebackground=BTN_AR, activeforeground=INK,
            font=("Malgun Gothic",9,"bold"),
            relief="flat", cursor="hand2",
            command=self._toggle_ar,
            padx=12, pady=7, bd=0, highlightthickness=0)
        self._ar_btn.pack(side="right")


    # ── 예약현황: GUI 팝업 없이 Digital World 내 예약 화면만 열기 ─────────────
    def _open_status_browser(self):
        if self._running:
            q_log("자동화 실행 중 — 잠시 후 다시 시도하세요.", "WARN"); return
        self._running = True
        self._set_status("예약현황 화면 여는 중...", WARN)
        q_log("예약현황 화면만 열기 → Digital World 내 예약", "STEP")

        def on_result(ok, _):
            self.after(0, lambda: self._set_status(
                "예약현황 화면 열림 ✅" if ok else "예약현황 화면 열기 실패 ❌", OK if ok else ERR))
            self._running = False

        _run_core({"action":"open_status", "headless":False},
                  self._on_core_log, on_result)

    # ── 예약현황 별도 창 (미사용: 버튼에서 더 이상 호출하지 않음) ─────────
    def _open_status_window(self):
        if self._status_win and self._status_win.winfo_exists():
            self._status_win.lift(); return

        win = tk.Toplevel(self)
        win.title("Digital World — 내 예약 현황")
        win.configure(bg=BG)
        win.geometry("860x520")
        win.minsize(700, 400)
        self._status_win = win

        # 헤더
        hdr = tk.Frame(win, bg=PANEL,
                       highlightthickness=1, highlightbackground=BORDER)
        hdr.pack(fill="x", padx=12, pady=(12,6))
        tk.Label(hdr, text="□  내 예약 현황",
                 bg=PANEL, fg=INK,
                 font=("Malgun Gothic",11,"bold")).pack(side="left", padx=14, pady=9)
        tk.Button(hdr, text="🔄 조회",
                  bg=BTN_REF, fg="white",
                  activebackground=BTN_REF, activeforeground="white",
                  font=("Malgun Gothic",9,"bold"),
                  relief="flat", cursor="hand2",
                  command=self._fetch_status,
                  padx=12, pady=5, bd=0, highlightthickness=0
                  ).pack(side="right", padx=10, pady=7)

        # 트리뷰
        cols = ("no","room","date","time","title","status")
        heads = {"no":"#","room":"회의실","date":"날짜",
                 "time":"시간","title":"회의명","status":"상태"}
        widths = {"no":36,"room":130,"date":95,"time":115,
                  "title":200,"status":70}

        tf = tk.Frame(win, bg=BORDER, bd=1)
        tf.pack(fill="both", expand=True, padx=12, pady=(0,6))

        self._tree = ttk.Treeview(tf, columns=cols, show="headings",
                                  style="SC.Treeview", selectmode="browse")
        for c in cols:
            self._tree.heading(c, text=heads[c])
            self._tree.column(c, width=widths[c],
                              anchor="center", stretch=(c=="title"))
        vsb = ttk.Scrollbar(tf, orient="vertical",
                            command=self._tree.yview,
                            style="SC.Vertical.TScrollbar")
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True)

        self._tree.tag_configure("odd",  background=PANEL2)
        self._tree.tag_configure("even", background=PANEL)
        self._tree.tag_configure("ok",   foreground=OK)
        self._tree.tag_configure("sub",  foreground=SUB)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_sel)

        # 하단
        bot = tk.Frame(win, bg=BG)
        bot.pack(fill="x", padx=12, pady=(0,10))
        self._cancel_lbl = tk.Label(
            bot, text="취소할 예약을 선택하세요",
            bg=BG, fg=SUB, font=("Malgun Gothic",9))
        self._cancel_lbl.pack(side="left", padx=4)

        self._btn_cancel = tk.Button(
            bot, text="🗑  선택 예약 취소",
            bg=BTN_CLR, fg="white",
            activebackground="#8a2828", activeforeground="white",
            font=("Malgun Gothic",10,"bold"),
            relief="flat", cursor="hand2",
            command=self._do_cancel,
            padx=14, pady=7, bd=0, highlightthickness=0)
        self._btn_cancel.pack(side="right")

        # 창 열면서 자동 조회
        win.after(300, self._fetch_status)

    # ── 현황 조회 ───────────────────────────────────────────────
    def _fetch_status(self):
        if self._running:
            q_log("자동화 실행 중 — 잠시 후 다시 시도하세요.", "WARN"); return
        self._running = True
        self._set_status("현황 조회 중...", WARN)
        q_log("예약 현황 조회 → smartcity_core.py", "STEP")

        def on_result(ok, data):
            self._reservations = data if isinstance(data, list) else []
            self.after(0, self._render_table)
            self.after(0, lambda: self._set_status(
                f"조회 완료 ({len(self._reservations)}건)", OK if ok else WARN))
            self._running = False

        _run_core({"action":"fetch","headless":self._vhl.get()},
                  self._on_core_log, on_result)

    def _render_table(self):
        if not (self._status_win and self._status_win.winfo_exists()): return
        for item in self._tree.get_children(): self._tree.delete(item)
        if not self._reservations:
            self._tree.insert("","end",
                values=("","예약 내역 없음","","","",""), tags=("odd","sub"))
            return
        for i, r in enumerate(self._reservations):
            tr = "even" if i%2==0 else "odd"
            ts = "sub" if "취소" in r.get("status","") else "ok"
            self._tree.insert("","end",
                values=(r.get("no",""), r.get("room",""), r.get("date",""),
                        r.get("time",""), r.get("title",""), r.get("status","확정")),
                tags=(tr, ts), iid=str(i))

    def _on_tree_sel(self, _=None):
        sel = self._tree.selection()
        if not sel: return
        idx = int(sel[0])
        if idx < len(self._reservations):
            r = self._reservations[idx]
            self._cancel_lbl.configure(
                text=f"선택: {r.get('room','')}  {r.get('date','')}  "
                     f"{r.get('time','')}  [{r.get('title','')}]",
                fg=WARN)

    # ── 예약 취소 ───────────────────────────────────────────────
    def _do_cancel(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("선택 필요","취소할 예약을 선택하세요."); return
        idx = int(sel[0])
        if idx >= len(self._reservations):
            messagebox.showerror("오류","항목 정보를 찾을 수 없습니다."); return
        r = self._reservations[idx]
        if not messagebox.askyesno("예약 취소",
            f"취소하시겠습니까?\n\n"
            f"  회의실 : {r.get('room','')}\n"
            f"  날짜   : {r.get('date','')}\n"
            f"  시간   : {r.get('time','')}\n"
            f"  회의명 : {r.get('title','')}"): return

        self._running = True
        self._btn_cancel.configure(state="disabled", text="⏳ 취소 중...")
        self._set_status("취소 처리 중...", WARN)
        self._sep(f"▶ 예약 취소  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        def on_result(ok, _):
            self.after(0, lambda: self._btn_cancel.configure(
                state="normal", text="🗑  선택 예약 취소"))
            self.after(0, lambda: self._set_status(
                "취소 완료 ✅" if ok else "취소 실패 ❌", OK if ok else ERR))
            self._running = False
            if ok: self.after(1500, self._fetch_status)

        _run_core({"action":"cancel","row":r,"headless":self._vhl.get()},
                  self._on_core_log, on_result)

    # ── 예약 실행 ───────────────────────────────────────────────
    def _do_reserve(self):
        if self._running:
            messagebox.showwarning("실행 중","이미 진행 중입니다."); return
        if not ENV.get("AD_USERNAME") or not ENV.get("AD_PASSWORD"):
            messagebox.showerror("인증 오류",
                ".env 파일에 AD_USERNAME / AD_PASSWORD 를 설정하세요."); return

        cfg = dict(action="reserve",
                   date  = self._vdate.get().strip(),
                   room  = self._vroom.get().strip(),
                   start = self._vstart.get(),
                   end   = self._vend.get(),
                   title = self._vtitle.get().strip(),
                   loc1  = self._vl1.get(),
                   loc2  = self._vl2.get(),
                   loc3  = self._vl3.get(),
                   location_path = f"{self._vl2.get()}/{self._vl3.get()}",
                   rtype = self._vrtype.get(),
                   headless = self._vhl.get())
        if not cfg["title"]: messagebox.showerror("오류","회의명을 입력하세요."); return
        if not cfg["date"]:  messagebox.showerror("오류","날짜를 입력하세요.");   return

        self._running = True
        self._stats["total"] += 1
        self._upd_stats()
        self._btn_run.configure(state="disabled", text="🔴  예약 진행중...")
        self._set_status("실행 중...", WARN)
        self._start_rec("Digital World 접속", 10)
        self._sep(f"▶ 예약 실행  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._info(f"  회의실 : {cfg['room']}")
        self._info(f"  날짜   : {cfg['date']}  {cfg['start']} ~ {cfg['end']}")
        self._info(f"  회의명 : {cfg['title']}")

        def on_result(ok, _):
            self._stats["success" if ok else "fail"] += 1
            self.after(0, self._upd_stats)
            self.after(0, lambda: self._stop_rec(ok))
            self.after(0, lambda: self._set_status(
                "예약 완료 ✅" if ok else "예약 실패 ❌", OK if ok else ERR))
            self.after(0, lambda: self._btn_run.configure(
                state="normal", text="▶  자동화 예약 실행"))
            self._running = False
            # 예약 성공 후 별도 GUI 현황창은 띄우지 않음

        _run_core(cfg, self._on_core_log, on_result)

    # ── core 로그 콜백 ──────────────────────────────────────────
    def _on_core_log(self, tag, msg):
        log_q.put({"msg":msg,"tag":tag,
                   "time":datetime.now().strftime("%H:%M:%S")})
        try:
            self.after(0, lambda t=tag, m=msg: self._update_rec_from_log(t, m))
        except Exception:
            pass

    # ── 로그 헬퍼 ───────────────────────────────────────────────
    def _line(self, t, tag, msg):
        self._logs.append({"time":t,"tag":tag,"msg":msg})
        self._txt.configure(state="normal")
        self._txt.insert("end", f"[{t}] ", "TIME")
        self._txt.insert("end", f"[{tag:4s}] ", tag)
        icon = {"OK":"✅","ERR":"❌","WARN":"⚠️","STEP":"▶","INFO":"ℹ"}.get(tag,"")
        self._txt.insert("end", f"{icon} {msg}\n", tag)
        self._txt.see("end")
        self._txt.configure(state="disabled")

    def _sep(self, text):
        self._txt.configure(state="normal")
        self._txt.insert("end", f"\n{'─'*58}\n", "SEP")
        self._txt.insert("end", f"  {text}\n", "STEP")
        self._txt.insert("end", f"{'─'*58}\n", "SEP")
        self._txt.see("end")
        self._txt.configure(state="disabled")

    def _info(self, text):
        self._txt.configure(state="normal")
        self._txt.insert("end", f"  {text}\n", "INFO")
        self._txt.see("end")
        self._txt.configure(state="disabled")

    def _flush(self):
        while not log_q.empty():
            i = log_q.get_nowait()
            self._line(i["time"], i["tag"], i["msg"])

    def _poll(self):
        self._flush(); self.after(200, self._poll)

    def _ar_tick(self):
        if self._ar: self._flush()
        self.after(2000, self._ar_tick)

    def _clear_log(self):
        if not messagebox.askyesno("확인","로그를 삭제할까요?"): return
        self._logs.clear()
        self._txt.configure(state="normal")
        self._txt.delete("1.0","end")
        self._txt.configure(state="disabled")


    def _quick_cancel(self):
        """하단 예약취소 버튼 — 현황창 열고 선택 유도, 선택돼 있으면 바로 취소"""
        if self._status_win and self._status_win.winfo_exists():
            self._status_win.lift()
            sel = self._tree.selection() if hasattr(self, "_tree") else ()
            if sel:
                self._do_cancel()
            else:
                messagebox.showinfo("예약 취소",
                    "취소할 예약을 목록에서 선택한 뒤\n"
                    "[선택 예약 취소] 버튼을 눌러주세요.")
        else:
            self._open_status_window()

    def _toggle_ar(self):
        self._ar = not self._ar
        if self._ar:
            self._ar_btn.configure(text="□ 자동정지", fg=SUB)
            self._ar_var.set("□ 자동새로고침: ON")
        else:
            self._ar_btn.configure(text="▶ 자동시작", fg=WARN)
            self._ar_var.set("□ 자동새로고침: OFF")

    # ── 예약 진행 REC 표시 ─────────────────────────────────────
    def _progress_bar_text(self, pct: int) -> str:
        pct = max(0, min(100, int(pct)))
        filled = max(0, min(10, round(pct / 10)))
        return "[" + "■" * filled + "□" * (10 - filled) + f"] {pct}%"

    def _start_rec(self, step="Digital World 접속", pct=10):
        self._rec_active = True
        self._rec_blink = True
        self._rec_step = step
        self._rec_percent = pct
        if hasattr(self, "_rec_label"):
            self._rec_label.configure(fg=WARN, bg="#1a1320")
        self._render_rec()
        self.after(450, self._blink_rec)

    def _blink_rec(self):
        if not self._rec_active:
            return
        self._rec_blink = not self._rec_blink
        self._render_rec()
        self.after(450, self._blink_rec)

    def _render_rec(self):
        if not hasattr(self, "_sv_rec"):
            return
        if self._rec_active:
            dot = "🔴" if self._rec_blink else "⚫"
            self._sv_rec.set(f"{dot} REC  예약 진행중 · {self._rec_step}  {self._progress_bar_text(self._rec_percent)}")
        else:
            self._sv_rec.set("⚪ 대기 중")

    def _set_rec(self, step, pct=None):
        if not self._rec_active:
            return
        self._rec_step = step
        if pct is not None:
            self._rec_percent = max(self._rec_percent, int(pct))
        self._render_rec()

    def _stop_rec(self, ok: bool):
        self._rec_active = False
        self._rec_blink = False
        if not hasattr(self, "_sv_rec"):
            return
        if ok:
            self._rec_percent = 100
            self._sv_rec.set(f"✅ 예약 완료  {self._progress_bar_text(100)}")
            self._rec_label.configure(fg=OK, bg="#101c16")
        else:
            self._sv_rec.set("❌ 예약 실패 · 로그를 확인해주세요")
            self._rec_label.configure(fg=ERR, bg="#1f1111")

    def _update_rec_from_log(self, tag, msg):
        if not self._rec_active:
            return
        text = str(msg or "")
        steps = [
            ("브라우저 실행", "브라우저 실행", 10),
            ("예약 목록 접속", "Digital World 접속", 20),
            ("목록 URL", "로그인/목록 확인", 28),
            ("검색:", "회의실 검색", 40),
            ("검색 날짜 입력 확인", "날짜 확인", 45),
            ("예약 대상 확인", "예약 대상 확인", 58),
            ("예약 입력창", "예약창 진입", 68),
            ("회의명/내용 입력", "회의정보 입력", 76),
            ("예약 버튼 실행", "예약 등록", 88),
            ("예약 링크 클릭", "확인 처리", 92),
            ("예약 프로세스 완료", "완료", 100),
        ]
        for key, step, pct in steps:
            if key in text:
                self._set_rec(step, pct)
                break

    def _upd_stats(self):
        for k,v in self._svars.items(): v.set(str(self._stats[k]))

    def _set_status(self, text, color=None):
        self._sv_status.set(text)
        if color: self._sl_status.configure(fg=color)


# ── 진입점 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    if not CORE.exists():
        print(f"[주의] smartcity_core.py 없음: {CORE}")
    root = App()
    root.mainloop()
