import os
import json
import uuid
import subprocess
import base64
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, HttpUrl
from openai import OpenAI


ROOT = Path(os.environ.get("CLIPFORGE_WORKDIR", "/tmp/clipforge"))
ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ClipForge AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
TRANSCRIBE = os.environ.get("TRANSCRIBE_MODEL", "gpt-4o-transcribe")
MAX_MB = int(os.environ.get("MAX_VIDEO_MB", "1200"))


class Analyze(BaseModel):
    video_url: HttpUrl
    clip_length: int = 8


class Render(BaseModel):
    job_id: str
    candidate: dict


def cmd(args):
    p = subprocess.run(
        args,
        capture_output=True,
        text=True
    )

    if p.returncode != 0:
        raise RuntimeError(p.stderr[-4000:])

    return p.stdout


def fetch_video(url, out):
    """
    Download a single video URL.

    yt-dlp is used only for videos the user is authorized to process.
    No credentials are hard-coded.
    """

    cmd([
        "yt-dlp",

        "--no-playlist",
        "--no-warnings",

        # Enable yt-dlp's EJS JavaScript challenge support.
        "--remote-components",
        "ejs:github",
        
        "-f",
        "bv*+ba/b",

        "--merge-output-format",
        "mp4",

        "-o",
        str(out),

        url
    ])

    if not out.exists():
        raise RuntimeError(
            "The media adapter did not produce a video file."
        )

    if out.stat().st_size > MAX_MB * 1024 * 1024:
        raise HTTPException(
            413,
            "Video is too large."
        )


def info(video):
    return json.loads(
        cmd([
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(video)
        ])
    )


def split_audio(video, folder):
    folder.mkdir(exist_ok=True)

    # Split audio into 6-minute WAV chunks.
    cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "segment",
        "-segment_time",
        "360",
        "-c:a",
        "pcm_s16le",
        str(folder / "chunk_%03d.wav")
    ])

    return sorted(folder.glob("chunk_*.wav"))


def transcribe_chunks(chunks):
    allseg = []
    offset = 0

    for p in chunks:

        with open(p, "rb") as f:
            r = client.audio.transcriptions.create(
                model=TRANSCRIBE,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        for s in r.segments:
            allseg.append({
                "start": float(s.start) + offset,
                "end": float(s.end) + offset,
                "text": s.text
            })

        # Get the exact duration of this chunk.
        try:
            duration_data = json.loads(
                cmd([
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(p)
                ])
            )

            offset += float(
                duration_data["format"]["duration"]
            )

        except Exception:
            offset += 360

    return allseg


def frames(video, duration, folder):
    folder.mkdir(exist_ok=True)

    # More frames for longer videos, capped at 28.
    n = min(
        28,
        max(
            10,
            int(duration / 15)
        )
    )

    out = []

    for i in range(n):

        t = (duration - 0.5) * i / max(1, n - 1)

        p = folder / f"{i:03d}.jpg"

        cmd([
            "ffmpeg",
            "-y",
            "-ss",
            str(t),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=768:-2",
            str(p)
        ])

        out.append((t, p))

    return out


def img(p):
    return (
        "data:image/jpeg;base64,"
        + base64.b64encode(
            p.read_bytes()
        ).decode()
    )


def rank(video_duration, segments, frs, length):

    # Candidate starts from speech.
    starts = set()

    for s in segments:

        starts.add(
            max(
                0,
                min(
                    video_duration - length,
                    float(s["start"]) - 1
                )
            )
        )

        if len(starts) >= 45:
            break

    # Also sample the video regularly.
    for i in range(
        min(
            12,
            max(
                1,
                int(
                    video_duration /
                    max(1, length * 2)
                )
            )
        )
    ):

        starts.add(
            max(
                0,
                min(
                    video_duration - length,
                    i * max(
                        1,
                        video_duration / 12
                    )
                )
            )
        )

    starts = sorted(starts)[:50]

    transcript = json.dumps(
        segments[:800],
        ensure_ascii=False
    )

    prompt = f"""
You are ClipForge, a professional short-form video editor.

The user says they are authorized to edit this video.

Find the best short-form moments.

Evaluate each candidate using:

1. Immediate hook
2. Emotional intensity or reaction
3. Surprise or humor
4. Self-contained context
5. Pacing
6. Replay value
7. Caption potential

Return ONLY valid JSON.

Required format:

{{
  "candidates": [
    {{
      "start": 0.0,
      "end": 8.0,
      "score": 96,
      "reason": "one concise reason",
      "caption": "short punchy caption"
    }}
  ]
}}

Video duration: {video_duration:.1f} seconds

Clip target length: {length} seconds

Candidate starts:
{starts}

Timestamped transcript:
{transcript}
"""

    content = [
        {
            "type": "input_text",
            "text": prompt
        }
    ]

    for t, p in frs:

        content.append({
            "type": "input_text",
            "text": (
                f"Representative frame at "
                f"{t:.1f} seconds."
            )
        })

        content.append({
            "type": "input_image",
            "image_url": img(p)
        })

    r = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "user",
                "content": content
            }
        ]
    )

    raw = r.output_text

    a = raw.find("{")
    b = raw.rfind("}")

    if a < 0:
        raise RuntimeError(
            "AI returned no JSON candidates."
        )

    data = json.loads(
        raw[a:b + 1]
    )

    result = []

    for c in data.get("candidates", []):

        st = max(
            0,
            min(
                video_duration - length,
                float(c["start"])
            )
        )

        en = min(
            video_duration,
            st + length
        )

        result.append({
            "start": st,
            "end": en,
            "score": int(c["score"]),
            "reason": str(c["reason"]),
            "caption": str(c["caption"])
        })

    return sorted(
        result,
        key=lambda x: x["score"],
        reverse=True
    )[:5]


