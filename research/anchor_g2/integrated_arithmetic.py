"""Exact terminal arithmetic block from frozen primary_i; eligibility is external."""
import math
from .frozen.measurements import _bounded_sum

def integrated_arithmetic(planes, r, b, nr, nb, monitor, out):
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
