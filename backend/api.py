import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError

from claimer import start as start_claimer
from models import Base, ConvergenceLog, PeakLock, SessionLocal, engine, lock_dict, row_dict

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
            return jsonify({"detail": "仅测量员可写，巡检岗只许观看"}), 403
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


def compute_peaks(db):
    """从办结集合算各断面峰值：|delta_mm| 最大的已办结行，同值取先办结的。"""
    rows = (
        db.query(ConvergenceLog)
        .filter(ConvergenceLog.status == "done")
        .order_by(ConvergenceLog.id)
        .all()
    )
    peaks = {}
    for r in rows:
        cur = peaks.get(r.chainage)
        if cur is None or abs(r.delta_mm) > abs(cur.delta_mm):
            peaks[r.chainage] = r
    return peaks


@app.get("/api/peaks")
@require_login
def list_peaks():
    """峰值墙：左列断面，右列峰值与时刻。已锁的列回锁区快照，不再重算。"""
    db = SessionLocal()
    try:
        chainages = [
            c
            for (c,) in db.query(ConvergenceLog.chainage)
            .distinct()
            .order_by(ConvergenceLog.chainage)
            .all()
        ]
        peaks = compute_peaks(db)
        locks = {lock.chainage: lock for lock in db.query(PeakLock).all()}
        out = []
        for chainage in chainages:
            lock = locks.get(chainage)
            if lock is not None:
                out.append(
                    {
                        "chainage": chainage,
                        "peak_delta_mm": lock.peak_delta_mm,
                        "peak_at": lock.peak_at.isoformat() if lock.peak_at else None,
                        "locked": True,
                        "locked_by": lock.locked_by,
                        "locked_at": lock.locked_at.isoformat() if lock.locked_at else None,
                    }
                )
                continue
            peak = peaks.get(chainage)
            out.append(
                {
                    "chainage": chainage,
                    # 未办结的断面不许虚填峰值
                    "peak_delta_mm": peak.delta_mm if peak else None,
                    "peak_at": peak.processed_at.isoformat()
                    if peak and peak.processed_at
                    else None,
                    "locked": False,
                    "locked_by": None,
                    "locked_at": None,
                }
            )
        return jsonify(out)
    finally:
        db.close()


@app.post("/api/peaks/lock")
@require_writer
def lock_peak():
    """锁定：把这一刻服务端重算的峰值与时刻压进锁区，客户端手改的数字一律不采信。"""
    body = request.get_json(silent=True) or {}
    chainage = (body.get("chainage") or "").strip()
    if not chainage:
        return jsonify({"detail": "桩号不能为空"}), 400
    db = SessionLocal()
    try:
        if db.query(PeakLock).filter(PeakLock.chainage == chainage).first() is not None:
            return jsonify({"detail": "该断面已锁定，锁区内容不再变"}), 409
        peak = compute_peaks(db).get(chainage)
        if peak is None:
            return jsonify({"detail": "未办结的断面没有峰值，不能锁定"}), 400
        lock = PeakLock(
            chainage=chainage,
            peak_delta_mm=peak.delta_mm,
            peak_at=peak.processed_at,
            locked_by=g.user["username"],
            locked_at=datetime.now(timezone.utc),
        )
        db.add(lock)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({"detail": "该断面已锁定，锁区内容不再变"}), 409
        db.refresh(lock)
        return jsonify(lock_dict(lock)), 201
    finally:
        db.close()


@app.get("/api/locks")
@require_login
def list_locks():
    """锁区专页数据：锁定时刻压成的只读副本，任何角色都只能看。"""
    db = SessionLocal()
    try:
        rows = db.query(PeakLock).order_by(PeakLock.locked_at.desc(), PeakLock.id.desc()).all()
        return jsonify([lock_dict(r) for r in rows])
    finally:
        db.close()
