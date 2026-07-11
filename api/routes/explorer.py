from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter(
    prefix="/explorer",
    tags=["Dataset Explorer"],
    include_in_schema=False,
)

templates = Jinja2Templates(directory="api/templates")

TOOLS = [
    {
        "name": "Dataset Overview",
        "slug": "overview",
        "description": "Warehouse totals, snapshot freshness, collection growth, and platform coverage.",
        "status": "Next",
    },
    {
        "name": "Markets",
        "slug": "markets",
        "description": "Search, filter, sort, paginate, and export prediction market records.",
        "status": "Next",
    },
    {
        "name": "Platforms",
        "slug": "platforms",
        "description": "Compare platform coverage, market counts, volume, liquidity, and freshness.",
        "status": "Planned",
    },
    {
        "name": "Movers",
        "slug": "movers",
        "description": "Inspect recent price, volume, and liquidity changes.",
        "status": "Planned",
    },
    {
        "name": "Market Matcher",
        "slug": "matcher",
        "description": "Compare likely equivalent markets across supported platforms.",
        "status": "Planned",
    },
    {
        "name": "Market Detail",
        "slug": "market-detail",
        "description": "Inspect historical observations for a selected market.",
        "status": "Planned",
    },
    {
        "name": "Dataset Health",
        "slug": "health",
        "description": "Review freshness, coverage, and data-quality summaries.",
        "status": "Planned",
    },
]


@router.get("")
@router.get("/")
def explorer_menu(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="explorer/menu.html",
        context={
            "page_title": "Dataset Explorer",
            "tools": TOOLS,
        },
    )


@router.get("/{tool_slug}")
def explorer_placeholder(request: Request, tool_slug: str):
    tool = next((item for item in TOOLS if item["slug"] == tool_slug), None)

    if tool is None:
        return templates.TemplateResponse(
            request=request,
            name="explorer/not_found.html",
            context={
                "page_title": "Tool not found",
                "tool_slug": tool_slug,
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="explorer/tool_placeholder.html",
        context={
            "page_title": tool["name"],
            "tool": tool,
        },
    )
