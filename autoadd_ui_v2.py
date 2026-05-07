import base64
import csv as _csv
import email.parser
import io
import json
import re as _re
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

try:
    import segno as _segno
    _HAS_QR = True
except ImportError:
    _HAS_QR = False


SCRIPT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = (
    SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else SCRIPT_DIR / ".venv" / "bin" / "python"
)

if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


HOST = "0.0.0.0"
PORT = 8766
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

_server_port: int = PORT
_local_ip: str = "127.0.0.1"


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def make_qr_png_b64(url: str) -> str:
    if not _HAS_QR:
        return ""
    try:
        qr = _segno.make(url, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=7, border=3, dark="#191F28", light="#FFFFFF")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""

job_lock = threading.Lock()
job_process: subprocess.Popen[str] | None = None
job_logs: list[str] = []
csv_review_path: str = ""
generic_waiting: bool = False
generic_waiting_msg: str = ""


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def add_if_value(command: list[str], option: str, value: str) -> None:
    value = str(value or "").strip()
    if value:
        command.extend([option, value])


def append_log(text: str) -> None:
    global csv_review_path, generic_waiting, generic_waiting_msg
    with job_lock:
        job_logs.append(text)
        m = _re.search(r"__CSV_REVIEW__:(.+)", text)
        if m:
            csv_review_path = m.group(1).strip()
        m2 = _re.search(r"__WAITING__:(.+)", text)
        if m2:
            generic_waiting = True
            generic_waiting_msg = m2.group(1).strip()


def vote_dir() -> Path:
    d = SCRIPT_DIR / "vote"
    d.mkdir(exist_ok=True)
    return d


