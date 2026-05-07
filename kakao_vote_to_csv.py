import argparse
import csv
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = (
    SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else SCRIPT_DIR / ".venv" / "bin" / "python"
)

if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

import pandas as pd


STOPWORDS = {
    "투표",
    "마감",
    "참여",
    "전체",
    "확인",
    "취소",
    "익명",
    "명",
    "명입니다",
    "오전",
    "오후",
    "today",
    "yes",
    "no",
    "ixic",
    "맵버별",
    "멤버별",
    "기찮여",
    "순서",
    "정렬",
    "운영진",
    "풀타임",
    "투표자",
    "참여자",
    "타임",
}
STOPWORD_PARTS = {
    "투표",
    "현황",
    "항목",
    "미참여",
    "참여",
    "타임",
    "수요일",
    "체육관",
    "정규운동",
    "refresh",
}
NAME_CORRECTIONS = {
    "정수반": "정수빈",
    "승현": "송현",
    "엠준흠": "문준흠",
    "엠주늠": "문준흠",
    "엠주흠": "문준흠",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DEFAULT_MEMBERS_PATH = SCRIPT_DIR / "members.csv"
TIME_SLOT_1 = "1"
TIME_SLOT_2 = "2"
TIME_SLOT_BOTH = "both"
TIME_SLOT_UNKNOWN = "unknown"
SORT_OK = "ok"
SORT_INVALID = "invalid"
SORT_UNKNOWN = "unknown"
OCR_CACHE_VERSION = 2


def clean_name(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^가-힣a-z0-9]", "", text)


def is_valid_name(name: str) -> bool:
    if not name:
        return False
    if name in STOPWORDS:
        return False
    if any(part in name for part in STOPWORD_PARTS):
        return False
    if len(name) < 2:
        return False
    if any(char.isdigit() for char in name):
        return False
    if re.fullmatch(r"[a-z]+", name) and len(name) < 3:
        return False
    if re.fullmatch(r"[가-힣]+", name) and len(name) > 5:
        return False
    return True


def load_clova_config() -> tuple[str, str]:
    config_path = SCRIPT_DIR / "site_config.json"
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg.get("clova_invoke_url", "") or os.environ.get("CLOVA_OCR_URL", "")
        key = cfg.get("clova_secret_key", "") or os.environ.get("CLOVA_OCR_SECRET", "")
        return url, key
    return os.environ.get("CLOVA_OCR_URL", ""), os.environ.get("CLOVA_OCR_SECRET", "")


def clova_ocr_image(image_path: Path) -> list[dict[str, object]]:
    import base64
    import uuid
    import requests

    invoke_url, secret_key = load_clova_config()
    if not invoke_url or not secret_key:
        raise RuntimeError(
            "CLOVA OCR 설정이 없습니다. site_config.json에 "
            "clova_invoke_url과 clova_secret_key를 추가하세요."
        )

    ext = image_path.suffix.lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        fmt = "jpeg"
    elif ext == "png":
        fmt = "png"
    else:
        fmt = "jpeg"

    with image_path.open("rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    endpoint = invoke_url.rstrip("/") + "/general"
    payload = {
        "version": "V2",
        "requestId": str(uuid.uuid4()),
        "timestamp": 0,
        "images": [{"format": fmt, "name": image_path.stem, "data": img_b64}],
    }
    resp = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "X-OCR-SECRET": secret_key},
        json=payload,
        timeout=20,
    )
    if resp.status_code == 401:
        raise RuntimeError("CLOVA OCR 인증 실패. clova_secret_key를 확인하세요.")
    resp.raise_for_status()

    items = []
    for image in resp.json().get("images", []):
        for field in image.get("fields", []):
            text = field.get("inferText", "").strip()
            if not text:
                continue
            confidence = float(field.get("inferConfidence", 1.0))
            vertices = field.get("boundingPoly", {}).get("vertices", [])
            if vertices:
                cx = sum(v.get("x", 0) for v in vertices) / len(vertices)
                cy = sum(v.get("y", 0) for v in vertices) / len(vertices)
            else:
                cx = cy = 0.0
            items.append({"text": text, "confidence": confidence, "x": cx, "y": cy})
    return items


def ocr_cache_path(image_path: Path) -> Path:
    key = hashlib.sha1(str(image_path.resolve()).encode("utf-8")).hexdigest()
    return SCRIPT_DIR / ".ocr_cache" / f"{key}.json"


