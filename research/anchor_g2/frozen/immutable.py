"""Bytes-owned immutable array views and explicit event lease lifetime."""
import numpy as np
from .errors import ContractViolation
from .runtime import digest, canonical

class FrozenArray:
    __slots__ = ("_bytes", "_shape", "_dtype", "_hash")
    def __init__(self, value):
        a = np.ascontiguousarray(value)
        if a.dtype.hasobject:
            raise ContractViolation("INPUT_CONTRACT_INVALID", "object array")
        self._bytes = a.tobytes(order="C")
        self._shape = tuple(a.shape)
        self._dtype = a.dtype.str
        self._hash = digest(self._bytes)
    @property
    def nbytes(self):
        return len(self._bytes)
    def view(self):
        a = np.frombuffer(self._bytes, dtype=np.dtype(self._dtype)).reshape(self._shape)
        if a.flags.writeable:
            raise ContractViolation("BUFFER_LIFETIME_VIOLATION")
        return a
    def descriptor(self, units):
        return {"sha256": self._hash, "shape": list(self._shape), "dtype": self._dtype,
                "units": units, "byte_order": "DTYPE_EXPLICIT", "layout": "C",
                "retention": "LEASE_OR_DESCRIPTOR_ONLY"}
    def verify(self):
        if digest(self._bytes) != self._hash:
            raise ContractViolation("SOURCE_IDENTITY_CHANGED")

_LEASE_SEAL = object()

class SharedLease:
    __slots__ = ("_planes", "_masks", "_common", "_active", "_monitor",
                 "_event_id", "_geometry_id", "_id", "_names")
    def __init__(self, planes, masks, common, event_id, geometry_id, monitor, *, seal):
        if seal is not _LEASE_SEAL:
            raise ContractViolation("GEOMETRY_NOT_ACCEPTED", "lease issuer")
        self._monitor = monitor
        self._names = []
        self._planes = tuple(FrozenArray(a) for a in planes)
        self._masks = tuple(FrozenArray(a) for a in masks)
        self._common = FrozenArray(common)
        self._active = True
        self._event_id, self._geometry_id = event_id, geometry_id
        all_arrays = self._planes + self._masks + (self._common,)
        for k, a in enumerate(all_arrays):
            name = "lease_" + str(k)
            monitor.own(name, a.nbytes)
            self._names.append(name)
        self._id = digest(canonical({"event": event_id, "geometry": geometry_id,
                    "arrays": [a.descriptor("COUNTS_OR_BOOL") for a in all_arrays]}))
    def _check(self):
        if not self._active:
            raise ContractViolation("BUFFER_LIFETIME_VIOLATION", "closed lease")
        for a in self._planes+self._masks+(self._common,):
            a.verify()
    @property
    def planes(self):
        self._check()
        return tuple(a.view() for a in self._planes)
    @property
    def common(self):
        self._check()
        return self._common.view()
    @property
    def identity(self):
        self._check()
        return self._id
    def provenance(self):
        self._check()
        return {"lease_id": self._id, "geometry_id": self._geometry_id,
                "event_id": self._event_id,
                "planes": [a.descriptor("CORRECTED_DN") for a in self._planes],
                "masks": [a.descriptor("BOOLEAN") for a in self._masks],
                "common": self._common.descriptor("BOOLEAN"), "crop_offset": [0, 0],
                "channel_order": [[0,770],[1,750],[2,780]], "writable_aliases": False}
    def close(self):
        if self._active:
            self._active = False
            self._planes, self._masks, self._common = (), (), None
            for name in self._names:
                self._monitor.release(name)
            self._names = []

def issue_accepted_lease(planes, masks, common, event_id, geometry, monitor):
    if geometry["state"] != "ACCEPTED":
        raise ContractViolation("GEOMETRY_NOT_ACCEPTED")
    return SharedLease(planes, masks, common, event_id, geometry["geometry_id"],
                       monitor, seal=_LEASE_SEAL)

def issue_unit_math_view(planes, common, event_id, custody_sha256, monitor):
    """Distinct unit-math view; never a compatible accepted GeometryRecord."""
    return SharedLease(planes,[common,common,common],common,event_id,
                       "UNIT_MATH_ONLY:"+custody_sha256,monitor,seal=_LEASE_SEAL)
