Source: https://foundry.mypaytm.com/onboarding/registering-compute

# Registering a machine you already own 
Personal laptops are being withdrawn
This page covers registering a box you own, and for a **server** that is still the right path. For a **personal laptop it is not** : those runs fail often for reasons unrelated to your code, including a full disk, the machine sleeping or shutting down, and network drops, and macOS offers no good process isolation for a box shared with a team. Foundry is moving these sessions onto team cloud machines. Use your team's compute, and talk to your DevOps if your team has not got one.
If you do register a laptop, scope it to **your own user rung** rather than to the team, so that nobody else's session lands on a machine you are about to close.
Foundry can run sessions on a box you provision and manage yourself, instead of one it creates for you. That is what `registered-host` is for: the machine runs `arkd` itself and dials out to the control plane, so Foundry needs no inbound route, no AWS credentials in your account, and never touches the box's lifecycle.
## Check this first: where the box will land 
Your role does not decide whether you can register a machine. It decides which rung the machine lands on, and the control plane reads that from the credential you enrol with:
Your credential| Where the box lands| Who can use it  
---|---|---  
tenant-admin| the tenant rung| everyone in the tenant  
team-admin| your team's rung| your team's subtree  
member| your own user rung| you, plus your admins  
A member enrolling their own laptop is a supported path, not a workaround: the gate softens from `compute.admin` to `compute.create`, which `member` carries. You then administer that box yourself, including its capacity.
A `viewer` grant is not enough for any of them, and neither is an ownerless service key: a credential with no human behind it has no user rung to land on, so it takes the admin gate and is refused.
If you want a shared box and are not an admin, ask your team admin to register it for you, or to grant you team-admin. That is the narrower ask and the one that usually gets answered: a team-admin can register a box into their own team and can grant within it, so nothing has to go up to a tenant admin. The [tenant admin guide](/onboarding/admin) covers grants. For the handoff path where an admin registers a box on someone else's behalf, see Registering on someone else's behalf below.
The fastest route through all of this is the **Enroll a machine** card on the Onboarding page of your control plane: it mints the right key for the choice you make, then hands you either the script below or the same steps one at a time.
## One-command enrollment 
On a Linux or macOS box you can skip the manual steps below and run `ark-enroll-host.sh`, which does the whole enrollment in one pass: it installs the `ark` CLI and links it into a system bin dir, installs the session runtime prerequisites (`bun` and Claude Code) when they are missing, logs in, enrolls `arkd` on port 19300, registers it as a boot service (a systemd unit on Linux, a LaunchAgent on macOS), sets admission capacity from the detected hardware, and verifies health. Re-running it is safe: it stops the box's own `arkd`, re-mints the host token, and refreshes everything in place.
[Download `ark-enroll-host.sh`](/ark-enroll-host.sh), or pull it straight onto the box. This doc site publishes it; the control plane does not serve it, so the URL is the doc site's, not your control plane's. The canonical copy lives in the repo at `scripts/ark-enroll-host.sh`, and `make audit` fails the build if the published copy drifts from it.
bash
    
    curl -fsSL https://foundry.mypaytm.com/ark-enroll-host.sh -o ark-enroll-host.sh
    chmod +x ark-enroll-host.sh
The onboarding page prints this same pair with your key filled in. If your deployment publishes the script somewhere else, set `ARK_ENROLL_SCRIPT_URL` on the control plane and the page will point at that instead.
Run it on the target machine, as root or as the user that should own the enrollment. `API_KEY` and `MACHINE_NAME` are required, and `ARK_SERVER` should name your control plane (it defaults to the Paytm production one):
bash
    
    API_KEY=ark_t-..._xxx MACHINE_NAME=my-box ./ark-enroll-host.sh
Do not prefix `ARK_SERVER`. The CLI reads it from the environment ahead of the login context this script just wrote, pairs it with `ARK_TOKEN` (which nothing sets here), and every call after the login then runs unauthenticated, failing with an auth error that says nothing about the cause. The script resolves the control plane on its own.
Capacity is detected from the hardware unless you override it with `MAX_SESSIONS` (default cores / 2), `RAM` (total memory minus 1-4Gi headroom), or `CPU` (cores * 100). To upgrade or repair a box that is already enrolled, keeping its token and capacity and with no API key, pass `--install-only`:
bash
    
    MACHINE_NAME=my-box ./ark-enroll-host.sh --install-only
Before you run it, the box needs three things:
  - A network path to the control plane over 443. Step 1 of the script checks this and names the fix if it fails: a route to `10.72.216.0/23` and the control-plane ingress security group.
  - An Ark API key (`ark_t-..._...`). Its role decides where the box lands, per the table above. A worker token is not an API key and will not work here: it clears only the `worker/*` methods.
  - Root or sudo for the boot service (recommended for shared boxes, where the enrollment then lives under `/root/.ark`), or a regular user with passwordless sudo. Without sudo the daemon runs detached and does not survive a reboot.