def list_vote_files() -> list[str]:
    return sorted(
        p.name for p in vote_dir().iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


# ── 커맨드 빌더 ────────────────────────────────────────────────────────────────

def build_command(data: dict) -> list[str]:
    command = [sys.executable, str(SCRIPT_DIR / "autoadd.py")]
    command.extend(["--step", data.get("step", "all")])

    add_if_value(command, "--image",      data.get("image", ""))
    add_if_value(command, "--input-dir",  data.get("input_dir", "vote"))
    add_if_value(command, "--output",     data.get("output", "participants.csv"))
    add_if_value(command, "--members",    data.get("members", "members.csv"))
    add_if_value(command, "--url",        data.get("url", ""))

    command.extend(["--gender",             data.get("gender", "남")])
    command.extend(["--level",              data.get("level", "D")])
    command.extend(["--time-slot",          data.get("time_slot", "unknown")])
    command.extend(["--min-confidence",     str(data.get("min_confidence", "0.35"))])
    command.extend(["--entry-time",         data.get("entry_time", "1")])
    command.extend(["--create-room-times",  data.get("create_room_times", "all")])
    command.extend(["--room-title-template",data.get("room_title_template", "{date} 정규운동 {time}타임")])
    command.extend(["--room-kind",          data.get("room_kind", "정모")])
    command.extend(["--room-game-type",     data.get("room_game_type", "자유게임")])
    command.extend(["--room-capacity",      data.get("room_capacity", "0") or "0"])
    add_if_value(command, "--court-name",  data.get("court_name", ""))
    add_if_value(command, "--room-start",  data.get("room_start", ""))
    add_if_value(command, "--skill-label", data.get("skill_label", ""))
    if data.get("use_chrome_debug") and data.get("chrome_debug_port"):
        command.extend(["--chrome-debug-port", str(data["chrome_debug_port"])])
    if data.get("use_game_mode"):
        command.extend(["--site-mode", "game"])

    bool_flags = {
        "latest_only":          "--latest-only",
        "include_unmatched":    "--include-unmatched",
        "no_cache":             "--no-cache",
        "entry_allow_unmatched":"--entry-allow-unmatched",
        "allow_same_name":      "--allow-same-name",
        "temporary_profile":    "--temporary-profile",
        "close_browser":        "--close-browser",
        "dry_run_entry":        "--dry-run-entry",
        "headless":             "--headless",
    }
    for key, flag in bool_flags.items():
        if data.get(key):
            command.append(flag)
    if not data.get("pause_after_ocr", True):
        command.append("--no-pause-after-ocr")
    return command


# ── 작업 제어 ──────────────────────────────────────────────────────────────────

def read_process_output(process: subprocess.Popen[str]) -> None:
    assert process.stdout
    for line in process.stdout:
        append_log(line)
    code = process.wait()
    append_log(f"\n종료 코드: {code}\n")
    with job_lock:
        global job_process
        if job_process is process:
            job_process = None


def start_job(data: dict) -> tuple[bool, str]:
    global job_process, job_logs, csv_review_path, generic_waiting, generic_waiting_msg
    with job_lock:
        if job_process and job_process.poll() is None:
            return False, "이미 실행 중입니다."
        job_logs = []
        csv_review_path = ""
        generic_waiting = False
        generic_waiting_msg = ""

    cmd = build_command(data)
    append_log("실행: " + " ".join(cmd) + "\n\n")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["AUTOADD_UI_PORT"] = str(_server_port)  # Chrome 버튼 → 서버 호출에 사용
    popen_kwargs: dict = dict(
        cwd=SCRIPT_DIR,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", env=env,
    )
    # Windows에서 서브프로세스 실행 시 검은 콘솔 창이 별도로 뜨지 않도록 설정
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    process = subprocess.Popen(cmd, **popen_kwargs)
    with job_lock:
        job_process = process
    threading.Thread(target=read_process_output, args=(process,), daemon=True).start()
    return True, "실행을 시작했습니다."


def continue_job() -> tuple[bool, str]:
    global csv_review_path, generic_waiting, generic_waiting_msg
    with job_lock:
        process = job_process
    if not process or process.poll() is not None or not process.stdin:
        return False, "실행 중인 작업이 없습니다."
    try:
        process.stdin.write("\n")
        process.stdin.flush()
        with job_lock:
            csv_review_path = ""
            generic_waiting = False
            generic_waiting_msg = ""
        append_log("\n[계속]\n")
        return True, "계속 진행합니다."
    except BrokenPipeError:
        return False, "입력할 수 없는 상태입니다."


def stop_job() -> tuple[bool, str]:
    with job_lock:
        process = job_process
    if not process or process.poll() is not None:
        return False, "실행 중인 작업이 없습니다."
    process.terminate()
    append_log("\n[중지 요청]\n")
    return True, "중지 요청을 보냈습니다."


def open_csv(path_value: str) -> tuple[bool, str]:
    path = Path(path_value or "participants.csv").expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    if not path.exists():
        return False, f"CSV 파일이 없습니다: {path}"
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        return False, str(exc)
    return True, "CSV를 열었습니다."


def save_uploaded_files(handler: "Handler") -> tuple[bool, str, list[str]]:
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw_body = handler.rfile.read(length)

    # email 파서로 multipart 분해 (cgi 모듈 없이)
    fake = f"Content-Type: {content_type}\r\n\r\n".encode() + raw_body
    try:
        msg = email.parser.BytesParser().parsebytes(fake)
    except Exception as exc:
        return False, str(exc), []

    vdir = vote_dir()
    saved: list[str] = []
    payload = msg.get_payload()
    if not isinstance(payload, list):
        payload = [payload]

    for part in payload:
        disp = part.get("Content-Disposition", "")
        m = _re.search(r'filename="([^"]+)"', disp)
        if not m:
            continue
        fname = Path(m.group(1)).name
        if Path(fname).suffix.lower() not in IMAGE_EXTS:
            continue
        data = part.get_payload(decode=True)
        if data:
            (vdir / fname).write_bytes(data)
            saved.append(fname)

    return True, f"{len(saved)}장 저장됨", saved


# ── HTML ───────────────────────────────────────────────────────────────────────

MOBILE_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>STAB AutoAdd System — 사진 전송</title>
<style>
:root{
  --blue:#0064FF; --blue-d:#0050D0; --blue-l:#EBF3FF;
  --green:#05C072; --red:#F04452; --red-l:#FFF0F0;
  --bg:#F2F4F7; --card:#fff; --border:#E8ECF0;
  --t1:#191F28; --t2:#6B7684; --t3:#B0B8C1;
}
@media(prefers-color-scheme:dark){
  :root{--bg:#111214;--card:#1C1E22;--border:#2A2D32;--t1:#F2F4F6;--t2:#8B95A1;--t3:#4E5968;--blue-l:#0A1A2E;}
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overscroll-behavior:none}
body{
  background:var(--bg);color:var(--t1);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:16px;-webkit-font-smoothing:antialiased;
}
.wrap{
  max-width:480px;margin:0 auto;
  padding:env(safe-area-inset-top,20px) 20px env(safe-area-inset-bottom,20px);
  min-height:100vh;display:flex;flex-direction:column;
}
/* 헤더 */
.header{
  display:flex;align-items:center;gap:10px;
  padding:18px 0 16px;
  border-bottom:1px solid var(--border);margin-bottom:20px;
}
.header-logo{font-size:20px;font-weight:800;color:var(--t1)}
.header-logo span{color:var(--blue)}
.header-sub{font-size:13px;color:var(--t2);margin-left:auto}
/* 상태 배너 */
.status-banner{
  display:flex;align-items:center;gap:10px;
  padding:14px 16px;border-radius:12px;
  background:var(--card);border:1px solid var(--border);
  margin-bottom:16px;font-size:14px;font-weight:600;
}
.status-banner .icon{font-size:20px}
.status-banner .text{flex:1}
.status-banner .count{font-size:13px;color:var(--t2)}
/* 파일 선택 */
.pick-btn{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:10px;width:100%;padding:32px 20px;
  background:var(--blue-l);border:2px dashed var(--blue);
  border-radius:16px;cursor:pointer;
  font:inherit;color:var(--blue);font-weight:700;font-size:16px;
  -webkit-appearance:none;appearance:none;
  transition:opacity .15s;
}
.pick-btn:active{opacity:.7}
.pick-btn .pick-icon{font-size:42px}
.pick-btn .pick-sub{font-size:13px;font-weight:500;color:var(--t2);text-align:center}
#file-input-mobile{display:none}
/* 선택 파일 미리보기 */
.preview-grid{
  display:grid;grid-template-columns:repeat(3,1fr);gap:8px;
  margin:16px 0;
}
.preview-cell{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;background:var(--border)}
.preview-cell img{width:100%;height:100%;object-fit:cover;display:block}
.preview-cell .del-btn{
  position:absolute;top:4px;right:4px;
  width:22px;height:22px;border-radius:50%;
  background:rgba(0,0,0,.55);color:#fff;
  border:none;font-size:14px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
}
.preview-more{
  aspect-ratio:1;border-radius:10px;
  background:var(--border);display:flex;
  align-items:center;justify-content:center;
  font-size:14px;font-weight:700;color:var(--t2);
}
/* 옵션 */
.option-row{
  display:flex;align-items:center;gap:12px;
  padding:14px 16px;background:var(--card);
  border:1px solid var(--border);border-radius:12px;
  margin-bottom:12px;cursor:pointer;
}
.option-row .opt-text{flex:1;font-size:14px;font-weight:600}
.option-row .opt-sub{font-size:12px;color:var(--t2);margin-top:2px}
.toggle{
  width:44px;height:26px;border-radius:13px;
  background:var(--border);position:relative;
  transition:background .2s;flex-shrink:0;
  border:none;cursor:pointer;
}
.toggle::after{
  content:'';position:absolute;top:3px;left:3px;
  width:20px;height:20px;border-radius:50%;background:#fff;
  transition:transform .2s;box-shadow:0 1px 3px rgba(0,0,0,.2);
}
.toggle.on{background:var(--blue)}
.toggle.on::after{transform:translateX(18px)}
/* 전송 버튼 */
.upload-btn{
  display:flex;align-items:center;justify-content:center;gap:10px;
  width:100%;padding:18px;margin-top:auto;padding-top:20px;
  background:var(--blue);color:#fff;
  border:none;border-radius:16px;
  font:inherit;font-size:17px;font-weight:800;
  cursor:pointer;transition:background .15s, opacity .15s;
}
.upload-btn:active:not(:disabled){background:var(--blue-d)}
.upload-btn:disabled{opacity:.4;cursor:not-allowed}
/* 진행 바 */
.progress-wrap{margin-top:12px;display:none}
.progress-bar{
  height:4px;background:var(--border);border-radius:4px;overflow:hidden;margin-bottom:8px;
}
.progress-fill{height:100%;background:var(--blue);width:0;transition:width .3s;border-radius:4px}
.progress-text{font-size:13px;color:var(--t2);text-align:center}
/* 결과 화면 */
.result{
  flex:1;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;gap:14px;
}
.result-icon{font-size:64px}
.result-title{font-size:22px;font-weight:800;color:var(--t1)}
.result-sub{font-size:15px;color:var(--t2);line-height:1.6}
.result-btn{
  padding:14px 32px;background:var(--blue);color:#fff;
  border:none;border-radius:12px;font:inherit;font-size:16px;font-weight:700;
  cursor:pointer;margin-top:8px;
}
.result-btn.ghost{background:var(--bg);color:var(--t1);border:1.5px solid var(--border)}
.result-btns{display:flex;flex-direction:column;gap:10px;width:100%;max-width:280px}
</style>
</head>
<body>
<div class="wrap" id="main-wrap">

  <div class="header">
    <div class="header-logo">STAB <span>AutoAdd</span> System</div>
    <div class="header-sub">Made by SGI</div>
  </div>

  <div class="status-banner" id="status-banner">
    <span class="icon">📁</span>
    <div>
      <div class="text">vote 폴더 현황</div>
      <div class="count" id="banner-count">불러오는 중...</div>
    </div>
  </div>

  <!-- 파일 선택 -->
  <button class="pick-btn" onclick="document.getElementById('file-input-mobile').click()">
    <span class="pick-icon">📸</span>
    <span>갤러리에서 사진 선택</span>
    <span class="pick-sub">투표 참여자 목록 스크린샷을 선택하세요<br>여러 장 동시 선택 가능</span>
  </button>
  <input type="file" id="file-input-mobile" multiple accept="image/jpeg,image/png"
         onchange="onPick(event)">

  <!-- 미리보기 그리드 -->
  <div class="preview-grid" id="preview-grid" style="display:none"></div>

  <!-- 옵션 -->
  <div class="option-row" onclick="toggleAutoClear()">
    <div>
      <div class="opt-text">이전 사진 자동 삭제</div>
      <div class="opt-sub">전송 전에 vote 폴더를 비웁니다</div>
    </div>
    <button class="toggle on" id="auto-clear-toggle" onclick="event.stopPropagation();toggleAutoClear()"></button>
  </div>

  <!-- 진행 상태 -->
  <div class="progress-wrap" id="progress-wrap">
    <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-text" id="progress-text">전송 중...</div>
  </div>

  <!-- 전송 버튼 -->
  <button class="upload-btn" id="upload-btn" disabled onclick="doUpload()">
    <span id="upload-btn-icon">📤</span>
    <span id="upload-btn-text">사진을 선택하면 활성화됩니다</span>
  </button>

</div>

<!-- 결과 화면 -->
<div class="wrap" id="result-wrap" style="display:none">
  <div class="result">
    <div class="result-icon" id="result-icon">✅</div>
    <div class="result-title" id="result-title">전송 완료</div>
    <div class="result-sub" id="result-sub"></div>
    <div class="result-btns">
      <button class="result-btn" onclick="resetPage()">더 보내기</button>
      <button class="result-btn ghost" onclick="window.close()">닫기</button>
    </div>
  </div>
</div>

<script>
const files = [];
let autoClear = true;

function toggleAutoClear() {
  autoClear = !autoClear;
  document.getElementById("auto-clear-toggle").classList.toggle("on", autoClear);
}

function onPick(e) {
  const picked = [...e.target.files].filter(f => /\.(jpe?g|png)$/i.test(f.name));
  files.length = 0;
  files.push(...picked);
  e.target.value = "";
  renderPreview();
  updateBtn();
}

function renderPreview() {
  const grid = document.getElementById("preview-grid");
  if(!files.length){ grid.style.display="none"; return; }
  grid.style.display = "grid";
  const MAX = 8;
  grid.innerHTML = "";
  files.slice(0, MAX).forEach((f, i) => {
    const cell = document.createElement("div");
    cell.className = "preview-cell";
    const img = document.createElement("img");
    img.src = URL.createObjectURL(f);
    const del = document.createElement("button");
    del.className = "del-btn";
    del.textContent = "×";
    del.onclick = () => { files.splice(i,1); renderPreview(); updateBtn(); };
    cell.appendChild(img); cell.appendChild(del);
    grid.appendChild(cell);
  });
  if(files.length > MAX) {
    const more = document.createElement("div");
    more.className = "preview-more";
    more.textContent = `+${files.length - MAX}`;
    grid.appendChild(more);
  }
}

function updateBtn() {
  const btn = document.getElementById("upload-btn");
  const txt = document.getElementById("upload-btn-text");
  const ico = document.getElementById("upload-btn-icon");
  if(files.length) {
    btn.disabled = false;
    txt.textContent = `${files.length}장 전송하기`;
    ico.textContent = "📤";
  } else {
    btn.disabled = true;
    txt.textContent = "사진을 선택하면 활성화됩니다";
    ico.textContent = "📤";
  }
}

async function doUpload() {
  if(!files.length) return;
  document.getElementById("upload-btn").disabled = true;
  document.getElementById("progress-wrap").style.display = "block";

  // 이전 파일 삭제
  if(autoClear) {
    setProgress(0, "이전 사진 삭제 중...");
    await fetch("/clear-vote", {method:"POST"});
  }

  // 파일 업로드
  setProgress(10, `0 / ${files.length}장 전송 중...`);
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  let saved = 0;
  try {
    const res = await fetch("/upload", {method:"POST", body:fd});
    const j = await res.json();
    saved = j.saved ? j.saved.length : 0;
    setProgress(100, `${saved}장 전송 완료`);
    setTimeout(() => showResult(true, saved), 400);
  } catch(e) {
    showResult(false, 0, String(e));
  }
}

function setProgress(pct, text) {
  document.getElementById("progress-fill").style.width = pct + "%";
  document.getElementById("progress-text").textContent = text;
}

function showResult(ok, count, err) {
  document.getElementById("main-wrap").style.display = "none";
  document.getElementById("result-wrap").style.display = "flex";
  document.getElementById("result-icon").textContent = ok ? "✅" : "⚠️";
  document.getElementById("result-title").textContent = ok ? "전송 완료!" : "전송 실패";
  document.getElementById("result-sub").textContent = ok
    ? `${count}장을 컴퓨터로 전송했습니다.\n이제 컴퓨터에서 실행 버튼을 누르세요.`
    : (err || "알 수 없는 오류가 발생했습니다.");
}

function resetPage() {
  files.length = 0;
  document.getElementById("main-wrap").style.display = "flex";
  document.getElementById("result-wrap").style.display = "none";
  document.getElementById("preview-grid").style.display = "none";
  document.getElementById("progress-wrap").style.display = "none";
  document.getElementById("progress-fill").style.width = "0";
  updateBtn();
  loadStatus();
}

async function loadStatus() {
  const r = await fetch("/vote-files");
  const j = await r.json();
  const cnt = j.files ? j.files.length : 0;
  document.getElementById("banner-count").textContent =
    cnt ? `현재 ${cnt}장 저장되어 있어요` : "아직 비어 있어요";
}

loadStatus();
updateBtn();
</script>
</body>
</html>
"""

HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>STAB AutoAdd System</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
/* ── 토스 디자인 시스템 ────────────────────────────────── */
:root {
  --blue:        #0064FF;
  --blue-light:  #EBF3FF;
  --blue-dark:   #0050D0;
  --green:       #05C072;
  --red:         #F04452;
  --red-light:   #FFF0F0;
  --orange:      #FF7B00;
  --bg:          #F2F4F7;
  --card:        #FFFFFF;
  --border:      #E8ECF0;
  --border2:     #D1D8E0;
  --t1:          #191F28;
  --t2:          #6B7684;
  --t3:          #B0B8C1;
  --r-card:      16px;
  --r-btn:       12px;
  --r-input:     10px;
  --shadow:      0 2px 16px rgba(0,0,0,.06);
  --shadow-sm:   0 1px 6px  rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#111214; --card:#1C1E22; --border:#2A2D32; --border2:#373B42;
    --t1:#F2F4F6; --t2:#8B95A1; --t3:#4E5968;
    --blue-light:#0A1A2E; --red-light:#1F0A0C;
    --shadow:0 2px 16px rgba(0,0,0,.4); --shadow-sm:0 1px 6px rgba(0,0,0,.3);
  }
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:15px}
body{
  background:var(--bg); color:var(--t1);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;
  line-height:1.5; -webkit-font-smoothing:antialiased;
}

/* ── 앱 바 ─────────────────────────────────────────────── */
.appbar{
  position:sticky; top:0; z-index:300;
  display:flex; align-items:center; gap:12px;
  height:56px; padding:0 28px;
  background:var(--card); border-bottom:1px solid var(--border);
  box-shadow:var(--shadow-sm);
}
.appbar-logo{font-size:18px;font-weight:800;letter-spacing:-.5px;color:var(--t1)}
.appbar-logo span{color:var(--blue)}
.appbar-made{font-size:11px;color:var(--t3);font-weight:500;margin-left:6px;align-self:flex-end;padding-bottom:1px}
.appbar-space{flex:1}
.appbar-hint{font-size:12px;color:var(--t3);display:flex;gap:6px;align-items:center}
kbd{
  display:inline-flex;align-items:center;padding:2px 7px;
  font:inherit;font-size:11px;font-weight:600;
  border:1px solid var(--border2);border-radius:6px;
  background:var(--bg);color:var(--t2);
}

/* ── 상태 칩 ─────────────────────────────────────────────── */
.chip{
  display:inline-flex;align-items:center;gap:6px;
  padding:5px 12px;border-radius:20px;
  font-size:12px;font-weight:700;
  background:var(--bg);color:var(--t2);border:1.5px solid var(--border2);
  transition:all .25s;
}
.chip-dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0}
.chip.run{background:#E8FBF1;color:#007A45;border-color:#9BE5C0}
.chip.run .chip-dot{animation:pulse 1s ease-in-out infinite}
.chip.done{background:var(--blue-light);color:var(--blue);border-color:#99C2FF}
.chip.err{background:var(--red-light);color:var(--red);border-color:#F9B0B5}
@media(prefers-color-scheme:dark){
  .chip.run{background:#0A2116;color:#05C072;border-color:#0D6B37}
  .chip.done{background:var(--blue-light);color:#5B9BFF;border-color:#1A4080}
}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

/* ── 메인 ─────────────────────────────────────────────────── */
main{max-width:860px;margin:0 auto;padding:24px 28px 60px}

/* ── 섹션 타이틀 ──────────────────────────────────────────── */
.section-title{
  font-size:13px;font-weight:700;color:var(--t2);
  text-transform:uppercase;letter-spacing:.6px;
  margin:28px 0 10px;
}
.section-title:first-child{margin-top:0}

/* ── 카드 ──────────────────────────────────────────────────── */
.card{
  background:var(--card);border-radius:var(--r-card);
  border:1px solid var(--border);box-shadow:var(--shadow-sm);
  overflow:hidden;margin-bottom:10px;
}
.card-head{
  display:flex;align-items:center;gap:10px;
  padding:14px 20px;cursor:pointer;user-select:none;
  border-bottom:1px solid var(--border);
}
.card-head:hover{background:var(--bg)}
.card-head h2{font-size:14px;font-weight:700;flex:1}
.card-head .emo{font-size:16px}
.card-head .caret{color:var(--t3);font-size:11px;transition:transform .2s}
.card.closed .card-head{border-bottom:none}
.card.closed .card-head .caret{transform:rotate(-90deg)}
.card.closed .card-body{display:none}
.card-body{padding:20px}

/* ── 작업 세그먼트 컨트롤 ─────────────────────────────────── */
.seg{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:6px; padding:6px;
  background:var(--bg);
  border:1px solid var(--border);
  border-radius:14px;
}
.seg-btn{
  padding:9px 4px;border:none;border-radius:10px;
  font:inherit;font-size:13px;font-weight:600;
  color:var(--t2);background:transparent;
  cursor:pointer;transition:all .18s;text-align:center;
}
.seg-btn:hover{color:var(--t1);background:var(--card)}
.seg-btn.active{
  background:var(--card);color:var(--blue);
  box-shadow:var(--shadow-sm);
}
@media(max-width:600px){
  .seg{grid-template-columns:1fr 1fr}
}

/* ── 파일 업로드 존 ───────────────────────────────────────── */
.drop-zone{
  border:2px dashed var(--border2);border-radius:14px;
  padding:32px 24px;text-align:center;
  cursor:pointer;transition:all .2s;
  background:var(--bg);
}
.drop-zone:hover,.drop-zone.over{
  border-color:var(--blue);background:var(--blue-light);
}
.drop-zone .dz-icon{font-size:36px;display:block;margin-bottom:10px}
.drop-zone .dz-title{font-size:15px;font-weight:700;color:var(--t1);margin-bottom:4px}
.drop-zone .dz-sub{font-size:13px;color:var(--t2)}
.drop-zone .dz-sub span{color:var(--blue);font-weight:600}
#file-input{display:none}

/* ── 파일 목록 ────────────────────────────────────────────── */
.file-list{margin-top:14px;display:flex;flex-direction:column;gap:6px}
.file-item{
  display:flex;align-items:center;gap:10px;
  padding:10px 14px;
  background:var(--bg);border:1px solid var(--border);
  border-radius:10px;font-size:13px;
}
.file-item .fi-thumb{
  width:36px;height:36px;border-radius:6px;
  object-fit:cover;flex-shrink:0;
  background:var(--border);
}
.file-item .fi-name{flex:1;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.file-item .fi-size{color:var(--t3);font-size:11px;white-space:nowrap}
.file-item .fi-del{
  width:22px;height:22px;border-radius:50%;
  border:none;background:var(--border);color:var(--t2);
  font-size:14px;cursor:pointer;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  transition:background .15s;
}
.file-item .fi-del:hover{background:var(--red-light);color:var(--red)}

.file-bar{
  display:flex;align-items:center;gap:8px;
  margin-top:12px;padding-top:12px;
  border-top:1px solid var(--border);
}
.file-count{font-size:13px;color:var(--t2);flex:1}
.file-count strong{color:var(--t1)}

/* ── 그리드 ──────────────────────────────────────────────── */
.grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;align-items:end}
.g2{grid-column:span 2}.g3{grid-column:span 3}.g4{grid-column:span 4}.g6{grid-column:span 6}
@media(max-width:720px){
  .grid{grid-template-columns:repeat(2,1fr)}
  .g2,.g3,.g4,.g6{grid-column:span 2}
}
@media(max-width:440px){
  .grid{grid-template-columns:1fr}
  .g2,.g3,.g4,.g6{grid-column:span 1}
}

/* ── 필드 ────────────────────────────────────────────────── */
.field{display:flex;flex-direction:column;gap:6px}
.field-label{
  font-size:12px;font-weight:700;color:var(--t2);
  display:flex;align-items:center;gap:5px;
}
.field-label .tip{
  font-size:11px;font-weight:500;color:var(--t3);
  cursor:help;border-bottom:1px dashed var(--t3);
}
input,select{
  font:inherit;font-size:14px;
  border:1.5px solid var(--border);border-radius:var(--r-input);
  padding:10px 13px;background:var(--card);color:var(--t1);
  width:100%;transition:border-color .15s,box-shadow .15s;
  -webkit-appearance:none;appearance:none;
}
select{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23b0b8c1' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;
  padding-right:34px;
}
input:focus,select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px rgba(0,100,255,.12)}
input.err{border-color:var(--red)}
input[type=range]{
  padding:0;height:4px;border:none;
  background:var(--border2);border-radius:4px;cursor:pointer;
  accent-color:var(--blue);
}
input[type=range]:focus{box-shadow:none}

/* ── 토글 버튼 그룹 (OCR 방식) ──────────────────────────── */
.toggle-group{display:flex;gap:0;border:1.5px solid var(--border);border-radius:var(--r-input);overflow:hidden}
.toggle-btn{
  flex:1;padding:9px 6px;border:none;
  font:inherit;font-size:13px;font-weight:600;
  color:var(--t2);background:var(--card);cursor:pointer;
  border-right:1px solid var(--border);transition:all .15s;text-align:center;
}
.toggle-btn:last-child{border-right:none}
.toggle-btn.on{background:var(--blue);color:#fff}
.toggle-btn:hover:not(.on){background:var(--bg)}

/* ── 슬라이더 필드 ───────────────────────────────────────── */
.slider-wrap{display:flex;align-items:center;gap:10px}
.slider-wrap input[type=range]{flex:1}
.slider-val{
  min-width:38px;text-align:right;
  font-size:13px;font-weight:700;color:var(--blue);
}

/* ── 체크박스 ────────────────────────────────────────────── */
.checks{display:flex;flex-wrap:wrap;gap:6px 0;padding-top:16px;margin-top:4px}
.chk{
  display:flex;align-items:center;gap:8px;
  width:50%;font-size:13px;font-weight:500;
  color:var(--t1);cursor:pointer;padding:5px 0;
}
.chk input[type=checkbox]{
  width:17px;height:17px;flex-shrink:0;
  border:1.5px solid var(--border2);border-radius:5px;
  cursor:pointer;accent-color:var(--blue);
  -webkit-appearance:checkbox;appearance:checkbox;
}
.chk-sub{font-size:11px;color:var(--t3);margin-left:25px;margin-top:-4px;width:100%}
@media(max-width:600px){.chk{width:100%}}

/* ── 버튼 ──────────────────────────────────────────────── */
button{
  font:inherit;border:none;border-radius:var(--r-btn);
  padding:12px 20px;font-weight:700;font-size:14px;
  cursor:pointer;transition:all .15s;white-space:nowrap;
  display:inline-flex;align-items:center;gap:7px;
}
button:active:not(:disabled){transform:scale(.97)}
button:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--blue);color:#fff;font-size:15px;padding:14px 28px;border-radius:14px}
.btn-primary:hover:not(:disabled){background:var(--blue-dark)}
.btn-ghost{background:var(--bg);color:var(--t1);border:1.5px solid var(--border)}
.btn-ghost:hover:not(:disabled){border-color:var(--blue);color:var(--blue)}
.btn-danger{background:var(--red-light);color:var(--red);border:1.5px solid #F9B0B5}
.btn-danger:hover:not(:disabled){background:#FFD6DA}
.btn-sm{font-size:12px;padding:7px 13px;border-radius:8px;font-weight:600}
.btn-icon{padding:10px}

/* ── 실행 바 ─────────────────────────────────────────────── */
.run-bar{
  display:flex;align-items:center;gap:10px;
  padding:20px;background:var(--card);
  border-radius:var(--r-card);border:1px solid var(--border);
  box-shadow:var(--shadow-sm);margin-bottom:10px;
}
.run-bar-actions{display:flex;gap:8px;flex-wrap:wrap}
.run-bar-status{flex:1;font-size:13px;color:var(--t2);min-width:0}
.run-bar-status.warn{color:var(--orange)}
.run-bar-status.err{color:var(--red)}
#csv-table th,#csv-table td{padding:4px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
#csv-table th{font-size:12px;color:var(--t2);font-weight:600;background:var(--surface);position:sticky;top:0}
#csv-table tr:last-child td{border-bottom:none}
#csv-table .tag-1{background:#EEF6FF;color:#2563EB;border-radius:4px;padding:1px 6px;font-size:12px}
#csv-table .tag-2{background:#F0FDF4;color:#16A34A;border-radius:4px;padding:1px 6px;font-size:12px}
#csv-table .tag-u{background:#FFF7ED;color:#EA580C;border-radius:4px;padding:1px 6px;font-size:12px}

/* ── 로그 ──────────────────────────────────────────────── */
.log-meta{
  display:flex;align-items:center;gap:8px;
  margin-bottom:8px;
}
.log-meta-text{font-size:12px;color:var(--t3);flex:1}
pre#log{
  background:#0D1117;color:#C9D1D9;
  border-radius:12px;padding:16px;
  font-family:"SF Mono",Menlo,Consolas,monospace;
  font-size:12px;line-height:1.7;
  height:420px;min-height:200px;
  overflow:auto;resize:vertical;
  border:1px solid #21262D;
  white-space:pre-wrap;word-break:break-all;
}
.lw{color:#E3B341}.le{color:#F85149}.lg{color:#3FB950}
.li{color:#79C0FF}.ld{color:#6E7681}.lc{color:#D2A8FF}

/* ── 섹션 숨김 ────────────────────────────────────────────── */
.step-all .s-ocr,.step-all .s-entry{display:block}
.step-ocr .s-entry{display:none}
.step-entry .s-ocr{display:none}
.step-rooms .s-ocr{display:none}

/* ── 구분선 ──────────────────────────────────────────────── */
.divider{height:1px;background:var(--border);margin:16px 0}

/* ── QR 카드 ─────────────────────────────────────────────── */
.qr-card-body{display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap}
.qr-img-wrap{flex-shrink:0}
.qr-img-wrap img{width:120px;height:120px;border-radius:10px;display:block;background:var(--border)}
.qr-img-placeholder{width:120px;height:120px;border-radius:10px;background:var(--bg);border:1.5px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--t2);text-align:center;padding:12px}
.qr-info{flex:1;min-width:160px}
.qr-title{font-size:14px;font-weight:700;margin-bottom:6px}
.qr-desc{font-size:12px;color:var(--t2);line-height:1.6;margin-bottom:10px}
.qr-url{font-size:12px;color:var(--blue);font-weight:600;word-break:break-all;margin-bottom:8px;cursor:pointer}
.qr-stat{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--t2)}
.qr-stat strong{font-size:13px;font-weight:700;color:var(--t1)}

/* ── 업로드 진행 토스트 ──────────────────────────────────── */
.toast{
  position:fixed;bottom:28px;left:50%;transform:translateX(-50%) translateY(20px);
  background:var(--t1);color:var(--bg);
  padding:11px 20px;border-radius:12px;
  font-size:13px;font-weight:600;
  opacity:0;transition:opacity .25s,transform .25s;
  pointer-events:none;z-index:500;
}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

@media(max-width:600px){
  .appbar{padding:0 16px}
  main{padding:16px 16px 50px}
  .run-bar{flex-wrap:wrap}
}
</style>
</head>
<body class="step-all">

<!-- 앱 바 -->
<div class="appbar">
  <div class="appbar-logo">STAB <span>AutoAdd</span> System</div>
  <div class="appbar-made">Made by SGI</div>
  <div id="chip" class="chip"><span class="chip-dot"></span><span id="chip-txt">대기 중</span></div>
  <div class="appbar-space"></div>
  <div class="appbar-hint" id="appbar-hint"></div>
</div>

<main>

  <!-- 작업 선택 -->
  <div class="section-title">작업 선택</div>
  <div class="seg" id="seg">
    <button class="seg-btn active" data-step="all"     onclick="setStep('all',this)">전체 실행</button>
    <button class="seg-btn"        data-step="ocr"     onclick="setStep('ocr',this)">사진 추출만</button>
    <button class="seg-btn"        data-step="entry"   onclick="setStep('entry',this)">게스트 추가만</button>
    <button class="seg-btn"        data-step="create-rooms" onclick="setStep('create-rooms',this)">방 생성 후 등록</button>
  </div>
  <input type="hidden" id="step" value="all">

  <!-- 실행 바 -->
  <div style="margin-top:10px"></div>
  <div class="run-bar">
    <div class="run-bar-actions">
      <button id="btn-run" class="btn-primary" onclick="doRun()">▶&nbsp;실행</button>
      <button id="btn-cont" class="btn-primary" onclick="doCont()" style="display:none;font-size:14px;padding:10px 20px">⏎&nbsp;계속 진행</button>
      <button id="btn-stop" class="btn-danger" onclick="doStop()">■&nbsp;중지</button>
    </div>
    <div id="run-status" class="run-bar-status"></div>
    <button class="btn-ghost btn-sm" onclick="doOpenCsv()">📄 CSV 열기</button>
  </div>

  <!-- CSV 검토 패널 -->
  <div id="csv-review-panel" style="display:none;margin-top:10px">
    <div class="card" style="border:2px solid var(--blue)">
      <div class="card-body" style="padding:12px 14px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
          <span style="font-weight:600;font-size:14px">📋 OCR 결과 확인</span>
          <span id="csv-countdown-text" style="font-size:13px;color:var(--t2)"></span>
          <div style="flex:1"></div>
          <button class="btn-ghost btn-sm" onclick="cancelCountdown()">카운트다운 취소</button>
          <button id="btn-csv-start" class="btn-primary" onclick="doContFromReview()" style="font-size:13px;padding:6px 16px">▶&nbsp;등록 시작</button>
        </div>
        <div style="overflow-x:auto;max-height:260px;overflow-y:auto">
          <table id="csv-table" style="width:100%;border-collapse:collapse;font-size:13px">
            <thead id="csv-thead"></thead>
            <tbody id="csv-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- QR 모바일 업로드 (step: all, ocr) -->
  <div class="s-ocr">
    <div class="section-title">모바일 업로드</div>
    <div class="card">
      <div class="card-body qr-card-body">
        <div class="qr-img-wrap" id="qr-img-wrap">
          <img id="qr-img" src="/qr.png" alt="QR" onerror="this.style.display='none';document.getElementById('qr-placeholder').style.display='flex'">
          <div class="qr-img-placeholder" id="qr-placeholder" style="display:none">segno 미설치<br>pip install segno</div>
        </div>
        <div class="qr-info">
          <div class="qr-title">📱 폰으로 스캔해서 사진 전송</div>
          <div class="qr-desc">같은 WiFi에 연결된 폰으로 QR을 스캔하면<br>모바일 업로드 페이지가 열립니다.</div>
          <div class="qr-url" id="qr-url" onclick="copyQrUrl()" title="클릭하여 복사">—</div>
          <div class="qr-stat">vote 폴더: <strong id="qr-file-count">—</strong></div>
        </div>
      </div>
    </div>
  </div>

  <!-- 파일 섹션 (step: all, ocr) -->
  <div class="s-ocr">
    <div class="section-title">투표 사진</div>
    <div class="card">
      <div class="card-body">

        <!-- 드래그 앤 드롭 존 -->
        <div class="drop-zone" id="dropZone"
             onclick="document.getElementById('file-input').click()"
             ondragover="onDragOver(event)"
             ondragleave="onDragLeave(event)"
             ondrop="onDrop(event)">
          <input type="file" id="file-input" multiple accept="image/jpeg,image/png,.jpg,.jpeg,.png"
                 onchange="onFilePick(event)">
          <span class="dz-icon">🖼️</span>
          <div class="dz-title">사진을 드래그하거나 클릭해서 추가</div>
          <div class="dz-sub">JPG · PNG · 여러 장 동시 선택 가능&nbsp;&nbsp;<span>클릭해서 파일 선택</span></div>
        </div>

        <!-- 선택된 파일 목록 -->
        <div id="file-list" class="file-list" style="display:none"></div>

        <!-- 파일 바 -->
        <div id="file-bar" class="file-bar" style="display:none">
          <div class="file-count" id="file-count"></div>
          <button class="btn-ghost btn-sm" onclick="clearVoteFiles()">🗑 전체 삭제</button>
        </div>

      </div>
    </div>
  </div>

  <!-- 파일 경로 (고급) -->
  <div class="s-ocr">
    <div class="card closed">
      <div class="card-head" onclick="toggleCard(this)">
        <span class="emo">⚙️</span>
        <h2>고급 파일 경로 설정</h2>
        <span style="font-size:12px;color:var(--t3);margin-right:6px">보통은 건드릴 필요 없어요</span>
        <span class="caret">▼</span>
      </div>
      <div class="card-body" style="padding-top:16px">
        <div class="grid">
          <div class="field g3">
            <div class="field-label">이미지 폴더 경로</div>
            <input id="input_dir" value="vote" placeholder="비우면 vote 폴더 자동 사용">
          </div>
          <div class="field g3">
            <div class="field-label">특정 이미지 파일 지정</div>
            <input id="image" placeholder="폴더 전체를 쓰려면 비워두세요">
          </div>
          <div class="field g3">
            <div class="field-label">참가자 CSV 출력</div>
            <input id="output" value="participants.csv">
          </div>
          <div class="field g3">
            <div class="field-label">회원 정보 CSV</div>
            <input id="members" value="members.csv">
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- OCR 설정 -->
  <div class="s-ocr">
    <div class="section-title">추출 설정</div>
    <div class="card">
      <div class="card-head" onclick="toggleCard(this)">
        <span class="emo">🔍</span>
        <h2>OCR 옵션</h2>
        <span class="caret">▼</span>
      </div>
      <div class="card-body">
        <div class="grid">
          <div class="field g2">
            <div class="field-label">기본 성별</div>
            <select id="gender"><option>남</option><option>여</option></select>
          </div>
          <div class="field g2">
            <div class="field-label">기본 급수</div>
            <select id="level">
              <option>D</option><option>S</option><option>A</option>
              <option>B</option><option>C</option><option>E</option>
              <option>F</option><option>N</option>
            </select>
          </div>
          <div class="field g2">
            <div class="field-label">기본 타임</div>
            <select id="time_slot">
              <option value="unknown">자동 감지</option>
              <option value="1">1타임</option>
              <option value="2">2타임</option>
              <option value="both">풀타임</option>
            </select>
          </div>
          <div class="field g6">
            <div class="field-label">
              OCR 최소 신뢰도
              <span class="tip" title="낮추면 더 많이 인식하지만 오인식도 늘어납니다">?</span>
            </div>
            <div class="slider-wrap">
              <input type="range" id="min_confidence" min="0.1" max="0.9" step="0.05" value="0.35"
                     oninput="document.getElementById('conf-val').textContent=parseFloat(this.value).toFixed(2);saveSettings()">
              <span class="slider-val" id="conf-val">0.35</span>
            </div>
          </div>
        </div>
        <div class="checks">
          <label class="chk"><input type="checkbox" id="latest_only"> 최근 사진 1장만</label>
          <label class="chk"><input type="checkbox" id="include_unmatched"> 미매칭 후보 포함</label>
          <label class="chk"><input type="checkbox" id="no_cache"> OCR 캐시 끄기</label>
          <label class="chk"><input type="checkbox" id="pause_after_ocr" checked> CSV 확인 후 계속</label>
        </div>
      </div>
    </div>
  </div>

  <!-- 사이트 등록 설정 -->
  <div class="s-entry">
    <div class="section-title">사이트 등록 설정</div>
    <div class="card">
      <div class="card-head" onclick="toggleCard(this)">
        <span class="emo">🌐</span>
        <h2>nearminton 등록</h2>
        <span class="caret">▼</span>
      </div>
      <div class="card-body">
        <div class="grid">
          <div class="field g6">
            <div class="field-label">사이트 URL <span class="tip" title="비우면 nearminton.com/moim.php 기본 URL 사용">?</span></div>
            <input id="url" placeholder="https://nearminton.com/moim.php">
          </div>
          <div class="field g2">
            <div class="field-label">게스트 타임</div>
            <select id="entry_time"><option>1</option><option>2</option><option>all</option></select>
          </div>
          <div class="field g2">
            <div class="field-label">생성 타임</div>
            <select id="create_room_times"><option>all</option><option>1</option><option>2</option></select>
          </div>
          <div class="field g2">
            <div class="field-label">모집 인원 <span class="tip" title="0이면 해당 타임 참가자 수로 자동 설정">?</span></div>
            <input id="room_capacity" value="0" type="number" min="0">
          </div>
          <div class="field g4">
            <div class="field-label">방 제목 템플릿 <span class="tip" title="{time} → 1 또는 2, {date} → 오늘 날짜(월/일)">?</span></div>
            <input id="room_title_template" value="{date} 정규운동 {time}타임">
          </div>
          <div class="field g2" id="moim-kind-field">
            <div class="field-label">모임 종류</div>
            <select id="room_kind"><option>정모</option><option>번개</option></select>
          </div>
          <div class="field g2">
            <div class="field-label">게임 방식</div>
            <select id="room_game_type"><option>자유게임</option><option>미니대회</option></select>
          </div>
          <div class="field g2">
            <div class="field-label">실력 구분</div>
            <input id="skill_label" placeholder="없으면 비워두세요">
          </div>
          <div class="field g3">
            <div class="field-label">구장</div>
            <input id="court_name" placeholder="서울과학기술대학교 실내 체육관">
          </div>
          <div class="field g3">
            <div class="field-label">시작 일시 <span class="tip" title="YYYY-MM-DD HH:MM 형식. 비우면 현재 시각">?</span></div>
            <input id="room_start" placeholder="2025-05-06 19:00">
          </div>
        </div>
        <!-- 동배게임 모드 안내 배너 -->
        <div id="game-mode-banner" style="display:none;margin-bottom:10px;padding:8px 12px;
             background:var(--blue-l);border-left:3px solid var(--blue);border-radius:6px;
             font-size:13px;color:var(--t1)">
          🎮 <strong>동배게임 백업 모드</strong> — 동배모임 게임생성 오류 시 사용합니다.<br>
          <span style="color:var(--t2)">competition?type=all에서 게임을 생성하고 게임판에서 바로 참가자를 등록합니다.</span>
        </div>
        <div class="checks">
          <label class="chk" style="font-weight:600">
            <input type="checkbox" id="use_game_mode" checked onchange="onGameModeChange()">
            🎮 동배게임 모드 사용 (백업)
          </label>
          <label class="chk"><input type="checkbox" id="allow_same_name"> 동명이인 허용</label>
          <label class="chk"><input type="checkbox" id="entry_allow_unmatched"> 미매칭 후보 등록</label>
          <label class="chk"><input type="checkbox" id="headless"> 브라우저 숨김 실행</label>
          <label class="chk"><input type="checkbox" id="temporary_profile"> 임시 Chrome 프로필</label>
          <label class="chk"><input type="checkbox" id="close_browser"> 완료 후 브라우저 닫기</label>
          <label class="chk"><input type="checkbox" id="dry_run_entry"> 등록 dry-run (테스트)</label>
          <label class="chk" style="align-items:center;gap:6px">
            <input type="checkbox" id="use_chrome_debug" onchange="document.getElementById('chrome_debug_port_wrap').style.display=this.checked?'flex':'none';saveSettings()">
            기존 Chrome 창 사용
            <span style="display:none;align-items:center;gap:4px" id="chrome_debug_port_wrap">
              포트 <input type="number" id="chrome_debug_port" value="9222" min="1024" max="65535"
                style="width:70px;padding:2px 6px;border:1px solid var(--border);border-radius:6px;font-size:13px"
                oninput="saveSettings()">
            </span>
          </label>
        </div>
      </div>
    </div>
  </div>

  <!-- 로그 -->
  <div class="section-title">로그</div>
  <div class="card">
    <div class="card-body">
      <div class="log-meta">
        <div class="log-meta-text" id="log-info">대기 중</div>
        <button class="btn-ghost btn-sm" onclick="copyLog()">복사</button>
        <button class="btn-ghost btn-sm" onclick="clearLog()">지우기</button>
      </div>
      <pre id="log"></pre>
    </div>
  </div>

</main>

<!-- 토스트 -->
<div class="toast" id="toast"></div>

<script>
// ── 상태 ─────────────────────────────────────────────────────
let cursor = 0, running = false, logLines = 0, timer = null;
const uploadedFiles = new Map(); // filename -> {name, size, dataUrl}

// ── 설정 저장 ─────────────────────────────────────────────────
const FIELDS  = ["step","image","input_dir","output","members","gender","level",
                 "time_slot","min_confidence","url","entry_time",
                 "create_room_times","room_title_template","room_capacity",
                 "room_kind","room_game_type","court_name","room_start","skill_label",
                 "chrome_debug_port"];
const CHECKS  = ["latest_only","include_unmatched","no_cache","pause_after_ocr",
                 "allow_same_name","entry_allow_unmatched","headless",
                 "temporary_profile","close_browser","dry_run_entry","use_chrome_debug",
                 "use_game_mode"];

function saveSettings() {
  const s = {};
  FIELDS.forEach(id => { const e=document.getElementById(id); if(e) s[id]=e.value; });
  CHECKS.forEach(id => { const e=document.getElementById(id); if(e) s[id]=e.checked; });
  try{ localStorage.setItem("autoadd_v2",JSON.stringify(s)); }catch(e){}
}
function loadSettings() {
  let s; try{ s=JSON.parse(localStorage.getItem("autoadd_v2")||"null"); }catch(e){}
  if(!s) return;
  // 기본값 마이그레이션: 구버전 기본값이면 새 기본값으로 교체
  const MIGRATIONS = { room_title_template: ["[우동배] {time}타임", "{date} 정규운동 {time}타임"] };
  Object.entries(MIGRATIONS).forEach(([id,[old_val,new_val]])=>{ if(s[id]===old_val) s[id]=new_val; });
  FIELDS.forEach(id => { const e=document.getElementById(id); if(e&&s[id]!=null) e.value=s[id]; });
  CHECKS.forEach(id => { const e=document.getElementById(id); if(e&&s[id]!=null) e.checked=s[id]; });
  // 기존 Chrome 창 포트 입력란 복원
  const dbgWrap = document.getElementById("chrome_debug_port_wrap");
  if(dbgWrap) dbgWrap.style.display = s.use_chrome_debug ? "flex" : "none";
  // 동배게임 모드 복원 (저장값이 없으면 기본 checked 유지)
  if(s.use_game_mode != null) onGameModeChange(s.use_game_mode);
  else onGameModeChange(true);
  // OCR 모드 버튼 복원
  // 신뢰도 표시 복원
  const conf = document.getElementById("min_confidence");
  if(conf) document.getElementById("conf-val").textContent = parseFloat(conf.value).toFixed(2);
  // step 세그먼트 복원
  if(s.step) {
    const btn = document.querySelector(`.seg-btn[data-step="${s.step}"]`);
    if(btn) setStep(s.step, btn, true);
  }
}
[...FIELDS,...CHECKS].forEach(id => {
  const e = document.getElementById(id);
  if(e) e.addEventListener("change", saveSettings);
});

// ── 단계 ────────────────────────────────────────────────────
function setStep(step, btn, silent) {
  document.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
  if(btn) btn.classList.add("active");
  document.getElementById("step").value = step;
  const map = {all:"step-all",ocr:"step-ocr",entry:"step-entry","create-rooms":"step-rooms"};
  document.body.className = map[step]||"step-all";
  if(!silent) saveSettings();
}

// ── 동배게임 모드 토글 ───────────────────────────────────────
function onGameModeChange(forcedValue) {
  const chk = document.getElementById("use_game_mode");
  if(forcedValue != null) chk.checked = !!forcedValue;
  const isGame = chk.checked;
  // 동배게임 모드이면 '방 생성 후 등록' 단계로 자동 전환
  if(isGame) {
    const btn = document.querySelector('.seg-btn[data-step="create-rooms"]');
    if(btn) setStep("create-rooms", btn, true);
  }
  // 동배게임에서는 '모임 종류' 필드가 불필요 → 숨김 처리
  const gameModeFields = document.getElementById("moim-kind-field");
  if(gameModeFields) gameModeFields.style.display = isGame ? "none" : "";
  // '동배게임 모드' 안내 배너 표시
  const banner = document.getElementById("game-mode-banner");
  if(banner) banner.style.display = isGame ? "block" : "none";
  saveSettings();
}

// ── 카드 접기/펼치기 ─────────────────────────────────────────
function toggleCard(hd) { hd.closest(".card").classList.toggle("closed"); }

// ── 파일 업로드 UI ────────────────────────────────────────────
function onDragOver(e) { e.preventDefault(); document.getElementById("dropZone").classList.add("over"); }
function onDragLeave() { document.getElementById("dropZone").classList.remove("over"); }
function onDrop(e) {
  e.preventDefault();
  document.getElementById("dropZone").classList.remove("over");
  handleFiles([...e.dataTransfer.files]);
}
function onFilePick(e) { handleFiles([...e.target.files]); e.target.value=""; }

function handleFiles(files) {
  const imgs = files.filter(f => /\.(jpe?g|png)$/i.test(f.name));
  if(!imgs.length) { showToast("JPG/PNG 파일만 선택할 수 있어요"); return; }
  imgs.forEach(f => {
    const reader = new FileReader();
    reader.onload = ev => {
      uploadedFiles.set(f.name, { name:f.name, size:f.size, dataUrl:ev.target.result, file:f });
      renderFileList();
    };
    reader.readAsDataURL(f);
  });
  uploadFiles(imgs);
}

function renderFileList() {
  const list = document.getElementById("file-list");
  const bar  = document.getElementById("file-bar");
  const cnt  = document.getElementById("file-count");
  if(uploadedFiles.size === 0) { list.style.display="none"; bar.style.display="none"; return; }
  list.style.display = "flex";
  bar.style.display  = "flex";
  cnt.innerHTML = `<strong>${uploadedFiles.size}장</strong> 선택됨`;
  list.innerHTML = "";
  uploadedFiles.forEach((info, name) => {
    const item = document.createElement("div");
    item.className = "file-item";
    item.innerHTML = `
      <img class="fi-thumb" src="${info.dataUrl}" alt="">
      <span class="fi-name">${esc(name)}</span>
      <span class="fi-size">${fmtSize(info.size)}</span>
      <button class="fi-del" onclick="removeFile('${esc(name)}')" title="삭제">×</button>`;
    list.appendChild(item);
  });
}

function removeFile(name) {
  uploadedFiles.delete(name);
  renderFileList();
  fetch("/remove-vote-file", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name})
  });
}

async function uploadFiles(files) {
  showToast(`${files.length}장 업로드 중...`);
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  try {
    const res = await fetch("/upload", {method:"POST", body:fd});
    const json = await res.json();
    if(json.ok) showToast(`${json.saved.length}장 업로드 완료`);
    else showToast("업로드 실패: " + json.message, true);
  } catch(e) { showToast("업로드 오류: " + e, true); }
}

async function clearVoteFiles() {
  if(!confirm("vote/ 폴더의 이미지를 모두 삭제할까요?")) return;
  uploadedFiles.clear();
  renderFileList();
  const res = await fetch("/clear-vote", {method:"POST"});
  const json = await res.json();
  showToast(json.message);
}

// 서버에 이미 있는 파일 불러오기 (새로고침 시)
async function loadExistingFiles() {
  const res = await fetch("/vote-files");
  const json = await res.json();
  if(json.files && json.files.length) {
    // 서버 파일은 썸네일 없이 이름만 표시
    json.files.forEach(name => {
      if(!uploadedFiles.has(name))
        uploadedFiles.set(name, { name, size:0, dataUrl:"", file:null });
    });
    renderFileList();
  }
}

// ── 데이터 수집 ───────────────────────────────────────────────
function collectData() {
  const d = {};
  FIELDS.forEach(id => { const e=document.getElementById(id); d[id]=e?e.value:""; });
  CHECKS.forEach(id => { const e=document.getElementById(id); d[id]=e?e.checked:false; });
  return d;
}

// ── 유효성 검사 ──────────────────────────────────────────────
function validate() {
  const c = parseFloat(document.getElementById("min_confidence").value);
  if(isNaN(c)||c<0||c>1) {
    document.getElementById("min_confidence").classList.add("err");
    setStatus("신뢰도는 0~1 사이여야 합니다.", "err"); return false;
  }
  document.getElementById("min_confidence").classList.remove("err");
  const step = document.getElementById("step").value;
  if((step==="all"||step==="ocr") && uploadedFiles.size===0) {
    const inp = document.getElementById("input_dir").value.trim();
    if(!inp) { setStatus("사진을 추가하거나 폴더 경로를 입력하세요.", "warn"); return false; }
  }
  return true;
}

// ── 상태/칩 ──────────────────────────────────────────────────
function setChip(state, txt) {
  const el = document.getElementById("chip");
  el.className = "chip " + state;
  document.getElementById("chip-txt").textContent = txt;
}
function setStatus(txt, cls="") {
  const el = document.getElementById("run-status");
  el.textContent = txt;
  el.className = "run-bar-status" + (cls?" "+cls:"");
}

// ── 로그 렌더링 ───────────────────────────────────────────────
function colorLine(t) {
  if(/^실행:/.test(t))          return `<span class="lc">${esc(t)}</span>`;
  if(/^(경고|주의):/.test(t))    return `<span class="lw">${esc(t)}</span>`;
  if(/^(오류|에러|Error|Traceback)/i.test(t)) return `<span class="le">${esc(t)}</span>`;
  if(/완료|저장 완료|성공/.test(t)) return `<span class="lg">${esc(t)}</span>`;
  if(/^종료 코드: 0/.test(t.trim())) return `<span class="lg">${esc(t)}</span>`;
  if(/^종료 코드:/.test(t.trim())) return `<span class="le">${esc(t)}</span>`;
  if(/^\[/.test(t.trim()))       return `<span class="ld">${esc(t)}</span>`;
  if(/사용 이미지|로드|처리/.test(t)) return `<span class="li">${esc(t)}</span>`;
  return esc(t);
}
function appendLog(lines) {
  const log = document.getElementById("log");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
  log.innerHTML += lines.map(colorLine).join("");
  logLines += lines.length;
  document.getElementById("log-info").textContent = `${logLines}줄`;
  if(atBottom) log.scrollTop = log.scrollHeight;
}

// ── CSV 검토 패널 ─────────────────────────────────────────────
let countdownTimer = null;
let countdownSec = 0;

function showCsvReview() {
  fetch("/csv-data").then(r=>r.json()).then(j=>{
    if(!j.ok || !j.rows.length) return;
    const SHOW_COLS = ["name","time_slot","gender","level","match_status"];
    const COL_LABELS = {name:"이름",time_slot:"타임",gender:"성별",level:"급수",match_status:"매칭"};
    const cols = (j.headers||[]).filter(h=>SHOW_COLS.includes(h));
    const thead = document.getElementById("csv-thead");
    const tbody = document.getElementById("csv-tbody");
    thead.innerHTML = "<tr>"+cols.map(c=>`<th>${COL_LABELS[c]||c}</th>`).join("")+"</tr>";
    tbody.innerHTML = j.rows.map(row=>{
      return "<tr>"+cols.map(c=>{
        let v = row[c]||"";
        if(c==="time_slot") v = `<span class="tag-${v}">${v}타임</span>`;
        else if(c==="match_status" && v==="unmatched") v = `<span class="tag-u">미매칭</span>`;
        return `<td>${v}</td>`;
      }).join("")+"</tr>";
    }).join("");
    document.getElementById("csv-review-panel").style.display = "block";
    startCountdown(8);
  });
}

function hideCsvReview() {
  document.getElementById("csv-review-panel").style.display = "none";
  cancelCountdown();
}

function startCountdown(sec) {
  cancelCountdown();
  countdownSec = sec;
  const txt = document.getElementById("csv-countdown-text");
  txt.textContent = `${countdownSec}초 후 자동으로 등록 시작`;
  countdownTimer = setInterval(()=>{
    countdownSec--;
    if(countdownSec <= 0) {
      cancelCountdown();
      doContFromReview();
    } else {
      txt.textContent = `${countdownSec}초 후 자동으로 등록 시작`;
    }
  }, 1000);
}

function cancelCountdown() {
  if(countdownTimer){ clearInterval(countdownTimer); countdownTimer=null; }
  const txt = document.getElementById("csv-countdown-text");
  if(txt) txt.textContent = "";
}

async function doContFromReview() {
  hideCsvReview();
  await doCont();
}

// ── 폴링 ─────────────────────────────────────────────────────
let wasWaiting = false;
let wasGenericWaiting = false;
async function poll() {
  try {
    const r = await fetch("/logs?cursor="+cursor);
    const j = await r.json();
    if(j.logs.length) appendLog(j.logs.join("").split(/(?<=\n)/));
    cursor = j.cursor;
    const was = running; running = j.running;
    document.getElementById("btn-run").disabled = running;

    // CSV 검토 상태 감지
    if(j.waiting && !wasWaiting) { showCsvReview(); }
    else if(!j.waiting && wasWaiting) { hideCsvReview(); }
    wasWaiting = !!j.waiting;

    // 일반 Enter 대기 감지
    const btnCont = document.getElementById("btn-cont");
    if(j.generic_waiting && !wasGenericWaiting) {
      btnCont.textContent = "⏎ " + (j.waiting_msg || "계속 진행");
      btnCont.style.display = "";
      setStatus(j.waiting_msg || "입력 대기 중...", "warn");
    } else if(!j.generic_waiting && wasGenericWaiting) {
      btnCont.style.display = "none";
    }
    wasGenericWaiting = !!j.generic_waiting;

    if(running) { setChip("run","실행 중..."); }
    else if(was) {
      setChip("done","완료");
      setStatus("작업이 완료되었습니다.");
      hideCsvReview();
      btnCont.style.display = "none";
      if(timer){ clearInterval(timer); timer=setInterval(poll,1500); }
    }
  } catch(e){ setStatus("서버 연결 오류","err"); }
}

// ── 버튼 핸들러 ──────────────────────────────────────────────
async function post(path, body={}) {
  try {
    const r = await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j = await r.json();
    if(j.message) setStatus(j.message, j.ok?"":"warn");
    return j;
  } catch(e){ setStatus("요청 실패: "+e,"err"); return {ok:false}; }
}

async function doRun() {
  if(!validate()) return;
  document.getElementById("log").innerHTML="";
  logLines=0; cursor=0;
  setChip("run","시작 중...");
  setStatus("실행 요청 중...");
  const res = await post("/run", collectData());
  if(res.ok) {
    running=true;
    document.getElementById("btn-run").disabled=true;
    if(timer) clearInterval(timer);
    await poll();
    timer = setInterval(poll, 500);
  } else { setChip("err","오류"); }
}
async function doCont() { hideCsvReview(); await post("/continue"); await poll(); }
async function doStop() {
  await post("/stop");
  setChip("err","중지됨");
  if(timer){ clearInterval(timer); timer=setInterval(poll,1500); }
}
function doOpenCsv() { post("/open-csv",{output:document.getElementById("output").value}); }
function clearLog() { document.getElementById("log").innerHTML=""; logLines=0; document.getElementById("log-info").textContent="0줄"; }
async function copyLog() {
  try { await navigator.clipboard.writeText(document.getElementById("log").textContent); showToast("로그 복사 완료"); }
  catch(e){ showToast("복사 실패: "+e, true); }
}

// ── 토스트 ───────────────────────────────────────────────────
let toastTimer;
function showToast(msg, isErr=false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.background = isErr ? "var(--red)" : "var(--t1)";
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>el.classList.remove("show"), 2500);
}

// ── 헬퍼 ─────────────────────────────────────────────────────
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function fmtSize(b){ if(!b) return ""; if(b<1024) return b+"B"; if(b<1048576) return (b/1024).toFixed(0)+"KB"; return (b/1048576).toFixed(1)+"MB"; }

// ── 키보드 단축키 ─────────────────────────────────────────────
document.addEventListener("keydown", e => {
  if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){ e.preventDefault(); doRun(); }
  if(e.key==="Escape"&&running){ e.preventDefault(); doStop(); }
});

// ── QR / 모바일 업로드 ───────────────────────────────────────
let _qrUrl = "";
async function loadQrInfo() {
  try {
    const r = await fetch("/local-info");
    const j = await r.json();
    _qrUrl = j.url || "";
    document.getElementById("qr-url").textContent = _qrUrl || "주소를 가져올 수 없습니다";
    if(!j.has_qr) {
      document.getElementById("qr-img").style.display = "none";
      document.getElementById("qr-placeholder").style.display = "flex";
    }
  } catch(e) {}
}
function copyQrUrl() {
  if(!_qrUrl) return;
  navigator.clipboard.writeText(_qrUrl)
    .then(() => showToast("URL 복사 완료"))
    .catch(() => showToast("복사 실패", true));
}
function updateQrFileCount(n) {
  const el = document.getElementById("qr-file-count");
  if(el) el.textContent = n === 0 ? "비어 있음" : n + "장";
}

// ── 키보드 힌트 (OS 감지) ────────────────────────────────────
(function(){
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const run = mac ? "<kbd>⌘</kbd><kbd>↵</kbd>" : "<kbd>Ctrl</kbd><kbd>Enter</kbd>";
  document.getElementById("appbar-hint").innerHTML =
    run + " 실행&nbsp;&nbsp;<kbd>Esc</kbd> 중지";
})();

// ── 초기화 ───────────────────────────────────────────────────
loadSettings();
loadExistingFiles();
loadQrInfo();
timer = setInterval(poll, 1500);
setInterval(async () => {
  try {
    const r = await fetch("/vote-files");
    const j = await r.json();
    updateQrFileCount(j.files ? j.files.length : 0);
  } catch(e) {}
}, 3000);
</script>
</body>
</html>
"""


# ── HTTP 핸들러 ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, data: dict, status: int = 200, cors: bool = False) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        # Chrome 페이지에서 fetch() 할 때 발생하는 CORS preflight 허용
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/logs":
            c = int(urllib.parse.parse_qs(parsed.query).get("cursor", ["0"])[0])
            with job_lock:
                logs = job_logs[c:]
                next_c = len(job_logs)
                is_running = bool(job_process and job_process.poll() is None)
                waiting = bool(csv_review_path and is_running)
                gwaiting = bool(generic_waiting and is_running)
                gwaiting_msg = generic_waiting_msg if gwaiting else ""
            self.send_json({
                "logs": logs, "cursor": next_c, "running": is_running,
                "waiting": waiting, "generic_waiting": gwaiting, "waiting_msg": gwaiting_msg,
            })
            return

        if parsed.path == "/vote-files":
            self.send_json({"files": list_vote_files()})
            return

        if parsed.path == "/mobile":
            body = MOBILE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/csv-data":
            with job_lock:
                path_str = csv_review_path
            path = Path(path_str) if path_str else SCRIPT_DIR / "participants.csv"
            if not path.exists():
                self.send_json({"ok": False, "rows": [], "headers": []})
                return
            try:
                with open(path, newline="", encoding="utf-8-sig") as f:
                    reader = _csv.DictReader(f)
                    headers = reader.fieldnames or []
                    rows = [dict(r) for r in reader]
                self.send_json({"ok": True, "headers": headers, "rows": rows})
            except Exception as exc:
                self.send_json({"ok": False, "rows": [], "headers": [], "error": str(exc)})
            return

        if parsed.path == "/local-info":
            mobile_url = f"http://{_local_ip}:{_server_port}/mobile"
            self.send_json({
                "ip": _local_ip,
                "port": _server_port,
                "url": mobile_url,
                "has_qr": _HAS_QR,
            })
            return

        if parsed.path == "/qr.png":
            mobile_url = f"http://{_local_ip}:{_server_port}/mobile"
            png_b64 = make_qr_png_b64(mobile_url)
            if not png_b64:
                self.send_error(503, "QR generation unavailable (pip install segno)")
                return
            png_bytes = base64.b64decode(png_b64)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(png_bytes)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/run":
            ok, msg = start_job(self.read_json())
            self.send_json({"ok": ok, "message": msg})
            return

        if self.path == "/continue":
            ok, msg = continue_job()
            self.send_json({"ok": ok, "message": msg})
            return

        # Chrome 탭에서 직접 호출하는 계속 진행 엔드포인트 (CORS 허용)
        if self.path == "/continue-from-browser":
            ok, msg = continue_job()
            self.send_json({"ok": ok, "message": msg}, cors=True)
            return

        if self.path == "/stop":
            ok, msg = stop_job()
            self.send_json({"ok": ok, "message": msg})
            return

        if self.path == "/open-csv":
            ok, msg = open_csv(self.read_json().get("output", "participants.csv"))
            self.send_json({"ok": ok, "message": msg})
            return

        if self.path == "/upload":
            ok, msg, saved = save_uploaded_files(self)
            self.send_json({"ok": ok, "message": msg, "saved": saved})
            return

        if self.path == "/clear-vote":
            removed = 0
            for p in vote_dir().iterdir():
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                    p.unlink()
                    removed += 1
            self.send_json({"ok": True, "message": f"{removed}장 삭제됨"})
            return

        if self.path == "/remove-vote-file":
            name = self.read_json().get("name", "")
            target = vote_dir() / Path(name).name
            if target.exists() and target.suffix.lower() in IMAGE_EXTS:
                target.unlink()
            self.send_json({"ok": True})
            return

        self.send_error(404)


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    global _local_ip, _server_port

    server = None
    for port in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        server = ThreadingHTTPServer((HOST, 0), Handler)

    actual_port = server.server_address[1]
    _server_port = actual_port
    _local_ip = get_local_ip()

    desktop_url = f"http://127.0.0.1:{actual_port}"
    mobile_url = f"http://{_local_ip}:{actual_port}/mobile"
    print(f"STAB AutoAdd System: {desktop_url}")
    print(f"모바일 업로드:  {mobile_url}")
    try:
        webbrowser.open(desktop_url)
    except Exception:
        pass
    server.serve_forever()


if __name__ == "__main__":
    main()
