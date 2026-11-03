"""Operator-defined bounded-region JPEG diagnostics, never scientific eligibility."""
import numpy as np
from .adapter import require, Scratch, sha, canonical
from .frozen.measurements import primary_p, unavailable_i
from .integrated_arithmetic import integrated_arithmetic


def evaluate_regions(view, definition):
    require(definition['schema'] == 'PROVISIONAL_RECTANGLES_V1', 'REGION_SCHEMA')
    require(type(definition['selection_rationale']) is str and definition['selection_rationale'].strip(),
            'REGION_RATIONALE_REQUIRED')
    h, w = view.common.shape
    masks = []
    for name in ('source', 'background'):
        box = definition[name]
        require(type(box) is list and len(box) == 4 and all(type(v) is int for v in box),
                'REGION_RECTANGLE_REQUIRED')
        x0, y0, x1, y1 = box
        require(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, 'REGION_OUT_OF_BOUNDS')
        mask = np.zeros((h, w), bool)
        mask[y0:y1, x0:x1] = True
        masks.append(mask)
    source, background = masks
    require(not (source & background).any(), 'REGIONS_OVERLAP')
    counts = {name: dict(requested=int(m.sum()), common=int((m & view.common).sum()),
                        mask_sha256=sha(m.tobytes(order='C')))
              for name, m in zip(('source', 'background'), masks)}
    result = dict(schema='PROVISIONAL_JPEG_DIAGNOSTICS', eligibility='NOT_ESTABLISHED',
                  scientific_decision='UNKNOWN', region_status='OPERATOR_DEFINED_UNVERIFIED',
                  definition=definition, definition_sha256=sha(canonical(definition)),
                  mask_encoding='C_ORDER_BOOLEAN_BYTES; shape equals reference image', counts=counts,
                  P_numerical=None, I_numerical=None, reason_codes=['INDEPENDENT_SUPPORT_NOT_ESTABLISHED',
                  'SOURCE_CONTAINMENT_AND_BACKGROUND_REPRESENTATIVENESS_UNKNOWN',
                  'JPEG_RESPONSE_AND_SEQUENTIAL_TIMING_UNVERIFIED'])
    if any((m & ~view.common).any() for m in masks):
        result['reason_codes'].append('REGION_OUTSIDE_COMMON_SUPPORT_NO_PIXELS_DROPPED')
        return result
    monitor = Scratch()
    result['P_numerical'] = primary_p(view, source, monitor)
    result['P_summary_scope'] = 'FULL_COMMON_VALID_FIELD; source_valid_count is ROI-specific'
    integrated = integrated_arithmetic(view.planes, source, background,
                    int(source.sum()), int(background.sum()), monitor, unavailable_i())
    result['I_numerical'] = integrated
    result['source_means'] = [v/int(source.sum()) for v in integrated['gross_signals']]
    gc, nc = integrated['gross_continuum'], integrated['net_continuum']
    result['net_to_gross_continuum'] = nc/gc if gc > 0 else None
    result['reason_codes'].append('RATIO_UNCERTAINTY_UNRESOLVED_EVEN_IF_NUMERIC')
    return result
