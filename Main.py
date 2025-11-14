import os
import json
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template, make_response
from sqlalchemy import Column, Integer, String, Boolean, DateTime, create_engine
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
import pytz

TZ=pytz.timezone("Asia/Jerusalem")

DATABASE_URL=os.getenv("DATABASE_URL","sqlite:///tasks.db")
engine=create_engine(DATABASE_URL,pool_pre_ping=True)
Session=scoped_session(sessionmaker(bind=engine))
Base=declarative_base()

def now():
    return datetime.now(TZ)

class Task(Base):
    __tablename__="tasks"
    id=Column(Integer,primary_key=True)
    name=Column(String)
    duration=Column(Integer)
    remaining=Column(Integer)
    status=Column(String)
    position=Column(Integer)
    start_time=Column(DateTime(timezone=True))
    end_time=Column(DateTime(timezone=True))
    is_work=Column(Boolean,default=False)

Base.metadata.create_all(engine)

app=Flask(__name__)

def reorder_positions():
    s=Session()
    tasks=s.query(Task).order_by(Task.position).all()
    for i,t in enumerate(tasks):
        t.position=i+1
        s.add(t)
    s.commit()
    s.close()

def compute_chain():
    s=Session()
    tasks=s.query(Task).order_by(Task.position).all()
    now_ts=now()
    for i,t in enumerate(tasks):
        if t.status=="running" and t.end_time:
            rem=int((t.end_time-now_ts).total_seconds())
            if rem<=0:
                t.remaining=0
                t.status="done"
                t.end_time=None
                s.add(t)
                if i+1<len(tasks):
                    nxt=tasks[i+1]
                    if nxt.status=="pending":
                        nxt.status="running"
                        nxt.end_time=now_ts+timedelta(seconds=nxt.remaining)
                        nxt.start_time=now_ts
                        s.add(nxt)
            else:
                t.remaining=rem
                s.add(t)
    s.commit()
    s.close()

def hhmmss(t):
    t=max(0,int(t))
    return str(timedelta(seconds=t))

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/state")
def state():
    compute_chain()
    s=Session()
    tasks=s.query(Task).order_by(Task.position).all()
    data=[]
    for t in tasks:
        data.append({
            "id":t.id,
            "name":t.name,
            "duration":t.duration,
            "remaining":t.remaining,
            "status":t.status,
            "position":t.position,
            "is_work":t.is_work,
            "start_str":t.start_time.strftime("%H:%M:%S") if t.start_time else "-",
            "end_str":t.end_time.strftime("%H:%M:%S") if t.end_time else "-"
        })
    work_total=sum(t.duration for t in tasks if t.is_work)
    total_remaining=sum(t.remaining for t in tasks if t.status!="done")
    overall_end=(now()+timedelta(seconds=total_remaining)).strftime("%H:%M:%S")
    res={
        "tasks":data,
        "now":now().strftime("%H:%M:%S"),
        "overall_end":overall_end,
        "work_total":hhmmss(work_total)
    }
    s.close()
    return jsonify(res)

@app.route("/add",methods=["POST"])
def add():
    j=request.json
    s=Session()
    name=j["name"]
    dur=j["hours"]*3600+j["minutes"]*60+j["seconds"]
    pos=int(j["pos"])
    tasks=s.query(Task).order_by(Task.position).all()
    for t in tasks:
        if t.position>=pos:
            t.position+=1
            s.add(t)
    t=Task(name=name,duration=dur,remaining=dur,status="pending",position=pos)
    s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/start/<int:tid>",methods=["POST"])
def start(tid):
    s=Session()
    t=s.get(Task,tid)
    if t:
        t.status="running"
        t.start_time=now()
        t.end_time=now()+timedelta(seconds=t.remaining)
        s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/pause/<int:tid>",methods=["POST"])
def pause(tid):
    s=Session()
    t=s.get(Task,tid)
    if t and t.end_time:
        rem=int((t.end_time-now()).total_seconds())
        t.remaining=max(0,rem)
        t.status="paused"
        t.end_time=None
        s.add(t)
        s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/reset/<int:tid>",methods=["POST"])
def reset(tid):
    s=Session()
    t=s.get(Task,tid)
    if t:
        t.remaining=t.duration
        t.status="paused"
        t.end_time=None
        t.start_time=None
        s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/pending/<int:tid>",methods=["POST"])
def pending(tid):
    s=Session()
    t=s.get(Task,tid)
    if t:
        t.status="pending"
        t.end_time=None
        s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/delete/<int:tid>",methods=["POST"])
def delete(tid):
    s=Session()
    t=s.get(Task,tid)
    if t:
        s.delete(t)
    s.commit()
    reorder_positions()
    s.close()
    return jsonify({"ok":True})

@app.route("/update/<int:tid>",methods=["POST"])
def update(tid):
    j=request.json
    s=Session()
    t=s.get(Task,tid)
    if t:
        t.name=j["name"]
        add_sec=j["hours"]*3600+j["minutes"]*60+j["seconds"]
        if add_sec>0:
            t.duration=add_sec
            t.remaining=add_sec
        s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/extend/<int:tid>",methods=["POST"])
def extend(tid):
    j=request.json
    s=Session()
    t=s.get(Task,tid)
    if t:
        add=j["hours"]*3600+j["minutes"]*60+j["seconds"]
        t.duration+=add
        t.remaining+=add
        s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/move/<int:tid>",methods=["POST"])
def move(tid):
    pos=int(request.json["pos"])
    s=Session()
    tasks=s.query(Task).order_by(Task.position).all()
    t=s.get(Task,tid)
    old=t.position
    if pos<1:pos=1
    if pos>len(tasks):pos=len(tasks)
    for x in tasks:
        if x.id==tid: continue
        if old<pos and old < x.position <= pos:
            x.position-=1
        elif pos<=x.position<old:
            x.position+=1
        s.add(x)
    t.position=pos
    s.add(t)
    s.commit()
    reorder_positions()
    s.close()
    return jsonify({"ok":True})

@app.route("/flag/<int:tid>",methods=["POST"])
def flag(tid):
    v=request.json["v"]
    s=Session()
    t=s.get(Task,tid)
    t.is_work=v
    s.add(t)
    s.commit()
    s.close()
    return jsonify({"ok":True})

@app.route("/export")
def export():
    s=Session()
    tasks=s.query(Task).order_by(Task.position).all()
    data=[{
        "name":t.name,
        "duration":t.duration,
        "remaining":t.remaining,
        "status":t.status,
        "position":t.position,
        "is_work":t.is_work
    } for t in tasks]
    raw=json.dumps({"tasks":data},ensure_ascii=False,indent=2)
    r=make_response(raw)
    r.headers["Content-Type"]="application/json"
    r.headers["Content-Disposition"]="attachment; filename=tasks.json"
    return r

@app.route("/import",methods=["POST"])
def impt():
    j=request.json
    arr=j["tasks"]
    s=Session()
    s.query(Task).delete()
    for t in arr:
        s.add(Task(
            name=t["name"],
            duration=t["duration"],
            remaining=t["remaining"],
            status=t["status"],
            position=t["position"],
            is_work=t["is_work"]
        ))
    s.commit()
    reorder_positions()
    s.close()
    return jsonify({"ok":True})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=5000)