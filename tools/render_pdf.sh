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

# pandoc resolves relative paths in include-in-header against CWD,
# so run pandoc from PROJECT_ROOT.
cd "$PROJECT_ROOT"
pandoc "$WORK/processed.md" \
    -o "$OUTPUT" \
    --defaults "$PANDOC_YAML" \
    --highlight-style=tango

echo "Output: $OUTPUT"
