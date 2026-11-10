"""Bound native BLAS workers before NumPy is loaded by the export pipeline.

The baker already runs independent jobs. Its narrow 3/6-column products do not
benefit from a second large native thread pool inside each job.
"""
from pathlib import Path
import ctypes,os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['GOTO_NUM_THREADS']='1'
import numpy as np

BLAS_THREADS=None
_libraries=[]
if os.name=='nt':
    for path in (Path(np.__file__).resolve().parent.parent/'numpy.libs').glob('*openblas*.dll'):
        library=ctypes.CDLL(str(path));_libraries.append(library)
        for prefix,suffix in [('scipy_','64_'),('','64_'),('scipy_',''),('','')]:
            try:
                setter=getattr(library,prefix+'openblas_set_num_threads'+suffix)
                getter=getattr(library,prefix+'openblas_get_num_threads'+suffix)
            except AttributeError:continue
            setter.argtypes=[ctypes.c_int];setter.restype=None;getter.argtypes=[];getter.restype=ctypes.c_int
            setter(1);BLAS_THREADS=getter()
            if BLAS_THREADS!=1:raise RuntimeError('无法限制贴图计算线程，请重新启动工具')
            break
