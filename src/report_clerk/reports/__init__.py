import importlib
from importlib.resources import files
from types import ModuleType


def module_name(report_id: str) -> str:
    return f"report_clerk.reports.{report_id}"


def template_name(report_id: str) -> str:
    return f"{report_id}.html"


def template_exists(report_id: str) -> bool:
    return files("report_clerk").joinpath("templates", template_name(report_id)).is_file()


def load(report_id: str) -> ModuleType:
    # Each report module defines SOURCES and build(services, report, reporting_date) -> template template_data.
    return importlib.import_module(module_name(report_id))
