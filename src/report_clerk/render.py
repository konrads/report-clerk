import base64
from importlib.resources import files
from pathlib import Path

import markdown
import nh3
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from report_clerk import reports
from report_clerk.config import ReportConfig

LOGO = Path(str(files("report_clerk").joinpath("assets", "company_logo.png")))
LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()
LOGO_CID = f"cid:{LOGO.name}"

# Claude's Markdown may carry injected HTML from the data; only plain formatting tags survive, no links or images.
ANALYSIS_TAGS = {"p", "br", "strong", "em", "ul", "ol", "li", "h3", "h4", "blockquote", "code", "table", "thead", "tbody", "tr", "th", "td"}

templates = Environment(loader=PackageLoader("report_clerk", "templates"), autoescape=select_autoescape())
templates.filters["money"] = lambda v: f"{v:,.2f}"


def analysis_html(text: str) -> Markup:
    return Markup(nh3.clean(markdown.markdown(text, extensions=["tables"]), tags=ANALYSIS_TAGS, attributes={}))


def html(definition: ReportConfig, template_data: dict, analysis: str = "", logo_src: str = LOGO_DATA_URI) -> str:
    template = templates.get_template(reports.template_name(definition.id))
    return template.render(
        definition=definition,
        title=definition.title or definition.id,
        analysis=analysis_html(analysis) if analysis else "",
        logo_src=logo_src,
        **template_data,
    )
