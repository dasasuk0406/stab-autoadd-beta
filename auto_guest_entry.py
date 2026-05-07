import argparse
import csv
from datetime import datetime
from difflib import SequenceMatcher
import json
import os
import re
import subprocess as _subprocess
import sys
import time
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = (
    SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else SCRIPT_DIR / ".venv" / "bin" / "python"
)

if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


# autoadd_ui_v2.py가 설정한 서버 포트 (0이면 UI 없이 실행 중)
_AUTOADD_UI_PORT: int = int(os.environ.get("AUTOADD_UI_PORT", "0"))

DEFAULT_CONFIG_PATH = SCRIPT_DIR / "site_config.json"
DEFAULT_CSV_PATH = SCRIPT_DIR / "participants.csv"
DEFAULT_SITE_URL = "https://nearminton.com/moim.php"
DEFAULT_CHROME_PROFILE_DIR = SCRIPT_DIR / "chrome_profile"
DEFAULT_MEMBERS_PATH = SCRIPT_DIR / "members.csv"
DEFAULT_COURT_NAME = "서울과학기술대학교 실내 체육관"
DEFAULT_ROOM_TITLE_TEMPLATE = "[우동배] {time}타임"
DONBAE_GAME_LIST_URL = "https://nearminton.com/competition?type=all"
DONBAE_GAME_REGIST_URL = "https://nearminton.com/ground_main/game_regist_n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="participants.csv의 참가자를 사이트 게스트 등록 화면에 자동 입력합니다."
    )
    parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH), help="참가자 CSV 경로")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="사이트 설정 JSON 경로")
    parser.add_argument("--members", default=str(DEFAULT_MEMBERS_PATH), help="회원 정보 CSV 경로")
    parser.add_argument("--url", default=DEFAULT_SITE_URL, help="nearminton 게임판 URL")
    parser.add_argument(
        "--time",
        choices=["1", "2", "all"],
        default="1",
        help="등록할 운동 타임. 1, 2, all 중 선택. 기본값: 1",
    )
    parser.add_argument(
        "--allow-unmatched",
        action="store_true",
        help="members.csv에 없는 OCR 후보도 등록합니다. 기본값은 제외",
    )
    parser.add_argument(
        "--allow-same-name",
        action="store_true",
        help="사이트에 같은 이름이 이미 있어도 등록합니다. 동명이인 수동 처리용",
    )
    parser.add_argument("--dry-run", action="store_true", help="브라우저 입력 없이 CSV만 확인합니다.")
    parser.add_argument("--headless", action="store_true", help="브라우저 창을 띄우지 않고 실행합니다.")
    parser.add_argument(
        "--close-browser",
        action="store_true",
        help="등록 완료 후 브라우저를 닫습니다. 기본값은 닫지 않음",
    )
    parser.add_argument(
        "--profile-dir",
        default=str(DEFAULT_CHROME_PROFILE_DIR),
        help="로그인 세션을 저장할 Chrome 프로필 폴더",
    )
    parser.add_argument(
        "--temporary-profile",
        action="store_true",
        help="로그인 세션을 저장하지 않는 임시 Chrome 프로필로 실행합니다.",
    )
    parser.add_argument(
        "--generic-config",
        action="store_true",
        help="site_config.json 선택자 기반 방식으로 실행합니다.",
    )
    parser.add_argument(
        "--create-rooms",
        action="store_true",
        help="1타임/2타임 게임방을 생성한 뒤 각 방에 참가자를 등록합니다.",
    )
    parser.add_argument(
        "--create-room-times",
        choices=["1", "2", "all"],
        default="all",
        help="--create-rooms 사용 시 생성할 게임방 타임. 기본값: all",
    )
    parser.add_argument(
        "--room-title-template",
        default=DEFAULT_ROOM_TITLE_TEMPLATE,
        help="게임방 제목 템플릿. {time} 자리에 1 또는 2가 들어갑니다.",
    )
    parser.add_argument("--room-kind", default="정모", help="모임종류 선택값. 기본값: 정모")
    parser.add_argument("--room-game-type", default="자유게임", help="게임방식 선택값. 기본값: 자유게임")
    parser.add_argument(
        "--court-name",
        default=DEFAULT_COURT_NAME,
        help="구장 검색어. 기본값: 서울과학기술대학교 실내 체육관",
    )
    parser.add_argument(
        "--skill-label",
        default="",
        help="실력구분 입력값. 비우면 입력하지 않습니다.",
    )
    parser.add_argument(
        "--room-capacity",
        type=int,
        default=0,
        help="모집인원 고정값. 0이면 해당 타임 등록 대상 인원으로 자동 입력합니다.",
    )
    parser.add_argument(
        "--room-start",
        default="",
        help="게임방 시작일시. 형식: YYYY-MM-DD HH:MM. 비우면 현재 시각을 사용합니다.",
    )
    parser.add_argument(
        "--chrome-debug-port",
        type=int,
        default=0,
        help="기존 Chrome 창에 연결할 원격 디버깅 포트. 0이면 새 Chrome 창을 엽니다.",
    )
    parser.add_argument(
        "--site-mode",
        choices=["moim", "game"],
        default="moim",
        help="사이트 등록 모드. moim=동배모임(기본), game=동배게임(백업)",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        example_path = SCRIPT_DIR / "site_config.example.json"
        raise FileNotFoundError(
            f"설정 파일이 없습니다: {config_path}\n"
            f"{example_path.name}을 site_config.json으로 복사한 뒤 실제 사이트 선택자로 수정하세요."
        )

    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    encodings = ("utf-8-sig", "cp949", "euc-kr", "utf-16")
    last_error = None
    for encoding in encodings:
        try:
            with csv_path.open("r", encoding=encoding, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeError as exc:
            last_error = exc
    raise UnicodeError(f"CSV 인코딩을 읽지 못했습니다: {csv_path}") from last_error


def normalize_for_match(value: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", value.strip().lower())


def _trailing_digits(normalized: str) -> str:
    """정규화된 이름 끝의 숫자 suffix를 반환한다. (예: '김도현23' → '23')
    괄호 학번 연도 구분 방식(김도현(23))에서 연도 부분을 추출하는 데 사용된다."""
    m = re.search(r"\d+$", normalized)
    return m.group() if m else ""


def normalize_gender(value: str) -> str:
    value = value.strip().lower()
    if value in {"남", "m", "man", "male", "남자"}:
        return "남"
    if value in {"여", "f", "woman", "female", "여자"}:
        return "여"
    return value


def normalize_level(value: str) -> str:
    value = value.strip().upper()
    label_to_level = {
        "초심": "E",
        "왕초심": "F",
        "비동호인": "N",
        "S(자강)": "S",
    }
    return label_to_level.get(value, value)


def load_members(members_path: Path) -> list[dict[str, str]]:
    if not members_path.exists():
        return []

    rows = read_csv_rows(members_path)
    members = []
    seen_members = set()
    for row in rows:
        name = row.get("name", "").strip()
        if not name:
            continue

        aliases = [
            normalize_for_match(alias)
            for alias in row.get("aliases", "").split("|")
            if alias.strip()
        ]
        aliases.append(normalize_for_match(name))
        member = {
            "member_id": row.get("member_id", "").strip(),
            "name": name,
            "gender": normalize_gender(row.get("gender", "").strip()),
            "level": normalize_level(row.get("level", "").strip()),
            "aliases": "|".join(dict.fromkeys(alias for alias in aliases if alias)),
        }
        dedupe_key = (
            member["member_id"] or normalize_for_match(member["name"]),
            member["gender"],
            member["level"],
        )
        if dedupe_key in seen_members:
            continue
        seen_members.add(dedupe_key)
        members.append(member)
    return members


def match_member(raw_name: str, members: list[dict[str, str]], min_score: float = 0.78) -> dict[str, str] | None:
    if not members:
        return None

    normalized = normalize_for_match(raw_name)
    # 이름 끝 숫자 suffix (학번 연도 구분용). 예: "김도현23" → "23", "김도현" → ""
    norm_digits = _trailing_digits(normalized)

    exact_matches = [
        member
        for member in members
        if normalized in member["aliases"].split("|")
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None

    # ── 준정확 매칭 ─────────────────────────────────────────────────────────────
    # 연도 suffix가 있을 때는 같은 연도를 가진 alias하고만 비교한다.
    # (예: "김도현23"이 "김도현21"·"김도현26"과 fuzzy 매칭되는 것을 방지)
    fuzzy_exact = []
    for member in members:
        matched = False
        for alias in member["aliases"].split("|"):
            alias_digits = _trailing_digits(alias)
            # 한 쪽이라도 연도 suffix가 있는데 값이 다르면 이 alias는 건너뜀
            if (norm_digits or alias_digits) and norm_digits != alias_digits:
                continue
            if len(normalized) >= 3 and len(alias) >= 3 and len(normalized) == len(alias):
                diff_count = sum(left != right for left, right in zip(normalized, alias))
                if diff_count <= 1:
                    matched = True
                    break
            if len(normalized) >= 2 and len(alias) >= 2:
                if normalized in alias or alias in normalized:
                    matched = True
                    break
        if matched:
            fuzzy_exact.append(member)
    fuzzy_exact = list({id(member): member for member in fuzzy_exact}.values())
    if len(fuzzy_exact) == 1:
        return fuzzy_exact[0]

    # ── SequenceMatcher 스코어링 ─────────────────────────────────────────────
    # 연도 suffix가 있으면 같은 연도의 alias만 스코어링에 포함한다.
    scored = []
    for member in members:
        aliases_to_score = [
            alias for alias in member["aliases"].split("|")
            if alias and not ((norm_digits or _trailing_digits(alias)) and norm_digits != _trailing_digits(alias))
        ]
        if not aliases_to_score:
            continue
        best_score = max(SequenceMatcher(None, normalized, alias).ratio() for alias in aliases_to_score)
        scored.append((best_score, member))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < min_score:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def build_display_name(name: str, entry_suffix: str) -> str:
    entry_suffix = (entry_suffix or "").strip()
    if not entry_suffix:
        return name
    if name.endswith(f" {entry_suffix}"):
        return name
    return f"{name} {entry_suffix}"


def load_participants(
    csv_path: Path,
    members: list[dict[str, str]],
    allow_unmatched: bool,
) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"참가자 CSV가 없습니다: {csv_path}")

    rows = read_csv_rows(csv_path)

    required = {"name", "gender", "level"}
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"CSV에 필요한 컬럼이 없습니다: {', '.join(sorted(missing))}")

    participants = []
    skipped = 0
    for row in rows:
        raw_name = row.get("name", "").strip()
        if not raw_name:
            continue

        participant = {
            "member_id": row.get("member_id", "").strip(),
            "name": raw_name,
            "gender": normalize_gender(row["gender"].strip()),
            "level": normalize_level(row["level"].strip()),
            "time_slot": row.get("time_slot", "all").strip() or "all",
            "entry_suffix": row.get("entry_suffix", "").strip(),
        }
        trusted_manual_row = (
            row.get("match_status", "").strip().lower() in {"manual", "verified", "user_verified"}
            or row.get("source_image", "").strip().lower() == "user_verified"
        )

        member = match_member(raw_name, members)
        if member:
            participant.update(
                {
                    "member_id": member["member_id"],
                    "name": member["name"],
                    "gender": member["gender"] or participant["gender"],
                    "level": member["level"] or participant["level"],
                }
            )
        elif members and not allow_unmatched and not trusted_manual_row:
            skipped += 1
            continue

        participant["display_name"] = (
            row.get("display_name", "").strip()
            or row.get("entry_name", "").strip()
            or build_display_name(participant["name"], participant["entry_suffix"])
        )
        participants.append(participant)

    if skipped:
        print(f"members.csv에 없어 제외한 OCR 후보: {skipped}명")
    return participants


def participant_key(participant: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        participant.get("member_id") or participant["name"],
        participant["gender"],
        participant["level"],
        participant.get("time_slot", "all"),
        participant.get("display_name", participant["name"]),
    )


def dedupe_participants(participants: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped = []
    seen = set()
    for participant in participants:
        key = participant_key(participant)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(participant)
    return deduped


def participant_matches_time(participant: dict[str, str], target_time: str) -> bool:
    if target_time == "all":
        return True
    time_slot = participant.get("time_slot", "all")
    return time_slot in {target_time, "all", "both"}


def _chrome_debug_reachable(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _find_chrome_binary() -> str:
    """OS별 Chrome 실행파일 경로를 반환한다."""
    if os.name == "nt":
        # Windows: 일반 설치(Program Files) + 사용자 설치(LocalAppData) 경로 모두 탐색
        local_app = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
        ]
        found = next((p for p in candidates if Path(p).exists()), None)
        return found or "chrome"  # PATH에 있으면 그걸 사용
    elif sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:
        # Linux: 일반적인 설치 경로 탐색
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]
        found = next((p for p in candidates if Path(p).exists()), None)
        return found or "google-chrome"


def _launch_chrome_with_debug_port(port: int, profile_dir: Path | None) -> None:
    """디버그 포트로 Chrome을 실행하고 준비될 때까지 대기한다."""
    chrome_bin = _find_chrome_binary()
    cmd = [chrome_bin, f"--remote-debugging-port={port}", "--window-size=1440,900",
           "--force-device-scale-factor=1"]
    if profile_dir:
        profile_dir.mkdir(parents=True, exist_ok=True)
        cmd += [f"--user-data-dir={profile_dir}", "--profile-directory=Default"]

    print(f"Chrome을 디버그 포트 {port}로 실행 중...")

    # Windows: 별도 터미널 창이 뜨지 않도록 CREATE_NO_WINDOW 플래그 사용
    kwargs: dict = {"stdout": _subprocess.DEVNULL, "stderr": _subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = _subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    _subprocess.Popen(cmd, **kwargs)

    for _ in range(30):
        time.sleep(0.5)
        if _chrome_debug_reachable(port):
            print("Chrome 준비 완료.")
            return
    raise RuntimeError(f"Chrome이 {port}포트로 실행되지 않았습니다. Chrome 설치 경로를 확인하세요.")


def start_driver(headless: bool, profile_dir: Path | None, debug_port: int = 0) -> webdriver.Chrome:
    options = Options()
    if debug_port:
        # 디버그 포트로 Chrome이 열려 있지 않으면 자동 실행
        if not _chrome_debug_reachable(debug_port):
            _launch_chrome_with_debug_port(debug_port, profile_dir)
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
        return webdriver.Chrome(options=options)
    if headless:
        options.add_argument("--headless=new")
    if profile_dir:
        profile_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1440,900")
    # macOS Retina 등 고DPI 화면에서 device pixel ratio가 CSS 뷰포트에 영향을 주어
    # 요소가 실제 화면 밖으로 밀리는 문제를 막기 위해 scale factor를 1로 고정합니다.
    options.add_argument("--force-device-scale-factor=1")
    return webdriver.Chrome(options=options)


def wait_for_element(driver: webdriver.Chrome, selector: str, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
    )


def wait_for_clickable_xpath(driver: webdriver.Chrome, xpath: str, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )


def wait_for_visible_xpath(driver: webdriver.Chrome, xpath: str, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.XPATH, xpath))
    )


def visible_elements(driver: webdriver.Chrome, by: str, selector: str):
    return [
        element
        for element in driver.find_elements(by, selector)
        if element.is_displayed()
    ]


def wait_for_any_visible(driver: webdriver.Chrome, by: str, selector: str, timeout: int = 15):
    def find_visible(_driver):
        elements = visible_elements(_driver, by, selector)
        return elements[0] if elements else False

    return WebDriverWait(driver, timeout).until(find_visible)


def debug_page_snapshot(driver: webdriver.Chrome, label: str) -> None:
    screenshot_path = SCRIPT_DIR / f"debug_{label}.png"
    html_path = SCRIPT_DIR / f"debug_{label}.html"
    driver.save_screenshot(str(screenshot_path))
    html_path.write_text(driver.page_source, encoding="utf-8")
    print(f"디버그 저장: {screenshot_path}")
    print(f"디버그 저장: {html_path}")


def safe_click(driver: webdriver.Chrome, element) -> None:
    """scrollIntoView 후 클릭. 실패 시 JavaScript click으로 폴백."""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'nearest'});", element)
        time.sleep(0.15)
    except Exception:
        pass
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def click_text(driver: webdriver.Chrome, text: str, timeout: int = 15) -> None:
    compact_text = re.sub(r"\s+", "", text)
    xpath = (
        "//*[self::button or self::a or self::label or self::div or self::span]"
        "[not(ancestor::*[contains(@style,'display: none')]) and "
        f"(normalize-space()='{text}' or translate(normalize-space(), ' ', '')='{compact_text}')]"
    )
    elements = WebDriverWait(driver, timeout).until(
        lambda _driver: [
            element
            for element in _driver.find_elements(By.XPATH, xpath)
            if element.is_displayed() and element.is_enabled()
        ]
    )
    safe_click(driver, elements[-1])


