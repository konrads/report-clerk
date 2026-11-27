import importlib
from types import ModuleType


def module_name(report_id: str) -> str:
    return f"report_clerk.reports.{report_id}"


def load(report_id: str) -> ModuleType:
    # Each report module defines SOURCES, SHEETS and build(services, report, reporting_date) -> data with "figures".
    return importlib.import_module(module_name(report_id))
