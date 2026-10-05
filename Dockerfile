FROM python:3.12-slim

# System packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno
# Deno is the recommended JavaScript runtime for yt-dlp YouTube support.
RUN curl -fsSL https://deno.land/install.sh | sh

ENV PATH="/root/.deno/bin:${PATH}"

# Verify Deno installation
RUN deno --version

# App directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Install/update yt-dlp and its EJS challenge solver
RUN pip install --no-cache-dir -U "yt-dlp[default]" "yt-dlp-ejs"

# Verify yt-dlp installation
RUN yt-dlp --version

# Copy the ClipForge application
COPY . .

# Render uses port 10000
EXPOSE 10000

# Start FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
