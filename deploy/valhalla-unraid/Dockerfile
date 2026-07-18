# djbriane/valhalla — thin serve-only wrapper over the OFFICIAL Valhalla image.
#
# Adds only a serve entrypoint so the Unraid template needs no command args.
# Tiles are NOT baked in — mount the pre-built tile set at /data (built by build-tiles.sh),
# which keeps this image tiny and decoupled from tile refreshes.
#
# Build & push (cross-build for the Intel NUC = amd64):
#   docker buildx build --platform linux/amd64 \
#     -t djbriane/valhalla:3.8.2 -t djbriane/valhalla:latest --push .
#
# Keep the FROM tag in sync with build-tiles.sh IMAGE (tile format is version-tied).
FROM ghcr.io/valhalla/valhalla:3.8.2

EXPOSE 8002

# Serve the pre-built tiles referenced by /data/valhalla.json with 1 worker thread.
# Override CMD if you need a different config path or thread count.
ENTRYPOINT ["/usr/local/bin/valhalla_service"]
CMD ["/data/valhalla.json", "1"]
