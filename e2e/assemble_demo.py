"""Join recruiter and candidate viewport recordings into a portable MP4."""

import json
import subprocess
import sys
from pathlib import Path


def main():
    artifacts = Path(sys.argv[1] if len(sys.argv) > 1 else ".state/evidence/deployed")
    timeline = {
        item["name"]: item["time"]
        for item in json.loads((artifacts / "video-timeline.json").read_text())
    }
    recruiter = max((artifacts / "recruiter-video").glob("*.webm"), key=lambda p: p.stat().st_mtime)
    candidate = max((artifacts / "candidate-video").glob("*.webm"), key=lambda p: p.stat().st_mtime)
    start, end = timeline["candidate_start"], timeline["candidate_end"]
    filters = (
        f"[0:v]trim=end={start},setpts=PTS-STARTPTS,fps=25,setsar=1[a];"
        "[1:v]setpts=PTS-STARTPTS,scale=1440:960:force_original_aspect_ratio=decrease,"
        "pad=1440:960:(ow-iw)/2:(oh-ih)/2:color=white,fps=25,setsar=1[b];"
        f"[0:v]trim=start={end},setpts=PTS-STARTPTS,fps=25,setsar=1[c];"
        "[a][b][c]concat=n=3:v=1:a=0[out]"
    )
    destination = artifacts / "jobhunter-agent-demo.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(recruiter),
            "-i",
            str(candidate),
            "-filter_complex",
            filters,
            "-map",
            "[out]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            str(destination),
        ],
        check=True,
    )
    print(destination)


if __name__ == "__main__":
    main()
