"""가짜 송신기 실행.

예:
  python -m rafi.sim --list
  python -m rafi.sim radar-then-rf
  python -m rafi.sim demo --fast
  python -m rafi.sim radar-only --at 10:10   (청소 시간대 표시 확인)
"""

import argparse
from datetime import datetime

import httpx

from rafi.sim.scenarios import SCENARIOS, Clock, Simulator, demo_sequence
from rafi.sim.sender import RejectedError, http_sender


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m rafi.sim", description="가짜 송신기")
    parser.add_argument("scenario", nargs="?", choices=[*SCENARIOS, "demo"])
    parser.add_argument("--list", action="store_true", help="시나리오 목록 보기")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="서버 주소")
    parser.add_argument("--fast", action="store_true", help="기다리지 않고 바로 보내기")
    parser.add_argument("--at", help="사건 시각을 오늘 이 시각으로 옮기기 (예: 10:10)")
    args = parser.parse_args(argv)

    if args.list or args.scenario is None:
        for scenario in SCENARIOS.values():
            print(f"{scenario.name:15s} {scenario.description} (예상: {scenario.expected})")
        print(f"{'demo':15s} 모든 시나리오를 차례로 실행")
        return

    start = datetime.now().astimezone()
    if args.at:
        hour, minute = map(int, args.at.split(":"))
        start = start.replace(hour=hour, minute=minute, second=0, microsecond=0)

    scenarios = demo_sequence() if args.scenario == "demo" else [SCENARIOS[args.scenario]]
    if args.fast and any(s.name == "device-down" for s in scenarios):
        print("주의: 끊김 판단은 서버의 실제 시각 기준이라 --fast에서는 device-down이 재현되지 않습니다")

    with httpx.Client(base_url=args.server, timeout=5) as client:
        simulator = Simulator(http_sender(client), Clock(start, realtime=not args.fast))
        try:
            for scenario in scenarios:
                simulator.run(scenario)
        except RejectedError as error:
            print(f"서버가 메시지를 거부했습니다. 설정이나 규격을 확인하세요: {error}")
            raise SystemExit(1) from error


if __name__ == "__main__":
    main()
