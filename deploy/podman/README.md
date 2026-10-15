# Podman Quadlet deploy (different machine, not testable here)
1. Copy repo to target, create `%h/vataic/.env` from `.env.example`.
2. Build: `podman build -t vataic-web -f Dockerfile.web .` (same for worker).
3. Install units: `cp deploy/podman/* ~/.config/containers/systemd/ && systemctl --user daemon-reload`.
4. Start: `systemctl --user start vataic-web vataic-worker`.
5. Verify: `curl localhost:5000/healthz` → `{"ok": true}`; submit scenario at `/jobs/new`.
App DB stays SQLite on the volume; the worker polls it in-process (no Temporal).
