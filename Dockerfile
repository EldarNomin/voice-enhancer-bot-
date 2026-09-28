FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
COPY scripts/configure_max.py ./scripts/configure_max.py
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
CMD ["python", "-m", "voice_enhancer"]

FROM base AS deepfilter
COPY scripts/install_deepfilter.py /tmp/install_deepfilter.py
RUN python /tmp/install_deepfilter.py /usr/local/bin/deep-filter \
    && rm /tmp/install_deepfilter.py

FROM base AS gtcrn
COPY docs/licenses/GTCRN-MIT.txt /opt/models/GTCRN-MIT.txt
RUN pip install --no-cache-dir ".[gtcrn]"
COPY scripts/install_gtcrn.py /tmp/install_gtcrn.py
RUN python /tmp/install_gtcrn.py /opt/models/gtcrn_simple.onnx \
    && rm /tmp/install_gtcrn.py

# The default build keeps the existing FFmpeg-only behavior.
FROM base AS standard
