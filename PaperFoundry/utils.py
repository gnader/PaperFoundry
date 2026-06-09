"""Utility helpers for PaperFoundry.

Currently provides PDF compression via pypdf (always available) with an
optional Pillow pass for image recompression and an optional Ghostscript
pass for maximum size reduction.
"""

import shutil
import subprocess
from io import BytesIO
from pathlib import Path

try:
    import pypdf
    from pypdf.generic import NameObject, NumberObject
except ImportError:
    raise ImportError("pypdf is required: pip install pypdf")

_CS_TO_MODE = {"/DeviceRGB": "RGB", "/DeviceGray": "L", "/DeviceCMYK": "CMYK"}


def compress_pdf(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    image_quality: int = 75,
    max_dimension: int | None = 1920,
    resolution_scale: float = 100.0,
    use_ghostscript: bool = True,
    gs_quality: str = "screen",
) -> Path:
    """Compress a PDF and write the result to *dst*.

    Three-stage strategy (each stage is independent and additive):

    1. **pypdf pass** — rewrites the PDF compressing content streams and removing duplicate/orphaned objects. Always runs.
    2. **Image recompression** (requires Pillow) — re-encodes every embedded raster image as JPEG at *image_quality*.
       Silently skipped when Pillow is not installed.
    3. **Ghostscript pass** (optional, default enabled) — feeds the result through ``gs``/``gswin64c`` for additional downsampling and font
       subsetting. Silently skipped when Ghostscript is not on PATH.

    Parameters
    ----------
    src:
        Path to the source PDF.
    dst:
        Destination path.  Defaults to ``<stem>_compressed.pdf`` next to *src*.
    image_quality:
        JPEG quality for embedded images (1–95).  Lower = smaller file.
        Default 75 is a good balance for screen reading.
    max_dimension:
        Cap the longest side of every image at this many pixels after all
        scaling.  ``None`` disables the cap.  Default 1920.
    resolution_scale:
        Scale all images by this percentage (e.g. ``50.0`` halves both
        dimensions).  Applied before *max_dimension*.  Default 100 (no change).
    use_ghostscript:
        Try Ghostscript after the pypdf+image pass (default ``True``).
    gs_quality:
        Ghostscript ``-dPDFSETTINGS`` preset — ``"screen"`` (72 dpi, default), ``"ebook"`` (150 dpi), ``"printer"`` (300 dpi).

    Returns
    -------
    Path
        Absolute path to the compressed output file.

    Raises
    ------
    FileNotFoundError
        If *src* does not exist.
    ValueError
        If *src* is not a ``.pdf`` file.
    """
    src = Path(src).resolve()
    if not src.exists():
        raise FileNotFoundError(f"PDF not found: {src}")
    if src.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {src.name}")

    if dst is None:
        dst = src.with_stem(src.stem + "_compressed")
    dst = Path(dst).resolve()

    # --- stage 1: pypdf rewrite -----------------------------------------------
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    for page in writer.pages:
        page.compress_content_streams()

    # --- stage 2: image recompression (Pillow) --------------------------------
    n_images = _recompress_images(writer, image_quality, max_dimension, resolution_scale)

    # Write to a temp file so Ghostscript can read it as a file path.
    _tmp = dst.with_suffix(".pypdf_tmp.pdf")
    with open(_tmp, "wb") as fh:
        writer.write(fh)

    # --- stage 3: Ghostscript -------------------------------------------------
    gs_bin = _find_ghostscript() if use_ghostscript else None
    if gs_bin:
        cmd = [
            gs_bin,
            "-sDEVICE=pdfwrite",
            "-dNOPAUSE",
            "-dBATCH",
            "-dQUIET",
            f"-dPDFSETTINGS=/{gs_quality}",
            f"-sOutputFile={dst}",
            str(_tmp),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        _tmp.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"Ghostscript failed (exit {result.returncode}):\n{result.stderr.strip()}")
    else:
        shutil.move(str(_tmp), dst)

    src_kb = src.stat().st_size / 1024
    dst_kb = dst.stat().st_size / 1024
    ratio = (1 - dst_kb / src_kb) * 100 if src_kb else 0
    scale_note = ""
    if resolution_scale != 100.0:
        scale_note += f" scale={resolution_scale:.0f}%"
    if max_dimension:
        scale_note += f" max={max_dimension}px"
    stages = f"pypdf + {n_images} images recompressed{scale_note}"
    if gs_bin:
        stages += f" + gs:{gs_quality}"
    print(f"{src.name}: {src_kb:.0f} KB -> {dst_kb:.0f} KB ({ratio:.1f}% reduction) [{stages}]")
    return dst