def ass(text, path, duration):

    safe = (
        text
        .replace("\\", "")
        .replace("{", "")
        .replace("}", "")
    )

    path.write_text(
        f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV
Style: Default,Arial,76,&H00FFFFFF,&H00000000,1,5,2,2,70,70,260

[Events]
Format: Layer,Start,End,Style,Text
Dialogue: 0,0:00:00.00,0:00:{duration:05.2f},Default,{safe}
""",
        encoding="utf-8"
    )


def render(job, c):

    src = job / "source.mp4"
    out = job / "clipforge_short.mp4"
    a = job / "caption.ass"

    dur = max(
        0.5,
        float(c["end"]) - float(c["start"])
    )

    ass(
        c.get("caption", ""),
        a,
        dur
    )

    vf = (
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"ass={a}"
    )

    cmd([
        "ffmpeg",
        "-y",

        "-ss",
        str(c["start"]),

        "-i",
        str(src),

        "-t",
        str(dur),

        "-vf",
        vf,

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "20",

        "-c:a",
        "aac",

        "-movflags",
        "+faststart",

        str(out)
    ])

    return out


@app.get("/")
def home():

    return HTMLResponse(
        (
            Path(__file__).parent /
            "index.html"
        ).read_text(
            encoding="utf-8"
        )
    )


@app.get("/health")
def health():

    return {
        "ok": True
    }


@app.post("/api/analyze")
def analyze(req: Analyze):

    if req.clip_length < 3 or req.clip_length > 30:

        raise HTTPException(
            400,
            "Clip length must be 3–30 seconds."
        )

    job = ROOT / uuid.uuid4().hex
    job.mkdir()

    source = job / "source.mp4"

    try:

        # 1. Download the video.
        fetch_video(
            str(req.video_url),
            source
        )

        # 2. Get video information.
        meta = info(source)

        duration = float(
            meta["format"]["duration"]
        )

        if duration < req.clip_length:

            raise HTTPException(
                400,
                "Video is shorter than the requested clip."
            )

        # 3. Transcribe.
        seg = transcribe_chunks(
            split_audio(
                source,
                job / "audio"
            )
        )

        # 4. Extract representative frames.
        fr = frames(
            source,
            duration,
            job / "frames"
        )

        # 5. Ask Luna to find the best moments.
        candidates = rank(
            duration,
            seg,
            fr,
            req.clip_length
        )

        # Save results.
        (
            job / "state.json"
        ).write_text(
            json.dumps({
                "candidates": candidates,
                "duration": duration
            }),
            encoding="utf-8"
        )

        return {
            "job_id": job.name,
            "duration": duration,
            "candidates": candidates
        }

    except HTTPException:
        raise

    except Exception as e:

        raise HTTPException(
            500,
            str(e)
        )


@app.post("/api/render")
def render_api(req: Render):

    if not re.fullmatch(
        r"[a-f0-9]{32}",
        req.job_id
    ):

        raise HTTPException(
            400,
            "Invalid job id."
        )

    job = ROOT / req.job_id
    state = job / "state.json"

    if not state.exists():

        raise HTTPException(
            404,
            "Job expired."
        )

    data = json.loads(
        state.read_text()
    )

    # Only render a candidate that was
    # actually returned by this job.
    allowed = False

    for c in data["candidates"]:

        if (
            abs(
                float(c["start"]) -
                float(req.candidate["start"])
            ) < 0.01
            and
            abs(
                float(c["end"]) -
                float(req.candidate["end"])
            ) < 0.01
        ):

            allowed = True
            break

    if not allowed:

        raise HTTPException(
            400,
            "Candidate is not part of this job."
        )

    render(
        job,
        req.candidate
    )

    return {
        "download_url":
            f"/download/{req.job_id}"
    }


@app.get("/download/{job_id}")
def download(job_id: str):

    if not re.fullmatch(
        r"[a-f0-9]{32}",
        job_id
    ):

        raise HTTPException(
            400,
            "Invalid job id."
        )

    p = (
        ROOT /
        job_id /
        "clipforge_short.mp4"
    )

    if not p.exists():

        raise HTTPException(
            404,
            "Clip not found."
        )

    return FileResponse(
        p,
        media_type="video/mp4",
        filename="clipforge_short.mp4"
    )


if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "10000"
            )
        )
    )
