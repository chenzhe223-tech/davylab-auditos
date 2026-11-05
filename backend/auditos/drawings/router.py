from pathlib import Path

from auditos.drawings.models import (
    DrawingRouteDecision,
    DrawingTask,
    EngineId,
    TakeoffRequest,
)


def classify_task(description: str, measurement_kind: str = "auto") -> DrawingTask:
    text = f"{description} {measurement_kind}".lower()
    if any(word in text for word in ("面积", "area", "房间", "闭合区域")):
        return DrawingTask.AREA_TAKEOFF
    if any(word in text for word in ("长度", "管线", "线缆", "桥架", "管道", "length")):
        return DrawingTask.LINEAR_TAKEOFF
    if any(word in text for word in ("数量", "台数", "设备", "块数", "count")):
        return DrawingTask.BLOCK_COUNT
    if any(word in text for word in ("图层", "标注", "文字", "尺寸", "结构", "summary")):
        return DrawingTask.TEXT_DIMENSIONS
    if any(word in text for word in ("附近", "范围", "坐标", "空间")):
        return DrawingTask.SPATIAL_REVIEW
    if any(word in text for word in ("渲染", "看图", "图面", "可视")):
        return DrawingTask.VISUAL_REVIEW
    if any(word in text for word in ("分析", "识别", "理解", "复杂")):
        return DrawingTask.COMPLEX_ANALYSIS
    return DrawingTask.STRUCTURE_SUMMARY


def route_drawing_task(request: TakeoffRequest) -> DrawingRouteDecision:
    extension = Path(request.filename).suffix.lower()
    task = classify_task(request.task_description, request.measurement_kind)
    reasons: list[str] = []
    constraints = [
        "第三方引擎仅提供解析候选；最终测量值由 AuditOS 验证/重算。",
        "不得将近似包围盒面积直接用于正式结算计量。",
        "必须保留图纸哈希、修订版本、图层和实体来源定位。",
        "任何单位/比例未确认时，不允许输出可用于结算的标准化工程量。",
    ]
    if extension == ".dwg":
        primary = EngineId.DWG_READER
        reasons.append("DWG Reader 负责受控 DWG→DXF 转换和图纸结构读取；正式几何量由 AuditOS 原生内核重算。")
        secondary = [EngineId.NATIVE, EngineId.DXF_CIVIL]
        if task in {DrawingTask.AREA_TAKEOFF, DrawingTask.LINEAR_TAKEOFF, DrawingTask.COMPLEX_ANALYSIS}:
            secondary.append(EngineId.CAD_AI_AGENT)
    elif extension == ".dxf":
        if task in {DrawingTask.AREA_TAKEOFF, DrawingTask.LINEAR_TAKEOFF, DrawingTask.BLOCK_COUNT}:
            primary = EngineId.NATIVE
            secondary = [EngineId.DWG_READER, EngineId.DXF_CIVIL, EngineId.CAD_AI_AGENT]
            reasons.append("DXF 测量由 AuditOS 确定性几何内核负责；外部引擎仅作独立交叉检查。")
        elif task in {DrawingTask.SPATIAL_REVIEW, DrawingTask.VISUAL_REVIEW}:
            primary = EngineId.DXF_CIVIL
            secondary = [EngineId.DWG_READER]
            reasons.append("空间筛查/渲染优先使用 DXF Civil 的专用只读工具。")
        elif task == DrawingTask.COMPLEX_ANALYSIS:
            primary = EngineId.CAD_AI_AGENT
            secondary = [EngineId.NATIVE, EngineId.DWG_READER, EngineId.DXF_CIVIL]
        else:
            primary = EngineId.DWG_READER
            secondary = [EngineId.NATIVE, EngineId.DXF_CIVIL]
    else:
        primary = EngineId.NATIVE
        secondary = []
        reasons.append("当前适配器仅接受 DWG/DXF；PDF/图片需后续独立矢量/栅格流程。")
    if not reasons:
        reasons.append(f"依据文件格式 {extension} 和任务类型 {task.value} 选择引擎。")
    if task == DrawingTask.AREA_TAKEOFF:
        constraints.append("面积任务优先采用精确闭合边界算法；HATCH/包围盒/栅格推断标记为 approximate。")
    if task in {DrawingTask.LINEAR_TAKEOFF, DrawingTask.AREA_TAKEOFF}:
        constraints.append("交叉引擎结果只用于差异定位，不可简单平均。")
    return DrawingRouteDecision(
        task=task, primary_engine=primary, secondary_engines=secondary,
        reasons=reasons, safety_constraints=constraints,
        requires_visual_review=task in {
            DrawingTask.AREA_TAKEOFF, DrawingTask.SPATIAL_REVIEW, DrawingTask.VISUAL_REVIEW
        },
        requires_human_review=True,
    )
