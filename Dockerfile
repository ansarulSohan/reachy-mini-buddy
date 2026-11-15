# Reachy Mini web dashboards, ready for Coolify (or any Docker host).
# Runs a simulated Reachy Mini inside the container, unless REACHY_HOST points to a real robot.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MUJOCO_GL=egl \
    PYOPENGL_PLATFORM=egl \
    HF_HOME=/opt/hf

# ffmpeg: audio; EGL/Mesa: off-screen 3D rendering without a GPU;
# GStreamer typelibs: the Reachy Mini daemon loads them even with media off;
# cairo/gobject-introspection: needed to build PyGObject (a reachy-mini dependency on Linux).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl libegl1 libgl1 libgles2 libgl1-mesa-dri libglib2.0-0 \
        gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 \
        gstreamer1.0-plugins-base \
        build-essential pkg-config libcairo2-dev libgirepository1.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

# Download the speech-to-text model at build time, so the first start is fast.
ARG STT_MODEL=small
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${STT_MODEL}', device='cpu', compute_type='int8')"

COPY reachy_buddy ./reachy_buddy
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD curl -fs http://127.0.0.1:${PORT:-8080}/healthz || exit 1
ENTRYPOINT ["/entrypoint.sh"]