def cached_ocr_image(image_path: Path, use_cache: bool = True) -> list[dict[str, object]]:
    if not use_cache:
        return clova_ocr_image(image_path)

    stat = image_path.stat()
    cache_path = ocr_cache_path(image_path)
    cache_meta = {
        "version": OCR_CACHE_VERSION,
        "path": str(image_path.resolve()),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }

    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("meta") == cache_meta:
                return cached.get("items", [])
        except (OSError, json.JSONDecodeError):
            pass

    items = clova_ocr_image(image_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump({"meta": cache_meta, "items": items}, f, ensure_ascii=False)
    except OSError:
        pass
    return items


def ocr_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return item[0]


def ocr_confidence(item) -> float:
    if isinstance(item, dict):
        return float(item.get("confidence", 0))
    return float(item[1])


def ocr_y(item) -> float:
    if isinstance(item, dict):
        return float(item.get("y", 0))
    return 0


def ocr_x(item) -> float:
    if isinstance(item, dict):
        return float(item.get("x", 0))
    return 0


def _group_ocr_by_row(items: list, y_threshold: float = 40.0) -> list[list]:
    """Group OCR items whose y-centers are within y_threshold pixels into the same row."""
    sorted_items = sorted(items, key=lambda v: (ocr_y(v), ocr_x(v)))
    rows: list[list] = []
    current: list = []
    row_y: float | None = None
    for item in sorted_items:
        y = ocr_y(item)
        if row_y is None or abs(y - row_y) <= y_threshold:
            current.append(item)
            row_y = y if row_y is None else (row_y * (len(current) - 1) + y) / len(current)
        else:
            rows.append(current)
            current = [item]
            row_y = y
    if current:
        rows.append(current)
    return rows


def extract_names_from_ocr(
    ocr_results: list[tuple[str, float]], min_confidence: float
) -> tuple[list[str], int]:
    """Return (names, person_row_count).

    names — individual tokens suitable for fuzzy matching (may include multiple
            tokens per foreign name).
    person_row_count — number of distinct image rows that contained at least one
                       valid name token; used for the display "후보 이름: N명".
    """
    confident = [item for item in ocr_results if ocr_confidence(item) >= min_confidence]
    rows = _group_ocr_by_row(confident)

    names = []
    person_rows = 0
    for row in rows:
        tokens = [clean_name(ocr_text(item)) for item in row]
        tokens = [NAME_CORRECTIONS.get(t, t) for t in tokens if t]
        valid_tokens = [t for t in tokens if is_valid_name(t)]
        if not valid_tokens:
            continue
        person_rows += 1
        for token in valid_tokens:
            names.append(token)

    deduped = []
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)

    return deduped, person_rows


def infer_time_slot(image_path: Path, ocr_results: list[tuple[str, float]], fallback: str) -> str:
    haystack = image_path.stem + " " + " ".join(ocr_text(item) for item in ocr_results)
    haystack = re.sub(r"\s+", "", haystack)

    if "풀타임" in haystack or "운영진" in haystack:
        return TIME_SLOT_BOTH
    if "1타임" in haystack or "1시간" in haystack:
        return TIME_SLOT_1
    if "2타임" in haystack or "2시간" in haystack:
        return TIME_SLOT_2
    return fallback


def infer_participant_limit(ocr_results: list[dict[str, object]]) -> int | None:
    top_texts = [
        ocr_text(item)
        for item in sorted(ocr_results, key=lambda value: ocr_y(value))
        if ocr_confidence(item) >= 0.25 and ocr_y(item) < 420
    ]
    haystack = re.sub(r"\s+", "", " ".join(top_texts))
    matches = re.findall(r"(\d{1,3})명", haystack)
    if not matches:
        return None
    return int(matches[-1])


def infer_vote_title(ocr_results: list[dict[str, object]], time_slot: str, limit: int | None) -> str:
    all_text = re.sub(r"\s+", "", " ".join(ocr_text(item) for item in ocr_results))
    limit_text = f"{limit}명" if limit is not None else "정원알수없음"
    if "운영진" in all_text:
        return f"운영진 {limit_text}"
    if "풀타임" in all_text:
        return f"풀타임 {limit_text}"
    if time_slot == TIME_SLOT_1:
        return f"1타임 {limit_text}"
    if time_slot == TIME_SLOT_2:
        return f"2타임 {limit_text}"

    top_candidates = [
        ocr_text(item).strip()
        for item in sorted(ocr_results, key=lambda value: ocr_y(value))
        if ocr_confidence(item) >= 0.25 and 120 <= ocr_y(item) <= 360
    ]
    for text in top_candidates:
        compact = re.sub(r"\s+", "", text)
        if "명" in compact and (
            "타임" in compact or "운영진" in compact or "풀타임" in compact
        ):
            return compact
    return f"{time_slot}:{limit if limit is not None else 'unknown'}"


