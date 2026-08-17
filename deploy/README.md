# Deploying the web UI on an Ark compute (internal-only)

This guide hosts the onboarding bot's web UI (`src/web.py`, a FastAPI app) on an
Ark registered-host (or any small internal box) so the team can open a URL and
ask questions. It stays **internal-only with no open bind**: the server listens
on loopback (`127.0.0.1`) and teammates reach it over an SSH tunnel.

## Why loopback + SSH tunnel

"No open bind" means the app must never listen on a public interface
(`0.0.0.0`). Binding to `127.0.0.1` guarantees only processes on the host — and
anyone who has SSH access to it — can reach the app. This is the safest default
and needs no firewall/security-group changes.

## Prerequisites on the host

- The box is an Ark registered-host (already enrolled) or any internal machine
  you can SSH into. It does **not** need to be enrolled for the web UI to run.
- Python 3.11+ and `git`.
- The **Cursor agent CLI** on `PATH` (`agent status` should work) — the answer
  layer shells out to it. Install with `curl https://cursor.com/install -fsS | bash`.
- A **`CURSOR_API_KEY`** (from https://cursor.com/dashboard/api).

## 1. Clone and configure

```bash
sudo git clone https://github.com/Paytm-Labs-Inc/ark-onboarding-bot.git /opt/ark-onboarding-bot
cd /opt/ark-onboarding-bot
printf 'CURSOR_API_KEY=crsr_your_key_here\n' | sudo tee .env >/dev/null
sudo chmod 600 .env
```

## 2. Run it

### Quick start (foreground)

```bash
./deploy/run_web.sh
# → serving on http://127.0.0.1:8765 (internal-only)
```

The script creates the `.venv`, installs `requirements.txt` on first run, then
starts uvicorn bound to loopback. Override the port with `WEB_PORT=9000 ./deploy/run_web.sh`.

### As a service (recommended for "always on")

```bash
sudo useradd -r -s /usr/sbin/nologin arkbot        # service account
sudo chown -R arkbot:arkbot /opt/ark-onboarding-bot
sudo cp deploy/ark-onboarding-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ark-onboarding-bot
systemctl status ark-onboarding-bot
journalctl -u ark-onboarding-bot -f               # live logs
```

## 3. Let the team reach it (SSH tunnel)

Each teammate runs this from their laptop, then opens `http://localhost:8765`:

```bash
ssh -N -L 8765:127.0.0.1:8765 <you>@<ark-host>
```

`-N` opens the tunnel without a remote shell; `-L 8765:127.0.0.1:8765` forwards
the local port 8765 to the host's loopback port 8765. Nothing is exposed
publicly — the app is only reachable through the authenticated SSH connection.

## 4. Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/   # expect 200
curl -s -X POST http://127.0.0.1:8765/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"how do I enroll a host?"}'
```

## Updating a deployment

```bash
cd /opt/ark-onboarding-bot
sudo -u arkbot git pull
sudo -u arkbot .venv/bin/pip install -r requirements.txt   # if deps changed
sudo systemctl restart ark-onboarding-bot
```

## Notes

- **Sessions are in-memory.** Multi-turn chat history lives in the process, so a
  restart clears every conversation. That is fine for a v1 internal demo.
- **Feedback** thumbs-up/down are appended to `eval/feedback.jsonl` on the host.
- **Alternative access (advanced).** If SSH tunnels are impractical you may bind
  to a private/VPN-only interface (`WEB_HOST=<private-ip>`) — but only behind a
  security group / firewall that blocks everything outside the corp network.
  Never bind `0.0.0.0` on a publicly reachable host.