Port 19300 is fixed because the control plane dials it. A re-run stops the box's own `arkd`; any other process on that port is fatal, and the script prints the exact `ss`/`kill` commands to clear it. Verified end to end on Ubuntu 24.04 and Debian 12: fresh enroll, idempotent re-run, and reboot survival.
The manual sequence below is what the script automates. Use it when you want to run the steps yourself, or for the admin-mints-token handoff to a non-admin.
## No-sudo / Jamf-managed Mac 
On a Jamf-managed Mac the login user is usually not in the sudoers file, so any step that writes a system directory fails. `sudo ln -sfn ~/.ark/bin/ark /usr/local/bin/ark` returns a sudoers error, later commands cannot find `ark`, and the shell reports exit code 127 with `ark: not found`. IT also owns the package managers, so you cannot `brew install bun` yourself. The steps below reach the same enrolled host without touching a system directory or asking for sudo. `ark-enroll-host.sh` already does all of this on its own when it finds no passwordless sudo; this section is the manual equivalent.
Put `ark` on your PATH through a directory you own instead of `/usr/local/bin`. `~/.local/bin` is the convention and is already on PATH in most shells:
bash
    
    mkdir -p ~/.local/bin
    ln -sfn ~/.ark/bin/ark ~/.local/bin/ark
If `~/.local/bin` is not on your PATH, add it once so future logins resolve `ark`. The download step already writes `ark` to `~/.ark/bin`, so pointing PATH at that directory works without the symlink at all:
bash
    
    echo 'export PATH="$HOME/.ark/bin:$PATH"' >> ~/.zshrc
Open a new shell or `source ~/.zshrc`, then confirm the binary resolves:
bash
    
    ark --version
Install `bun` without sudo. The bun.sh installer writes to `~/.bun` under your home and needs no root:
bash
    
    curl -fsSL https://bun.sh/install | bash
That puts `bun` at `~/.bun/bin/bun` and appends `~/.bun/bin` to your shell profile. Open a new shell and run `bun --version` to confirm. If your Mac blocks the installer, ask IT to install `bun` through Jamf Self Service, then re-check with `bun --version`. Do not symlink `bun` into `/usr/local/bin`: that write needs sudo and is not required, because `arkd` and the sessions it spawns run as you and inherit your PATH.
Enrol with `--launchd` as your own login user, not through sudo. The LaunchAgent runs as you and inherits the PATH above, so it finds `ark`, `bun`, and `claude` under your home without a system-directory install:
bash
    
    ark host enroll flights-box --isolation direct --launchd
The rest of this page applies unchanged from here. Verify with `ark --version` and `ark host status flights-box`, then dispatch a small session pinned to the box.
## Enrolling the machine 
`ark compute create` refuses `registered-host` on purpose, because enrollment has to mint a worker token that the machine will present when it dials in. Run this on the machine itself, signed in with the credential whose rung you want the box on:
bash
    
    ark host enroll flights-box --isolation direct --detach
That mints a token, writes it to the host's token file with mode 600, and starts `arkd` dialing the control plane. `--isolation` takes `direct` or `docker`.
`--detach` backgrounds the daemon, which does not survive a reboot. On a machine you want back after a restart, install a service instead: `--launchd` on macOS writes a LaunchAgent that starts at login, and `--systemd` on Linux (as root) writes a unit that starts at boot. Use one of those for any shared or long-lived box.
Re-running enrollment against the same name rotates the token. The previous credential stops working immediately, so do not re-enroll a box that is already running sessions unless you mean to cut it off.
## Registering on someone else's behalf 
`ark host enroll` calls the enrolment RPC every time it runs, and it enrols onto the caller's OWN rung. So handing someone the enroll command does not put the box where you meant it to go, and for a shared box it puts it somewhere they cannot reach.
The path that does work splits the credential from the command. An admin, team or tenant, mints the token, then the machine's owner starts the daemon with it. The owner needs no admin credential, because `ark host start` never calls the gated endpoint.
The admin mints it, either through the `compute` MCP tool with `op='register_host'`, or by running `ark host enroll <name>` and reading the token out of the host file. Send the token over a private channel, never a shared one. It is the enrollment secret and it grants fleet membership.
The machine's owner then places the token and starts the daemon, with no admin credential involved:
bash
    
    mkdir -p ~/.ark/host/registered-host/flights-box
    printf '%s' '<token>' > ~/.ark/host/registered-host/flights-box/token
    chmod 600 ~/.ark/host/registered-host/flights-box/token
    
    ark host start flights-box --detach
`ark host start` reads the saved token and reconnects without contacting the admin-gated endpoint, which is why this path works for a member.
## Verify 
From any machine with your Foundry credential:
bash
    
    ark compute list
    ark compute show flights-box
`ark compute list` reports each target's `architecture` as `arm64` or `x86_64` once the box has checked in, so it is also how you confirm an arm64 machine enrolled as arm64. On the machine itself, `ark host status flights-box` reports whether `arkd` is running.
Read `host status` as a local check and nothing more. Its `Enrolled: yes` reflects a token file on disk rather than the control plane, so a box whose compute row has been deleted, or which was enrolled with a credential nobody holds any more, keeps reporting itself enrolled and healthy while the fleet cannot see it at all. `ark compute list` from another machine is the proof that matters.
Finish by dispatching a small session pinned to the box with `--compute flights-box`, which is the only check that proves dispatch works end to end and not just registration.
## Next steps 
  - [Compute and isolation](/guide/compute): the kinds, the isolation pairs, and how placement picks a box.
  - [Tenant admin guide](/onboarding/admin): grants, teams, and who can register what.
  - [Team infrastructure](/onboarding/team-infra): peering and the cross-account role.
