import os, json, pytz
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from contextlib import contextmanager

# הגדרות בסיסיות
TZ = pytz.timezone("Asia/Jerusalem")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://matan_nb_user:Qzcukb3uonnqU3wgDxKyzkxeEaT83PJp@dpg-d40u1m7gi27c73d0oorg-a:5432/matan_nb"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Session = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
Base = declarative_base()

@contextmanager
def session_scope():
    s = Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

def now(): return datetime.now(TZ)
def hhmmss(total): total=max(0,int(total)); return f"{total//3600:02d}:{(total%3600)//60:02d}:{total%60:02d}"

# טבלת משימות
class Task(Base):
    __tablename__="tasks"
    id=Column(Integer,primary_key=True)
    name=Column(String,nullable=False)
    duration=Column(Integer,nullable=False)
    remaining=Column(Integer,nullable=False)
    status=Column(String,nullable=False)
    end_time=Column(DateTime(timezone=True))
    position=Column(Integer,nullable=False,default=0)
    is_work=Column(Boolean,nullable=False,default=False)

    def to_dict(self):
        rem=self.remaining
        if self.status=="running" and self.end_time:
            if self.end_time.tzinfo is None: self.end_time=TZ.localize(self.end_time)
            rem=max(0,int((self.end_time-now()).total_seconds()))
        return {"id":self.id,"name":self.name,"duration":self.duration,"remaining":rem,
                "status":self.status,"position":self.position,"is_work":self.is_work}

Base.metadata.create_all(engine)
app=Flask(__name__)

def recompute_chain():
    with session_scope() as s:
        tasks=s.query(Task).order_by(Task.position.asc()).all()
        n=now()
        for i,t in enumerate(tasks):
            if t.status=="running" and t.end_time:
                if t.end_time.tzinfo is None:t.end_time=TZ.localize(t.end_time)
                rem=int((t.end_time-n).total_seconds())
                if rem<=0:
                    t.status="done"; t.remaining=0; t.end_time=None; s.add(t)
                    if i+1<len(tasks):
                        nxt=tasks[i+1]
                        if nxt.status=="pending":
                            nxt.status="running"; nxt.end_time=n+timedelta(seconds=nxt.remaining); s.add(nxt)
                else:
                    t.remaining=rem; s.add(t)

@app.route("/")
def index(): return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks=s.query(Task).order_by(Task.position.asc()).all()
        payload=[t.to_dict() for t in tasks]
    total_work=sum(int(x.duration or 0) for x in tasks if x.is_work)
    return jsonify({"ok":True,"tasks":payload,"work_total_hhmmss":hhmmss(total_work),
                    "now":now().strftime("%H:%M:%S")})

@app.route("/add",methods=["POST"])
def add():
    d=request.json or {}; name=d.get("name","משימה חדשה")
    dur=d.get("hours",0)*3600+d.get("minutes",0)*60+d.get("seconds",0)
    with session_scope() as s:
        pos=s.query(Task).count()
        s.add(Task(name=name,duration=dur,remaining=dur,status="pending",position=pos))
    return jsonify(ok=True)

@app.route("/update/<int:tid>",methods=["POST"])
def update_task(tid):
    d=request.json or {}
    with session_scope() as s:
        t=s.get(Task,tid)
        if t:
            t.name=d.get("name",t.name)
            dur=d.get("hours",0)*3600+d.get("minutes",0)*60+d.get("seconds",0)
            t.duration=dur; t.remaining=dur
            s.add(t)
    return jsonify(ok=True)

@app.route("/extend/<int:tid>",methods=["POST"])
def extend(tid):
    d=request.json or {}; add=d.get("hours",0)*3600+d.get("minutes",0)*60+d.get("seconds",0)
    with session_scope() as s:
        t=s.get(Task,tid)
        if t:
            t.duration+=add; t.remaining+=add; s.add(t)
    return jsonify(ok=True)

@app.route("/start/<int:tid>",methods=["POST"])
def start(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t: t.status="running"; t.end_time=now()+timedelta(seconds=t.remaining); s.add(t)
    return jsonify(ok=True)

@app.route("/pause/<int:tid>",methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t and t.end_time:
            rem=int((t.end_time-now()).total_seconds())
            t.remaining=max(0,rem); t.status="paused"; t.end_time=None; s.add(t)
    return jsonify(ok=True)

@app.route("/done/<int:tid>",methods=["POST"])
def done(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t: t.status="done"; t.remaining=0; t.end_time=None; s.add(t)
    return jsonify(ok=True)

@app.route("/set_pending/<int:tid>",methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t: t.status="pending"; t.end_time=None; s.add(t)
    return jsonify(ok=True)

@app.route("/skip/<int:tid>",methods=["POST"])
def skip(tid):
    with session_scope() as s:
        tasks=s.query(Task).order_by(Task.position.asc()).all()
        n=now()
        for i,t in enumerate(tasks):
            if t.id==tid:
                t.status="done"; t.remaining=0; t.end_time=None; s.add(t)
                if i+1<len(tasks):
                    nxt=tasks[i+1]
                    if nxt.status=="pending":
                        nxt.status="running"; nxt.end_time=n+timedelta(seconds=nxt.remaining); s.add(nxt)
                break
    return jsonify(ok=True)

@app.route("/delete/<int:tid>",methods=["POST"])
def delete(tid):
    with session_scope() as s:
        s.query(Task).filter_by(id=tid).delete()
    return jsonify(ok=True)

@app.route("/reorder_single",methods=["POST"])
def reorder_single():
    d=request.json or {}; tid=d.get("task_id"); pos=int(d.get("new_position",1))-1
    with session_scope() as s:
        tasks=s.query(Task).order_by(Task.position.asc()).all()
        ids=[t.id for