def infer_entry_suffix(vote_title: str, time_slot: str) -> str:
    compact = re.sub(r"\s+", "", vote_title)
    if "운영진" in compact:
        return "운"
    if "풀타임" in compact:
        return "풀"
    if time_slot == TIME_SLOT_1:
        return "1탐"
    if time_slot == TIME_SLOT_2:
        return "2탐"
    return ""


def infer_sort_status(ocr_results: list[dict[str, object]]) -> str:
    haystack = re.sub(r"\s+", "", " ".join(ocr_text(item) for item in ocr_results)).lower()
    if any(marker in haystack for marker in ("이름순", "가나다", "닉네임순")):
        return SORT_INVALID
    if "투표참여자순서정렬" in haystack or "참여자순서정렬" in haystack:
        return SORT_OK
    return SORT_UNKNOWN


def longest_overlap(left: list[str], right: list[str]) -> int:
    max_size = min(len(left), len(right))
    for size in range(max_size, 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def merge_ordered_name_pages(pages: list[dict[str, object]]) -> list[dict[str, str]]:
    if not pages:
        return []

    remaining = pages[:]
    start_index = 0
    best_start_score = -1
    for index, page in enumerate(remaining):
        names = page["names"]
        incoming = max(
            (
                longest_overlap(other["names"], names)
                for other in remaining
                if other is not page
            ),
            default=0,
        )
        outgoing = max(
            (
                longest_overlap(names, other["names"])
                for other in remaining
                if other is not page
            ),
            default=0,
        )
        score = outgoing - incoming
        if score > best_start_score:
            best_start_score = score
            start_index = index

    current = remaining.pop(start_index)
    merged_names = list(current["names"])
    merged_sources = {name: current["source_image"] for name in current["names"]}

    while remaining:
        best_index = 0
        best_overlap = -1
        for index, page in enumerate(remaining):
            overlap = longest_overlap(merged_names, page["names"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = index
        page = remaining.pop(best_index)
        overlap = max(best_overlap, 0)
        for name in page["names"][overlap:]:
            if name not in merged_sources:
                merged_names.append(name)
                merged_sources[name] = page["source_image"]

    return [
        {
            "name": name,
            "source_image": merged_sources[name],
        }
        for name in merged_names
    ]


def time_slots_for(value: str) -> list[str]:
    value = (value or TIME_SLOT_UNKNOWN).strip().lower()
    if value in {TIME_SLOT_BOTH, "full", "fulltime", "풀타임", "운영진", "all"}:
        return [TIME_SLOT_1, TIME_SLOT_2]
    if value in {TIME_SLOT_1, "1타임", "one"}:
        return [TIME_SLOT_1]
    if value in {TIME_SLOT_2, "2타임", "two"}:
        return [TIME_SLOT_2]
    return [TIME_SLOT_UNKNOWN]


def split_gender_suffix(name: str, default_gender: str) -> tuple[str, str]:
    if re.fullmatch(r"[가-힣]{3,6}[남여]", name):
        return name[:-1], name[-1]
    return name, default_gender


def normalize_for_match(value: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", value.strip().lower())


def _trailing_digits(normalized: str) -> str:
    """정규화된 이름 끝의 숫자 suffix를 반환한다. (예: '김도현23' → '23')
    괄호 학번 연도 구분 방식(김도현(23))에서 연도 부분을 추출하는 데 사용된다."""
    m = re.search(r"\d+$", normalized)
    return m.group() if m else ""


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
            "aliases": "|".join(dict.fromkeys(aliases)),
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


def build_rows(
    detected: list[dict[str, str]],
    members: list[dict[str, str]],
    default_gender: str,
    default_level: str,
    include_unmatched: bool,
) -> list[dict[str, str]]:
    rows = []
    seen = set()

    for item in detected:
        raw_name = item["name"]
        clean_participant_name, gender = split_gender_suffix(raw_name, default_gender)
        level = default_level
        member_id = ""
        match_status = "ocr"

        member = match_member(clean_participant_name, members)
        if member:
            member_id = member["member_id"]
            clean_participant_name = member["name"]
            gender = member["gender"] or gender
            level = member["level"] or level
            match_status = "matched"
        elif members and not include_unmatched:
            continue
        elif not re.fullmatch(r"[가-힣]{2,5}(\(\d{1,4}\))?", clean_participant_name):
            continue

        for time_slot in time_slots_for(item["time_slot"]):
            # 겹쳐 찍힌 같은 투표 항목은 1명으로 합칩니다. 동명이인은 members.csv에
            # member_id를 다르게 넣으면 별도 사람으로 유지할 수 있습니다.
            dedupe_key = (member_id or clean_participant_name, gender, level, time_slot)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append(
                {
                    "member_id": member_id,
                    "name": clean_participant_name,
                    "gender": gender,
                    "level": level,
                    "time_slot": time_slot,
                    "entry_suffix": item.get("entry_suffix", ""),
                    "match_status": match_status,
                    "source_image": item["source_image"],
                }
            )
    return rows


def write_csv(rows: list[dict[str, str]], output_path: Path) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return df


def find_latest_image(search_dir: Path) -> Path:
    image_paths = [
        path
        for path in search_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not image_paths:
        raise FileNotFoundError(
            f"캡처 이미지 파일을 찾지 못했습니다: {search_dir}\n"
            "이 폴더에 .jpg, .jpeg 또는 .png 파일을 넣고 다시 실행하세요."
        )
    return max(image_paths, key=lambda path: path.stat().st_mtime)


def find_images(search_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in search_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def default_input_dir() -> Path:
    vote_dir = SCRIPT_DIR / "vote"
    if vote_dir.exists():
        return vote_dir
    return SCRIPT_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="카카오톡 투표 캡처 이미지에서 참가자 이름을 추출해 CSV로 저장합니다."
    )
    parser.add_argument(
        "-i",
        "--image",
        default=None,
        help="입력 이미지 또는 폴더 경로. 생략하면 vote 폴더의 캡처 이미지를 자동 사용합니다.",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="처리할 캡처 이미지 폴더. 기본값: vote 폴더가 있으면 vote, 없으면 현재 스크립트 폴더",
    )
    parser.add_argument(
        "--all-images",
        action="store_true",
        default=True,
        help="입력 폴더의 모든 .jpg/.jpeg/.png 파일을 처리합니다. 기본값: 켜짐",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="가장 최근 캡처 이미지 하나만 처리합니다.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="participants.csv",
        help="출력 CSV 경로. 기본값: participants.csv",
    )
    parser.add_argument(
        "--gender",
        default="남",
        help="CSV에 넣을 기본 성별. 기본값: 남",
    )
    parser.add_argument(
        "--level",
        default="D",
        help="CSV에 넣을 기본 급수. 기본값: D",
    )
    parser.add_argument(
        "--members",
        default=str(DEFAULT_MEMBERS_PATH),
        help="성별/급수/별칭 기준 회원 CSV 경로. 기본값: members.csv",
    )
    parser.add_argument(
        "--include-unmatched",
        action="store_true",
        help="members.csv가 있어도 매칭되지 않은 OCR 후보를 함께 저장합니다.",
    )
    parser.add_argument(
        "--time-slot",
        default=TIME_SLOT_UNKNOWN,
        choices=[TIME_SLOT_1, TIME_SLOT_2, TIME_SLOT_BOTH, TIME_SLOT_UNKNOWN],
        help="이미지에서 투표 항목을 못 찾았을 때 넣을 타임. 기본값: unknown",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.35,
        help="OCR 최소 신뢰도. 기본값: 0.35",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="OCR 캐시를 사용하지 않고 매번 새로 읽습니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_paths = []
    if args.image:
        image_path = Path(args.image).expanduser()
        if not image_path.is_absolute():
            image_path = SCRIPT_DIR / image_path
        if image_path.is_dir():
            image_paths = find_images(image_path)
        else:
            image_paths = [image_path]
    elif args.latest_only:
        input_dir = Path(args.input_dir).expanduser() if args.input_dir else default_input_dir()
        if not input_dir.is_absolute():
            input_dir = SCRIPT_DIR / input_dir
        image_paths = [find_latest_image(input_dir)]
    else:
        input_dir = Path(args.input_dir).expanduser() if args.input_dir else default_input_dir()
        if not input_dir.is_absolute():
            input_dir = SCRIPT_DIR / input_dir
        image_paths = find_images(input_dir)

    output_path = Path(args.output).expanduser()
    if not output_path.is_absolute():
        output_path = SCRIPT_DIR / output_path

    if not image_paths:
        print("처리할 캡처 이미지 파일이 없습니다.")
        return

    members_path = Path(args.members).expanduser()
    if not members_path.is_absolute():
        members_path = SCRIPT_DIR / members_path
    members = load_members(members_path)

    print("OCR: Naver CLOVA Vision API")
    pages_by_vote = {}
    verified_vote_keys = set()
    for image_path in image_paths:
        print(f"사용 이미지: {image_path}")
        ocr_results = cached_ocr_image(image_path, not args.no_cache)
        time_slot = infer_time_slot(image_path, ocr_results, args.time_slot)
        participant_limit = infer_participant_limit(ocr_results)
        vote_title = infer_vote_title(ocr_results, time_slot, participant_limit)
        entry_suffix = infer_entry_suffix(vote_title, time_slot)
        vote_key = (time_slot, participant_limit, vote_title)
        sort_status = infer_sort_status(ocr_results)
        names, person_count = extract_names_from_ocr(ocr_results, args.min_confidence)
        if sort_status == SORT_OK:
            verified_vote_keys.add(vote_key)
            sort_message = "정렬 확인"
        elif sort_status == SORT_INVALID:
            sort_message = "잘못된 정렬"
        else:
            sort_message = "정렬 확인 불가"
        limit_message = f"{participant_limit}명" if participant_limit is not None else "알 수 없음"
        print(
            f"  제목: {vote_title}, 추정 타임: {time_slot}, 정원: {limit_message}, "
            f"후보 이름: {person_count}명, {sort_message}"
        )
        pages_by_vote.setdefault(vote_key, []).append(
            {
                "source_image": image_path.name,
                "names": names,
                "sort_status": sort_status,
                "time_slot": time_slot,
                "participant_limit": participant_limit,
                "vote_title": vote_title,
                "entry_suffix": entry_suffix,
            }
        )

    detected = []
    for vote_key, pages in pages_by_vote.items():
        time_slot, participant_limit, vote_title = vote_key
        has_verified_sort = vote_key in verified_vote_keys
        valid_pages = []
        for page in pages:
            if page["sort_status"] == SORT_INVALID:
                print(f"경고: {page['source_image']} 파일은 투표 참여자 순서 정렬이 아니어서 제외합니다.")
                continue
            if page["sort_status"] == SORT_UNKNOWN and not has_verified_sort:
                print(
                    f"경고: {page['source_image']} 파일은 정렬 상태를 확인하지 못해서 제외합니다. "
                    "카카오톡에서 '투표 참여자 순서 정렬'로 바꾼 뒤 다시 캡처하세요."
                )
                continue
            valid_pages.append(page)

        ordered_names = merge_ordered_name_pages(valid_pages)
        if participant_limit is None:
            print(f"경고: {vote_title} 정원을 읽지 못했습니다. 안전을 위해 이 투표는 제외합니다.")
            continue
        if len(ordered_names) > participant_limit:
            print(
                f"정원 제한: {vote_title} 후보 {len(ordered_names)}명 중 "
                f"위에서부터 {participant_limit}명만 저장합니다."
            )
            ordered_names = ordered_names[:participant_limit]

        detected.extend(
            {
                "name": item["name"],
                "time_slot": time_slot,
                "entry_suffix": pages[0].get("entry_suffix", ""),
                "source_image": item["source_image"],
            }
            for item in ordered_names
        )

    if not detected:
        print("이름을 추출하지 못했습니다. 이미지 해상도나 캡처 영역을 확인하세요.")
        return

    rows = build_rows(detected, members, args.gender, args.level, args.include_unmatched)
    df = write_csv(rows, output_path)

    print(f"추출 완료: {len(df)}명")
    import pandas as _pd
    with _pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", None):
        print(df.to_string(index=False))
    print(f"저장 완료: {output_path}")
    print("gender/level 기본값은 필요하면 CSV에서 수정한 뒤 등록하세요.")


if __name__ == "__main__":
    main()
