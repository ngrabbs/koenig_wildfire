"""Unregistered JPEG reductions. No geometry or scientific eligibility claims."""
import math
import numpy as np
from .adapter import Scratch, canonical, require, sha
from .frozen.measurements import _bounded_sum


def spectral_summary(signals, cancellation_scale=None):
    """Same 1:2 continuum weighting for native per-camera totals.

    Frozen integrated_arithmetic requires one shared mask/count, so it cannot
    represent different native camera apertures. Reuse its bounded reductions
    and the exact weighting here without altering that frozen shared-grid path.
    """
    require(len(signals) == 3 and all(math.isfinite(x) for x in signals), 'NUMERIC_NONFINITE')
    c = (signals[1] + 2 * signals[2]) / 3
    d = signals[0] - c
    scale = max(1.0, sum(abs(x) for x in signals), cancellation_scale or 0.0)
    roundoff_bound = 8 * np.finfo(np.float64).eps * scale
    reason = ('DENOM_NONPOSITIVE' if c <= 0 else
              'DENOM_NEAR_ROUNDOFF' if c <= roundoff_bound else 'DENOM_UNCERTAINTY_UNRESOLVED')
    return dict(signals=signals, continuum=c, excess=d,
                numeric_ratio=d/c if c > roundoff_bound else None,
                supported_ratio=None, denominator_reason=reason,
                roundoff_bound=float(roundoff_bound))


def rectangle(box, shape):
    require(type(box) is list and len(box) == 4 and all(type(x) is int for x in box),
            'REGION_RECTANGLE_REQUIRED')
    x0, y0, x1, y1 = box
    h, w = shape
    require(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, 'REGION_OUT_OF_BOUNDS')
    mask = np.zeros(shape, bool)
    mask[y0:y1, x0:x1] = True
    return mask


def region_stats(image, mask, monitor):
    count = int(mask.sum())
    total = _bounded_sum(image, mask, monitor)
    return dict(pixel_count=count, sum=total, mean=total/count,
                zero_count=int(((image == 0) & mask).sum()),
                saturation_ge254_count=int(((image >= 254) & mask).sum()),
                mask_sha256=sha(mask.tobytes(order='C')))


def evaluate_native(images, channels, mode, definition=None):
    monitor = Scratch()
    result = dict(schema='PROVISIONAL_JPEG_DIAGNOSTICS', processing_mode=mode,
                  measurement_kind='GROSS_WHOLE_FRAME' if mode == 'whole-frame' else 'NATIVE_BACKGROUND_SUBTRACTED',
                  scientific_decision='UNKNOWN', eligibility='NOT_ESTABLISHED',
                  common_support='NOT_DEFINED_WITHOUT_REGISTRATION', P_numerical=None,
                  channels=[], gross=None, net=None, reason_codes=[
                      'CAMERA_FIELD_OF_VIEW_AND_PIXEL_SCALE_UNVERIFIED',
                      'JPEG_RESPONSE_AND_SEQUENTIAL_TIMING_UNVERIFIED',
                      'NO_RADIOMETRIC_OR_CORRESPONDENCE_VALIDATION'])
    definitions = {}
    if mode == 'independent-boxes':
        require(type(definition) is dict and definition.get('schema') == 'INDEPENDENT_RECTANGLES_V1',
                'INDEPENDENT_CAMERA_REGIONS_REQUIRED')
        for key in ('selection_rationale', 'correspondence_description'):
            require(type(definition.get(key)) is str and definition[key].strip(), 'REGION_DESCRIPTION_REQUIRED')
        entries = definition['cameras']
        require(type(entries) is list and len(entries) == 3, 'THREE_CAMERA_REGION_DEFINITIONS_REQUIRED')
        for entry in entries:
            key = (entry['camera_id'], entry['wavelength_nm'])
            require(key not in definitions, 'DUPLICATE_CAMERA_REGION')
            require(type(entry.get('source_containment_note')) is str and entry['source_containment_note'].strip(),
                    'SOURCE_CONTAINMENT_NOTE_REQUIRED')
            definitions[key] = entry
        require(set(definitions) == {(c['camera_id'], c['wavelength_nm']) for c in channels},
                'CAMERA_REGION_MAPPING_MISMATCH')
        result.update(definition=definition, definition_sha256=sha(canonical(definition)))
        result['reason_codes'].append('SOURCE_CONTAINMENT_AND_BACKGROUND_COMPARABILITY_UNVERIFIED')
    else:
        require(mode == 'whole-frame' and definition is None, 'INVALID_NATIVE_MODE')
        result['reason_codes'].extend(['NO_BACKGROUND_SUBTRACTION', 'SMALL_SOURCE_WHOLE_FRAME_DILUTION'])
    gross, net, background_totals = [], [], []
    saturated = False
    for image, channel in zip(images, channels):
        band = dict(camera_id=channel['camera_id'], wavelength_nm=channel['wavelength_nm'],
                    shape=list(image.shape), frame_pixels=int(image.size))
        if mode == 'whole-frame':
            band['source'] = region_stats(image, np.ones(image.shape, bool), monitor)
            band['background'] = None
        else:
            entry = definitions[(channel['camera_id'], channel['wavelength_nm'])]
            source = rectangle(entry['source'], image.shape)
            background = rectangle(entry['background'], image.shape)
            require(not (source & background).any(), 'REGIONS_OVERLAP')
            band.update(source=region_stats(image, source, monitor),
                        background=region_stats(image, background, monitor))
            subtraction = band['source']['pixel_count'] * band['background']['mean']
            band['net_signal'] = band['source']['sum'] - subtraction
            net.append(band['net_signal'])
            background_totals.append(subtraction)
            saturated |= band['background']['saturation_ge254_count'] > 0
        gross.append(band['source']['sum'])
        saturated |= band['source']['saturation_ge254_count'] > 0
        result['channels'].append(band)
    result['gross'] = spectral_summary(gross)
    if mode == 'independent-boxes':
        result['net'] = spectral_summary(net, sum(abs(x) for x in gross + background_totals))
        gc = result['gross']['continuum']
        result['net_to_gross_continuum'] = result['net']['continuum']/gc if gc > 0 else None
        if any(x < 0 for x in net):
            result['reason_codes'].append('NEGATIVE_NET_CHANNEL_SIGNAL')
        if len({c['source']['pixel_count'] for c in result['channels']}) != 1:
            result['reason_codes'].append('UNEQUAL_SOURCE_PIXEL_COUNTS_NO_AREA_NORMALIZATION')
    if len({tuple(a.shape) for a in images}) != 1:
        result['reason_codes'].append('DIFFERENT_IMAGE_DIMENSIONS_NO_RESAMPLING')
    if saturated:
        result['reason_codes'].append('SATURATION_PRESENT_NO_PIXELS_REMOVED')
    for metric in (result['gross'], result['net']):
        if metric is not None:
            if saturated:
                metric['numeric_ratio'] = None
                metric['denominator_reason'] = 'RATIO_WITHHELD_SATURATION'
            result['reason_codes'].append(metric['denominator_reason'])
    return result