def click_any_text(driver: webdriver.Chrome, labels: tuple[str, ...], timeout: int = 15) -> None:
    last_error = None
    for label in labels:
        try:
            click_text(driver, label, timeout=timeout)
            return
        except TimeoutException as exc:
            last_error = exc
    raise last_error or TimeoutException()


def safe_clickable_xpath(driver: webdriver.Chrome, xpath: str, timeout: int = 15):
    """wait_for_clickable_xpath + safe_click 조합."""
    element = wait_for_clickable_xpath(driver, xpath, timeout=timeout)
    safe_click(driver, element)
    return element


def click_create_room_button(driver: webdriver.Chrome, timeout: int = 15) -> None:
    safe_default_content(driver)
    selectors = (
        "button.regist_box",
        "[onclick*='game_regist']",
        "[onclick*='create_game']",
    )
    for selector in selectors:
        elements = visible_elements(driver, By.CSS_SELECTOR, selector)
        if elements:
            driver.execute_script("arguments[0].click();", elements[-1])
            return

    click_any_text(
        driver,
        ("게임생성", "게임 생성", "게임방생성", "게임방 생성", "모임방생성", "방만들기"),
        timeout=timeout,
    )


def dismiss_any_alert(driver: webdriver.Chrome) -> None:
    """떠 있는 알림창을 모두 닫는다."""
    for _ in range(5):
        try:
            alert = driver.switch_to.alert
            text = alert.text
            alert.accept()
            print(f"  알림 자동 닫기: {text!r}")
            time.sleep(0.3)
        except Exception:
            break


