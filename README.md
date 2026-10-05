# ClipForge AI — one service

This is the closest complete test deployment package: one Render web service serves the mobile UI and the FastAPI backend.

## What it does

Paste a video URL -> backend retrieves the authorized source -> transcribes audio -> samples representative frames -> AI ranks candidate moments -> generates a caption -> FFmpeg renders a 9:16 MP4 -> browser gets a download link.

The UI is locked to vertical scrolling and does not allow pinch zoom.

## Important

Use this only for videos you own or have permission to process. The included media adapter uses `yt-dlp` for the URL supplied by the user. It is not intended to bypass private videos, authentication controls, DRM, or other access restrictions.

## Deploy to Render

Render supports Docker web services and gives each service an `onrender.com` URL. It can deploy from a Git repository and supports environment variables/secrets.

1. Put this project into a GitHub repository.
2. Open Render and choose **New -> Web Service**.
3. Connect the repository.
4. Select **Docker**.
5. Keep the Dockerfile path as `/Dockerfile`.
6. Add the environment variable:
   - `OPENAI_API_KEY` = your OpenAI API key
7. Deploy.
8. Open the generated `https://YOUR-SERVICE.onrender.com` URL on your iPhone.
9. Paste an authorized video URL and press Analyze.

Render's free web services can spin down after inactivity, so the first request can be slower.

## OpenAI model

`OPENAI_MODEL` defaults to `gpt-6-astra`. You can change it in Render environment variables if your API account has access to another model.

The backend keeps the OpenAI key server-side. Never paste the key into the website JavaScript.

## Notes about long videos

The backend splits audio into 6-minute chunks for transcription and samples a limited number of representative frames to control processing cost. The AI then uses the transcript + frame evidence to rank candidate moments.

For a production version, add:
- background job queue
- persistent object storage
- job progress via WebSockets/SSE
- automatic cleanup
- authenticated users
- stronger SSRF/source validation
- word-level animated captions
- face/speaker-aware 9:16 reframing
- scene-change detection
- retry handling
- usage limits

## Current output

The renderer makes a 1080x1920 H.264/AAC MP4 with a simple burned-in caption.

This is deliberately a test build, not a finished commercial platform.
