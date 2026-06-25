# -*- coding: utf-8 -*-
"""
Digital_world_core.py — Digital World GUI 연동용 Playwright Core
======================================================
GUI(Digital_world_gui.py)에서 JSON 1줄을 stdin으로 받아 예약을 실행합니다.

지원 action
- reserve : 예약 실행
- fetch   : 내 예약 현황 조회(기본 구조 제공)
- cancel  : 예약 취소(선택 예약 취소 구조 제공)

중요 수정점
- GUI가 호출하던 Digital_world_core.py 누락 문제 해결
- 기존 smartcity_book.py에서 정상 동작하던 예약 로직을 JSON payload 기반으로 변경
- Digital World 구형 showModalDialog/더블클릭 팝업 대신 reservationCreateView.do 직접 진입
- alert/confirm/dialog 자동 accept 처리 = 키보드 Enter 효과
"""
import json
import os
import pathlib
import re
import sys
import time
from urllib.parse import quote

try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print(json.dumps({"type":"log","tag":"ERR","msg":f"playwright import 실패: {e}"}, ensure_ascii=False), flush=True)
    print(json.dumps({"type":"result","ok":False,"data":[]}, ensure_ascii=False), flush=True)
    raise

# ── 경로/URL ─────────────────────────────────────────────────────
BASE = pathlib.Path(r"D:\smart_city")
PROFILE_DIR = BASE / "pw-profile"
OUT = BASE / "capture"
OUT.mkdir(parents=True, exist_ok=True)

URL = "https://digitalworld.sec.samsung.net/mtr/forwardMtrEdit.do?reqNo=30&_menuId=AVEAVRzaBAloQPFg&_menuF=true#none"
MY_URL = "https://digitalworld.sec.samsung.net/mtr/forwardMtrEdit.do?reqNo=30&_menuId=AVEAVRzaBAloQPFg&_menuF=true#none"
CREATE_URL = "https://digitalworld.sec.samsung.net/mtr/forwardMtrEdit.do?reqNo=30&_menuId=AVEAVRzaBAloQPFg&_menuF=true#none"

# GUI 위치값 → Digital World locationId 매핑
# 기존 확인값은 유지하고, R5는 화면의 #searchRoom option 텍스트에서 동적으로 찾습니다.
LOCATION_MAP = {
    ("2C", "D동", "2층"): "8aa9036c2400aac2012403088bbf0081",
}


def emit(tag, msg):
    print(json.dumps({"type":"log","tag":tag,"msg":msg}, ensure_ascii=False), flush=True)


def result(ok, data=None):
    print(json.dumps({"type":"result","ok":bool(ok),"data":data or []}, ensure_ascii=False), flush=True)


