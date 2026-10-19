"""Shared Python access to the self-contained offline text alarm feature."""
from fanuc_workbench.extensions.alarms_v1_1_0.feature import analyze_alarms, compare_alarms

__all__ = ['analyze_alarms', 'compare_alarms']