def _recompress_images(
    writer: pypdf.PdfWriter,
    quality: int,
    max_dimension: int | None,
    resolution_scale: float,
) -> int:
    """Re-encode all raster image XObjects as JPEG using Pillow.

    Recurses into Form XObjects (which can nest images arbitrarily deep).
    Returns the number of images successfully replaced.
    """
    try:
        from PIL import Image
    except ImportError:
        return 0

    seen: set[int] = set()
    replaced = 0

    def _process_xobjects(xobjects) -> int:
        nonlocal replaced
        if hasattr(xobjects, "get_object"):
            xobjects = xobjects.get_object()
        count = 0
        for key in list(xobjects.keys()):
            ref = xobjects[key]
            obj = ref.get_object() if hasattr(ref, "get_object") else ref
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            subtype = str(obj.get("/Subtype", ""))
            if subtype == "/Form":
                # Recurse into nested form XObjects.
                nested_res = obj.get("/Resources", {})
                if hasattr(nested_res, "get_object"):
                    nested_res = nested_res.get_object()
                nested_xobj = nested_res.get("/XObject", {})
                if nested_xobj:
                    _process_xobjects(nested_xobj)
                continue
            if subtype != "/Image" or obj.get("/ImageMask"):
                continue

            w = int(obj.get("/Width", 0))
            h = int(obj.get("/Height", 0))

            cs = obj.get("/ColorSpace", "/DeviceRGB")
            if isinstance(cs, pypdf.generic.ArrayObject) and str(cs[0]) == "/Indexed":
                continue

            current_filter = str(obj.get("/Filter", ""))

            try:
                if current_filter == "/DCTDecode":
                    # get_data() on DCTDecode passes through raw JPEG bytes unchanged
                    # (pypdf has no built-in JPEG decoder). Use Pillow directly.
                    img = Image.open(BytesIO(obj._data))
                else:
                    cs_name = str(cs) if not isinstance(cs, pypdf.generic.ArrayObject) else "/DeviceRGB"
                    mode = _CS_TO_MODE.get(cs_name)
                    if mode is None:
                        continue
                    raw = obj.get_data()
                    if len(raw) != w * h * len(mode):
                        continue
                    img = Image.frombytes(mode, (w, h), raw)

                if img.mode == "CMYK":
                    img = img.convert("RGB")
                elif img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")

                # Apply resolution_scale then cap at max_dimension.
                tw, th = img.width, img.height
                if resolution_scale != 100.0:
                    tw = int(tw * resolution_scale / 100)
                    th = int(th * resolution_scale / 100)
                if max_dimension is not None:
                    longest = max(tw, th)
                    if longest > max_dimension:
                        scale = max_dimension / longest
                        tw = int(tw * scale)
                        th = int(th * scale)
                if (tw, th) != (img.width, img.height):
                    img = img.resize((max(tw, 1), max(th, 1)), Image.LANCZOS)

                out_cs = "/DeviceRGB" if img.mode == "RGB" else "/DeviceGray"
                buf = BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True)
                jpeg = buf.getvalue()

                if len(jpeg) >= len(obj._data):
                    continue

                obj._data = jpeg
                obj[NameObject("/Filter")] = NameObject("/DCTDecode")
                obj[NameObject("/Width")] = NumberObject(img.width)
                obj[NameObject("/Height")] = NumberObject(img.height)
                obj[NameObject("/Length")] = NumberObject(len(jpeg))
                obj[NameObject("/ColorSpace")] = NameObject(out_cs)
                for drop in ("/DecodeParms", "/SMask"):
                    if drop in obj:
                        del obj[NameObject(drop)]

                replaced += 1
            except Exception:
                continue
        return count

    for page in writer.pages:
        resources = page.get("/Resources", {})
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        xobjects = resources.get("/XObject", {})
        if xobjects:
            _process_xobjects(xobjects)

    return replaced


def _find_ghostscript() -> str | None:
    for name in ("gs", "gswin64c", "gswin32c"):
        if shutil.which(name):
            return name
    return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m PaperFoundry.utils <input.pdf> [output.pdf] [quality=75] [max_dim=1920,0=off] [scale%=100]")
        sys.exit(1)

    q = int(sys.argv[3]) if len(sys.argv) > 3 else 75
    max_dim = int(sys.argv[4]) if len(sys.argv) > 4 else 1920
    scale = float(sys.argv[5]) if len(sys.argv) > 5 else 100.0
    out = compress_pdf(
        sys.argv[1],
        sys.argv[2] if len(sys.argv) > 2 else None,
        image_quality=q,
        max_dimension=max_dim if max_dim > 0 else None,
        resolution_scale=scale,
    )
    print(f"Output: {out}")
