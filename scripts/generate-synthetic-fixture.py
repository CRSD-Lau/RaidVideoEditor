"""Generate a tiny multi-track raid-like fixture with deterministic combat metadata."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def build_command(destination: Path) -> list[str]:
    font = "fontfile='C\\:/Windows/Fonts/segoeui.ttf'"
    video = (
        "testsrc2=size=640x360:rate=30:duration=30,"
        "drawbox=x=0:y=0:w=iw:h=60:color=black@0.7:t=fill,"
        f"drawtext={font}:text='DOWNTIME':fontcolor=white:fontsize=28:x=20:y=16,"
        f"drawtext={font}:text='TRASH PULL':fontcolor=yellow:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2:"
        "enable='between(t,3,8)',"
        f"drawtext={font}:text='BOSS WIPE':fontcolor=red:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2:"
        "enable='between(t,10,15)',"
        f"drawtext={font}:text='BOSS KILL':fontcolor=lime:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2:"
        "enable='between(t,21,29)'"
    )
    game = (
        "aevalsrc=0.10*sin(2*PI*220*t)*"
        "(between(t\\,3\\,8)+between(t\\,10\\,15)+between(t\\,21\\,29)):"
        "s=48000:d=30"
    )
    discord = (
        "aevalsrc=0.08*sin(2*PI*660*t)*"
        "(between(t\\,4\\,5)+between(t\\,11\\,12)+between(t\\,14\\,15)+"
        "between(t\\,22\\,24)+between(t\\,27\\,28)):s=48000:d=30"
    )
    microphone = (
        "aevalsrc=0.06*(sin(2*PI*115*t)+0.55*sin(2*PI*230*t)+"
        "0.25*sin(2*PI*345*t))*between(mod(t\\,4)\\,0.2\\,1.8):s=48000:d=30"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        video,
        "-f",
        "lavfi",
        "-i",
        game,
        "-f",
        "lavfi",
        "-i",
        discord,
        "-f",
        "lavfi",
        "-i",
        microphone,
        "-filter_complex",
        "[1:a][2:a][3:a]amix=inputs=3:duration=longest:normalize=0[mix]",
        "-map",
        "0:v:0",
        "-map",
        "[mix]",
        "-map",
        "1:a:0",
        "-map",
        "2:a:0",
        "-map",
        "3:a:0",
        "-metadata:s:a:0",
        "title=Full Stream Mix",
        "-metadata:s:a:1",
        "title=Game Audio",
        "-metadata:s:a:2",
        "title=Discord",
        "-metadata:s:a:3",
        "title=Microphone",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-y",
        str(destination),
    ]


def write_combat_log(destination: Path) -> None:
    content = """7/26 20:00:03.000  PLAYER_REGEN_DISABLED,Player-1,"Synthetic Raider"
7/26 20:00:08.000  PLAYER_REGEN_ENABLED,Player-1,"Synthetic Raider"
7/26 20:00:10.000  ENCOUNTER_START,9001,"Synthetic Wipe Boss",14,20
7/26 20:00:15.000  ENCOUNTER_END,9001,"Synthetic Wipe Boss",14,20,0
7/26 20:00:21.000  ENCOUNTER_START,9001,"Synthetic Wipe Boss",14,20
7/26 20:00:28.000  UNIT_DIED,0000000000000000,"",0x80000000,0x80000000,Creature-0-0-0-0-9001-0000000000,"Synthetic Wipe Boss"
7/26 20:00:29.000  ENCOUNTER_END,9001,"Synthetic Wipe Boss",14,20,1
"""
    destination.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "samples" / "generated",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recording = output_dir / "synthetic-raid.mkv"
    combat_log = output_dir / "WoWCombatLog.txt"
    if args.force or not recording.is_file():
        try:
            subprocess.run(build_command(recording), check=True)
        except FileNotFoundError as exc:
            raise SystemExit("ffmpeg is required to generate the fixture") from exc
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"ffmpeg fixture generation failed ({exc.returncode})") from exc
    write_combat_log(combat_log)
    print(recording)
    print(combat_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
