"""Shared Python facade for the self-contained offline SNPX evidence inspector."""
from fanuc_workbench.extensions.snpx_v1_3_0.feature import inspect_evidence, inspect_io_evidence

__all__ = ['inspect_evidence', 'inspect_io_evidence']
