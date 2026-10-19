"""Shared local Python entry point for the self-contained SNPX decode feature."""
from fanuc_workbench.extensions.snpx.feature import FORMATS, decode_capture

__all__ = ['FORMATS', 'decode_capture']
