"""Pinned pure extraction. No dispatcher, authorization, ORB or service imports.
Structural compatibility only; no parity execution in B2.
"""
import hashlib,json
import cv2
import numpy as np
class MethodError(ValueError):pass
class InputIntegrityError(ValueError):pass
PREPARATION={'domain':'CONDITIONALLY_CORRECTED_FIXEDPOINT_TO_8BIT_COPY_V1','conversion':'floor_clip_u16_div256','normalization_percentiles':[1,99],'clahe_clip':2.0,'clahe_grid':[8,8],'work_width':1024,'resize':'INTER_AREA','coordinates':'PIXEL_CENTER_X_PLUS_HALF_OVER_SCALE_MINUS_HALF','features':4000,'ratio':0.75,'mutual':True,'descriptor_mask_erosion_work_px':16,'rng_seed':0,'threads':1}
ALL_GATES=['correspondence_count','consensus','spatial_support','model_compatibility','corner_overlap','native_residual']
METHOD='R4B_SHARED_ORB_V1'
def digest(b):return hashlib.sha256(b).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def prepare(encoded,mask):
 normalize=False
 if type(normalize) is not bool or not isinstance(encoded,np.ndarray) or encoded.dtype!=np.uint16 or encoded.ndim!=2 or min(encoded.shape)<64 or not isinstance(mask,np.ndarray) or mask.shape!=encoded.shape or mask.dtype!=bool:raise MethodError('MATCHING_INPUT_CONTRACT')
 cv2.setNumThreads(1);cv2.setRNGSeed(0)
 original=digest(encoded.tobytes());a=np.clip(encoded.astype(np.float32)/256,0,255).astype(np.uint8)
 if not mask.any():raise MethodError('NO_MATCHING_SUPPORT')
 lo,hi=np.percentile(a[mask],[1,99]);contrast=float(hi-lo)
 if contrast<1:raise MethodError('DIM_OR_FLAT_MATCHING_IMAGE')
 # CLAHE once at native resolution; texture uses pre-CLAHE matching pixels.
 pre=a.copy();enh=cv2.createCLAHE(clipLimit=2,tileGridSize=(8,8)).apply(a)
 h,w=a.shape;ww=min(1024,w);hh=max(1,round(h*ww/w));sx,sy=ww/w,hh/h
 work=cv2.resize(enh,(ww,hh),interpolation=cv2.INTER_AREA);prework=cv2.resize(pre,(ww,hh),interpolation=cv2.INTER_AREA)
 wm=cv2.resize(mask.astype(np.float32),(ww,hh),interpolation=cv2.INTER_AREA)>=1
 feature_mask=cv2.erode(wm.astype(np.uint8),np.ones((33,33),np.uint8),borderType=cv2.BORDER_CONSTANT,borderValue=0)*255
 if digest(encoded.tobytes())!=original:raise MethodError('SPECTRAL_ARRAY_MUTATION')
 return dict(image=work,mask=wm,feature_mask=feature_mask,pre=prework,scale=[sx,sy],native_shape=[h,w],identity=digest(canonical(dict(method=METHOD,settings=PREPARATION,normalization=normalize,encoded_sha256=original,mask_sha256=digest(mask.tobytes()),work_sha256=digest(work.tobytes())))))
def corrected(image,gain,dark,mode):
 value=(image.astype(np.float32)-dark)*gain
 if mode=='phase4_fixedpoint':return np.clip(value*256,0,65535).astype(np.uint16)
 raise InputIntegrityError('Unknown correction mode')
def skipped(method,reason):return dict(method=method,status='NOT_EVALUATED',reason=reason,matrix=None,checks=[dict(gate=k,status='NOT_EVALUATED') for k in ALL_GATES],fallback_used=False)

def project(p,m):
 z=np.c_[p,np.ones(len(p))]@m.T
 if (np.abs(z[:,2])<1e-12).any():raise MethodError('PROJECTIVE_HORIZON')
 return z[:,:2]/z[:,2,None]

def corners(shape):
 h,w=shape;return np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]],np.float64)

