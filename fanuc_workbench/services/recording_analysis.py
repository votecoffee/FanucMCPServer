"""Shared Python access to the self-contained offline recording feature."""
from fanuc_workbench.extensions.recording_v1_1_0.summary import summarize_recording
from fanuc_workbench.extensions.recording_v1_1_0.feature import inspect_bundle

__all__ = ['summarize_recording', 'inspect_bundle']
