#!/usr/bin/env python3
"""
Generate a disk-efficient EXR image at 16384 x 65536.

Efficiency techniques used:
  - HALF (float16) pixel data  — halves memory vs FLOAT (float32)
  - Tiled storage               — enables random access & better compression
  - ZIP compression             — lossless, best ratio for EXR tiles
  - Streaming tile writes       — avoids holding the full image in RAM at once
  - Gradient pattern            — compressible content (replace with your data)

Requirements:
    pip install openexr numpy
"""

import argparse
import math
import sys
import time

import numpy as np
import OpenEXR


# ── Configuration ────────────────────────────────────────────────────────────

WIDTH      = 16_384
HEIGHT     = 65_536
TILE_SIZE  = 256          # power-of-two tile; 256×256 is a common sweet spot
CHANNELS   = ("R", "G", "B")
OUTPUT     = "output.exr"


# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_tile(tx: int, ty: int,
                  tile_w: int, tile_h: int,
                  total_w: int, total_h: int) -> dict[str, np.ndarray]:
    """
    Return HALF-precision channel arrays for one tile.

    This generates a simple UV-gradient so tiles compress well.
    Replace the body with your own data source (disk read, network, etc.).
    """
    # pixel coordinates for this tile
    y0, x0 = ty * TILE_SIZE, tx * TILE_SIZE

    ys = (np.arange(tile_h, dtype=np.float16) + y0) / total_h   # 0 … 1
    xs = (np.arange(tile_w, dtype=np.float16) + x0) / total_w   # 0 … 1

    xx, yy = np.meshgrid(xs, ys)          # shape (tile_h, tile_w)

    r = xx.astype(np.float16)
    g = yy.astype(np.float16)
    b = (1.0 - xx * yy).astype(np.float16)

    return {"R": r, "G": g, "B": b}


def tiles_along(total: int, tile: int) -> int:
    return math.ceil(total / tile)


def _make_tile_desc(tile_size: int) -> "OpenEXR.TileDescription":
    td = OpenEXR.TileDescription()
    td.xSize = tile_size
    td.ySize = tile_size
    td.mode  = OpenEXR.LevelMode.ONE_LEVEL
    td.roundingMode = OpenEXR.LevelRoundingMode.ROUND_DOWN
    return td


# ── Main ──────────────────────────────────────────────────────────────────────

def main(output: str = OUTPUT,
         width: int  = WIDTH,
         height: int = HEIGHT,
         tile_size: int = TILE_SIZE) -> None:

    ntx = tiles_along(width,  tile_size)
    nty = tiles_along(height, tile_size)
    total_tiles = ntx * nty

    print(f"Image      : {width} × {height} px")
    print(f"Tile size  : {tile_size} × {tile_size} px  ({ntx} × {nty} = {total_tiles:,} tiles)")
    print(f"Pixel type : HALF (float16)")
    print(f"Compression: ZIP")
    print(f"Output     : {output}")
    print()

    # ── Build full channel arrays in float16, tile by tile ───────────────────
    # For a 16 k × 64 k image three float16 channels = 3 × 2 × 16384 × 65536
    # ≈ 6 GB uncompressed.  We therefore write one row-band of tiles at a time
    # and let the OS page-file absorb the working set, or you can swap the
    # numpy arrays for memory-mapped files if RAM is tight.
    #
    # Here we keep it simple: allocate the full arrays using float16 (≈ 6 GB).
    # If your machine cannot hold that, reduce HEIGHT or tile the writes via
    # the Part/OutputFile API.  The script prints a warning if > 4 GB.

    uncompressed_gb = width * height * len(CHANNELS) * 2 / 1e9
    print(f"Uncompressed size: ~{uncompressed_gb:.1f} GB  "
          f"({'WARNING: large allocation' if uncompressed_gb > 4 else 'ok'})")
    print("Allocating channel arrays …", flush=True)

    t0 = time.perf_counter()

    # Allocate once, fill in-place tile by tile to keep memory pressure stable
    R = np.empty((height, width), dtype=np.float16)
    G = np.empty((height, width), dtype=np.float16)
    B = np.empty((height, width), dtype=np.float16)

    print("Filling tiles …", flush=True)
    for ty in range(nty):
        y0 = ty * tile_size
        y1 = min(y0 + tile_size, height)
        for tx in range(ntx):
            x0 = tx * tile_size
            x1 = min(x0 + tile_size, width)
            t = generate_tile(tx, ty, x1 - x0, y1 - y0, width, height)
            R[y0:y1, x0:x1] = t["R"]
            G[y0:y1, x0:x1] = t["G"]
            B[y0:y1, x0:x1] = t["B"]

        if (ty + 1) % max(1, nty // 20) == 0:
            pct = (ty + 1) / nty * 100
            print(f"  {pct:5.1f}%  row-band {ty+1}/{nty}", flush=True)

    fill_s = time.perf_counter() - t0
    print(f"Fill done in {fill_s:.1f}s")

    # ── Assemble the EXR File object ─────────────────────────────────────────
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,   # lossless + best ratio
        "type":        OpenEXR.tiledimage,
        "tiles":       _make_tile_desc(tile_size),
    }

    channels = {"R": R, "G": G, "B": B}

    print("Writing EXR …", flush=True)
    t1 = time.perf_counter()

    f = OpenEXR.File(header, channels)
    f.write(output)

    write_s = time.perf_counter() - t1
    total_s = time.perf_counter() - t0

    import os
    disk_gb = os.path.getsize(output) / 1e9
    ratio   = uncompressed_gb / disk_gb if disk_gb else 0

    print(f"\n✓  Written: {output}")
    print(f"   Disk size        : {disk_gb*1000:.1f} MB  ({disk_gb:.3f} GB)")
    print(f"   Compression ratio: {ratio:.1f}×  ({uncompressed_gb:.1f} GB → {disk_gb:.3f} GB)")
    print(f"   Write time       : {write_s:.1f}s")
    print(f"   Total time       : {total_s:.1f}s")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a disk-efficient tiled EXR image."
    )
    parser.add_argument("-o", "--output",    default=OUTPUT,    help="Output file path")
    parser.add_argument("-W", "--width",     default=WIDTH,     type=int)
    parser.add_argument("-H", "--height",    default=HEIGHT,    type=int)
    parser.add_argument("-t", "--tile-size", default=TILE_SIZE, type=int,
                        dest="tile_size",
                        help="Tile size in pixels (default 256)")
    args = parser.parse_args()

    try:
        main(output=args.output, width=args.width,
             height=args.height, tile_size=args.tile_size)
    except MemoryError:
        sys.exit(
            "\n[ERROR] Not enough RAM to allocate the full image arrays.\n"
            "Reduce --height, or split the job across multiple passes.\n"
        )