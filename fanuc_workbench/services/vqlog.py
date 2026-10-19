"""Shared local Python entry point for the self-contained VQLOG feature."""
from fanuc_workbench.extensions.vqlog.feature import EVENT_TYPES, analyze_file, parse_line

__all__ = ['EVENT_TYPES', 'analyze_file', 'parse_line']
