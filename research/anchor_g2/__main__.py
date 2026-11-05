import argparse
import json
from .adapter import evaluate
from pathlib import Path

parser = argparse.ArgumentParser(description='Read-only Option A diagnostics; never a fire classifier.')
parser.add_argument('request', help='Explicit triplet/provenance JSON manifest')
parser.add_argument('--exploratory-jpeg', action='store_true',
                    help='Explicit uncalibrated JPEG code-value diagnostics; never the strict pathway')
parser.add_argument('--output', help='New JSON file; refuses existing paths, including inputs')
parser.add_argument('--processing-mode', choices=('registered-boxes', 'independent-boxes', 'whole-frame'),
                    help='JPEG processing path; default is registered-boxes (or manifest selection)')
args = parser.parse_args()
if args.processing_mode and not args.exploratory_jpeg:
    parser.error('--processing-mode requires --exploratory-jpeg')
if args.output and Path(args.output).exists():
    parser.error('--output must name a new file')
if args.exploratory_jpeg:
    from .exploratory_jpeg import evaluate_jpeg
    result = evaluate_jpeg(args.request, opt_in=True, processing_mode=args.processing_mode)
else:
    result = evaluate(args.request)
encoded = json.dumps(result, indent=2, allow_nan=False)
if args.output:
    with open(args.output, 'x', encoding='utf-8') as stream:
        stream.write(encoded + '\n')
else:
    print(encoded)
raise SystemExit(0 if result['status'] == 'DIAGNOSTIC_ONLY' else 2)