def load_env():
    env = {}
    candidates = [
        pathlib.Path.cwd() / ".env",
        pathlib.Path(__file__).parent / ".env",
        BASE / ".env",
        BASE / ".env.txt",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
            break
    return env

ENV = load_env()
AD_USER = ENV.get("AD_USERNAME", "")
AD_PW = ENV.get("AD_PASSWORD", "")


def normalize_time(v: str) -> str:
    v = str(v or "").strip()
    if ":" in v:
        return v.replace(":", "")[:4]
    return v.zfill(4)[:4]


def with_colon(hhmm: str) -> str:
    hhmm = normalize_time(hhmm)
    return f"{hhmm[:2]}:{hhmm[2:]}"


def _norm_loc_text(v: str) -> str:
    return re.sub(r"[\s/()>\-_(]+", "", str(v or "")).lower()


def get_location_id(payload):
    """고정 매핑 우선. 없으면 None 반환 후 페이지 option에서 동적 탐색."""
    explicit = payload.get("location_id") or payload.get("locationId")
    if explicit:
        return explicit
    loc1 = payload.get("loc1", "")
    loc2 = payload.get("loc2", "")
    loc3 = payload.get("loc3", "")
    key = (loc1, loc2, loc3)
    return LOCATION_MAP.get(key)


def resolve_location_id_from_page(page, payload):
    """Digital World 검색 select(#searchRoom)의 option 텍스트에서 R5/A타워/층을 찾아 locationId를 얻는다."""
    mapped = get_location_id(payload)
    if mapped:
        return mapped

    loc2 = str(payload.get("loc2") or "모바일연구소(R5)")
    loc3 = str(payload.get("loc3") or "A타워/22층")
    path = str(payload.get("location_path") or f"{loc2}/{loc3}")
    tokens = [t for t in re.split(r"[/ >]+", path) if t and t != "전체"]
    wanted = _norm_loc_text("".join(tokens))

    page.wait_for_selector("#searchRoom", timeout=20000)
    options = page.evaluate("""() => [...document.querySelectorAll('#searchRoom option')]
      .map(o => ({value:o.value, text:(o.textContent||'').trim()}))
      .filter(o => o.value && o.text)""")

    # 1) 전체 문자열 포함
    for o in options:
        nt = _norm_loc_text(o.get('text'))
        if wanted and (wanted in nt or nt in wanted):
            emit("INFO", f"R5 위치 자동 감지: {o.get('text')}")
            return o.get('value')

    # 2) 토큰 모두 포함
    norm_tokens = [_norm_loc_text(t) for t in tokens if t]
    for o in options:
        nt = _norm_loc_text(o.get('text'))
        if norm_tokens and all(t in nt for t in norm_tokens):
            emit("INFO", f"R5 위치 자동 감지: {o.get('text')}")
            return o.get('value')

    sample = " | ".join([o.get('text','') for o in options[:12]])
    raise RuntimeError(f"R5 위치를 #searchRoom에서 찾지 못했습니다: {path} / 후보: {sample}")


def install_dialog_accept(ctx):
    def on_dialog(d):
        emit("INFO", f"알림 자동 확인(Enter): {d.message[:80]}")
        try:
            d.accept()
        except Exception:
            pass
    ctx.on("page", lambda pg: pg.on("dialog", on_dialog))
    return on_dialog


def ensure_login(page):
    if "forwardMtrEdit" in page.url:
        return True
    emit("STEP", "로그인 필요 → AD 자동 로그인 시도")

    ad = page.locator("a:has-text('AD 통합 인증'), a[href*='goAD']")
    if ad.count() > 0:
        try:
            ad.first.click()
        except Exception:
            pass
    else:
        try:
            page.evaluate("if (typeof goAD==='function') goAD(); else if(document.myForm) document.myForm.submit();")
        except Exception:
            pass

    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    time.sleep(0.5)

    if AD_USER:
        for sel in ["input[name*=user i]", "input[id*=user i]", "input[name*=id i]:not([type=password])", "input[type=text]:visible"]:
            loc = page.locator(sel)
            if loc.count() > 0:
                try:
                    loc.first.fill(AD_USER)
                    emit("INFO", "아이디 입력 완료")
                    break
                except Exception:
                    pass
    if AD_PW:
        pw = page.locator("input[type=password]:visible")
        if pw.count() > 0:
            try:
                pw.first.fill(AD_PW)
                emit("INFO", "비밀번호 입력 완료")
            except Exception:
                pass

    clicked = False
    for sel in ["#loginBtn", "input[type=submit]", "button[type=submit]", "input[value*=로그인]", "button:has-text('로그인')"]:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                loc.first.click()
                clicked = True
                break
            except Exception:
                pass
    if not clicked:
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

    try:
        page.wait_for_url("**forwardMtrEdit**", timeout=10000)
        emit("OK", "로그인 성공")
        return True
    except Exception:
        emit("WARN", "자동 로그인 실패 → 10초 수동 로그인 대기")
        try:
            page.wait_for_url("**forwardMtrEdit**", timeout=10000)
            return True
        except Exception:
            return False


def open_reservation_list(page):
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(0.5)
    if "reservationList" not in page.url:
        if not ensure_login(page):
            raise RuntimeError("로그인 실패")
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(0.5)
    emit("INFO", f"목록 URL: {page.url}")


def normalize_date(v: str) -> str:
    """GUI 입력 날짜를 Digital World 검색창용 yyyy-mm-dd 로 정리."""
    v = str(v or "").strip().replace(".", "-").replace("/", "-")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if not m:
        return v
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def set_search_date(page, date: str):
    """Digital World 날짜 입력창/숨김필드/datepicker 값을 모두 요청 날짜로 강제 동기화."""
    date = normalize_date(date)
    page.wait_for_selector("#searchDate", timeout=20000)

    # Digital World가 오늘 날짜로 다시 돌리는 경우가 있어, 화면에 있는 모든 날짜 관련 input을 같이 변경한다.
    page.evaluate(r"""(date) => {
      const norm = (v) => String(v || '').trim();
      const ymd = /^\d{4}[-\/.]\d{1,2}[-\/.]\d{1,2}$/;
      const compact = date.replaceAll('-', '');

      function setVal(el, val) {
        try { el.removeAttribute('readonly'); } catch(e) {}
        try { el.removeAttribute('disabled'); } catch(e) {}
        try { el.value = val; } catch(e) {}
        try { el.setAttribute('value', val); } catch(e) {}
        try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
        try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
        try { el.dispatchEvent(new Event('blur', {bubbles:true})); } catch(e) {}
      }

      // 1) 대표 검색일 필드
      const main = document.querySelector('#searchDate');
      if (main) setVal(main, date);

      // 2) name/id에 date가 들어가거나 현재 날짜 형식 값이 들어있는 모든 input 동기화
      document.querySelectorAll('input').forEach(el => {
        const idn = ((el.id || '') + ' ' + (el.name || '')).toLowerCase();
        const val = norm(el.value);
        if (idn.includes('date') || idn.includes('day') || ymd.test(val)) {
          setVal(el, date);
        }
      });

      // 3) yyyyMMdd 형식 hidden 필드가 있으면 같이 동기화
      document.querySelectorAll('input').forEach(el => {
        const val = norm(el.value);
        if (/^\d{8}$/.test(val)) setVal(el, compact);
      });

      // 4) jQuery datepicker 내부값까지 동기화
      if (window.$) {
        try { $('#searchDate').val(date); } catch(e) {}
        try {
          const parts = date.split('-').map(Number);
          if ($.datepicker && $('#searchDate').datepicker) {
            $('#searchDate').datepicker('setDate', new Date(parts[0], parts[1]-1, parts[2]));
          }
        } catch(e) {}
        try { $('input[id*=Date],input[name*=Date],input[id*=date],input[name*=date]').val(date).trigger('input').trigger('change').trigger('blur'); } catch(e) {}
      }
    }""", date)

    # 키보드 방식도 추가. datepicker가 포커스 이벤트를 요구하는 경우 대응.
    try:
        loc = page.locator("#searchDate")
        loc.click(timeout=3000)
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(date)
        page.keyboard.press("Tab")
    except Exception:
        pass

    # 최종 재강제: 키보드 입력 후 오늘로 돌아가는 경우 방지
    page.evaluate(r"""(date) => {
      const el = document.querySelector('#searchDate');
      if (el) { el.value = date; el.setAttribute('value', date); }
      if (window.$) { try { $('#searchDate').val(date).trigger('change'); } catch(e) {} }
    }""", date)

    try:
        actual = page.eval_on_selector("#searchDate", "e => e.value")
        emit("INFO", f"검색 날짜 입력 확인: {actual}")
        # 진단용: 날짜 관련 필드 상태 출력
        fields = page.evaluate(r"""() => [...document.querySelectorAll('input')]
          .filter(e => ((e.id||'')+(e.name||'')).toLowerCase().includes('date') || /^\d{4}[-\/.]\d{1,2}[-\/.]\d{1,2}$/.test(e.value||''))
          .slice(0,8).map(e => `${e.id||'-'}/${e.name||'-'}=${e.value}`)""")
        if fields:
            emit("INFO", "날짜 필드 상태: " + " | ".join(fields))
    except Exception:
        pass

def search_room(page, date, location_id):
    date = normalize_date(date)
    set_search_date(page, date)
    page.select_option("#searchRoom", value=location_id)

    # select change 이벤트 강제 발생
    try:
        page.evaluate(r"""(locationId) => {
          const el = document.querySelector('#searchRoom');
          if (!el) return;
          el.value = locationId;
          if (window.$) { try { $('#searchRoom').val(locationId).trigger('change'); } catch(e) {} }
          try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
        }""", location_id)
    except Exception:
        pass

    # 검색 버튼 클릭 직전에도 한 번 더 날짜를 고정한다.
    set_search_date(page, date)
    page.click("#btnSearch")
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    time.sleep(0.5)

    # 검색 후 사이트가 날짜를 오늘로 되돌렸는지 바로 검증
    try:
        actual = page.eval_on_selector("#searchDate", "e => e.value")
        if normalize_date(actual) != date:
            emit("WARN", f"검색 후 날짜가 바뀜: 요청={date}, 화면={actual} → 재설정 후 재검색")
            set_search_date(page, date)
            page.select_option("#searchRoom", value=location_id)
            page.click("#btnSearch")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            time.sleep(0.5)
    except Exception:
        pass


def _norm_room_name(v: str) -> str:
    """Digital World 화면의 회의실 표기 흔들림 대응용 정규화."""
    return re.sub(r"[\s_\-·>\(\)\[\]]+", "", str(v or "")).lower()


def _visible_room_names(page):
    """검색 결과에서 현재 화면에 보이는 회의실명을 최대한 수집."""
    try:
        return page.evaluate("""() => {
          const rows = [...document.querySelectorAll('tr.listTR, tr')];
          return rows.map(tr => (tr.innerText || '').trim())
            .filter(t => t && /회의실|강의장|접견실|B0|F0/.test(t))
            .slice(0, 30);
        }""")
    except Exception:
        return []



def _all_add_candidates(page):
    """페이지 전체에서 예약 가능한 _add(...) 셀 후보를 수집한다."""
    try:
        return page.evaluate(r"""() => [...document.querySelectorAll('td.room[ondblclick*=\"_add\"], [ondblclick*=\"_add\"]')]
          .map(el => ({text:(el.innerText||'').trim(), ondbl: el.getAttribute('ondblclick') || ''}))
          .filter(x => x.ondbl)
          .slice(0, 500)""")
    except Exception:
        return []

def _parse_add_args(ondbl):
    m = re.search(r"_add\((.*)\)", ondbl or "")
    if not m:
        return None
    raw = m.group(1)
    # Digital World _add 인자는 대부분 작은따옴표 문자열이므로 quote 기준 우선 파싱
    vals = re.findall(r"'([^']*)'", raw)
    if len(vals) >= 5:
        return vals
    vals = [a.strip().strip("'").strip('\"') for a in raw.split(',')]
    return vals if len(vals) >= 5 else None

def extract_create_params(page, room_match, start):
    """회의실 행과 시작시간 셀을 찾아 생성폼 파라미터를 추출.

    기존에는 tr.listTR + has_text(정확 문자열)에 의존해서,
    화면 표기가 조금만 달라도 '회의실 행을 찾지 못했습니다'가 발생했습니다.
    여기서는 1) 정확 검색, 2) 정규화 fuzzy 검색, 3) ondblclick의 roomNm 직접 검색 순으로 보강합니다.
    """
    start = normalize_time(start)
    target_norm = _norm_room_name(room_match)

    # 1) 기존 방식
    row = page.locator("tr.listTR").filter(has_text=room_match)
    if row.count() == 0:
        # 2) 전체 행에서 정규화 비교
        rows = page.locator("tr.listTR, tr")
        found = None
        for i in range(rows.count()):
            try:
                txt = rows.nth(i).inner_text(timeout=1000)
            except Exception:
                continue
            n = _norm_room_name(txt)
            if target_norm and (target_norm in n or n in target_norm):
                found = rows.nth(i)
                break
        if found is not None:
            row = found

    # 3) 아직 못 찾으면 ondblclick 안의 roomNm 후보에서 찾기
    if hasattr(row, 'count') and row.count() == 0:
        cells = page.locator("td.room[ondblclick*='_add']")
        for i in range(cells.count()):
            ondbl = cells.nth(i).get_attribute("ondblclick") or ""
            m = re.search(r"_add\((.*)\)", ondbl)
            if not m:
                continue
            args = [a.strip().strip("'") for a in m.group(1).split(",")]
            if len(args) > 1 and target_norm in _norm_room_name(args[1]):
                # 시작시간 셀이 맞는지 뒤에서 다시 검사
                row = cells.nth(i).locator("xpath=ancestor::tr[1]")
                break

    if hasattr(row, 'count') and row.count() == 0:
        # 행 텍스트 매칭이 실패해도 ondblclick 후보에 회의실명이 있으면 바로 파라미터 추출 가능
        allc = _all_add_candidates(page)
        hits = []
        for item in allc:
            a = _parse_add_args(item.get('ondbl',''))
            if a and len(a) > 1 and target_norm in _norm_room_name(a[1]):
                hits.append(item.get('ondbl',''))
        if hits:
            emit("WARN", "회의실 행 텍스트 매칭 실패 → _add 후보에서 직접 파라미터 추출")
            args = _parse_add_args(hits[0])
            roomid, roomnm, approveyn, manager, primary = args[0], args[1], args[2], args[3], args[4]
            return roomid, roomnm, approveyn, manager
        names = _visible_room_names(page)
        hint = " / 화면 감지: " + " | ".join(names[:8]) if names else ""
        raise RuntimeError(f"회의실 행을 찾지 못했습니다: {room_match}{hint}")

    # 시작시간 셀 찾기 — 작은따옴표/공백 변형까지 대응
    cell = row.first.locator(f"td.room[ondblclick*=\"'{start}',\"]") if hasattr(row, 'first') else row.locator(f"td.room[ondblclick*=\"'{start}',\"]")
    if cell.count() == 0:
        base = row.first if hasattr(row, 'first') else row
        cells = base.locator("td.room[ondblclick*='_add']")
        for i in range(cells.count()):
            ondbl = cells.nth(i).get_attribute("ondblclick") or ""
            if f"'{start}'" in ondbl or f",{start}," in ondbl:
                cell = cells.nth(i)
                break
    base = row.first if hasattr(row, 'first') else row

    def parse_add(ondbl):
        return _parse_add_args(ondbl)

    # 정확한 시작시간 셀이 안 보이는 경우가 있음:
    # Digital World 표가 가로 스크롤/부분 렌더링이라 14:00 컬럼을 DOM에서 못 잡아도
    # 같은 회의실 행의 다른 빈 셀에서 roomid/manager를 추출한 뒤 생성폼을 직접 연다.
    if cell.count() == 0:
        cells = base.locator("td.room[ondblclick*='_add']")
        candidates = []
        for i in range(cells.count()):
            od = cells.nth(i).get_attribute("ondblclick") or ""
            a = parse_add(od)
            if not a:
                continue
            c_start = a[5] if len(a) > 5 else ""
            c_primary = a[4] if len(a) > 4 else ""
            candidates.append((c_start, c_primary, od))
        visible_starts = ", ".join([with_colon(x[0]) for x in candidates if x[0]][:12])
        if visible_starts:
            emit("WARN", f"{with_colon(start)} 셀 직접 탐색 실패 → 같은 회의실 정보로 예약창 직접 진입 시도 / 감지시간: {visible_starts}")
        else:
            emit("WARN", f"{with_colon(start)} 셀 직접 탐색 실패 → 같은 회의실 정보로 예약창 직접 진입 시도")
        # 가능하면 빈 슬롯(primary 없음)을 우선 사용하고, 없으면 첫 셀 사용
        chosen = None
        for c_start, c_primary, od in candidates:
            if not c_primary:
                chosen = od
                break
        if chosen is None and candidates:
            chosen = candidates[0][2]
        if not chosen:
            # 행 내부에서 못 찾으면 페이지 전체 _add 후보에서 회의실명으로 다시 찾는다.
            allc = _all_add_candidates(page)
            target = _norm_room_name(room_match)
            room_hits = []
            for item in allc:
                a = parse_add(item.get('ondbl', ''))
                if not a or len(a) < 2:
                    continue
                if target and target in _norm_room_name(a[1]):
                    room_hits.append((a, item.get('ondbl','')))
            if room_hits:
                emit("WARN", f"행 내부 셀 없음 → 페이지 전체 후보에서 {room_hits[0][0][1]} 파라미터 추출")
                chosen = room_hits[0][1]
            else:
                sample = []
                for item in allc[:12]:
                    a = parse_add(item.get('ondbl',''))
                    if a and len(a) > 5:
                        sample.append(f"{a[1]}/{with_colon(a[5])}")
                hint = " / 후보: " + " | ".join(sample) if sample else ""
                raise RuntimeError(f"{with_colon(start)} 시작 셀을 찾지 못했고, 회의실 예약 파라미터도 추출하지 못했습니다.{hint}")
        ondbl = chosen
    else:
        ondbl = cell.first.get_attribute("ondblclick") or "" if hasattr(cell, 'first') else cell.get_attribute("ondblclick") or ""

    args = parse_add(ondbl)
    if not args:
        raise RuntimeError(f"예약 셀의 _add 인자를 추출하지 못했습니다: {(ondbl or '')[:120]}")
    roomid, roomnm, approveyn, manager, primary = args[0], args[1], args[2], args[3], args[4]

    # 정확히 요청한 시작 셀을 찾은 경우에만 primary로 예약불가 판정.
    # fallback에서는 다른 시간 셀의 primary일 수 있으므로 createView에서 최종 검증하게 둔다.
    if cell.count() > 0 and primary:
        raise RuntimeError(f"해당 슬롯은 이미 예약되어 있습니다. primary={primary}")
    return roomid, roomnm, approveyn, manager


def reserve(payload):
    date = normalize_date(payload.get("date") or time.strftime("%Y-%m-%d"))
    room = payload.get("room") or "회의실7_B09"
    start = normalize_time(payload.get("start") or "1400")
    end = normalize_time(payload.get("end") or "1500")
    title = payload.get("title") or "AX팀 내부회의"
    headless = bool(payload.get("headless", False))
    location_id = get_location_id(payload)

    emit("STEP", "브라우저 실행")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            no_viewport=True,
            args=["--start-maximized"],
        )
        on_dialog = install_dialog_accept(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", on_dialog)

        try:
            emit("STEP", "Digital World 예약 목록 접속")
            open_reservation_list(page)

            if not location_id:
                location_id = resolve_location_id_from_page(page, payload)
            emit("STEP", f"검색: {date} / {room} / {start}~{end}")
            search_room(page, date, location_id)
            try:
                page.screenshot(path=str(OUT / "gui_1_search.png"), full_page=True)
            except Exception:
                pass

            roomid, roomnm, approveyn, manager = extract_create_params(page, room, start)
            emit("OK", f"예약 대상 확인: {roomnm} / 요청시간 {with_colon(start)}~{with_colon(end)}")

            create_url = (
                f"{CREATE_URL}?reservationApprove={approveyn}"
                f"&reservationManager={manager}"
                f"&reservationRoomId={roomid}"
                f"&reservationRoomNm={quote(roomnm)}"
                f"&searchDate={date}&searchStime={start}&searchEtime={end}"
                f"&locationId={location_id}"
            )
            cp = ctx.new_page()
            cp.on("dialog", on_dialog)
            emit("STEP", "예약 입력창 직접 열기")
            cp.goto(create_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(0.2)

            emit("STEP", "회의명/내용 입력")
            cp.fill("#reservationTitle", title)
            try:
                cp.fill("#reservationPerpose", title)
            except Exception:
                pass

            # 시간이 select로 존재하면 명시 동기화
            try:
                cp.select_option("#reservationStime", value=start)
                cp.select_option("#reservationEtime", value=end)
            except Exception:
                pass
            try:
                cp.evaluate(
                    "([s,e]) => {"
                    "var a=document.getElementById('contentsStartTime');"
                    "var b=document.getElementById('contentsEndTime');"
                    "if(a)a.value=s; if(b)b.value=e;"
                    "}", [with_colon(start), with_colon(end)]
                )
            except Exception:
                pass

            # 예약자 본인 체크/동기화
            try:
                cp.evaluate("""
                () => {
                  const chk = document.getElementById('chkMySelf');
                  if (chk) {
                    chk.checked = true;
                    if (window.$) $('#chkMySelf').prop('checked', true);
                    if (typeof fncChkMySelf === 'function') fncChkMySelf();
                  }
                }
                """)
            except Exception as e:
                emit("WARN", f"예약자 본인 설정 확인 필요: {e}")

            try:
                cp.screenshot(path=str(OUT / "gui_2_filled.png"), full_page=True)
            except Exception:
                pass

            emit("STEP", "예약 버튼 실행")
            btn = cp.get_by_role("link", name="예약", exact=True)
            if btn.count() > 0:
                btn.first.click()
                emit("INFO", "예약 링크 클릭")
            else:
                fn = cp.evaluate("typeof _doUpdate === 'function' ? '_doUpdate' : (typeof _doSave === 'function' ? '_doSave' : '')")
                if fn:
                    cp.evaluate(f"{fn}();")
                    emit("INFO", f"{fn}() 호출")
                else:
                    raise RuntimeError("예약 버튼 또는 저장 함수를 찾지 못했습니다.")

            # 확인/완료 alert는 on_dialog가 자동 확인 = Enter
            time.sleep(0.4)
            try:
                cp.screenshot(path=str(OUT / "gui_3_result.png"), full_page=True)
            except Exception:
                pass

            # 고속모드: 예약 버튼 실행 후 목록 재검색을 생략하고 바로 종료합니다.
            # 확인/완료 alert는 install_dialog_accept()가 자동 확인합니다.
            ctx.close()
            emit("OK", "예약 프로세스 완료")
            result(True, [])
        except Exception as e:
            try:
                page.screenshot(path=str(OUT / "gui_error.png"), full_page=True)
            except Exception:
                pass
            ctx.close()
            emit("ERR", str(e))
            result(False, [])


def _set_mylist_date_range(page, days=60):
    """내 예약 화면의 기간을 오늘~days일 뒤로 넓히고 조회한다."""
    from datetime import date as _date, timedelta
    start = _date.today().strftime("%Y-%m-%d")
    end = (_date.today() + timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        page.evaluate(r"""([s,e]) => {
          const inputs = [...document.querySelectorAll('input')];
          const dateInputs = inputs.filter(x => /date|day|search/i.test((x.id||'')+(x.name||'')) || /^\d{4}-\d{2}-\d{2}$/.test(x.value||''));
          function setVal(el, val) {
            try { el.removeAttribute('readonly'); } catch(e) {}
            try { el.value = val; el.setAttribute('value', val); } catch(e) {}
            try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
            try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
            try { el.dispatchEvent(new Event('blur', {bubbles:true})); } catch(e) {}
          }
          if (dateInputs[0]) setVal(dateInputs[0], s);
          if (dateInputs[1]) setVal(dateInputs[1], e);
          if (window.$) {
            try { $(dateInputs[0]).val(s).trigger('change'); } catch(e) {}
            try { $(dateInputs[1]).val(e).trigger('change'); } catch(e) {}
          }
        }""", [start, end])
        emit("INFO", f"내 예약 조회 기간: {start} ~ {end}")
    except Exception as e:
        emit("WARN", f"조회 기간 설정 실패(무시): {e}")

    # 조회 버튼/검색 버튼이 있으면 클릭. 없으면 현재 결과를 그대로 파싱.
    try:
        clicked = page.evaluate(r"""() => {
          const btns = [...document.querySelectorAll('a,button,input[type=button],input[type=submit]')];
          const b = btns.find(x => /조회|검색|Search/i.test((x.innerText||x.value||'').trim()));
          if (b) { b.click(); return true; }
          if (typeof doSearch === 'function') { doSearch(); return true; }
          if (typeof fnSearch === 'function') { fnSearch(); return true; }
          return false;
        }""")
        if clicked:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            time.sleep(0.6)
    except Exception:
        pass


def _parse_my_reservations(page):
    """Digital World 내 예약 표를 GUI 테이블 형식으로 변환한다."""
    return page.evaluate(r"""() => {
      const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
      const tables = [...document.querySelectorAll('table')];
      let best = null;
      for (const t of tables) {
        const txt = norm(t.innerText);
        if (/회의날짜|회의명|결재상태|예약자|회의실/.test(txt)) { best = t; break; }
      }
      if (!best) best = document;

      const headerCells = [...best.querySelectorAll('thead th, tr th')].map(th => norm(th.innerText));
      function idxBy(words) {
        for (const w of words) {
          const i = headerCells.findIndex(h => h.includes(w));
          if (i >= 0) return i;
        }
        return -1;
      }
      const idx = {
        status: idxBy(['결재상태','상태']),
        datetime: idxBy(['회의날짜','예약일시','일시','날짜']),
        title: idxBy(['회의명','제목']),
        room: idxBy(['회의실','장소','위치']),
        user: idxBy(['예약자'])
      };

      const rows = [...best.querySelectorAll('tbody tr, tr')];
      const out = [];
      for (const tr of rows) {
        const cells = [...tr.querySelectorAll('td')].map(td => norm(td.innerText));
        if (cells.length < 2) continue;
        const joined = cells.join(' ');
        if (!/\d{4}-\d{2}-\d{2}/.test(joined) && !/결재|완료|예약/.test(joined)) continue;
        if (/조회된|내역 없음|데이터가 없습니다|검색 결과가 없습니다/.test(joined)) continue;

        let dt = idx.datetime >= 0 ? cells[idx.datetime] : (cells.find(c => /\d{4}-\d{2}-\d{2}/.test(c)) || '');
        let title = idx.title >= 0 ? cells[idx.title] : '';
        let status = idx.status >= 0 ? cells[idx.status] : '';
        let room = idx.room >= 0 ? cells[idx.room] : '';

        // 헤더가 없거나 컬럼 수가 적은 구형 표 대응: [상태, 회의날짜, 회의명, 예약자, ...]
        if (!dt && cells.length >= 2) dt = cells[1];
        if (!title && cells.length >= 3) title = cells[2];
        if (!status && cells.length >= 1) status = cells[0];

        // 회의실 컬럼이 화면에 없는 경우 제목/상세 링크에 남아있는 텍스트라도 사용
        if (!room) {
          const roomLike = cells.find(c => /회의실|강의장|접견실|_[A-Z]?\d+|[A-Z]\d{1,2}/.test(c) && c !== title);
          room = roomLike || '';
        }

        let date = '';
        let time = '';
        const m = dt.match(/(\d{4}-\d{2}-\d{2})\s*([0-9:]{4,5})?\s*[~\-–]\s*(?:(\d{4}-\d{2}-\d{2})\s*)?([0-9:]{4,5})?/);
        if (m) {
          date = m[1];
          time = `${m[2] || ''}~${m[4] || ''}`.replace(/^~|~$/g, '');
        } else {
          const d = dt.match(/\d{4}-\d{2}-\d{2}/);
          if (d) date = d[0];
          const ts = dt.match(/\d{1,2}:\d{2}/g) || [];
          if (ts.length) time = ts.join('~');
        }

        if (!title && cells.length) title = cells.find(c => c && !c.includes(date) && !/결재|완료/.test(c)) || '';
        out.push({
          no: String(out.length + 1),
          room: room || '-',
          date: date || dt,
          time: time || '-',
          title: title || '-',
          status: status || '확정',
          raw: cells
        });
      }
      return out;
    }""")


def fetch(payload):
    headless = bool(payload.get("headless", False))
    emit("STEP", "예약 현황 조회 접속")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            no_viewport=True,
            args=["--start-maximized"],
        )
        on_dialog = install_dialog_accept(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", on_dialog)
        try:
            page.goto(MY_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(0.5)
            if "forwardMtrEdit" not in page.url:
                if not ensure_login(page):
                    raise RuntimeError("로그인 실패")
                page.goto(MY_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(0.5)
            emit("INFO", f"내 예약 URL: {page.url}")
            _set_mylist_date_range(page, 60)
            data = _parse_my_reservations(page)
            try:
                page.screenshot(path=str(OUT / "gui_mylist.png"), full_page=True)
            except Exception:
                pass
            emit("OK", f"예약 현황 {len(data)}건 조회")
            ctx.close()
            result(True, data)
        except Exception as e:
            try:
                page.screenshot(path=str(OUT / "gui_mylist_error.png"), full_page=True)
            except Exception:
                pass
            ctx.close()
            emit("ERR", f"예약 현황 조회 실패: {e}")
            result(False, [])


def open_status(payload):
    """GUI 팝업/파싱 없이 Digital World '내 예약' 화면만 브라우저로 띄운다."""
    emit("STEP", "예약현황 화면 열기")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        on_dialog = install_dialog_accept(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", on_dialog)
        try:
            page.goto(MY_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(0.5)
            # 로그인 화면이면 기존 자동 로그인 1회만 시도. 실패해도 브라우저는 열어둔다.
            if "forwardMtrEdit" not in page.url:
                emit("STEP", "로그인 필요 → AD 자동 로그인 시도")
                ensure_login(page)
                try:
                    page.goto(MY_URL, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(0.5)
                except Exception:
                    pass
            emit("OK", "예약현황 화면을 브라우저에 열었습니다.")
            result(True, [])
            # 사용자가 화면을 볼 수 있도록 브라우저를 잠시 유지
            time.sleep(300)
            ctx.close()
            return
        except Exception as e:
            emit("ERR", f"예약현황 화면 열기 실패: {e}")
            result(False, [])
            try:
                time.sleep(30)
                ctx.close()
            except Exception:
                pass


def cancel(payload):
    emit("WARN", "취소 기능은 아직 매핑 정보가 부족해 실행하지 않았습니다.")
    result(False, [])


def main():
    try:
        raw = sys.stdin.readline().strip()
        payload = json.loads(raw) if raw else {"action":"reserve"}
    except Exception as e:
        emit("ERR", f"입력 JSON 파싱 실패: {e}")
        result(False, [])
        return

    action = payload.get("action", "reserve")
    try:
        if action == "reserve":
            reserve(payload)
        elif action == "fetch":
            fetch(payload)
        elif action == "open_status":
            open_status(payload)
        elif action == "cancel":
            cancel(payload)
        else:
            emit("ERR", f"알 수 없는 action: {action}")
            result(False, [])
    except Exception as e:
        emit("ERR", str(e))
        result(False, [])


if __name__ == "__main__":
    main()
