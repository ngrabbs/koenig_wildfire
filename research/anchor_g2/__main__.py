import argparse
import json
from .adapter import evaluate

parser = argparse.ArgumentParser(description='Read-only Option A diagnostics; never a fire classifier.')
parser.add_argument('request', help='Explicit triplet/provenance JSON manifest')
args = parser.parse_args()
result = evaluate(args.request)
print(json.dumps(result, indent=2, allow_nan=False))
raise SystemExit(0 if result['status'] == 'DIAGNOSTIC_ONLY' else 2)
