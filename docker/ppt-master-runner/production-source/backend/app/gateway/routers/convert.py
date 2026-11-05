import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.gateway.markdown_pdf import markdown_to_pdf_bytes

router = APIRouter(prefix="/api", tags=["convert"])

_MAX_MARKDOWN_CHARS = 1_000_000


class MarkdownToPdfRequest(BaseModel):
    markdown: str = Field(min_length=1)
    filename: str = Field(default="report")


@router.post("/convert/markdown/pdf")
async def convert_markdown_to_pdf(body: MarkdownToPdfRequest):
    if len(body.markdown) > _MAX_MARKDOWN_CHARS:
        raise HTTPException(status_code=413, detail="Markdown too large")

    filename = re.sub(r"[^\w\-\u4e00-\u9fff ]+", "", body.filename).strip() or "report"
    pdf = markdown_to_pdf_bytes(body.markdown, title=filename)
    encoded = quote(f"{filename}.pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )
