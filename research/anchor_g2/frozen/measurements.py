"""Separate P and unit I calculations on one immutable view; no classifier."""
import math
import numpy as np
from .constants import P_METHOD,I_METHOD
from .immutable import FrozenArray
from .errors import ContractViolation

def _map_descriptor(a, units, monitor):
    monitor.own("map_publication_copy",int(a.nbytes))
    try:
        desc=FrozenArray(a).descriptor(units)
        desc["retention"]="HASH_ONLY_NOT_RETAINED_IN_RESULT"
        desc["finite_count"]=int(np.isfinite(a).sum())
        desc["invalid_representation"]="FALSE_MASK" if a.dtype==bool else "NAN_WITH_VALID_MASK"
        return desc
    finally:
        monitor.release("map_publication_copy")

def unavailable_p(reason="GEOMETRY_NOT_ACCEPTED"):
    return {"method_id":P_METHOD,"status":"NOT_EVALUATED","reason":reason,
      "numeric_valid":False,"denominator_resolved":"UNKNOWN","scientific_decision":"UNKNOWN",
      "geometry_id":None,"lease_id":None,"common_count":0,"additive_count":0,"valid_count":0,
      "frame_count":0,"source_requested_count":0,"source_valid_count":0,
      "median_delta":None,"p99_delta":None,"mean_delta":None,
      "valid_fraction_common":None,"valid_fraction_frame":None,
      "continuum":None,"excess":None,"delta":None,"valid_mask":None,"reason_masks":None,
      "radiometric_compatibility":"UNKNOWN","covariance":None,
      "uncertainty_status":"UNSUPPORTED_IN_MINIMUM_B2"}

def primary_p(view,source,monitor):
    with monitor.stage("P_same_location"):
        monitor.count("P")
        planes=view.planes;common=view.common
        on,low,high=planes
        if any(a.dtype!=np.float32 or a.shape!=common.shape for a in planes):
            raise ContractViolation("INPUT_CONTRACT_INVALID","P representation")
        c=(low+2*high)/3
        d=on-c
        additive=common&np.isfinite(on)&np.isfinite(low)&np.isfinite(high)&np.isfinite(c)&np.isfinite(d)
        valid=additive&(c>=4)
        reasons={"outside_common":~common,
                 "nonfinite":common&~(np.isfinite(on)&np.isfinite(low)&np.isfinite(high)&np.isfinite(c)&np.isfinite(d)),
                 "nonpositive_continuum":additive&(c<=0),
                 "positive_below_floor":additive&(c>0)&(c<4)}
        delta=np.full(on.shape,np.nan,np.float32)
        np.divide(d,c,out=delta,where=valid)
        monitor.own("P_work_arrays",sum(a.nbytes for a in [c,d,delta,additive,valid])+sum(a.nbytes for a in reasons.values()))
        try:
            n=int(valid.sum());nc=int(common.sum());total=int(common.size)
            result=unavailable_p()
            result.update(status=("DEFINED_DIAGNOSTIC" if n==nc else "PARTIAL_DIAGNOSTIC") if n else "UNDEFINED",
              reason="NUMERICAL_ONLY" if n else "BELOW_LEGACY_NUMERIC_FLOOR",
              numeric_valid=bool(n),geometry_id=view.provenance()["geometry_id"],lease_id=view.identity,
              common_count=nc,additive_count=int(additive.sum()),valid_count=n,frame_count=total,
              source_requested_count=int(source.sum()),source_valid_count=int((source&valid).sum()),
              valid_fraction_common=n/nc if nc else None,valid_fraction_frame=n/total if total else None)
            if not n and additive.any() and not (c[additive]>0).any():
                result["reason"]="DENOM_NONPOSITIVE"
            result["reason_masks"]={k:_map_descriptor(v,"BOOLEAN",monitor) for k,v in reasons.items()}
            if n:
                # Float64 summaries; mask selection is explicitly owned temporary memory.
                values=delta[valid].astype(np.float64)
                monitor.own("P_summary_selection",int(values.nbytes))
                try:
                    result.update(median_delta=float(np.median(values)),p99_delta=float(np.percentile(values,99)),
                                  mean_delta=float(values.mean(dtype=np.float64)))
                finally:
                    monitor.release("P_summary_selection")
            result.update(continuum=_map_descriptor(c,"CORRECTED_DN",monitor),
                          excess=_map_descriptor(d,"CORRECTED_DN",monitor),
                          delta=_map_descriptor(delta,"DIMENSIONLESS",monitor),
                          valid_mask=_map_descriptor(valid,"BOOLEAN",monitor))
            return result
        finally:
            monitor.release("P_work_arrays")

