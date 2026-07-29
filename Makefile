# Trainable — repo-level build helpers.

# RunPod worker image (code runner + kernel gateway + serving launcher).
# Publishing this image is a prerequisite for COMPUTE_PROVIDER=runpod;
# point RUNPOD_WORKER_IMAGE at wherever you push it.
RUNPOD_IMAGE ?= ghcr.io/lucastononro/trainable-runpod-worker:latest

.PHONY: runpod-image
runpod-image:
	docker buildx build --platform linux/amd64 \
		-f docker/runpod-worker/Dockerfile \
		-t $(RUNPOD_IMAGE) \
		--push .
