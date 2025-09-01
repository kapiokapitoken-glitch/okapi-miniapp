import os, math, random, time, hashlib
from typing import List
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Okapi Rope Miner API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

SECRET = os.getenv("SECRET_KEY","okapi")
DURATION = int(os.getenv("ROPE_DURATION_SEC", 60))
ANGLE_MIN = math.radians(-70); ANGLE_MAX = math.radians(70)
ANGULAR_SPEED = math.radians(90)
ROPE_SPEED = 320.0
ORIGIN_X, ORIGIN_Y = 240, 100

class StartResp(BaseModel): duration_sec:int; seed_hint:str
class SubmitReq(BaseModel): telegram_id:int; taps_ms:List[int]; started_at:int
class SubmitResp(BaseModel): ok:bool; score:int

def today_seed():
    t = datetime.now(timezone.utc).strftime("%Y%m%d")
    return hashlib.sha256(f"{SECRET}-ROPE-{t}".encode()).hexdigest()

class Item:
    def __init__(self,x,y,kind): self.x=x; self.y=y; self.kind=kind

VALUES = {"gold_big":100,"gold_med":50,"gold_small":20,"rock":5,"bag":0}
WEIGHTS= {"gold_big":3.0,"gold_med":2.0,"gold_small":1.0,"rock":3.0,"bag":1.5}
BOUNDS = {"w":480,"h":640,"floor_y":560}
KINDS  = ["gold_big","gold_med","gold_small","gold_small","rock","rock","bag"]

def gen_scene(seed:str):
    rng=random.Random(seed); items=[]
    for _ in range(18):
        k=rng.choice(KINDS); x=rng.randint(40,BOUNDS["w"]-40); y=rng.randint(300,BOUNDS["floor_y"])
        items.append(Item(x,y,k))
    for it in items:
        if it.kind=="bag": VALUES["bag"]=rng.randint(10,80)
    return items

def angle_at(t_ms:int)->float:
    span=ANGLE_MAX-ANGLE_MIN; period=(span/ANGULAR_SPEED)*2.0
    t=(t_ms/1000.0)%period; down=t <= (span/ANGULAR_SPEED)
    if down: return ANGLE_MIN + ANGULAR_SPEED*t
    t2=t-(span/ANGULAR_SPEED); return ANGLE_MAX - ANGULAR_SPEED*t2

HOOK_R=12; ITEM_R=18
def cast_line(angle, items):
    x,y=ORIGIN_X,ORIGIN_Y; vx,vy=math.cos(angle),math.sin(angle); length=0.0
    while True:
        x+=vx*4; y+=vy*4; length+=4
        if y>=BOUNDS["floor_y"] or x<0 or x>BOUNDS["w"] or y<0 or y>BOUNDS["h"]:
            return None, length
        for it in items:
            if math.hypot(it.x-x,it.y-y) <= (HOOK_R+ITEM_R):
                return it, length

def retract_time(length, kind):
    base = length/ROPE_SPEED
    return base*(1.0 + WEIGHTS[kind]*0.6)

@app.get("/rope/start", response_model=StartResp)
def rope_start():
    s=today_seed(); return StartResp(duration_sec=DURATION, seed_hint=s[:8])

@app.post("/rope/submit", response_model=SubmitResp)
def rope_submit(req: SubmitReq):
    now_ms=int(time.time()*1000)
    if now_ms - req.started_at > (DURATION+3)*1000: raise HTTPException(400,"time exceeded")
    items=gen_scene(today_seed()); score=0; t_cursor=0
    for tap in req.taps_ms:
        if tap < t_cursor: continue
        ang=angle_at(tap); hit,length=cast_line(ang,items)
        travel_ms=int((length/ROPE_SPEED)*1000)
        if not hit:
            t_cursor = tap + travel_ms + travel_ms
            if t_cursor > DURATION*1000: break
            continue
        val=VALUES[hit.kind]; rtime=int(retract_time(length, hit.kind)*1000)
        score+=val; items.remove(hit)
        t_cursor = tap + travel_ms + rtime
        if t_cursor > DURATION*1000: break
    return SubmitResp(ok=True, score=score)
