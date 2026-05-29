# generate_exr.py

A Python script that generates a large, disk-efficient OpenEXR image at **16384 × 65536 pixels** using tiled storage, half-precision floats, and ZIP compression.

---

## Features

- **HALF (float16) pixels** — half the bytes of float32 with no loss of HDR range for most use cases
- **Tiled storage** — 256×256 tiles enable per-tile compression and fast random-access reads by any EXR-aware tool
- **ZIP compression** — lossless deflate; consistently the best compression ratio for EXR tile data
- **Tile-by-tile fill** — pixel arrays are filled incrementally to keep memory pressure stable
- **Progress reporting** — prints fill percentage and final disk stats on completion

---

## Requirements

- Python 3.10+
- [openexr](https://pypi.org/project/openexr/) ≥ 3.4
- [numpy](https://pypi.org/project/numpy/)

```bash
pip install openexr numpy
```

---

## Usage

### Default (16384 × 65536)

```bash
python generate_exr.py
```

### Custom size and output path

```bash
python generate_exr.py -W 16384 -H 65536 -o output.exr
```

### All options

```
-o, --output       Output file path          (default: output.exr)
-W, --width        Image width in pixels     (default: 16384)
-H, --height       Image height in pixels    (default: 65536)
-t, --tile-size    Tile size in pixels       (default: 256)
```

---

## Output

The script prints a summary when finished:

```
Image      : 16384 × 65536 px
Tile size  : 256 × 256 px  (64 × 256 = 16384 tiles)
Pixel type : HALF (float16)
Compression: ZIP
Output     : output.exr

Uncompressed size: ~6.1 GB  (WARNING: large allocation)
Allocating channel arrays …
Filling tiles …
  ...
✓  Written: output.exr
   Disk size        : 120.4 MB  (0.120 GB)
   Compression ratio: 50.7×  (6.1 GB → 0.120 GB)
   Write time       : 18.2s
   Total time       : 42.6s
```

> Actual disk size depends on content complexity. The bundled UV-gradient test pattern typically achieves 40–65× compression.

---

## Memory requirements

At full resolution, three float16 channels require approximately **6 GB of RAM** before compression. If your machine cannot hold this:

- Reduce `--height` to process the image in horizontal strips
- Use memory-mapped NumPy arrays (`np.memmap`) instead of in-memory arrays
- Process and write one tile row at a time using the lower-level `OpenEXR.OutputFile` API

---

## Customising the pixel data

The `generate_tile()` function is the only place that produces pixel values. Replace its body with your own data source — a disk read, a network stream, a render result, etc.:

```python
def generate_tile(tx, ty, tile_w, tile_h, total_w, total_h):
    # Return a dict of float16 arrays keyed by channel name
    data = load_my_data(tx * TILE_SIZE, ty * TILE_SIZE, tile_w, tile_h)
    return {
        "R": data[..., 0].astype(np.float16),
        "G": data[..., 1].astype(np.float16),
        "B": data[..., 2].astype(np.float16),
    }
```

---

## EXR header written

| Attribute | Value |
|---|---|
| `type` | `tiledimage` |
| `compression` | `ZIP_COMPRESSION` |
| `tiles` | `TileDescription(256, 256, ONE_LEVEL, ROUND_DOWN)` |
| `pixelAspectRatio` | `1.0` |
| Channels | R, G, B — all `HALF` |

---

## Compression options

If you need to trade lossless accuracy for even smaller files, swap `ZIP_COMPRESSION` in the header for one of the alternatives below:

| Compression | Type | Notes |
|---|---|---|
| `ZIP_COMPRESSION` | Lossless | Best ratio for varied data; default in this script |
| `ZIPS_COMPRESSION` | Lossless | ZIP applied per scanline rather than per tile |
| `PIZ_COMPRESSION` | Lossless | Often better for noisy/photographic data |
| `DWAA_COMPRESSION` | Lossy | Very small files; perceptual quality loss |
| `DWAB_COMPRESSION` | Lossy | Like DWAA with larger block size |

---

## Further reading

**[Uncovering an iOS 26 ImageIO Vulnerability — ZygoSec Blog](https://zygosec.com/blog)** (Billy Ellis, May 2026)

This script was originally developed as part of the research described in the post above. The blog covers a root-cause analysis and working PoC for a vulnerability patched in iOS 26.5, inside Apple's `ImageIO` framework — specifically in `EXRReadPlugin::decodeBlockAppleEXR`.

The bug was an unchecked integer overflow in the buffer-size calculation:

```c
// Vulnerable (iOS 26.4.2)
v16  = 4 * this->dword260 * this->dwordF4;   // width × height × channels
size = v16 * this->unsigned_int138;
buf  = malloc_type_malloc(size, ...);         // size can wrap to 0
```

The width (`0x4000` / 16384) and height (`0x10000` / 65536) values used in that multiplication come directly from the EXR image's `dataWindow` header field. This script generates an image with exactly those dimensions, with pixel data large enough to overflow the 32-bit accumulator:

```
0x4 * 0x4000 * 0x10000 = 0x1_00000000  →  truncated to 0x0
```

`malloc(0)` returns a valid 16-byte allocation; the subsequent pixel copy then overflows that buffer, eventually corrupting heap memory with attacker-controlled pixel bytes (`0x41 0x41 …`).

The fix in iOS 26.5 adds an `is_mul_ok` guard around the multiplication before it is passed to `malloc_type_malloc`.

---

## License

MIT