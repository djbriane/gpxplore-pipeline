# Valhalla on Unraid — setup & operations runbook

Self-host the gpxplore Valhalla routing service on the home-lab NUC (Unraid, 32 GB). The NUC
**builds** the us-west tile set (offline, manual cadence) **and serves** it 24/7; Cloudflare
Tunnel is the private-origin front door at `valhalla.gpxplore.net`. **$0/mo.** Decision context: wayfinder ticket
`djbriane/gpxplore-pipeline#8` (see the map, issue #4).

---

## 1. Architecture at a glance

```
                build (occasional, ~20 min)        serve (always-on)
  Geofabrik  ─▶  ghcr.io/valhalla/valhalla  ─▶  /mnt/user/appdata/valhalla  ◀─  djbriane/valhalla
  us-west.pbf     (official image, raw bins)      valhalla.json + tiles/         (your thin image)
   browser ─▶ Cloudflare Worker (#10) ─▶ Cloudflare Tunnel ─┬─▶ Valhalla :8002
                                                           └─▶ enrichment helper :8003 ─┐
                                                                 │                     │
                                              manifest + surface sidecar (/data, ro)    └─▶ Valhalla
```

- **Two images, same Valhalla version** (tile format is version-tied):
  - **serve** = `djbriane/valhalla:<ver>` — your Docker Hub image, a thin `FROM official + serve entrypoint`. Tiles are **not** baked in.
  - **build** = `ghcr.io/valhalla/valhalla:<ver>` — the official image; `build-tiles.sh` drives its raw binaries.
- **Data** lives under `/mnt/user/appdata/valhalla`. Since #14 each build is a self-contained,
  versioned directory and the serve container mounts a `current` symlink at the active one:
  ```
  /mnt/user/appdata/valhalla/
    builds/us-west-<YYMMDD>/   valhalla.json, tiles/ (~4.5 GB), admins.sqlite, timezones.sqlite,
                              surface_provenance.sqlite (sidecar, #11), tile_manifest.json
    builds/us-west-<prev>/     the retained N-1 build (rollback target)
    current -> builds/us-west-<YYMMDD>   the serve container mounts THIS as /data
    extract.osm.pbf            (~3.1 GB) shared scratch input — reused/overwritten, NOT kept per build
  ```
  Retention & rollback: §4b.
- **Measured** (#5): build ~20–25 min, peak build RAM ~11 GB (trivial on 32 GB — native Docker gives full host RAM, no VM cap), serving RAM ~420 MB (tiles are lazy-loaded/mmap'd).

### ⚠️ Image-source guardrail (this bit us before)
- **Build base = GHCR** `ghcr.io/valhalla/valhalla` — actively maintained (rebuilt per-commit; amd64+arm64).
- **NEVER** the Docker Hub `valhalla/valhalla` image — **abandoned since 2023-03-16.** Many old Unraid templates and blog posts point at it; it's the likely cause of prior grief.
- **Do NOT use the Community-Apps marketplace Valhalla template** — it's built around the *turnkey auto-build* image (different env vars, auto-downloads/builds on start). Swapping its Repository to our serve-only image leaves mismatched config → breakage. Use the custom template in this dir instead.
- NUC is Intel → **amd64**. Always cross-build the serve image for `linux/amd64`.

---

## 2. Files in this directory

| file | role |
|---|---|
| `Dockerfile` | thin serve image: `FROM ghcr.io/valhalla/valhalla:<ver>` + `valhalla_service` entrypoint |
| `valhalla-serve.xml` | custom Unraid template → `djbriane/valhalla`, `/data` mount at `…/valhalla/current`, port 8002, no command args |
| `cloudflared.xml` | custom Unraid template → Cloudflare Tunnel connector (dashboard token model), the outbound-only front door (#14, §7) |
| `Dockerfile.enrichment` | stdlib Python image for the NUC-side `POST /trace/attributes` helper (#15) |
| `enrichment-helper.xml` | custom Unraid template → helper, shared read-only `/data`, internal Valhalla URL, port 8003 |
| `enrichment_helper/` | request validation, ≤190 km/8k-point chunking, Valhalla client, sidecar join, pure normalization, HTTP shell |
| `build-tiles.sh` | one-shot NUC build: download extract → config/admins/(timezones)/tiles → **surface sidecar (#11)** → provenance manifest → **promote `current` symlink + prune to N-1 (#14)** → restart serve container |
| `rollback.sh` | repoint `current` at the previous (or a named) retained build + restart serve — no rebuild (#14, §4b) |
| `build_surface_sidecar.py` | pyosmium pass over the *same* extract → `surface_provenance.sqlite` (`way_id` → explicit surface tags); invoked by `build-tiles.sh` in a throwaway `debian:bookworm-slim` container (#11) |
| `verify_sidecar.py` | post-build check: re-runs #6's BDR tracks through `trace_attributes` with `edge.way_id`, joins the sidecar, reports the tagged/inferred/unknown split (#11) |
| `README.md` | this runbook |

---

## 3. First-time setup

### 3a. Build & push the serve image (from a dev box with buildx + Docker Hub login)
```bash
cd deploy/valhalla-unraid
docker buildx build --platform linux/amd64 \
  -t djbriane/valhalla:3.8.2 -t djbriane/valhalla:latest --push .
# verify: linux/amd64 present
docker buildx imagetools inspect djbriane/valhalla:3.8.2 | grep -i platform
```

### 3b. Create the Unraid container (custom template — NOT the marketplace)
**Method 1 — import the template (repeatable):**
1. Copy `valhalla-serve.xml` to the NUC at `/boot/config/plugins/dockerMan/templates-user/my-valhalla.xml` (the flash `config` share).
2. Unraid → **Docker → Add Container** → **Template** dropdown → **User templates → valhalla**.
3. Confirm image `djbriane/valhalla:3.8.2`, path `/data`→`/mnt/user/appdata/valhalla/current`, port `8002`; set **Restart policy: unless-stopped** → **Apply**.

**Method 2 — manual Add Container (no file):** Name `valhalla`; Repository `djbriane/valhalla:3.8.2`; Network `bridge`; Path `/data`↔`/mnt/user/appdata/valhalla/current` (rw); Port `8002`↔`8002` (tcp); Extra Parameters `--restart unless-stopped`; no Post Arguments → Apply.

> **Mount the `current` symlink, not the appdata root (#14).** `build-tiles.sh` builds into
> `builds/us-west-<YYMMDD>/` and flips `current` at the finished build; the serve container follows
> that symlink. It won't exist until the first build runs — start the container after §3c (or it will
> report a missing `/data`), or run §3c first.

### 3c. Build the tiles on the NUC
See §4. The serve container has nothing to serve until this runs — the first build also creates the
`current` symlink the container mounts.

### 3d. Smoke test
```bash
docker restart valhalla
curl -s localhost:8002/status | head -c 200
curl -s localhost:8002/route --data \
  '{"locations":[{"lat":39.7392,"lon":-104.9903},{"lat":40.015,"lon":-105.2705}],"costing":"motorcycle"}' | head -c 300
```
Geometry back = live.

### 3e. Build and run the enrichment helper (#15)

Build from the **repository root** so the Dockerfile can copy the helper package:

```bash
docker buildx build --platform linux/amd64 \
  -f deploy/valhalla-unraid/Dockerfile.enrichment \
  -t djbriane/gpxplore-enrichment:latest --push .
```

Import `enrichment-helper.xml` as a second Unraid user template. Put `gpxplore-enrichment`,
`valhalla`, and `cloudflared` on the same user-defined Docker network; retain
`VALHALLA_URL=http://valhalla:8002`. Both application containers mount the same
`/mnt/user/appdata/valhalla/current`, but the helper's mount is **read-only**. Restart both after
a build or rollback so Docker re-resolves the `current` symlink.

Smoke test from inside the helper container (port 8003 is deliberately **not** published on the NUC
host; cloudflared reaches it over `gpxplore-net`):

```bash
docker exec gpxplore-enrichment python -c \
  'import urllib.request; print(urllib.request.urlopen("http://localhost:8003/version").read().decode())'
```

The response should carry the active manifest version. A POST through the protected Tunnel should
return the service envelope (`status`, `match`, `chunks`) plus normalized `segments`,
`pointRoughness`, and `summary`.
The helper does not own CORS, public rate limits, Access credentials, or origin-down translation;
those remain Worker responsibilities fixed by `specs/routing-api-contract--planned.md`.

---

## 4. Building / refreshing tiles

Run `build-tiles.sh` on the NUC (it uses the NUC's Docker). Since #14 it builds into a **versioned,
self-contained** directory (`builds/us-west-<YYMMDD>/`) and only **on success** atomically flips the
`current` symlink at it, then prunes to the newest `$RETAIN` builds (default 2 = current + N-1) and
restarts the serve container. The in-progress build is invisible to the serve container until the flip,
so a failed or half-finished build never takes the service down (the old `current` keeps serving).
Rollback and the retention knobs: §4b.

> **⚠️ Copy BOTH scripts together.** Since #11, `build-tiles.sh` invokes its sibling
> `build_surface_sidecar.py` (it mounts its own directory into the sidecar container via
> `${BASH_SOURCE[0]}`). They **must live in the same directory** on the NUC. **Pasting only
> `build-tiles.sh`** into User Scripts will build tiles fine but the sidecar step fails
> (`No such file … build_surface_sidecar.py`). Put both on disk and run the file by path.

- **SSH / web terminal** (simplest now): copy the whole `deploy/valhalla-unraid/` dir (or at
  least `build-tiles.sh` + `build_surface_sidecar.py`) to the NUC, `chmod +x build-tiles.sh`, run
  `./build-tiles.sh`.
- **User Scripts plugin**: point the User Script at the on-disk path
  (`bash /path/to/build-tiles.sh`) rather than pasting the body → **Run in Background**. (Or
  `BUILD_SIDECAR=0` to keep the old paste-only flow, tiles only.)

The standalone block below is **tiles-only** (no sidecar, **no versioning/promotion**) — it builds
flat into `$APPDATA` for illustration. For the sidecar, the `current` symlink, and N-1 retention, use
`build-tiles.sh`.

```bash
APPDATA=/mnt/user/appdata/valhalla
IMG=ghcr.io/valhalla/valhalla:3.8.2
mkdir -p $APPDATA
curl -fL https://download.geofabrik.de/north-america/us-west-latest.osm.pbf -o $APPDATA/extract.osm.pbf   # ~3.1 GB
docker run --rm -v $APPDATA:/data $IMG valhalla_build_config \
  --mjolnir-tile-dir /data/tiles --mjolnir-admin /data/admins.sqlite --mjolnir-timezone /data/timezones.sqlite > $APPDATA/valhalla.json
docker run --rm -v $APPDATA:/data $IMG valhalla_build_admins -c /data/valhalla.json /data/extract.osm.pbf
docker run --rm -v $APPDATA:/data $IMG valhalla_build_timezones > $APPDATA/timezones.sqlite   # NOTE the '>' redirect
docker run --rm -v $APPDATA:/data $IMG valhalla_build_tiles -c /data/valhalla.json /data/extract.osm.pbf   # ~20 min
docker restart valhalla
```

Notes:
- **`valhalla_build_timezones` writes to STDOUT** — you must `>` redirect it to a file, not pass a path. (Timezones are optional — only needed for time-of-day routing, which gpxplore doesn't use. `BUILD_TIMEZONES=0` skips it.)
- **`admins.sqlite`** gives border/driving-side attribution — cheap, keep it.
- **No elevation** in the build (the app derives elevation/effort from GPX geometry).
- **Disk budget** in appdata: extract ~3.1 GB (one shared copy) + tiles ~4.5 GB + sidecar (tens of MB)
  **per retained build**. With the default N-1 retention (`RETAIN=2`) that's ~**12 GB**
  (3.1 + 2×~4.5). Set `RETAIN=1` to keep only the current build (~8 GB, no rollback target).
- **Provenance**: `build-tiles.sh` writes `tile_manifest.json` (`data_version = us-west-<YYMMDD>`, extract sha, Valhalla version, plus a `sidecar` block) — the version handle the API wrapper (#10) stamps on responses.
- **Costing profiles** (`motorcycle` + `adv_balanced`/`avoid_highways`, #7) are request-time options handled by the API wrapper — not part of the tile build.

---

## 4a. Surface-provenance sidecar (#11)

The tile build also emits **`surface_provenance.sqlite`** from the *same* `extract.osm.pbf` (a
`BUILD_SIDECAR=0` env var skips it). It exists because Valhalla **fabricates** a surface from road
class + use for any way with no `surface`/`tracktype`/`smoothness`/`mtb:scale`/`sac_scale` tag
(`pbfgraphparser.cc:3020-3071`), and `trace_attributes` exposes no tag provenance — so a surveyed
`surface=gravel` is otherwise indistinguishable from a class-based guess. The sidecar makes
"surveyed vs guessed" visible per segment. Full rationale + contract: `specs/surface-normalization--planned.md` §1–2.

- **Schema:** `surface_provenance(way_id INTEGER PRIMARY KEY, surface, tracktype, smoothness, mtb_scale, sac_scale)` + a `meta` table (`data_version`, `extract_sha256`, way counts). `way_id` is the primary-key index the #10 join needs.
- **Only tagged ways are stored** (any explicit surface-ish tag). Untagged highway ways are omitted — they're recoverable as Valhalla's road-class default. **Presence therefore means `hasExplicitSurface = true`.** The raw tag values are captured now but UNUSED in v1 (they seed a deferred v2 smoothness taxonomy with no rebuild).
- **Must share the extract with the tiles** or the `way_id` join drifts — hence one coherent build, one `data_version`.
- **The #10 service join** (`edge.way_id` → sidecar) reads: present → `tagged`; absent & `way_id != 0` → `inferred`; `way_id == 0` / no matched edge → `unknown`.

**Verify after a build** (needs the serve container up + the `#6` BDR GPX set):
```bash
python3 verify_sidecar.py \
  --sidecar /mnt/user/appdata/valhalla/surface_provenance.sqlite \
  --service http://localhost:8002/trace_attributes
# expect: official BDRs skew high-`tagged`; a non-zero `inferred` share; and some
# inferred-but-paved `unclassified` roads flagged (rather than silently trusted).
```

The sidecar pass runs pyosmium in a throwaway `debian:bookworm-slim` container (the Valhalla image has
none). It installs Debian's **prebuilt** `python3-pyosmium` (3.6.0) via `apt` per run — deliberately
**not** `pip install osmium`, which ships no wheels and would compile from source (cmake/boost/protozero).
Needs internet during the build; override the image with `PYOSMIUM_IMAGE`.

---

## 4b. Retention & rollback (#14)

The serve container mounts `/mnt/user/appdata/valhalla/current`, a symlink into `builds/`. Each build
lands in its own `builds/us-west-<YYMMDD>/`; `build-tiles.sh` flips `current` at it **only after the
build succeeds**, then keeps the newest `$RETAIN` builds (default **2** = current + the previous, N-1)
and deletes older ones. So the previous good tile set is always on disk as a rollback target.

**Why a symlink.** The flip is atomic (`ln -sfn` + `mv -T`), so there's never a window where `current`
is missing or points at a half-built directory. Docker resolves the bind-mount source when the
container (re)starts, so **`docker restart valhalla` after a flip re-resolves `current`** and the
container comes up on the new build — that's the hot-swap `build-tiles.sh` does at the end, and the
same mechanism `rollback.sh` relies on.

**Roll back** (no rebuild — just repoint + restart):
```bash
./rollback.sh --list          # show retained builds; * marks current
./rollback.sh                 # roll back to the previous (N-1) build
./rollback.sh us-west-260701  # roll to a specific retained build
```
It refuses if there's no previous build or the named build isn't retained. `RETAIN` on `build-tiles.sh`
controls how many builds survive a rebuild (`RETAIN=1` keeps only current and leaves no rollback
target; a larger value keeps more history at ~4.5 GB each).

**Verify (AC #14):** after a fresh build, `readlink /mnt/user/appdata/valhalla/current` points at the
new `builds/us-west-<YYMMDD>`, `ls builds/` shows the N-1 build still present, and `./rollback.sh`
followed by the §3d smoke test serves the previous tiles; roll forward again with
`./rollback.sh us-west-<new>`. `current/tile_manifest.json` is the `data_version` source of truth and
is readable off `/data` in the serve container (`docker exec valhalla cat /data/tile_manifest.json`).

> **Confirm the serve container binds the symlink, not a resolved path.** The hot-swap relies on Docker
> re-resolving `current` at each restart — standard bind-mount behavior. Sanity-check once:
> `docker inspect valhalla -f '{{json .HostConfig.Binds}}'` should show `…/valhalla/current`, **not** a
> canonicalized `…/builds/us-west-<YYMMDD>`. If it shows a resolved build path, the mount was created
> against the target rather than the symlink — recreate the container with the `…/current` path (§3b).

**Migrating an existing flat install.** If the NUC already has tiles built flat in
`/mnt/user/appdata/valhalla` (pre-#14), just run the new `build-tiles.sh` once: it creates
`builds/us-west-<YYMMDD>/` + `current` and repoints the container (update its mount to `…/current`
first, §3b). The old flat `tiles/`, `valhalla.json`, `admins.sqlite`, `surface_provenance.sqlite`,
`tile_manifest.json` at the appdata root are now orphaned — delete them after verifying the new build
serves, to reclaim ~4.5 GB. (`extract.osm.pbf` stays; it's the shared scratch input.)

---

## 5. Updating to a new Valhalla version

Valhalla ships often (check <https://github.com/valhalla/valhalla/releases>). **Tile format can change
between versions, so a version bump means rebuilding tiles** — the serve image and the tiles must be
the same Valhalla version. Keep the build image, the `Dockerfile` `FROM`, and the serve tag in lockstep.

**Procedure** (example: 3.8.2 → 3.9.0):

1. **Bump the version in three places:**
   - `Dockerfile` → `FROM ghcr.io/valhalla/valhalla:3.9.0`
   - `build-tiles.sh` → `IMAGE="${IMAGE:-ghcr.io/valhalla/valhalla:3.9.0}"`
   - `valhalla-serve.xml` → `<Repository>djbriane/valhalla:3.9.0</Repository>`
2. **Rebuild & push the serve image:**
   ```bash
   cd deploy/valhalla-unraid
   docker buildx build --platform linux/amd64 -t djbriane/valhalla:3.9.0 -t djbriane/valhalla:latest --push .
   ```
3. **Rebuild tiles with the matching build image** (§4). The *old* serve container keeps serving old
   tiles during this, so no downtime yet.
4. **Point the Unraid container at the new tag:** Docker → `valhalla` → **Edit** → Repository
   `djbriane/valhalla:3.9.0` → **Apply** (pulls + recreates). It comes up on the new version against the
   freshly rebuilt, matching tiles.
5. **Verify** (§3d). Roll back by reverting the tag + restoring the previous tiles if needed.

**Ordering rule:** push new image → rebuild tiles → swap the container. Never run a new-version serve
container against old-version tiles.

> **Pin, don't float.** Keep the Unraid Repository on an explicit version tag (not `:latest`) so Unraid's
> "check for updates" never silently pulls a new Valhalla whose format mismatches your tiles. Updates are
> deliberate, via the steps above.

---

## 6. Day-to-day operations

- **Restart / stop / start:** Unraid Docker tab, or `docker restart valhalla`.
- **Logs:** `docker logs -f valhalla` (serve). Build logs: your SSH/terminal session running
  `build-tiles.sh` (or the User Scripts log), plus the transient `ghcr.io/valhalla/valhalla`
  (tiles) and `debian:bookworm-slim` (sidecar) containers' stdout.
- **Health:** `GET /status` → 200. Quick route test in §3d.
- **Data location:** everything is in `/mnt/user/appdata/valhalla` — back that up (or just the ability to
  rebuild from `build-tiles.sh`; tiles are reproducible, so a backup is optional).
- **RAM:** serve idles at a few hundred MB; grows with routing coverage + page cache. No limit needed.

---

## 6a. SSH access & key-only hardening

Admin is done as `root` over SSH from a dev box (there's no useful non-root path on Unraid — webGUI
"users" are SMB-only, and any account that can reach the Docker socket is root-equivalent anyway, so a
non-root docker admin buys almost nothing for real setup cost).

**Key install (Unraid 7.x).** Root's home is in RAM, so authorized keys must live on the flash. The 7.x
webGUI (**Users → root → SSH authorized keys**) writes them to `/boot/config/ssh/root/authorized_keys`;
`/root/.ssh` is a **symlink** to `/boot/config/ssh/root`, so the flash copy *is* the live copy (no
boot-time sync). (The old single-file `/boot/config/ssh/root.pubkeys` is gone in 7.x — it's a `root/`
**directory** now.) The dev box uses a dedicated key
(`~/.ssh/id_ed25519_nuc`) and a `nuc` alias in `~/.ssh/config` so it's independently revocable.

**Key-only hardening (persistent).** `sshd_config` is regenerated in RAM every boot (it only sets
`PermitRootLogin yes`; password auth is the compiled default), so a one-time edit reverts on reboot.
Persist it via a flash drop-in re-applied from `/boot/config/go`:

- Flash drop-in `/boot/config/ssh/sshd_config.d/99-hardening.conf`:
  ```
  PasswordAuthentication no
  KbdInteractiveAuthentication no
  PermitRootLogin prohibit-password
  ```
- `/boot/config/go` re-applies it each boot: copy the drop-in into `/etc/ssh/sshd_config.d/`, prepend
  an `Include /etc/ssh/sshd_config.d/*.conf` to the top of `sshd_config` (sshd takes the **first**
  value for a keyword, so the Include must precede the stock `PermitRootLogin yes`), then `sshd -t`
  and reload sshd. Applying it live (same steps by hand) takes effect without a reboot.
- Optional source scoping: prefix the `authorized_keys` line with
  `from="192.168.7.0/24,100.64.0.0/10"` (LAN + Tailscale CGNAT) so a leaked key can't be used from the
  open internet while keeping the Tailscale path working.

### Recovery — if the dev box (and its key) are lost

Disabling SSH password auth does **not** lock you out: the webGUI and local console authenticate with
the **root password**, a separate path from SSH keys. In order of convenience:

1. **webGUI** `http://zoopnuc.local` (or `http://192.168.7.129`) with the root password → Users → root →
   paste a new SSH public key. Back in within a minute.
2. **Local console** (monitor + keyboard) → root shell with the root password.
3. **Tailscale** (`100.117.203.127`) → off-LAN path to the webGUI / SSH even if you're not on the LAN.
4. **USB flash** physically holds `/boot/config/ssh/root/authorized_keys`; keep an Unraid **Tools →
   Flash Backup** so the whole config is restorable if the stick dies.

> The **root password is the master recovery credential** — keep it in a password manager. Everything
> above depends on it, and none of it depends on any SSH key surviving.

---

## 7. Exposure — Cloudflare Tunnel + Access (#14)

Front the NUC origin with a **Cloudflare Tunnel** (outbound-only — **no ports opened on the router**)
and lock it down with a **Cloudflare Access** policy so only a credentialed caller reaches Valhalla; the
public internet gets a **403**. Topology (final): `browser → Worker (#10) → Tunnel → NUC Valhalla`;
Cloudflare's edge supplies TLS / CORS / rate-limit / WAF. The Worker doesn't exist yet, so today an
**email** Access policy also lets *you* reach the hostname in a browser for testing.

**Prerequisites:** a domain on Cloudflare (free plan is fine — the Tunnel needs a zone to attach a
public hostname to) and Cloudflare **Zero Trust** enabled on the account (free tier covers this).

### 7a. Create the tunnel (dashboard, token-based)
1. **one.dash.cloudflare.com → Networks → Tunnels → Create a tunnel** → connector type **Cloudflared** →
   name it (e.g. `nuc-valhalla`).
2. On the install screen, **copy the token** (the long string after `--token`). Ignore the OS install
   snippets — it runs as a container.

### 7b. Run the connector on Unraid
Add the `cloudflared` container from **`cloudflared.xml`** in this dir (Docker → Add Container → User
templates → cloudflared), paste the token into **`TUNNEL_TOKEN`**, restart policy **unless-stopped**,
Apply. Within ~30 s the tunnel shows **HEALTHY** in the dashboard. Outbound-only; nothing opened on the
router.

**Origin reachability** — cloudflared has to reach the Valhalla container. Two ways:
- **Simple:** point the public hostname (§7c) at the NUC's LAN IP — `http://<NUC-LAN-IP>:8002`. Give the
  NUC a **DHCP reservation** so the IP is stable.
- **Cleaner (no IP dependency):** put the `valhalla` and `cloudflared` containers on the **same
  user-defined Docker network** and use `http://valhalla:8002`. Cloudflare's own docs recommend keeping
  `cloudflared` on the same network as the origin.

### 7c. Add the private origin hostnames

The deployed Valhalla origin is **`valhalla.gpxplore.net`**. Its tunnel route targets
`http://valhalla:8002` on the shared Docker network (or `<NUC-LAN-IP>:8002` when using the LAN-IP
form). Cloudflare terminates TLS and the Access application protects the hostname.

Add a second hostname for the helper—`enrichment.gpxplore.net` is the suggested name—routed to
`http://gpxplore-enrichment:8003` (or `<NUC-LAN-IP>:8003`) behind the **same** tunnel. Apply the same
Access service-token policy. This is a second private origin hostname, not a second public API: the
Worker remains the only browser-facing surface.

### 7d. Lock it down — Cloudflare Access (this is the AC that gives the origin its privacy)
Straight off §7c the hostname is **wide open** — anyone with the URL can burn the NUC's CPU. Put a
**Cloudflare Access** application on it with **two policies** on the same app (Access allows any matching
policy through):

**one.dash.cloudflare.com → Access → Applications → Add an application → Self-hosted**, Application
domain = your `valhalla.<domain>` hostname, then add policies:

- **Service-token policy (end state, per #10 — this is what makes public requests 403).** First
  **Access → Service Auth → Service Tokens → Create** a token; save the **Client ID** and **Client
  Secret** (shown once). Then on the app add a policy with **Action = Service Auth**, Include =
  **Service Token = `<that token>`**. The future Worker attaches `CF-Access-Client-Id` /
  `CF-Access-Client-Secret` (as Worker secrets) on every origin subrequest; a request without them → 403.
- **Email policy (interim, so you can test in a browser now).** Action = **Allow**, Include =
  **Emails = `<your email>`**. Cloudflare emails you a one-time PIN; after login the browser reaches the
  hostname. Remove or tighten this once the Worker is the only intended caller.

> The **Access policy — not the Tunnel — is what closes the origin.** A bare tunnel hostname is public;
> the 403 acceptance criterion is satisfied by the Access application above.

### 7e. Verify end-to-end (AC #14)
```bash
DOMAIN=valhalla.gpxplore.net
# 1. Uncredentialed public request -> BLOCKED by Access, NOT served by Valhalla:
curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/status
#    Expect NOT 200. Exact code depends on which policies are on the app:
#      - service-token policy ONLY (end state, per AC #3): 403.
#      - with the interim EMAIL policy also present: 302 -> Access login page (still blocked).
#    Either way the request never reaches Valhalla. To assert the literal 403, test while the
#    service-token policy is the app's ONLY policy.

# 2. Credentialed request (service-token headers) -> reaches Valhalla:
curl -s https://$DOMAIN/status \
  -H "CF-Access-Client-Id: <client-id>" \
  -H "CF-Access-Client-Secret: <client-secret>" | head -c 200            # expect 200 + status JSON

# 3. Credentialed POST /route, costing motorcycle -> geometry back (§3d, now through the tunnel):
curl -s https://$DOMAIN/route \
  -H "CF-Access-Client-Id: <client-id>" \
  -H "CF-Access-Client-Secret: <client-secret>" \
  --data '{"locations":[{"lat":39.7392,"lon":-104.9903},{"lat":40.015,"lon":-105.2705}],"costing":"motorcycle"}' \
  | head -c 300
```
`(1)` blocked (302/403, not 200) and `(2)`/`(3)` = 200 with geometry ⇒ the origin is reachable **only**
by a credentialed caller, worldwide over HTTPS, with zero open router ports.

**Fallback:** if routing ever needs datacenter reliability, the same tiles + serve image drop onto
Hetzner CAX11 (~$6.50/mo) — only the origin location changes. See `specs/valhalla-hosting--research.md`.

---

## 8. Troubleshooting (from real experience)

| symptom | cause | fix |
|---|---|---|
| build log shows nothing for minutes (SSH session or User Scripts) | output is block-buffered off-TTY; first steps (3 GB download, admins parse) are quiet | it's fine — `valhalla_build_tiles` floods the log later. Confirm via `ls -lh $APPDATA` (extract growing) and `docker ps` (a valhalla build container running). |
| garbled binary blob in the build log | `valhalla_build_timezones` was given a path instead of a `>` redirect, so it dumped the DB to stdout | use `valhalla_build_timezones > $APPDATA/timezones.sqlite` (already fixed in `build-tiles.sh`). Harmless — tiles still build. |
| routes return empty / errors right after container start | tiles not built yet (or `valhalla.json` missing) | run `build-tiles.sh`, then `docker restart valhalla`. |
| `Admin db … not found` warning during build | `valhalla_build_admins` didn't run before `build_tiles` | run the admins step first (build script does). Warning is non-fatal. |
| container runs but every route fails after a version bump | new-version serve reading old-version tiles | rebuild tiles at the new version (§5). |
| prior marketplace container "just didn't work" | it used the abandoned Docker Hub image / turnkey auto-build assumptions | use the custom template + `djbriane/valhalla` here; ignore the marketplace Valhalla entry. |
| sidecar step: `No such file … build_surface_sidecar.py` | `build-tiles.sh` was pasted/run without its sibling `build_surface_sidecar.py` in the same dir (it mounts its own `${BASH_SOURCE[0]}` dir) | copy **both** files to the same NUC dir and run by path (§4). Tiles still built fine — just rerun for the sidecar. |
| sidecar step fails at `apt-get install python3-pyosmium` | no outbound internet in the throwaway container, or Debian mirror hiccup | ensure the NUC has internet during build. `BUILD_SIDECAR=0` skips the sidecar without blocking tiles. (Do **not** switch to `pip install osmium` — it has no wheels and compiles from source.) |
| `verify_sidecar.py` shows LOW tagged / ZERO inferred | sidecar and tiles built from *different* extracts, or `edge.way_id` missing from the request filter | rebuild tiles + sidecar in one `build-tiles.sh` run (shared `data_version`); confirm the request lists `edge.way_id`. |
| serve container won't start / `/data` empty after Add Container | mounted `…/valhalla/current` but no build has run yet, so the symlink doesn't exist | run `build-tiles.sh` once (creates `builds/…` + `current`), then start the container (§3b, §3c). |
| built new tiles but the service still serves the old ones | serve container wasn't restarted, so its bind mount still points at the previous `current` target | `docker restart valhalla` (build-tiles.sh does this automatically; `rollback.sh` too). Restart re-resolves the symlink. |
| `rollback.sh`: "no previous build to roll back to" | only one build retained (`RETAIN=1`, or the NUC has only ever built once) | nothing to roll back to; rebuild history accrues as you run `build-tiles.sh` with `RETAIN>=2`. |
| tunnel never goes HEALTHY in the dashboard | wrong/expired `TUNNEL_TOKEN`, or no outbound internet from the NUC | recopy the token from the tunnel's install screen into the `cloudflared` container; confirm the NUC has outbound HTTPS. |
| cloudflared logs `dial tcp: lookup valhalla … no such host` | hostname points at `valhalla:8002` but the two containers aren't on the same user-defined Docker network | put both on one custom network, or point the public hostname at `<NUC-LAN-IP>:8002` instead (§7b). |
| every request to the tunnel hostname returns 403 (including your own browser) | Access is doing its job, but there's no policy that admits you | add the **email** Allow policy (§7d) and complete the one-time-PIN login; service-token callers must send the `CF-Access-Client-*` headers. |
| helper returns `configuration_error` | `/data/tile_manifest.json` or `/data/surface_provenance.sqlite` is absent, unreadable, or their `data_version` values differ | mount the active `…/valhalla/current` build read-only in the helper and rebuild tiles + sidecar together. |
| helper cannot reach Valhalla | `VALHALLA_URL=http://valhalla:8002` is set but the containers do not share a user-defined network | put them on the same network, or use the stable NUC LAN URL for `VALHALLA_URL`. |