def unavailable_i(reason="GEOMETRY_NOT_ACCEPTED"):
    return {"method_id":I_METHOD,"status":"NOT_EVALUATED","reason":reason,
      "numeric_valid":False,"additive_status":"NOT_EVALUATED","denominator_resolved":"UNKNOWN",
      "geometry_id":None,"lease_id":None,"selection_id":None,
      "source_requested_count":0,"source_observable_count":0,"background_requested_count":0,
      "background_observable_count":0,"source_complete":"UNKNOWN",
      "gross_signals":None,"background_means":None,"net_signals":None,
      "gross_continuum":None,"gross_excess":None,"gross_numeric_ratio":None,
      "net_continuum":None,"additive_excess":None,"numeric_ratio":None,"supported_ratio":None,
      "flags":[],"units":"CORRECTED_DN_PIXELS","channel_order":[770,750,780],
      "radiometric_compatibility":"UNKNOWN","scientific_decision":"UNKNOWN",
      "covariance":None,"uncertainty_status":"UNSUPPORTED_IN_MINIMUM_B2"}

def _bounded_sum(plane,mask,monitor):
    total=np.float64(0)
    for start in range(0,plane.shape[0],32):
        selected=plane[start:start+32][mask[start:start+32]]
        monitor.own("I_reduction_stripe",int(selected.nbytes))
        try:
            total+=selected.sum(dtype=np.float64)
        finally:
            monitor.release("I_reduction_stripe")
    return float(total)

def _finite_on_mask(plane,mask,monitor):
    for start in range(0,plane.shape[0],32):
        selected=plane[start:start+32][mask[start:start+32]]
        monitor.own("I_validation_stripe",int(selected.nbytes+selected.size))
        try:
            if not np.isfinite(selected).all():
                return False
        finally:
            monitor.release("I_validation_stripe")
    return True

def primary_i(view,payload,monitor,containment_status="UNKNOWN"):
    with monitor.stage("I_unit_source_background"):
        monitor.count("I")
        planes=view.planes;common=view.common
        r=payload.source.view();b=payload.background.view()
        out=unavailable_i()
        out.update(status="INELIGIBLE",geometry_id=view.provenance()["geometry_id"],lease_id=view.identity,
                   selection_id=payload.selection_id)
        if r.dtype!=bool or b.dtype!=bool or r.shape!=common.shape or b.shape!=common.shape:
            out["reason"]="INPUT_CONTRACT_INVALID";return out
        nr,nb=int(r.sum()),int(b.sum())
        out.update(source_requested_count=nr,background_requested_count=nb,
                   source_observable_count=int((r&common).sum()),
                   background_observable_count=int((b&common).sum()),
                   source_complete=containment_status)
        if not nr or not nb:
            out.update(status="UNDEFINED",reason="EMPTY_SUPPORT");return out
        if (r&b).any():
            out["reason"]="BACKGROUND_UNSUPPORTED";return out
        if containment_status=="UNKNOWN":
            out["reason"]="SOURCE_CONTAINMENT_UNKNOWN";return out
        if containment_status!="PASS_GENERATED_ONLY" or np.any(r&~common):
            out["reason"]="SOURCE_TRUNCATED";return out
        if np.any(b&~common):
            out["reason"]="BACKGROUND_UNSUPPORTED";return out
        if not all(_finite_on_mask(a,r,monitor) and _finite_on_mask(a,b,monitor) for a in planes):
            out["reason"]="NUMERIC_NONFINITE";return out
        gross=[];beta=[];net=[]
        for a in planes:
            monitor.count("I_source_reduction");t=_bounded_sum(a,r,monitor)
            monitor.count("I_background_reduction");bg=_bounded_sum(a,b,monitor)/nb
            gross.append(t);beta.append(bg);net.append(t-nr*bg)
        gc=(gross[1]+2*gross[2])/3;gd=gross[0]-gc
        nc=(net[1]+2*net[2])/3;nd=net[0]-nc
        if not all(math.isfinite(x) for x in gross+beta+net+[gc,gd,nc,nd]):
            out["reason"]="NUMERIC_NONFINITE";return out
        out.update(status="UNDEFINED",reason="DENOM_UNRESOLVED" if nc>0 else "DENOM_NONPOSITIVE",
           numeric_valid=True,additive_status="DEFINED_DIAGNOSTIC",
           gross_signals=gross,background_means=beta,net_signals=net,
           gross_continuum=gc,gross_excess=gd,gross_numeric_ratio=gd/gc if gc>0 else None,
           net_continuum=nc,additive_excess=nd,numeric_ratio=nd/nc if nc>0 else None)
        if any(x<0 for x in net):
            out["flags"].append("SOURCE_SIGNAL_UNRESOLVED")
        # No covariance model is implemented; no numeric ratio is promoted to supported_ratio.
        return out
