"""fiberphoto: fiber photometry (calcium imaging) analysis for TDMS recordings."""
from .config import PipelineConfig
from .pipeline import run_batch, run_file

__version__ = "0.3.1"
__all__ = ["PipelineConfig", "run_file", "run_batch", "__version__"]
