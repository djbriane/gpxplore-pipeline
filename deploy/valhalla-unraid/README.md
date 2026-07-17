# Valhalla on Unraid — setup & operations runbook

Self-host the gpxplore Valhalla routing service on the home-lab NUC (Unraid, 32 GB). The NUC
**builds** the us-west tile set (offline, manual cadence) **and serves** it 24/7; Cloudflare
Tunnel is the public front door. **$0/mo.** Decision context: wayfinder ticket
`djbriane/gpxplore-pipeline#8` (see the map, issue #4).

---

## 1. Architecture at a glance

```
                build (occasional, ~20 min)        serve (always-on)
  Geofabrik  ─▶  ghcr.io/valhalla/valhalla  ─▶  /mnt/user/appdata/valhalla  ◀─  djbriane/valhalla
  us-west.pbf     (official image, raw bins)      valhalla.json + tiles/         (your thin image)
                                                          │  :8002
   browser ─▶ Cloudflare Worker (wrapper: #10) ─▶ Cloudflare Tunnel ─▶ NUC valhalla container
```

- **Two images, same Valhalla version** (tile format is version-tied):
  - **serve** = `djbriane/valhalla:<ver>` — your Docker Hub image, a thin `FROM official + serve entrypoint`. Tiles are **not** baked in.
  - **build** = `ghcr.io/valhalla/valhalla:<ver>` — the official image; `build-tiles.sh` drives its raw binaries.
- **Data** lives in `/mnt/user/appdata/valhalla` (mounted as `/data`): `valhalla.json`, `tiles/` (~4.5 GB), `admins.sqlite`, `timezones.sqlite` (optional), `extract.osm.pbf` (~3.1 GB), `surface_provenance.sqlite` (sidecar, #11), `tile_manifest.json`.
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
| `valhalla-serve.xml` | custom Unraid template → `djbriane/valhalla`, `/data` mount, port 8002, no command args |
| `build-tiles.sh` | one-shot NUC build: download extract → config/admins/(timezones)/tiles → **surface sidecar (#11)** → provenance manifest → restart serve container |
| `build_surface_sidecar.py` | pyosmium pass over the *same* extract → `surface_provenance.sqlite` (`way_id` → explicit surface tags); invoked by `build-tiles.sh` in a throwaway `python:3.12-slim` container (#11) |
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
3. Confirm image `djbriane/valhalla:3.8.2`, path `/data`→`/mnt/user/appdata/valhalla`, port `8002`; set **Restart policy: unless-stopped** → **Apply**.

**Method 2 — manual Add Container (no file):** Name `valhalla`; Repository `djbriane/valhalla:3.8.2`; Network `bridge`; Path `/data`↔`/mnt/user/appdata/valhalla` (rw); Port `8002`↔`8002` (tcp); Extra Parameters `--restart unless-stopped`; no Post Arguments → Apply.

### 3c. Build the tiles on the NUC
See §4. The serve container has nothing to serve until this runs.

### 3d. Smoke test
```bash
docker restart valhalla
curl -s localhost:8002/status | head -c 200
curl -s localhost:8002/route --data \
  '{"locations":[{"lat":39.7392,"lon":-104.9903},{"lat":40.015,"lon":-105.2705}],"costing":"motorcycle"}' | head -c 300
```
Geometry back = live.

---

## 4. Building / refreshing tiles

Run `build-tiles.sh` on the NUC (it uses the NUC's Docker).

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

The standalone block below is **tiles-only** (no sidecar) — for the sidecar use `build-tiles.sh`.

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
- **Disk budget** in appdata: extract ~3.1 GB + tiles ~4.5 GB + sidecar (tens of MB) ≈ **8 GB**.
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

## 7. Exposure — Cloudflare Tunnel (planned)

Run `cloudflared` (Unraid container) with a tunnel routing a hostname → `http://<nuc-ip>:8002`.
Outbound-only — **no ports opened on the router.** Cloudflare's edge supplies CORS / rate-limit / WAF.
Per #10, the **wrapper Worker** (chunking, `surface_class`, versioning) sits in front and treats this
tunnel origin as its private backend: `browser → Worker → Tunnel → NUC Valhalla`.

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
