#!/bin/sh
# Trainable RunPod worker entrypoint.
#
# Serverless workers mount the network volume at /runpod-volume (fixed);
# pods mount it wherever volumeMountPath says (we use /data). Symlink
# /data -> /runpod-volume when needed so the SDK preamble's /data paths
# work identically in both.
set -e

if [ ! -e /data ] && [ -d /runpod-volume ]; then
    ln -s /runpod-volume /data
fi

# An explicit docker start cmd (pods pass one) wins over role dispatch.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

case "${TRAINABLE_ROLE:-runner}" in
    kernel)
        exec python -u -m worker.kernel_gateway
        ;;
    serving)
        exec python -u /opt/trainable/worker/serving_launcher.py
        ;;
    *)
        exec python -u /opt/trainable/worker/runner_handler.py
        ;;
esac
