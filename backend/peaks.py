"""拱顶历史峰值：只从办结集合（status=done）按断面取绝对值最大的读数。

未办结（pending）的断面不许虚填峰值——它们根本不进入这里的结果。
"""
from sqlalchemy import func

from models import ConvergenceLog


def section_peaks(db) -> dict[str, dict]:
    """返回 {断面: {peak_mm, peak_at}}，仅含有办结读数的断面。

    每个断面取 abs(delta_mm) 最大的办结行；并列时以最早测量时刻为准
    （峰值时刻是“首次达到该峰值”的读数时刻）。
    """
    rows = (
        db.query(
            ConvergenceLog.chainage,
            ConvergenceLog.delta_mm,
            ConvergenceLog.created_at,
        )
        .filter(ConvergenceLog.status == "done")
        .order_by(
            func.abs(ConvergenceLog.delta_mm).desc(),
            ConvergenceLog.created_at.asc(),
            ConvergenceLog.id.asc(),
        )
        .all()
    )
    peaks: dict[str, dict] = {}
    for chainage, delta_mm, created_at in rows:
        if chainage not in peaks:
            peaks[chainage] = {"peak_mm": float(delta_mm), "peak_at": created_at}
    return peaks
