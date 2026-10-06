import math
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from claimer import start as start_claimer
from models import Base, ConvergenceLog, PeakLock, SessionLocal, engine, lock_dict, row_dict
from peaks import section_peaks

SECRET = os.environ.get("JWT_SECRET", "tunnelconv-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

app = Flask(__name__)


def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(ConvergenceLog).count() > 0:
            return
        now = datetime.now(timezone.utc)
        for chainage, delta, expect in (("K12+180", 1.2, "合格"), ("K18+040", 5.6, "超限")):
            from rules import judge

            verdict, reason = judge(delta)
            assert verdict == expect
            db.add(
                ConvergenceLog(
                    chainage=chainage,
                    delta_mm=delta,
                    status="done",
                    verdict=verdict,
                    reason=reason,
                    created_by="surveyor",
                    created_at=now,
                    processed_at=now,
                )
            )
        db.commit()
    finally:
        db.close()


seed()
start_claimer()


def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth[7:].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def require_writer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        if user["role"] != "writer":
            return jsonify({"detail": "仅测量员可提交收敛读数"}), 403
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "tunnel-convergence-desk"})


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return jsonify({"detail": "用户名或密码错误"}), 401
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return jsonify({"access_token": token, "username": username, "role": user["role"]})


@app.get("/api/logs")
@require_login
def list_logs():
    db = SessionLocal()
    try:
        rows = db.query(ConvergenceLog).order_by(ConvergenceLog.id.desc()).all()
        return jsonify([row_dict(r) for r in rows])
    finally:
        db.close()


@app.post("/api/logs")
@require_writer
def create_log():
    body = request.get_json(silent=True) or {}
    chainage = (body.get("chainage") or "").strip()
    if not chainage:
        return jsonify({"detail": "桩号不能为空"}), 400
    try:
        delta_mm = float(body.get("delta_mm"))
    except (TypeError, ValueError):
        return jsonify({"detail": "收敛值必须是数字"}), 400
    db = SessionLocal()
    try:
        row = ConvergenceLog(
            chainage=chainage,
            delta_mm=delta_mm,
            status="pending",
            created_by=g.user["username"],
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return jsonify(row_dict(row)), 201
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 拱顶历史峰值墙 + 锁区
# ---------------------------------------------------------------------------


def _wall_rows(db) -> list[dict]:
    """峰值墙：墙面上每个出现过的断面一行。

    有办结读数才有实时峰值；只挂着 pending 的断面 peak 为 None（不许虚填）。
    已锁断面附上冻结副本，前端照副本显示，新办结不再改动它。
    """
    sections = [
        chainage
        for chainage, in db.query(ConvergenceLog.chainage)
        .group_by(ConvergenceLog.chainage)
        .order_by(func.min(ConvergenceLog.id))
        .all()
    ]
    peaks = section_peaks(db)
    locks = {lk.chainage: lk for lk in db.query(PeakLock).all()}
    wall = []
    for chainage in sections:
        live = peaks.get(chainage)
        lock = locks.get(chainage)
        wall.append(
            {
                "chainage": chainage,
                "has_done": live is not None,
                "live_peak_mm": live["peak_mm"] if live else None,
                "live_peak_at": live["peak_at"].isoformat() if live else None,
                "locked": lock is not None,
                "lock": lock_dict(lock) if lock else None,
            }
        )
    return wall


@app.get("/api/peaks")
@require_login
def get_peaks():
    db = SessionLocal()
    try:
        return jsonify(_wall_rows(db))
    finally:
        db.close()


@app.post("/api/peaks/lock")
@require_writer
def lock_peaks():
    """把此刻的峰值与时刻压进锁区。

    不带 body：锁定所有“有办结峰值且尚未锁”的断面；
    body {"chainage": "..."}：只锁该断面。已经锁的断面原样保留、绝不重算。
    """
    body = request.get_json(silent=True) or {}
    only = (body.get("chainage") or "").strip() or None
    db = SessionLocal()
    try:
        peaks = section_peaks(db)
        locked_now = []
        skipped = []
        targets = [only] if only else sorted(peaks)
        for chainage in targets:
            exists = (
                db.query(PeakLock.id).filter(PeakLock.chainage == chainage).first()
            )
            if exists is not None:
                continue  # 已锁列不再变
            live = peaks.get(chainage)
            if live is None:
                # 未办结断面没有峰值，不许虚填
                skipped.append(chainage)
                continue
            row = PeakLock(
                chainage=chainage,
                peak_mm=live["peak_mm"],
                peak_at=live["peak_at"],
                locked_at=datetime.now(timezone.utc),
                locked_by=g.user["username"],
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                # 并发下别的线程/请求先锁了：已锁即定格，放弃本次写入
                db.rollback()
                continue
            db.refresh(row)
            locked_now.append(lock_dict(row))
        return jsonify({"locked": locked_now, "skipped": skipped})
    finally:
        db.close()


@app.get("/api/locks")
@require_login
def list_locks():
    db = SessionLocal()
    try:
        rows = db.query(PeakLock).order_by(PeakLock.chainage).all()
        return jsonify([lock_dict(r) for r in rows])
    finally:
        db.close()


def _parse_iso(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@app.patch("/api/locks/<int:lock_id>")
@require_writer
def patch_lock(lock_id: int):
    """锁区专页手改：直写数字，任何路径都不会触发重算。"""
    body = request.get_json(silent=True) or {}
    db = SessionLocal()
    try:
        row = db.get(PeakLock, lock_id)
        if row is None:
            return jsonify({"detail": "锁区记录不存在"}), 404
        if "peak_mm" in body:
            try:
                value = float(body.get("peak_mm"))
            except (TypeError, ValueError):
                return jsonify({"detail": "峰值必须是数字"}), 400
            if not math.isfinite(value):
                return jsonify({"detail": "峰值必须是有限数字"}), 400
            row.peak_mm = value  # 手改直存，不回办结集合重算
        if "peak_at" in body:
            dt = _parse_iso(body.get("peak_at"))
            if dt is None:
                return jsonify({"detail": "峰值时刻格式无法识别"}), 400
            row.peak_at = dt
        db.commit()
        db.refresh(row)
        return jsonify(lock_dict(row))
    finally:
        db.close()
