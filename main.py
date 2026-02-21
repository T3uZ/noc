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

    print("==== WEBHOOK RECEBIDO ====")
    print(json.dumps(payload, indent=2))

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

    question = data.get("question", "").lower().strip()

    if not question:
        return {"response": "Pergunta vazia."}

    db = SessionLocal()

    # 🔎 Extrai palavras relevantes
    words = re.findall(r'[a-z0-9\-]{3,}', question)

    # remove palavras muito genéricas
    stopwords = {"para", "teve", "tivemos", "alerta", "equipamento", "host", "ainda", "esta", "está"}
    words = [w for w in words if w not in stopwords]

    if not words:
        db.close()
        return {"response": "Não consegui identificar o host na pergunta."}

    # 🎯 Ranking por ocorrência
    host_scores = {}

    for word in words:
        matches = db.query(Event.host)\
            .filter(func.lower(Event.host).like(f"%{word}%"))\
            .distinct()\
            .all()

        for (host_name,) in matches:
            host_scores[host_name] = host_scores.get(host_name, 0) + 1

    if not host_scores:
        db.close()
        return {"response": "Nenhum host correspondente encontrado."}

    # Ordena por relevância
    sorted_hosts = sorted(host_scores, key=host_scores.get, reverse=True)

    # Limita aos 3 mais relevantes
    sorted_hosts = sorted_hosts[:3]

    context_data = {}

    for host_name in sorted_hosts:
        events = db.query(Event)\
            .filter(Event.host == host_name)\
            .order_by(Event.created_at.desc())\
            .limit(30)\
            .all()

        total = len(events)
        open_problems = sum(1 for e in events if e.status == "PROBLEM")
        last_status = events[0].status if events else "Unknown"
        last_event = events[0].trigger_name if events else None
        last_severity = events[0].severity if events else None

        context_data[host_name] = {
            "score": host_scores[host_name],
            "total_events": total,
            "open_problems": open_problems,
            "last_status": last_status,
            "last_event": last_event,
            "last_severity": last_severity
        }

    db.close()

    # 🔥 Se quiser resposta direta sem IA quando for pergunta simples:
    if "fora" in question or "down" in question:
        main_host = sorted_hosts[0]
        if context_data[main_host]["last_status"] == "PROBLEM":
            return {
                "response": f"O equipamento {main_host} ainda está com problema. "
                            f"Último alerta: {context_data[main_host]['last_event']} "
                            f"(Severidade: {context_data[main_host]['last_severity']})."
            }
        else:
            return {
                "response": f"O equipamento {main_host} está normal no momento."
            }

    # 🤖 Caso contrário, envia contexto para IA
    response = ask_ai(question, context_data)

    return {"response": response}