def safe_default_content(driver: webdriver.Chrome) -> None:
    """알림창이 있어도 안전하게 default_content로 전환한다."""
    for _ in range(3):
        try:
            driver.switch_to.default_content()
            return
        except UnexpectedAlertPresentException:
            dismiss_any_alert(driver)
    driver.switch_to.default_content()


def inject_continue_button(driver: webdriver.Chrome, message: str = "") -> None:
    """Chrome 현재 탭에 '계속 진행' 플로팅 버튼을 주입한다.

    버튼 클릭 시 autoadd_ui_v2.py 서버의 /continue-from-browser 로 POST 요청을 보내
    UI 페이지로 돌아오지 않아도 자동으로 계속 진행된다.
    _AUTOADD_UI_PORT 가 0(UI 없이 실행)이면 주입하지 않는다.
    """
    if not _AUTOADD_UI_PORT:
        return
    label = message or "계속 진행"
    js = f"""
(function(){{
  var EID = 'autoadd-float-btn';
  if (document.getElementById(EID)) return;
  var btn = document.createElement('div');
  btn.id = EID;
  btn.innerHTML = '<span style="font-size:18px">▶</span>&nbsp;{label}';
  var S = btn.style;
  S.cssText = [
    'position:fixed','bottom:28px','right:28px','z-index:2147483647',
    'background:linear-gradient(135deg,#0064FF,#0050D0)',
    'color:#fff','padding:14px 22px','border-radius:14px',
    'font-size:15px','font-weight:700','cursor:pointer',
    'box-shadow:0 6px 28px rgba(0,100,255,0.45)',
    'font-family:-apple-system,BlinkMacSystemFont,sans-serif',
    'display:flex','align-items:center','gap:8px',
    'user-select:none','transition:transform 0.15s,opacity 0.15s',
    'letter-spacing:-0.2px'
  ].join(';');
  btn.onmouseenter = function(){{ S.transform='scale(1.04)'; S.opacity='0.92'; }};
  btn.onmouseleave = function(){{ S.transform='scale(1)'; S.opacity='1'; }};
  btn.onclick = function(){{
    btn.innerHTML = '<span style="font-size:16px">⏳</span>&nbsp;처리 중...';
    S.opacity = '0.7';
    S.pointerEvents = 'none';
    fetch('http://127.0.0.1:{_AUTOADD_UI_PORT}/continue-from-browser', {{
      method: 'POST',
      mode: 'cors',
      headers: {{'Content-Type': 'application/json'}},
      body: '{{}}'
    }}).then(function(r){{ return r.json(); }})
      .then(function(j){{
        btn.innerHTML = '<span>✅</span>&nbsp;완료';
        setTimeout(function(){{ if(btn.parentNode) btn.remove(); }}, 1000);
      }})
      .catch(function(){{
        btn.innerHTML = '<span>⚠️</span>&nbsp;재시도';
        S.opacity = '1'; S.pointerEvents = '';
      }});
  }};
  document.body.appendChild(btn);
}})();
"""
    try:
        safe_default_content(driver)
        driver.execute_script(js)
    except Exception:
        pass