def overlap(m,shape):
 try:p=project(corners(shape),m).astype(np.float32)
 except MethodError:return 0.
 if not np.isfinite(p).all() or not cv2.isContourConvex(p) or cv2.contourArea(p,oriented=True)<=0:return 0.
 area,_=cv2.intersectConvexConvex(p,corners(shape).astype(np.float32));return float(area/((shape[0]-1)*(shape[1]-1)))

def phase_failure(reason,state='REJECTED',checks=None):
 return dict(method='R4B_COMMON_PREP_PHASE_V2',status=state,reason=reason,matrix=None,checks=checks or [dict(gate='work_support',status='FAIL'),dict(gate='texture',status='NOT_EVALUATED'),dict(gate='phase_correlation',status='NOT_EVALUATED'),dict(gate='ncc',status='NOT_EVALUATED'),dict(gate='corner_overlap',status='NOT_EVALUATED')],texture_pre_clahe=None,ncc=None,response_diagnostic=None,fallback_used=False)

def phase_prepared(p,q):
 if p['native_shape']!=q['native_shape']:raise MethodError('PHASE_SHAPE')
 if not p['mask'].any() or not q['mask'].any():return phase_failure('EMPTY_RESIZED_PHASE_SUPPORT')
 texture=min(float(np.mean(np.hypot(cv2.Sobel(x['pre'],cv2.CV_32F,1,0),cv2.Sobel(x['pre'],cv2.CV_32F,0,1))[x['mask']])) for x in [p,q])
 if not np.isfinite(texture) or texture<1:return phase_failure('PHASE_TEXTURE')
 a=p['image'].astype(np.float32);b=q['image'].astype(np.float32)
 # OpenCV may multiply correlation arguments in-place by Hann at optimal DFT sizes.
 window=cv2.createHanningWindow((a.shape[1],a.shape[0]),cv2.CV_32F)
 (dx,dy),response=cv2.phaseCorrelate(a.copy(),b.copy(),window)
 native=np.array([dx,dy])/p['scale'];m=np.eye(3);m[:2,2]=native
 if not np.isfinite(native).all() or not np.isfinite(response):return phase_failure('NONFINITE_PHASE','FAILED')
 workm=np.float32([[1,0,dx],[0,1,dy]]);warped=cv2.warpAffine(a,workm,(a.shape[1],a.shape[0]),flags=cv2.INTER_LINEAR)
 mask=(cv2.warpAffine(p['mask'].astype(np.float32),workm,(a.shape[1],a.shape[0]),flags=cv2.INTER_LINEAR)>=1)&q['mask']
 av,bv=warped[mask],b[mask]
 ncc=float(np.corrcoef(av,bv)[0,1]) if len(av)>2 and np.std(av)>0 and np.std(bv)>0 else None
 if ncc is not None and not np.isfinite(ncc):ncc=None
 ov=overlap(m,p['native_shape']);ok=ncc is not None and ncc>=.5 and ov>=.6
 checks=[dict(gate='work_support',status='PASS'),dict(gate='texture',status='PASS',value=texture),dict(gate='phase_correlation',status='PASS'),dict(gate='ncc',status='PASS' if ncc is not None and ncc>=.5 else 'FAIL',value=ncc,threshold=.5),dict(gate='corner_overlap',status='PASS' if ov>=.6 else 'FAIL',value=ov,threshold=.6)]
 return dict(method='R4B_COMMON_PREP_PHASE_V2',status='PASS' if ok else 'REJECTED',reason='ENGINEERING_ACCEPTANCE_ONLY' if ok else 'PHASE_NCC_OR_OVERLAP',matrix=m.tolist() if ok else None,phase_work_displacement=[dx,dy],native_displacement=native.tolist(),ncc=ncc,ncc_domain='PRISTINE_PREPARED_UNTAPERED_ALIGNED_WORK_PIXELS',texture_pre_clahe=texture,response_diagnostic=float(response),corner_overlap_fraction=ov,residual_native_px='NOT_AVAILABLE_NO_CORRESPONDENCES',source_preparation=p['identity'],target_preparation=q['identity'],clahe_applications=1,checks=checks,fallback_used=False)
