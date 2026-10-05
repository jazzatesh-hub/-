FROM python:3.12-slim

RUN 

# Install Deno, the JS runtime recommended by yt-dlp
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -U "yt-dlp[default]"

COPY . .

EXPOSE 10000

CMD ["uvicorn","main:app","--host","0.0.0.0","--port","10000"]