def remove_continue_button(driver: webdriver.Chrome) -> None:
    """주입된 플로팅 버튼을 제거한다."""
    try:
        safe_default_content(driver)
        driver.execute_script(
            "var b=document.getElementById('autoadd-float-btn'); if(b) b.remove();"
        )
    except Exception:
        pass


def switch_to_entry_frame(driver: webdriver.Chrome, timeout: int = 15) -> None:
    safe_default_content(driver)
    WebDriverWait(driver, timeout).until(
        EC.frame_to_be_available_and_switch_to_it((By.ID, "board"))
    )


def is_entry_frame_open(driver: webdriver.Chrome) -> bool:
    safe_default_content(driver)
    frames = visible_elements(driver, By.CSS_SELECTOR, "iframe#board")
    if not frames:
        return False
    src = frames[0].get_attribute("src") or ""
    return "enter_form" in src


def is_on_enter_form_page(driver: webdriver.Chrome) -> bool:
    """입장전용으로 진입하면 enter_form이 iframe이 아닌 직접 페이지로 열린다."""
    try:
        return "enter_form" in driver.current_url
    except Exception:
        return False


def open_nearminton_entry_modal(driver: webdriver.Chrome) -> None:
    safe_default_content(driver)

    # 입장전용 경로: enter_form 페이지가 직접 열려 있으면 iframe 전환 불필요
    if is_on_enter_form_page(driver):
        try:
            wait_for_any_visible(
                driver,
                By.XPATH,
                "//*[contains(normalize-space(), '게스트 입력')] | "
                "//input[not(@id='phone') and (not(@type) or @type='text')]",
                timeout=15,
            )
        except TimeoutException:
            debug_page_snapshot(driver, "entry_form_timeout")
            raise
        return

    # 기존 경로: enter_form이 iframe#board 안에 있는 경우
    if not is_entry_frame_open(driver):
        try:
            driver.execute_script("if (typeof Enter === 'function') { Enter(); }")
        except Exception:
            pass

        if not is_entry_frame_open(driver):
            add_buttons = visible_elements(driver, By.CSS_SELECTOR, "#add_btn, #add_btn1 button")
            if add_buttons:
                safe_click(driver, add_buttons[0])
            else:
                click_text(driver, "입장")

    switch_to_entry_frame(driver)
    try:
        wait_for_any_visible(
            driver,
            By.XPATH,
            "//*[contains(normalize-space(), '게스트 입력')] | "
            "//input[not(@id='phone') and (not(@type) or @type='text')]",
            timeout=15,
        )
    except TimeoutException:
        debug_page_snapshot(driver, "entry_modal_timeout")
        raise


def set_nearminton_name(driver: webdriver.Chrome, name: str) -> None:
    candidates = visible_elements(
        driver,
        By.XPATH,
        "//input[not(@id='phone') and "
        "(not(@type) or @type='text') and "
        "(contains(@placeholder, '이름') or contains(@value, '이름'))]",
    )
    if not candidates:
        candidates = visible_elements(
            driver,
            By.XPATH,
            "//input[not(@id='phone') and (not(@type) or @type='text')]",
        )
    if not candidates:
        raise RuntimeError("게스트 이름 입력칸을 찾지 못했습니다.")

    input_box = candidates[0]
    safe_click(driver, input_box)
    input_box.clear()
    input_box.send_keys(name)


def click_nearminton_option(driver: webdriver.Chrome, value: str) -> None:
    labels = [value]
    if value == "S":
        labels.append("S(자강)")
    elif value == "E":
        labels.append("초심")
    elif value == "F":
        labels.append("왕초심")
        labels.append("왕초보")
    elif value == "N":
        labels.append("비동호인")

    quoted_labels = " or ".join(f"normalize-space()='{label}'" for label in labels)
    xpath = f"//*[self::button or self::a or self::label or self::div][{quoted_labels}]"
    safe_clickable_xpath(driver, xpath)


def submit_nearminton_guest(driver: webdriver.Chrome) -> None:
    click_text(driver, "입장하기")
    time.sleep(1.2)
    close_alerts_if_any(driver)


def current_nearminton_guest_names(driver: webdriver.Chrome) -> set[str]:
    safe_default_content(driver)
    elements = driver.find_elements(By.CSS_SELECTOR, "#enterlist h3")
    names = set()
    for element in elements:
        text = element.text.strip()
        if not text:
            continue
        # 화면 표기는 "홍길동 D"처럼 급수가 붙습니다.
        names.add(text.rsplit(" ", 1)[0])
    return names


def close_alerts_if_any(driver: webdriver.Chrome) -> None:
    try:
        alert = driver.switch_to.alert
        alert.accept()
        time.sleep(0.3)
        return
    except Exception:
        pass

    for text in ("확인", "OK"):
        elements = driver.find_elements(
            By.XPATH,
            f"//*[self::button or self::a][normalize-space()='{text}']",
        )
        for element in elements:
            if element.is_displayed() and element.is_enabled():
                safe_click(driver, element)
                time.sleep(0.3)
                return


def clear_and_type(element, value: str, driver: webdriver.Chrome | None = None) -> None:
    if driver:
        safe_click(driver, element)
    else:
        element.click()
    element.clear()
    element.send_keys(value)


def find_field_after_label(driver: webdriver.Chrome, label_text: str, tag_selector: str = "input|select|textarea"):
    xpath = (
        f"//*[normalize-space()='{label_text}']"
        f"/following::*[self::input or self::select or self::textarea][1]"
    )
    return wait_for_visible_xpath(driver, xpath)


def find_field_after_any_label(driver: webdriver.Chrome, label_texts: tuple[str, ...]):
    last_error = None
    for label_text in label_texts:
        try:
            return find_field_after_label(driver, label_text)
        except TimeoutException as exc:
            last_error = exc
    raise last_error or TimeoutException()


def set_field_after_any_label(driver: webdriver.Chrome, label_texts: tuple[str, ...], value: str) -> None:
    field = find_field_after_any_label(driver, label_texts)
    clear_and_type(field, value)


