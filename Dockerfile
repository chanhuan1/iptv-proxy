FROM alpine:3.21

RUN apk add --no-cache python3 py3-pip ffmpeg x264-libs && \
    pip install flask --break-system-packages

WORKDIR /app
COPY app.py .
# channels.json and iptv_channels.m3u must be mounted at runtime

EXPOSE 18888

CMD ["python3", "app.py"]
