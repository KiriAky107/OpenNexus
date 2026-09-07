"""交互预览复用导出使用的有界解析器和几何计算。"""
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.plot.parser import parse_source
from app.plot.render import render_svg
from app.plot.model import PlotDiagnostic, StaticRenderResult

router = APIRouter(prefix='/api/plots', tags=['Function Plot'])
_slots = asyncio.Semaphore(2)

class PlotRequest(BaseModel):
    source: str = Field(max_length=20000)
    theme_id: str = Field(default='light', max_length=100)

class PlotResponse(BaseModel):
    result: StaticRenderResult | None = None
    diagnostics: list[PlotDiagnostic] = Field(default_factory=list)
    node_count: int = 0

def preview(request):
    """同步解析并渲染函数图，供受并发限制的异步路由在线程中调用。"""
    parsed = parse_source(request.source)
    if parsed.plot is None:
        return PlotResponse(diagnostics=parsed.diagnostics)
    if parsed.plot.node_count > 8000:
        return PlotResponse(node_count=parsed.plot.node_count, diagnostics=[PlotDiagnostic(
            severity='error', code='PLOT_BUDGET_EXCEEDED', message='图表累计表达式节点超过 8000 上限')])
    return PlotResponse(result=render_svg(parsed.plot, request.theme_id),
                        diagnostics=parsed.diagnostics, node_count=parsed.plot.node_count)

@router.post('/function', response_model=PlotResponse)
async def render_function(request: PlotRequest):
    # 绘图属于 CPU 密集任务，限制并发并移入线程，避免阻塞事件循环。
    async with _slots:
        return await asyncio.to_thread(preview, request)
