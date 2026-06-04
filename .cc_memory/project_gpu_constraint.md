---
name: project-gpu-constraint
description: GPU is fully occupied by nz-legal-rag/buildingconsents; ask before freeing memory for Mira
metadata:
  type: project
---

RTX 4060 mobile (8GB VRAM) is currently full, occupied by a systemd --user llama.cpp server
running for the nz-legal-rag and buildingconsents projects:
- qwen3-8B model
- embedder
- reranker

**Why:** Those services are live/active; displacing them would break other work.

**How to apply:** Before starting or testing any LLM for Mira that needs GPU memory
(Ollama pull, llama.cpp instance, etc.), ask the user first. Do not `systemctl --user stop`
anything or assume GPU memory is available.

The existing llama.cpp server is OpenAI-compatible - Mira's config can point to it
for Phase 1 development if the user is OK sharing the endpoint.

wstream has two builds:
- GPU build: ~/proj/priv/wstream/build/bin/wstream  (DO NOT use - takes GPU memory)
- CPU build: ~/proj/priv/wstream/build-cpu/bin/wstream  (use this for Mira)
