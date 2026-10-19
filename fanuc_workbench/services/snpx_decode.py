"""Shared local Python entry point for the self-contained SNPX decode feature."""
from fanuc_workbench.extensions.snpx_v1_2_0.feature import FORMATS, compare_captures, decode_capture

__all__ = ['FORMATS', 'compare_captures', 'decode_capture']
