#!/usr/bin/env bash
#
# render_pdf.sh — render a markdown doc (with embedded mermaid diagrams) to PDF.
#
# Usage:
#   tools/render_pdf.sh input.md [output.pdf] [pandoc-yaml-config]
#
# Defaults:
#   output.pdf       = docs/build/<basename>.pdf
#   pandoc-yaml-cfg  = tools/pandoc/pandoc-ipad-readable.yaml
#
# Pipeline:
#   1. Extract every ```mermaid block from the markdown.
#   2. Render each diagram to PNG via the mermaid.ink HTTP API
#      (requires internet — no headless browser needed).
#   3. Splice the PNGs back into the markdown.
#   4. Run pandoc with the project's pandoc YAML defaults.
#
# Requirements:
#   - python3 with mermaid-py  (pip install mermaid-py)
#   - pandoc
#   - lualatex  (TeX Live; on Debian: texlive-luatex texlive-fonts-extra)
#   - internet access (for mermaid.ink)
#
# Environment:
#   PAYLOAD_PDF_MAX_PX  Longest edge, in pixels, that a photo is downscaled to
#                      before embedding (default 1600; set 0 to disable).
#                      Full-resolution phone photos make a 24 MB PDF; 1600 px
#                      is well past what 300 dpi print needs and lands ~2 MB.
#
# Adapted from /workspace/notes/reports/render_mermaid_pdf.sh

set -euo pipefail

# Resolve project root from this script's location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT="${1:?Usage: $0 input.md [output.pdf] [pandoc-yaml.yaml]}"

# Default output is docs/build/<basename>.pdf next to the project root.
DEFAULT_OUT="$PROJECT_ROOT/docs/build/$(basename "${INPUT%.md}").pdf"
OUTPUT="${2:-$DEFAULT_OUT}"

PANDOC_YAML="${3:-$PROJECT_ROOT/tools/pandoc/pandoc-ipad-readable.yaml}"

mkdir -p "$(dirname "$OUTPUT")"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMGDIR="$WORK/images"
mkdir -p "$IMGDIR"

cat > "$WORK/preprocess.py" <<'PYEOF'
import re, sys, os, warnings
warnings.filterwarnings("ignore")

input_file, imgdir, work = sys.argv[1], sys.argv[2], sys.argv[3]

with open(input_file, 'r') as f:
    content = f.read()

pattern = r'```(?:\{\.mermaid\}|mermaid)\s*\n(.*?)```'
matches = list(re.finditer(pattern, content, re.DOTALL))

if not matches:
    with open(os.path.join(work, 'processed.md'), 'w') as f:
        f.write(content)
    print("No mermaid blocks found, passing through.", file=sys.stderr)
    sys.exit(0)

print(f"Found {len(matches)} mermaid diagram(s)", file=sys.stderr)

from mermaid import Mermaid
from mermaid.graph import Graph

result = content
for i, match in enumerate(reversed(matches)):
    idx = len(matches) - 1 - i
    diagram_code = match.group(1).strip()
    img_path = os.path.join(imgdir, f"diagram_{idx}.png")

    try:
        g = Graph(f'diagram_{idx}', diagram_code)
        m = Mermaid(g)
        m.to_png(img_path)
        img_ref = f'\n![Diagram {idx + 1}]({img_path}){{ width=85% }}\n'
        result = result[:match.start()] + img_ref + result[match.end():]
        print(f"  Rendered diagram {idx + 1}/{len(matches)}", file=sys.stderr)
    except Exception as e:
        print(f"  WARNING: Failed to render diagram {idx + 1}: {e}", file=sys.stderr)

with open(os.path.join(work, 'processed.md'), 'w') as f:
    f.write(result)

print("All diagrams rendered.", file=sys.stderr)
PYEOF

python3 "$WORK/preprocess.py" "$INPUT" "$IMGDIR" "$WORK"

# The processed markdown lives in $WORK, but its image links are written
# relative to the *source* document (e.g. docs/payload_build.md refers to
# img/payload1.jpg).
INPUT_DIR="$(cd "$(dirname "$INPUT")" && pwd)"

# Downscale oversized photos into $WORK so the PDF stays a sane size.
# Originals on disk are never touched.
MAX_PX="${PAYLOAD_PDF_MAX_PX:-1600}"

cat > "$WORK/downscale.py" <<'PYEOF2'
import os, re, sys

md_file, imgdir, input_dir, project_root, max_px = sys.argv[1:6]
max_px = int(max_px)

if max_px <= 0:
    sys.exit(0)

try:
    from PIL import Image
except ImportError:
    print("  Pillow not installed - embedding images at full size.", file=sys.stderr)
    sys.exit(0)

RASTER = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

with open(md_file) as f:
    content = f.read()

def resolve(link):
    if os.path.isabs(link):
        return link if os.path.exists(link) else None
    for base in (input_dir, project_root):
        cand = os.path.join(base, link)
        if os.path.exists(cand):
            return cand
    return None

seen = {}
saved_before = saved_after = 0

def shrink(match):
    global saved_before, saved_after
    link = match.group(2).strip()
    if link.startswith(("http://", "https://", "data:")):
        return match.group(0)
    if os.path.splitext(link)[1].lower() not in RASTER:
        return match.group(0)

    src = resolve(link)
    if src is None:
        return match.group(0)

    attrs = match.group(3) or ""

    if src in seen:
        return f"![{match.group(1)}]({seen[src]}){attrs}"

    try:
        im = Image.open(src)
    except Exception as e:
        print(f"  WARNING: could not open {link}: {e}", file=sys.stderr)
        return match.group(0)

    if max(im.size) <= max_px:
        return match.group(0)

    before = os.path.getsize(src)
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    out = os.path.join(imgdir, "scaled_%d_%s" % (len(seen), os.path.basename(src)))
    out = os.path.splitext(out)[0] + ".jpg"
    im.save(out, "JPEG", quality=88, optimize=True, progressive=True)

    after = os.path.getsize(out)
    saved_before += before
    saved_after += after
    seen[src] = out
    print("  %s: %dx%d, %.1f MB -> %.1f MB"
          % (os.path.basename(src), im.size[0], im.size[1],
             before / 1e6, after / 1e6), file=sys.stderr)
    return f"![{match.group(1)}]({out}){attrs}"

content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)(\{[^}]*\})?', shrink, content)

with open(md_file, "w") as f:
    f.write(content)

if seen:
    print("Downscaled %d image(s) to <=%d px: %.1f MB -> %.1f MB"
          % (len(seen), max_px, saved_before / 1e6, saved_after / 1e6),
          file=sys.stderr)
PYEOF2

python3 "$WORK/downscale.py" "$WORK/processed.md" "$IMGDIR" "$INPUT_DIR" "$PROJECT_ROOT" "$MAX_PX"

# pandoc resolves relative paths in include-in-header against CWD,
# so run pandoc from PROJECT_ROOT. Give it a resource path covering the
# source dir, the project root, and $WORK so document images and the
# rendered mermaid PNGs both resolve.
cd "$PROJECT_ROOT"
pandoc "$WORK/processed.md" \
    -o "$OUTPUT" \
    --defaults "$PANDOC_YAML" \
    --resource-path=".:$INPUT_DIR:$PROJECT_ROOT:$WORK" \
    --highlight-style=tango

echo "Output: $OUTPUT"
