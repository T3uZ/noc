from fastapi import FastAPI, HTTPException
from sqlalchemy import or_
from database import Base, engine, SessionLocal
from models import Event
from config import SECRET_TOKEN
from ai_agent import ask_ai
import re
from sqlalchemy import func
import json

app = FastAPI(title="NOC AI API")

Base.metadata.create_all(bind=engine)

# =========================
# RECEBER WEBHOOK ZABBIX
# =========================
@app.post("/zabbix/webhook")
async def receive_event(payload: dict):

    if payload.get("token") != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    db = SessionLocal()

    existing = db.query(Event).filter(
        Event.event_id == payload["event_id"]
    ).first()

    if existing:
        db.close()
        return {"status": "already_exists"}

    event = Event(
        event_id=payload["event_id"],
        host=payload["host"],
        trigger_name=payload["trigger_name"],
        status=payload["status"],
        severity=payload["severity"],
        raw_data=json.dumps(payload)
    )

    db.add(event)
    db.commit()
    db.close()

    return {"status": "saved"}


# =========================
# BUSCA FLEXÍVEL POR HOST
# =========================
def find_hosts_like(db, partial_name: str):
    return db.query(Event.host)\
        .filter(Event.host.ilike(f"%{partial_name}%"))\
        .distinct()\
        .all()


# =========================
# CONSULTAR EVENTOS POR HOST (PARCIAL)
# =========================
@app.get("/events/search/{partial_host}")
def search_events(partial_host: str):

    db = SessionLocal()

    hosts = find_hosts_like(db, partial_host)

    if not hosts:
        db.close()
        return {"message": "Nenhum host encontrado."}

    result = {}

    for (host_name,) in hosts:
        events = db.query(Event)\
            .filter(Event.host == host_name)\
            .order_by(Event.created_at.desc())\
            .limit(20)\
            .all()

        result[host_name] = events

    db.close()
    return result


# =========================
# PERGUNTAR PARA IA
# =========================
@app.post("/ask")
def ask_host(data: dict):

    question = data.get("question")

    db = SessionLocal()

    # 🔎 Extrai palavras em MAIÚSCULO (possíveis hosts)
    words = re.findall(r'\b[A-Z0-9\-]{3,}\b', question)

    if not words:
        db.close()
        return {"response": "Não consegui identificar o host na pergunta."}

    possible_hosts = []

    for word in words:
        matches = db.query(Event.host)\
            .filter(Event.host.ilike(f"%{word}%"))\
            .distinct()\
            .all()

        for (host_name,) in matches:
            possible_hosts.append(host_name)

    if not possible_hosts:
        db.close()
        return {"response": "Nenhum host correspondente encontrado."}

    context_data = {}

    for host_name in possible_hosts:
        events = db.query(Event)\
            .filter(Event.host == host_name)\
            .order_by(Event.created_at.desc())\
            .limit(30)\
            .all()

        total = len(events)
        open_problems = sum(1 for e in events if e.status == "PROBLEM")
        last_status = events[0].status if events else "Unknown"

        context_data[host_name] = {
            "total_events": total,
            "open_problems": open_problems,
            "last_status": last_status,
            "last_event": events[0].trigger_name if events else None
        }

    db.close()

    response = ask_ai(question, context_data)

    return {"response": response}