def set_field_after_label(driver: webdriver.Chrome, label_text: str, value: str) -> None:
    set_field_after_any_label(driver, (label_text,), value)


def set_field_value_with_js(driver: webdriver.Chrome, field, value: str) -> None:
    driver.execute_script(
        """
        const field = arguments[0];
        const value = arguments[1];
        field.value = value;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        field,
        value,
    )


def set_field_after_any_label_with_js(driver: webdriver.Chrome, label_texts: tuple[str, ...], value: str) -> None:
    field = find_field_after_any_label(driver, label_texts)
    set_field_value_with_js(driver, field, value)


def select_matching_option(select: Select, value: str) -> None:
    aliases = {
        "자유": ("자유", "자유게임", "F"),
        "자유게임": ("자유게임", "자유", "F"),
        "미니대회": ("미니대회", "T"),
        "정모": ("정모", "1"),
        "번개": ("번개", "2"),
    }
    candidates = aliases.get(value, (value,))
    for candidate in candidates:
        try:
            select.select_by_visible_text(candidate)
            return
        except Exception:
            pass
        try:
            select.select_by_value(candidate)
            return
        except Exception:
            pass

    normalized_candidates = [re.sub(r"\s+", "", candidate) for candidate in candidates]
    for option in select.options:
        option_text = re.sub(r"\s+", "", option.text)
        option_value = option.get_attribute("value") or ""
        if any(
            candidate and (candidate in option_text or option_text in candidate or candidate == option_value)
            for candidate in normalized_candidates
        ):
            select.select_by_value(option_value)
            return

    raise TimeoutException(f"선택값을 찾지 못했습니다: {value}")


def choose_field_after_any_label(driver: webdriver.Chrome, label_texts: tuple[str, ...], value: str) -> None:
    field = find_field_after_any_label(driver, label_texts)
    tag_name = field.tag_name.lower()
    if tag_name == "select":
        select_matching_option(Select(field), value)
        return

    safe_click(driver, field)
    option_xpath = (
        f"//*[self::option or self::li or self::div or self::span]"
        f"[normalize-space()='{value}' and not(ancestor::*[contains(@style,'display: none')])]"
    )
    safe_clickable_xpath(driver, option_xpath)


def choose_field_after_label(driver: webdriver.Chrome, label_text: str, value: str) -> None:
    choose_field_after_any_label(driver, (label_text,), value)


def create_nearminton_room(
    driver: webdriver.Chrome,
    title: str,
    kind: str,
    game_type: str,
    capacity: int,
    court_name: str,
    start_datetime: str,
    skill_label: str = "",
) -> None:
    safe_default_content(driver)
    try:
        click_create_room_button(driver, timeout=8)
    except TimeoutException:
        debug_page_snapshot(driver, "create_room_button_timeout")
        raise

    wait_for_any_visible(
        driver,
        By.XPATH,
        "//*[normalize-space()='모임방명'] | //*[normalize-space()='모임명'] | //input[contains(@placeholder, '방 제목')]",
        timeout=15,
    )
    set_field_after_any_label(driver, ("모임방명", "모임명"), title)
    choose_field_after_label(driver, "모임종류", kind)
    choose_field_after_label(driver, "게임방식", game_type)
    set_field_after_any_label_with_js(driver, ("시작일",), start_datetime)
    set_field_after_label(driver, "모집인원", str(capacity))
    if skill_label:
        set_field_after_label(driver, "실력구분 - 친선대회 실력 표시", skill_label)

    set_field_after_any_label(driver, ("구장 검색", "구장"), court_name)
    time.sleep(0.5)
    court_xpath = (
        f"//*[self::td or self::div or self::span]"
        f"[normalize-space()='{court_name}' and not(ancestor::*[contains(@style,'display: none')])]"
    )
    safe_clickable_xpath(driver, court_xpath)
    click_text(driver, "생성하기")
    close_alerts_if_any(driver)
    time.sleep(2.5)


def enter_nearminton_room(driver: webdriver.Chrome, title: str) -> None:
    safe_default_content(driver)
    compact_title = re.sub(r"\s+", "", title)
    title_xpath = (
        "//*[contains(normalize-space(), '{title}') or "
        "contains(translate(normalize-space(), ' ', ''), '{compact_title}')]"
    ).format(title=title, compact_title=compact_title)
    try:
        WebDriverWait(driver, 15).until(
            lambda _driver: [
                element
                for element in _driver.find_elements(By.XPATH, title_xpath)
                if element.is_displayed()
            ]
        )
    except TimeoutException:
        debug_page_snapshot(driver, "enter_room_title_timeout")
        raise
    button_xpath = (
        f"({title_xpath})"
        "/ancestor::*[.//*[normalize-space()='입장전용']][1]"
        "//*[self::button or self::a][normalize-space()='입장전용']"
    )
    try:
        safe_clickable_xpath(driver, button_xpath, timeout=5)
    except TimeoutException:
        click_text(driver, "입장전용")
    time.sleep(2)


def room_title_exists(driver: webdriver.Chrome, title: str, timeout: int = 3) -> bool:
    safe_default_content(driver)
    compact_title = re.sub(r"\s+", "", title)
    title_xpath = (
        "//*[contains(normalize-space(), '{title}') or "
        "contains(translate(normalize-space(), ' ', ''), '{compact_title}')]"
    ).format(title=title, compact_title=compact_title)
    try:
        WebDriverWait(driver, timeout).until(
            lambda _driver: [
                element
                for element in _driver.find_elements(By.XPATH, title_xpath)
                if element.is_displayed()
            ]
        )
        return True
    except TimeoutException:
        return False


def room_times_for(value: str) -> list[str]:
    if value == "all":
        return ["1", "2"]
    return [value]


def participants_for_time(participants: list[dict[str, str]], target_time: str) -> list[dict[str, str]]:
    return [
        participant
        for participant in participants
        if participant_matches_time(participant, target_time)
    ]


def register_nearminton_guest(
    driver: webdriver.Chrome,
    participant: dict[str, str],
    allow_same_name: bool,
) -> None:
    display_name = participant.get("display_name", participant["name"])
    if not allow_same_name and display_name in current_nearminton_guest_names(driver):
        print(f"  건너뜀: 이미 등록됨 - {display_name}")
        return

    open_nearminton_entry_modal(driver)
    set_nearminton_name(driver, display_name)
    click_nearminton_option(driver, participant["gender"])
    click_nearminton_option(driver, participant["level"])
    submit_nearminton_guest(driver)


# ─── 동배게임 (competition 모드) ───────────────────────────────────────────────

def create_donbae_game(
    driver: webdriver.Chrome,
    title: str,
    game_type: str,
    court_name: str,
    start_datetime: str,
    skill_label: str = "",
    room_password: str = "",
) -> str:
    """현재 페이지의 게임 생성 버튼을 클릭해 동배게임을 생성하고 게임판 URL을 반환한다.

    room_password가 지정되면 '비밀게임' 체크박스를 활성화하고 비밀번호를 입력한다.
    game_regist_n 같은 고정 URL로 직접 탐색하지 않고 버튼 click → 폼 대기 방식을 사용한다.
    이렇게 해야 서버 라우팅 오류(404 등)를 우회할 수 있다.
    """
    safe_default_content(driver)
    try:
        click_create_room_button(driver, timeout=8)
    except TimeoutException:
        debug_page_snapshot(driver, "create_donbae_game_button_timeout")
        raise

    # 생성 폼이 열릴 때까지 대기
    # 동배게임: '게임방명', 동배모임: '모임방명' — 둘 다 허용
    wait_for_any_visible(
        driver,
        By.XPATH,
        "//*[normalize-space()='게임방명'] | //*[normalize-space()='게임 방명'] | "
        "//*[normalize-space()='모임방명'] | //*[normalize-space()='모임명'] | "
        "//input[contains(@placeholder, '방')]",
        timeout=15,
    )

    # 방 제목
    set_field_after_any_label(
        driver, ("게임방명", "게임 방명", "모임방명", "모임명", "제목", "방 제목"), title
    )

    # 비밀게임 체크박스 + 비밀번호 설정
    if room_password:
        try:
            # 체크박스: label 내부 혹은 '비밀게임' 레이블 다음 input[@type='checkbox']
            secret_chk_xpath = (
                "//label[contains(normalize-space(),'비밀게임') or contains(normalize-space(),'비밀 게임')]"
                "//input[@type='checkbox'] | "
                "//input[@type='checkbox']"
                "[parent::*[contains(normalize-space(),'비밀게임')] or "
                " following-sibling::*[contains(normalize-space(),'비밀게임')] or "
                " preceding-sibling::*[contains(normalize-space(),'비밀게임')]]"
            )
            try:
                chk = wait_for_visible_xpath(driver, secret_chk_xpath, timeout=5)
            except TimeoutException:
                # 레이블 텍스트 자체를 클릭해서 체크박스 토글
                lbl_xpath = "//*[normalize-space()='비밀게임' or normalize-space()='비밀 게임']"
                chk = wait_for_visible_xpath(driver, lbl_xpath, timeout=3)

            if not (hasattr(chk, "is_selected") and chk.is_selected()):
                safe_click(driver, chk)
                time.sleep(0.5)

            # 비밀번호 입력칸 (체크 후 나타남)
            pwd_field_xpath = (
                "//input[@type='password'] | "
                "//input[contains(@placeholder,'비밀번호') or "
                " contains(@id,'pwd') or contains(@name,'pwd') or "
                " contains(@id,'password') or contains(@name,'password')]"
            )
            try:
                pwd_field = wait_for_visible_xpath(driver, pwd_field_xpath, timeout=5)
                safe_click(driver, pwd_field)
                pwd_field.clear()
                pwd_field.send_keys(room_password)
            except TimeoutException:
                # label 기반 탐색 폴백
                try:
                    set_field_after_any_label(driver, ("비밀번호", "패스워드"), room_password)
                except TimeoutException:
                    print("  경고: 비밀번호 입력칸을 찾지 못했습니다.")
            print(f"  비밀번호 설정: {room_password}")
        except TimeoutException:
            print("  경고: 비밀게임 체크박스를 찾지 못했습니다. 비밀번호 미설정으로 진행합니다.")

    # 게임방식 / 모임방식
    try:
        choose_field_after_any_label(driver, ("게임방식", "게임 방식", "모임방식"), game_type)
    except TimeoutException:
        pass

    # 시작일
    try:
        set_field_after_any_label_with_js(driver, ("시작일", "시작 일"), start_datetime)
    except TimeoutException:
        pass

    # 실력구분
    if skill_label:
        try:
            set_field_after_any_label(driver, ("실력구분", "실력 구분"), skill_label)
        except TimeoutException:
            pass

    # 구장 검색
    if court_name:
        try:
            set_field_after_any_label(driver, ("구장 검색", "구장"), court_name)
            time.sleep(0.5)
            court_xpath = (
                f"//*[self::td or self::div or self::span or self::li]"
                f"[normalize-space()='{court_name}' and "
                f"not(ancestor::*[contains(@style,'display: none')])]"
            )
            safe_clickable_xpath(driver, court_xpath, timeout=5)
        except TimeoutException:
            pass

    click_text(driver, "생성하기")
    close_alerts_if_any(driver)
    time.sleep(2.5)

    board_url = driver.current_url
    print(f"  게임판 URL: {board_url}")
    return board_url


def donbae_game_exists(driver: webdriver.Chrome, title: str, timeout: int = 3) -> bool:
    """게임 목록에 해당 제목의 게임이 있는지 확인한다."""
    compact_title = re.sub(r"\s+", "", title)
    title_xpath = (
        "//*[contains(normalize-space(), '{title}') or "
        "contains(translate(normalize-space(), ' ', ''), '{compact_title}')]"
    ).format(title=title, compact_title=compact_title)
    try:
        WebDriverWait(driver, timeout).until(
            lambda _driver: [
                element
                for element in _driver.find_elements(By.XPATH, title_xpath)
                if element.is_displayed()
            ]
        )
        return True
    except TimeoutException:
        return False


def enter_donbae_game_board(driver: webdriver.Chrome, title: str) -> None:
    """게임 목록에서 해당 제목의 게임판으로 이동한다."""
    compact_title = re.sub(r"\s+", "", title)
    title_xpath = (
        "//*[contains(normalize-space(), '{title}') or "
        "contains(translate(normalize-space(), ' ', ''), '{compact_title}')]"
    ).format(title=title, compact_title=compact_title)
    try:
        WebDriverWait(driver, 15).until(
            lambda _driver: [
                element
                for element in _driver.find_elements(By.XPATH, title_xpath)
                if element.is_displayed()
            ]
        )
    except TimeoutException:
        debug_page_snapshot(driver, "donbae_game_title_timeout")
        raise

    elements = driver.find_elements(By.XPATH, title_xpath)
    visible = [e for e in elements if e.is_displayed()]
    if not visible:
        raise TimeoutException(f"동배게임을 찾지 못했습니다: {title}")
    safe_click(driver, visible[0])
    time.sleep(2)


def current_donbae_game_board_names(driver: webdriver.Chrome) -> set[str]:
    """게임판에 현재 등록된 참가자 이름 목록을 반환한다.

    동배게임 게임판도 동배모임과 동일하게 #enterlist h3 구조를 사용한다.
    """
    safe_default_content(driver)
    return current_nearminton_guest_names(driver)


def _dismiss_swal_dialog(driver: webdriver.Chrome) -> None:
    """화면에 열린 sweetalert2 또는 DOM 모달 다이얼로그를 닫는다."""
    # sweetalert2 확인 버튼
    try:
        btns = visible_elements(driver, By.CSS_SELECTOR, ".swal2-confirm, .swal2-popup button")
        if btns:
            safe_click(driver, btns[0])
            time.sleep(0.5)
            return
    except Exception:
        pass
    # 일반 '확인' / '닫기' 버튼
    for text in ("확인", "OK", "닫기"):
        try:
            els = visible_elements(
                driver, By.XPATH,
                f"//*[self::button or self::a][normalize-space()='{text}']",
            )
            if els:
                safe_click(driver, els[0])
                time.sleep(0.3)
                return
        except Exception:
            pass


def register_donbae_game_guest(
    driver: webdriver.Chrome,
    participant: dict[str, str],
    allow_same_name: bool,
) -> None:
    """동배게임 게임판에 참가자를 등록한다.

    [구조 확인]
    game_board 페이지에는 <iframe id="board" src="enter_form?ROOM_ID=..."> 가
    항상 열려 있다 — 동배모임과 완전히 동일한 enter_form 구조.
    하단의 '등록'(id=regist_btn, onclick=add_match)은 게임 매칭용 버튼이므로
    클릭하지 않는다.

    열려 있는 sweetalert 팝업(최소 하나이상의 플레이어... 등)을 먼저 닫은 뒤,
    동배모임과 동일한 register_nearminton_guest() 흐름을 재사용한다.
    """
    safe_default_content(driver)
    # 게임 매칭 관련 팝업이 열려 있으면 먼저 닫기
    _dismiss_swal_dialog(driver)
    # 동배모임 등록 흐름 재사용 (iframe#board → enter_form)
    register_nearminton_guest(driver, participant, allow_same_name)


# ──────────────────────────────────────────────────────────────────────────────

def click_if_configured(driver: webdriver.Chrome, selector: str, seconds_after: float) -> None:
    if not selector:
        return
    element = wait_for_element(driver, selector)
    element.click()
    time.sleep(seconds_after)


def fill_text(driver: webdriver.Chrome, selector: str, value: str) -> None:
    element = wait_for_element(driver, selector)
    element.clear()
    element.send_keys(value)


def fill_choice(driver: webdriver.Chrome, selector: str, value: str) -> None:
    element = wait_for_element(driver, selector)
    tag_name = element.tag_name.lower()

    if tag_name == "select":
        select = Select(element)
        try:
            select.select_by_visible_text(value)
        except Exception:
            select.select_by_value(value)
        return

    element.clear()
    element.send_keys(value)


def mapped_value(config: dict, field: str, value: str) -> str:
    return config.get("values", {}).get(field, {}).get(value, value)


def register_participant(driver: webdriver.Chrome, config: dict, participant: dict[str, str]) -> None:
    selectors = config["selectors"]
    timing = config.get("timing", {})

    click_if_configured(
        driver,
        selectors.get("new_button", ""),
        float(timing.get("after_new_click_seconds", 0.5)),
    )

    fill_text(driver, selectors["name"], participant.get("display_name", participant["name"]))
    fill_choice(driver, selectors["gender"], mapped_value(config, "gender", participant["gender"]))
    fill_choice(driver, selectors["level"], mapped_value(config, "level", participant["level"]))

    wait_for_element(driver, selectors["submit_button"]).click()
    time.sleep(float(timing.get("after_submit_seconds", 1.0)))

    success_selector = selectors.get("success_indicator", "")
    if success_selector:
        try:
            wait_for_element(driver, success_selector, timeout=5)
        except TimeoutException:
            print(f"주의: 등록 성공 표시를 확인하지 못했습니다: {participant['name']}")


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv).expanduser()
    config_path = Path(args.config).expanduser()
    members_path = Path(args.members).expanduser()

    members = load_members(members_path)
    if members:
        print(f"회원 정보 로드: {len(members)}명")
    else:
        print("회원 정보가 비어 있습니다. OCR 후보를 그대로 사용합니다.")

    all_participants = dedupe_participants(
        load_participants(csv_path, members, args.allow_unmatched)
    )
    if args.create_rooms:
        times_to_create = room_times_for(args.create_room_times)
        print("게임방 생성 대상:")
        for time_slot in times_to_create:
            room_participants = participants_for_time(all_participants, time_slot)
            print(f"- {time_slot}타임: {len(room_participants)}명")
    else:
        participants = [
            participant
            for participant in all_participants
            if participant_matches_time(participant, args.time)
        ]
        print(f"등록 대상: {len(participants)}명")

    _today = datetime.now()
    _date_str = f"{_today.month}/{_today.day}"

    if args.dry_run:
        if args.create_rooms:
            for time_slot in room_times_for(args.create_room_times):
                title = args.room_title_template.format(time=time_slot, date=_date_str)
                room_participants = participants_for_time(all_participants, time_slot)
                capacity = args.room_capacity or len(room_participants)
                print(f"\n[{title}] 모집인원 {capacity}명")
                for participant in room_participants:
                    print(
                        f"- {participant.get('display_name', participant['name'])} / "
                        f"{participant['gender']} / {participant['level']} / "
                        f"time={participant.get('time_slot', 'all')}"
                    )
        else:
            for participant in participants:
                print(
                    f"- {participant.get('display_name', participant['name'])} / {participant['gender']} / "
                    f"{participant['level']} / time={participant.get('time_slot', 'all')}"
                )
        return

    profile_dir = None if args.temporary_profile else Path(args.profile_dir).expanduser()
    if profile_dir:
        print(f"Chrome 로그인 세션 저장 폴더: {profile_dir}")

    driver = start_driver(args.headless, profile_dir, args.chrome_debug_port)

    try:
        if args.generic_config:
            config = load_config(config_path)
            driver.get(config["url"])
            time.sleep(float(config.get("wait_after_open_seconds", 3)))

            if config.get("pause_for_login", True):
                print("__WAITING__:로그인/등록 화면 준비가 끝나면 Enter를 누르세요...")
                inject_continue_button(driver, "로그인 완료 → 계속 진행")
                input("로그인/등록 화면 준비가 끝나면 Enter를 누르세요...")
                remove_continue_button(driver)

            for index, participant in enumerate(participants, start=1):
                print(f"[{index}/{len(participants)}] 등록 중: {participant.get('display_name', participant['name'])}")
                register_participant(driver, config, participant)

            print("등록 완료")
            return

        # 동배게임 모드는 competition 목록 URL로 이동
        open_url = args.url
        if args.site_mode == "game" and (not args.url or args.url == DEFAULT_SITE_URL):
            open_url = DONBAE_GAME_LIST_URL

        driver.get(open_url)
        time.sleep(3)

        # ── 동배게임 백업 모드 ──────────────────────────────────────────────────
        if args.site_mode == "game":
            print("__WAITING__:nearminton에 로그인되어 있고 동배게임 목록이 보이면 Enter를 누르세요...")
            inject_continue_button(driver, "로그인 완료 → 계속 진행")
            input("nearminton에 로그인되어 있고 동배게임 목록이 보이면 Enter를 누르세요...")
            remove_continue_button(driver)
            game_list_url = driver.current_url
            room_start = args.room_start.strip() or datetime.now().strftime("%Y-%m-%d %H:%M")

            for time_slot in room_times_for(args.create_room_times):
                room_participants = participants_for_time(all_participants, time_slot)
                if not room_participants:
                    print(f"{time_slot}타임 등록 대상이 없어 건너뜁니다.")
                    continue

                title = args.room_title_template.format(time=time_slot, date=_date_str)

                if donbae_game_exists(driver, title):
                    print(f"[{time_slot}타임] 기존 동배게임 사용: {title}")
                    enter_donbae_game_board(driver, title)
                else:
                    print(f"[{time_slot}타임] 동배게임 생성 중: {title}")
                    create_donbae_game(
                        driver,
                        title=title,
                        game_type=args.room_game_type,
                        court_name=args.court_name,
                        start_datetime=room_start,
                        skill_label=args.skill_label,
                        room_password=_today.strftime("%m%d"),
                    )
                    # 생성 후 게임판으로 자동 이동됨

                for index, participant in enumerate(room_participants, start=1):
                    print(
                        f"[{time_slot}타임 {index}/{len(room_participants)}] 등록 중: "
                        f"{participant.get('display_name', participant['name'])}"
                    )
                    register_donbae_game_guest(driver, participant, args.allow_same_name)

                # 다음 타임을 위해 게임 목록으로 돌아감
                driver.get(game_list_url)
                time.sleep(1.5)

            print("동배게임 등록 완료")
            return

        if args.create_rooms:
            print("__WAITING__:로그인되어 있고 모임의 게임방 탭이 보이면 Enter를 누르세요...")
            inject_continue_button(driver, "로그인 완료 → 계속 진행")
            input("로그인되어 있고 모임의 게임방 탭이 보이면 Enter를 누르세요...")
            remove_continue_button(driver)
            game_list_url = driver.current_url
            room_start = args.room_start.strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
            # 동배모임 생성 실패 시 동배게임 백업으로 전환했는지 여부
            _game_fallback_active = False

            for time_slot in room_times_for(args.create_room_times):
                room_participants = participants_for_time(all_participants, time_slot)
                if not room_participants:
                    print(f"{time_slot}타임 등록 대상이 없어 건너뜁니다.")
                    continue

                title = args.room_title_template.format(time=time_slot, date=_date_str)
                capacity = args.room_capacity or len(room_participants)

                # ── 동배게임 백업 모드 (이미 폴백 전환된 경우) ──────────────────
                if _game_fallback_active:
                    if donbae_game_exists(driver, title):
                        print(f"[{time_slot}타임] 기존 동배게임 사용: {title}")
                        enter_donbae_game_board(driver, title)
                    else:
                        print(f"[{time_slot}타임] 동배게임 생성 중: {title}")
                        create_donbae_game(
                            driver,
                            title=title,
                            game_type=args.room_game_type,
                            court_name=args.court_name,
                            start_datetime=room_start,
                            skill_label=args.skill_label,
                            room_password=_today.strftime("%m%d"),
                        )
                    for index, participant in enumerate(room_participants, start=1):
                        print(
                            f"[{time_slot}타임 {index}/{len(room_participants)}] 등록 중: "
                            f"{participant.get('display_name', participant['name'])}"
                        )
                        register_donbae_game_guest(driver, participant, args.allow_same_name)
                    driver.get(game_list_url)
                    time.sleep(1.5)
                    continue

                # ── 동배모임 일반 모드 ────────────────────────────────────────
                if room_title_exists(driver, title):
                    print(f"[{time_slot}타임] 기존 게임방 사용: {title}")
                    enter_nearminton_room(driver, title)
                else:
                    print(f"[{time_slot}타임] 게임방 생성 중: {title} / 모집인원 {capacity}명")
                    try:
                        create_nearminton_room(
                            driver,
                            title=title,
                            kind=args.room_kind,
                            game_type=args.room_game_type,
                            capacity=capacity,
                            court_name=args.court_name,
                            start_datetime=room_start,
                            skill_label=args.skill_label,
                        )
                        driver.get(game_list_url)
                        time.sleep(1.5)
                        enter_nearminton_room(driver, title)
                    except Exception as _err:
                        # ── 동배모임 생성 실패 → 동배게임으로 자동 폴백 ──────────
                        print(f"  [경고] 동배모임 게임방 생성 실패 ({_err.__class__.__name__})")
                        print("  동배게임 백업 모드로 전환합니다...")
                        _game_fallback_active = True
                        game_list_url = DONBAE_GAME_LIST_URL
                        driver.get(game_list_url)
                        time.sleep(2)
                        print(f"[{time_slot}타임] 동배게임 생성 중: {title}")
                        try:
                            create_donbae_game(
                                driver,
                                title=title,
                                game_type=args.room_game_type,
                                court_name=args.court_name,
                                start_datetime=room_start,
                                skill_label=args.skill_label,
                                room_password=_today.strftime("%m%d"),
                            )
                        except Exception as _err2:
                            print(f"  동배게임 생성도 실패: {_err2}")
                            continue
                        for index, participant in enumerate(room_participants, start=1):
                            print(
                                f"[{time_slot}타임 {index}/{len(room_participants)}] 등록 중: "
                                f"{participant.get('display_name', participant['name'])}"
                            )
                            register_donbae_game_guest(driver, participant, args.allow_same_name)
                        driver.get(game_list_url)
                        time.sleep(1.5)
                        continue  # 이 타임은 이미 처리 완료

                # 동배모임 정상 흐름: 입장 등록
                for index, participant in enumerate(room_participants, start=1):
                    print(
                        f"[{time_slot}타임 {index}/{len(room_participants)}] 입장 등록 중: "
                        f"{participant.get('display_name', participant['name'])}"
                    )
                    register_nearminton_guest(driver, participant, args.allow_same_name)

                driver.get(game_list_url)
                time.sleep(1.5)

            if _game_fallback_active:
                print("게임방 생성 및 입장 등록 완료 (동배게임 백업 모드 사용됨)")
            else:
                print("게임방 생성 및 입장 등록 완료")
            return

        print("__WAITING__:로그인되어 있고 게임판이 보이면 Enter를 누르세요...")
        inject_continue_button(driver, "로그인 완료 → 계속 진행")
        input("로그인되어 있고 게임판이 보이면 Enter를 누르세요...")
        remove_continue_button(driver)

        for index, participant in enumerate(participants, start=1):
            print(f"[{index}/{len(participants)}] 입장 등록 중: {participant.get('display_name', participant['name'])}")
            register_nearminton_guest(driver, participant, args.allow_same_name)

        print("입장 등록 완료")
    finally:
        if args.close_browser:
            driver.quit()
        else:
            print("__WAITING__:브라우저를 열어둡니다. 종료하려면 Enter를 누르세요...")
            inject_continue_button(driver, "브라우저 닫기")
            input("브라우저를 열어둡니다. 종료하려면 Enter를 누르세요...")
            remove_continue_button(driver)


if __name__ == "__main__":
    main()
