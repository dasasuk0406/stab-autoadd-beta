import argparse
import os
import subprocess
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="투표 사진 OCR 추출과 nearminton 게스트 등록을 한 번에 또는 단계별로 실행합니다."
    )
    parser.add_argument(
        "--step",
        choices=["all", "ocr", "entry", "create-rooms"],
        default="",
        help="실행 단계. 생략하면 메뉴에서 선택합니다.",
    )

    parser.add_argument("-i", "--image", default="", help="OCR 입력 이미지 또는 폴더 경로")
    parser.add_argument("--input-dir", default="", help="OCR 입력 폴더. 기본값: vote 폴더")
    parser.add_argument("--latest-only", action="store_true", help="가장 최근 캡처 이미지 하나만 OCR 처리")
    parser.add_argument("-o", "--output", default="participants.csv", help="참가자 CSV 출력/입력 경로")
    parser.add_argument("--members", default="members.csv", help="회원 정보 CSV 경로")
    parser.add_argument("--include-unmatched", action="store_true", help="OCR 결과에 미매칭 후보도 저장")
    parser.add_argument("--gender", default="남", help="OCR 기본 성별")
    parser.add_argument("--level", default="D", help="OCR 기본 급수")
    parser.add_argument(
        "--time-slot",
        default="unknown",
        choices=["1", "2", "both", "unknown"],
        help="OCR에서 투표 항목을 못 찾았을 때 넣을 타임",
    )
    parser.add_argument("--min-confidence", type=float, default=0.35, help="OCR 최소 신뢰도")
    parser.add_argument("--ocr-mode", choices=["clova"], default="clova", help="OCR 처리 방식")
    parser.add_argument("--no-cache", action="store_true", help="OCR 캐시 미사용")

    parser.add_argument("--url", default="", help="nearminton 게임판 또는 모임 URL")
    parser.add_argument(
        "--entry-time",
        choices=["1", "2", "all"],
        default="1",
        help="게스트만 등록할 운동 타임",
    )
    parser.add_argument("--entry-allow-unmatched", action="store_true", help="등록 단계에서 미매칭 후보도 허용")
    parser.add_argument("--allow-same-name", action="store_true", help="사이트에 같은 이름이 있어도 등록")
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    parser.add_argument("--close-browser", action="store_true", help="완료 후 브라우저 닫기")
    parser.add_argument("--profile-dir", default="", help="Chrome 로그인 세션 프로필 폴더")
    parser.add_argument("--temporary-profile", action="store_true", help="임시 Chrome 프로필 사용")
    parser.add_argument("--chrome-debug-port", type=int, default=0, help="기존 Chrome 창 연결 포트 (0=새 창)")
    parser.add_argument("--generic-config", action="store_true", help="site_config.json 선택자 기반 등록")
    parser.add_argument(
        "--site-mode",
        choices=["moim", "game"],
        default="moim",
        help="사이트 등록 모드. moim=동배모임(기본), game=동배게임(백업)",
    )
    parser.add_argument("--dry-run-entry", action="store_true", help="등록 단계 CSV 확인만 수행")
    parser.add_argument(
        "--pause-after-ocr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="전체 실행에서 OCR 후 CSV 검수용 Enter 대기. 기본값: 켜짐",
    )

    parser.add_argument("--create-room-times", choices=["1", "2", "all"], default="all")
    parser.add_argument("--room-title-template", default="[우동배] {time}타임")
    parser.add_argument("--room-kind", default="정모")
    parser.add_argument("--room-game-type", default="자유게임")
    parser.add_argument("--court-name", default="")
    parser.add_argument("--skill-label", default="")
    parser.add_argument("--room-capacity", type=int, default=0)
    parser.add_argument("--room-start", default="")
    return parser.parse_args()


def choose_step() -> str:
    print("실행할 작업을 선택하세요.")
    print("1. 투표 사진 추출 후 게임방 생성 및 게스트 등록")
    print("2. 투표 사진 추출만")
    print("3. 게스트 추가만")
    print("4. 게임방 생성 후 등록")
    choice = input("번호 입력 [1]: ").strip() or "1"
    return {
        "1": "all",
        "2": "ocr",
        "3": "entry",
        "4": "create-rooms",
    }.get(choice, "all")


def add_if_value(command: list[str], option: str, value: str) -> None:
    if value:
        command.extend([option, value])


def run_command(command: list[str]) -> None:
    print("\n실행:", " ".join(command))
    subprocess.run(command, cwd=SCRIPT_DIR, check=True)


def build_ocr_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, str(SCRIPT_DIR / "kakao_vote_to_csv.py")]
    add_if_value(command, "--image", args.image)
    add_if_value(command, "--input-dir", args.input_dir)
    command.extend(["--output", args.output])
    command.extend(["--members", args.members])
    command.extend(["--gender", args.gender])
    command.extend(["--level", args.level])
    command.extend(["--time-slot", args.time_slot])
    command.extend(["--min-confidence", str(args.min_confidence)])
    if args.latest_only:
        command.append("--latest-only")
    if args.include_unmatched:
        command.append("--include-unmatched")
    if args.no_cache:
        command.append("--no-cache")
    return command


def build_entry_command(args: argparse.Namespace, create_rooms: bool) -> list[str]:
    command = [sys.executable, str(SCRIPT_DIR / "auto_guest_entry.py")]
    command.extend(["--csv", args.output])
    command.extend(["--members", args.members])
    add_if_value(command, "--url", args.url)
    add_if_value(command, "--profile-dir", args.profile_dir)
    if create_rooms:
        command.append("--create-rooms")
        command.extend(["--create-room-times", args.create_room_times])
        command.extend(["--room-title-template", args.room_title_template])
        command.extend(["--room-kind", args.room_kind])
        command.extend(["--room-game-type", args.room_game_type])
        add_if_value(command, "--court-name", args.court_name)
        add_if_value(command, "--skill-label", args.skill_label)
        command.extend(["--room-capacity", str(args.room_capacity)])
        add_if_value(command, "--room-start", args.room_start)
    else:
        command.extend(["--time", args.entry_time])

    if args.entry_allow_unmatched:
        command.append("--allow-unmatched")
    if args.allow_same_name:
        command.append("--allow-same-name")
    if args.headless:
        command.append("--headless")
    if args.close_browser:
        command.append("--close-browser")
    if args.temporary_profile:
        command.append("--temporary-profile")
    if args.chrome_debug_port:
        command.extend(["--chrome-debug-port", str(args.chrome_debug_port)])
    if args.generic_config:
        command.append("--generic-config")
    if args.dry_run_entry:
        command.append("--dry-run")
    if args.site_mode and args.site_mode != "moim":
        command.extend(["--site-mode", args.site_mode])
    return command


def main() -> None:
    args = parse_args()
    step = args.step or choose_step()

    if step in {"all", "ocr"}:
        run_command(build_ocr_command(args))

    if step == "all" and args.pause_after_ocr:
        csv_path = (SCRIPT_DIR / args.output).resolve()
        print(f"\n__CSV_REVIEW__:{csv_path}")
        input(f"CSV를 확인하세요: {csv_path}\n확인 후 게스트 추가를 계속하려면 Enter를 누르세요...")

    if step in {"all", "entry", "create-rooms"}:
        run_command(build_entry_command(args, create_rooms=step in {"all", "create-rooms"}))


if __name__ == "__main__":
    main()
