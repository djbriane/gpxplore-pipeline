# Valhalla hosting & tile deployment — research (#8)

**Status:** research
**Date:** 2026-07-16
**Ticket:** djbriane/gpxplore-pipeline#8 (routing map is #4; tile build measured in #5)

## Headline recommendation

Run the always-on Valhalla server on a **Hetzner Cloud CAX11** (ARM, 2 vCPU / 4 GB RAM / 40 GB NVMe, **€5.99/mo ≈ ~$6.50/mo**). The 40 GB instance disk swallows the 4.48 GB tile tar with room to spare, so **no separate block volume is needed**.

Ship tiles as a **pre-built `valhalla_tiles.tar`** (the tar the offline build in #5 already produces), served by the official **`ghcr.io/valhalla/valhalla-scripted`** image in serve-only mode (`use_tiles_ignore_pbf=True`, `force_rebuild=False`). Deliver each new build by **uploading the tar to Cloudflare R2 (versioned key, ~$0.07/mo, zero egress) and pulling it onto the host, then doing an atomic swap + container restart** — the manual, review-gated cadence from #5 does not justify baking 4.48 GB into a Docker image or running a rebuild on the host.

**All-in cost: ~$6.50–7/mo** (Hetzner instance + trivial R2 storage), comfortably inside the map's ~$5–20/mo target.

If you would rather trade a few dollars for a fully managed deploy/rollback story (no SSH, `fly deploy` + volumes), **Fly.io shared-cpu-1x 2 GB + a 5 GB volume ≈ ~$12/mo** is the runner-up. Railway is a poor fit (see table).

Why this profile is cheap to serve: #5 measured serving RAM at a **flat ~418 MB regardless of tile-set size** because Valhalla lazy-loads / memory-maps tiles per request. The serve host is bounded by routing coverage/concurrency + OS page cache, not by the 4.48 GB on disk. So a small, cheap box is genuinely enough; disk (to hold the tar) is the only "large" requirement.

---

## Per-host comparison

Target profile: ~4.5 GB persistent disk for tiles, 1–2 GB RAM to start (headroom for cache/concurrency; measured baseline ~418 MB), always-on, cheap.

| Host / plan | vCPU | RAM | Disk | Persistent-disk story | Price (as of 2026-07-16) | Fit |
|---|---|---|---|---|---|---|
| **Hetzner CAX11** (ARM) ⭐ | 2 | 4 GB | 40 GB NVMe | Instance disk holds tar directly; optional volume €0.0572/GB/mo | **€5.99/mo (~$6.50)** | **Best.** Disk + RAM both generous; cheapest. You manage the box (SSH/Docker). |
| Hetzner CX23 (x86) | 2 | 4 GB | 40 GB | same | €5.49/mo (~$6.00) | Great; x86 if you want to match a dev/build arch. |
| Hetzner CAX21 (ARM) | 4 | 8 GB | 80 GB | same | €10.49/mo (~$11.40) | Overkill for one region; buys concurrency headroom. |
| **Fly.io shared-cpu-1x** | 1 | 2 GB | via Fly Volume | Fly Volume @ **$0.15/GB/mo**; 5 GB ≈ $0.75/mo | VM **$11.11/mo** + ~$0.75 vol ≈ **~$12/mo** | Runner-up. Managed `fly deploy`, volumes mount at deploy — fits the sync model. Pricier per GB of RAM/disk. |
| Fly.io shared-cpu-1x 1 GB | 1 | 1 GB | Fly Volume | as above | $5.92/mo + vol ≈ ~$6.70 | Works (baseline is ~418 MB) but thin headroom for cache/concurrency. |
| **Railway Hobby** | usage | usage | Volume, **5 GB max** | $0.15/GB/mo, **5 GB cap on Hobby** | $5/mo base ($5 usage incl.), then $10/GB-RAM/mo, $20/vCPU/mo | **Poor fit.** 4.48 GB tar barely fits the 5 GB volume cap with no working room; always-on RAM+CPU usage burns past the $5 credit fast. |
| Railway Pro | usage | usage | Volume up to 1 TB | same rates | $20/mo base ($20 usage incl.) | Fits but $20 floor + metered RAM/CPU/disk ≈ top of budget for one small service. |

Notes:
- **Hetzner volume pricing** (€0.0572/GB/mo) is community-reported (costgoat, 2026-07-16); Hetzner's own dynamic pages didn't render the number. It is moot for the recommendation since the 40 GB instance disk already holds the tar — flagged in Open Questions.
- **ARM (CAX) is fine for Valhalla:** the official images are multi-arch (arm64 published alongside amd64). If the #7 motorcycle costing work or the build were ever pinned to x86 behavior, use **CX23** (x86, €5.49/mo) instead — same specs, essentially same price.
- **Fly egress:** Fly and Railway meter outbound bandwidth; a routing API for a local-first planner is low-egress, so this is negligible but non-zero. Hetzner includes 20 TB/mo.

---

## How tiles get to the host — options & tradeoffs

The offline build (#5) already emits a memory-mappable **`valhalla_tiles.tar`** (`build_tar=True` default). Serving from that tar is the fast-load, low-RAM path. Three delivery mechanisms:

### (a) Bake tiles into the Docker image — not recommended
Copy the 4.48 GB tar into an image layer; redeploy the whole image per tile update.
- ✅ Fully atomic and reproducible; rollback = redeploy the previous image tag; no separate state to manage.
- ❌ Every tile refresh means building and pushing/pulling a **4.5 GB+ image**. Slow registry churn, slow `docker pull` / `fly deploy`, wasted bandwidth for a graph that changes on a slow manual cadence. The image is 99% data, 1% code.
- Verdict: the coupling of code and 4.5 GB of data per push is not worth it here.

### (b) Tiles on persistent disk / volume, synced in place — recommended core
Keep the `valhalla-scripted` image small (pull once); the tar lives on the host's mapped `custom_files/` (Hetzner instance disk) or a mounted volume (Fly). New builds are rsync'd/copied in and the container is restarted.
- ✅ No registry churn; only the ~4.5 GB tar moves, and only on refresh. Fast, incremental, cheap.
- ✅ Serve-only mode is first-class: drop the tar in `custom_files/`, set `use_tiles_ignore_pbf=True`, `force_rebuild=False`, record its md5 in `.file_hashes.txt`, restart — loaded on boot with no rebuild (Valhalla docker README, "Run Valhalla with pre-built tiles").
- ❌ Tile state lives outside the image → you own its backup/versioning (mitigated by (c)).

### (c) Object storage as the distribution channel (Cloudflare R2 / S3) — recommended companion
Local build uploads the tar to a **versioned R2 key** (e.g. `tiles/us-west/2026-07-16/valhalla_tiles.tar`); the host pulls the chosen version at deploy time.
- ✅ Decouples the local build machine from the host; gives an audit trail / immutable version history / instant rollback target.
- ✅ **R2 storage ~$0.015/GB/mo → 4.48 GB ≈ $0.07/mo, and R2 has zero egress fees**, so pulling to the host is free. (S3 works too but charges egress.)
- ❌ One extra hop, but it is exactly the "publish a build, then let the host fetch it" shape the review-gated workflow wants.

**Recommended: (b) + (c).** Tiles served from the host's local disk (Hetzner) or a Fly Volume, distributed as versioned tars via R2. Baking into the image (a) is reserved for the case where you want deploy atomicity above all and don't mind the 4.5 GB pushes.

---

## Update workflow (new local build → live, minimal downtime)

Assumes Hetzner CAX11 running `valhalla-scripted` with `custom_files/` on the instance disk; the container was started with `-e use_tiles_ignore_pbf=True -e force_rebuild=False`.

1. **Build locally** (per #5): produce `valhalla_tiles.tar` and its md5.
2. **Publish**: upload to a versioned R2 key, e.g. `s3://gpx-tiles/us-west/2026-07-16/valhalla_tiles.tar` (+ a sidecar `.md5`). Review-gate here.
3. **Stage on host**: `aws s3 cp`/`rclone copy` the new tar to a staging path *next to* `custom_files/` (not over the live file), e.g. `~/valhalla/staging/valhalla_tiles.tar`. Verify md5.
4. **Atomic swap**: move the staged tar into `custom_files/valhalla_tiles.tar` (same filesystem → `mv` is atomic) and update the md5 line in `custom_files/.file_hashes.txt`.
5. **Restart**: `docker restart valhalla`. Because tiles are a pre-built tar (memory-mapped, no rebuild), reload is a few seconds → **downtime ≈ seconds**.
6. **Verify**: poll `/status` and confirm the reported tileset build id changed; run a smoke `/route` (e.g. Denver→Boulder, as in #5).
7. **Rollback**: keep the previous tar (or re-pull the prior R2 version), swap it back, restart.

**For true zero-downtime** (optional, if seconds of blip matter): run the new container on a second port with the new tar, health-check it, then flip a reverse proxy (Caddy/nginx/Cloudflare) from old→new and retire the old container. Not necessary for a hobby planning tool, but cheap to add later.

---

## Rough monthly cost (recommended setup)

| Item | Cost |
|---|---|
| Hetzner CAX11 (2 vCPU / 4 GB / 40 GB NVMe, always-on) | €5.99/mo ≈ **$6.50** |
| Cloudflare R2 tile storage (~4.5 GB @ $0.015/GB, keep a couple versions ≈ 10 GB) | ~**$0.15** |
| R2 egress (host pulls) | **$0.00** |
| **Total** | **≈ $6.50–7/mo** |

Runner-up (Fly.io, managed): ~$12/mo (VM $11.11 + 5 GB volume ~$0.75) + trivial R2/egress.

---

## Sources (all fetched 2026-07-16)

First-party host pricing/docs:
- Fly.io pricing (VM per-RAM rates; Volumes $0.15/GB/mo): https://fly.io/docs/about/pricing/
- Hetzner June 2026 price adjustment (official; CAX11 €5.99, CAX21 €10.49, CX23 €5.49): https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- Hetzner Cloud (product overview): https://www.hetzner.com/cloud
- Hetzner Cloud Volumes overview (10 GB–10 TB range; hourly billing, monthly cap): https://docs.hetzner.com/cloud/volumes/overview/
- Railway pricing (Hobby $5 incl. $5 usage / Pro $20; RAM $10/GB-mo, vCPU $20/mo, Volume $0.15/GB-mo, egress $0.05/GB; Hobby volume cap 5 GB, Pro up to 1 TB): https://railway.com/pricing and https://docs.railway.com/reference/pricing/plans

Valhalla runtime/deployment (first-party):
- Valhalla docker README — serve pre-built `valhalla_tiles.tar` via `use_tiles_ignore_pbf=True` + `force_rebuild=False` + `.file_hashes.txt`; `build_tar` default; env vars: local clone `/Users/djbriane/Development/gpxplore/valhalla/docker/README.md`
- Official image: `ghcr.io/valhalla/valhalla-scripted` (multi-arch) — https://github.com/valhalla/valhalla/pkgs/container/valhalla-scripted

Community / secondary (clearly not first-party — used only where official pages didn't render numbers):
- costgoat Hetzner calculator (CAX specs + block-storage €0.0572/GB/mo): https://costgoat.com/pricing/hetzner
- Cloudflare R2 pricing (storage $0.015/GB-mo, zero egress) — verify before committing: https://developers.cloudflare.com/r2/pricing/  *(not independently re-fetched this session; standard published rate — confirm.)*

Internal measurements (do not relitigate):
- #5 build/serve results: `/Users/djbriane/Development/gpxplore/gpxplore-pipeline/prototypes/valhalla-tile-build/results-us-west.md` (4.48 GB tiles on disk; ~418 MB serving RAM, flat vs tile size; ~12 GB peak is *build* memory, offline only).

---

## Open questions

1. **R2 rate not re-fetched this session** — confirm Cloudflare R2 storage ($0.015/GB-mo) and zero-egress terms on the live pricing page before wiring up the pipeline. Cost impact is tiny either way (~$0.07/mo).
2. **Hetzner volume price** (€0.0572/GB/mo, community-sourced) is unverified against Hetzner's own page — but irrelevant unless the tar outgrows the 40 GB instance disk (it won't for us-west; revisit if coverage expands to full US or multi-region).
3. **ARM vs x86:** recommendation uses ARM (CAX11) for price. Confirm the #7 motorcycle costing profiles behave identically on arm64 serving (routing is data-driven, so expected yes); if any doubt, CX23 (x86, €5.49) is a drop-in swap.
4. **Ship admin/timezone DBs?** #5 built without `admins.sqlite`. Border-crossing penalties, driving-side, and time-dependent routing need `admins.sqlite`/`tz.sqlite`. Decide whether to include them in the served bundle (adds some size, not GBs) — affects the tar contents, not the host choice.
5. **Always-on vs scale-to-zero:** Fly can scale a Machine to zero to save money, but a cold start re-mmaps the tar (seconds of latency on first request). For a snappy planner, keep it always-on (the basis of the cost estimates). Hetzner is always-on by nature.
6. **Concurrency sizing untested:** ~418 MB baseline was a single route. 4 GB RAM gives generous page-cache/concurrency headroom, but real concurrent-load memory for the BDR planner hasn't been measured. Worth a quick load test before calling it done.
7. **TLS / reverse proxy & backups:** the host will need TLS termination (Caddy or Cloudflare in front) and a basic backup/redeploy runbook for the box — out of scope for pricing but required before production.
