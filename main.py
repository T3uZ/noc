from fastapi import FastAPI, HTTPException
from sqlalchemy import func
from database import Base, engine, SessionLocal
from models import Event
from config import SECRET_TOKEN
from ai_agent import ask_ai
from datetime import datetime, timedelta
import json
import re

app = FastAPI(title="NOC AI API")

Base.metadata.create_all(bind=engine)

# =========================================================
# 🔎 CONFIGURAÇÕES INTELIGENTES
# =========================================================

STOPWORDS = {
    "para", "teve", "tivemos", "alerta", "equipamento",
    "host", "ainda", "esta", "está", "algum", "alguma",
    "hoje", "ontem", "problema", "problemas", "do", "da",
    "de", "no", "na"
}

STATUS_KEYWORDS = {
    "fora": "PROBLEM",
    "down": "PROBLEM",
    "problema": "PROBLEM",
    "alerta": "PROBLEM",
    "erro": "PROBLEM",
    "ok": "OK",
    "normal": "OK",
    "resolvido": "OK"
}

SEVERITY_KEYWORDS = {
    "warning": "Warning",
    "average": "Average",
    "high": "High",
    "disaster": "Disaster"
}

MAX_EVENTS_QUERY = 100


# =========================================================
# 🩺 HEALTHCHECK
# =========================================================
@app.get("/health")
def health():
    return {"status": "running"}


# =========================================================
# 📥 WEBHOOK ZABBIX
# =========================================================
@app.post("/zabbix/webhook")
async def receive_event(payload: dict):

    if payload.get("token") != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    required_fields = ["event_id", "host", "trigger_name", "status", "severity"]

    for field in required_fields:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing field: {field}")

    db = SessionLocal()

    try:
        existing = db.query(Event).filter(
            Event.event_id == payload["event_id"]
        ).first()

        if existing:
            return {"status": "already_exists"}

        event = Event(
            event_id=str(payload["event_id"]),
            host=payload["host"],
            trigger_name=payload["trigger_name"],
            status=payload["status"].upper(),
            severity=payload["severity"].capitalize(),
            raw_data=json.dumps(payload)
        )

        db.add(event)
        db.commit()

        return {"status": "saved"}

    finally:
        db.close()


# =========================================================
# 🔍 BUSCA FLEXÍVEL DE HOST
# =========================================================
def find_hosts_by_keywords(db, keywords):

    host_scores = {}

    for word in keywords:
        matches = db.query(Event.host)\
            .filter(func.lower(Event.host).like(f"%{word}%"))\
            .distinct()\
            .all()

        for (host_name,) in matches:
            host_scores[host_name] = host_scores.get(host_name, 0) + 1

    return sorted(host_scores, key=host_scores.get, reverse=True)


# =========================================================
# 🤖 PERGUNTAS INTELIGENTES NOC
# =========================================================
@app.post("/ask")
def ask_host(data: dict):

    question = data.get("question", "").lower().strip()

    if not question:
        return {"response": "Pergunta vazia."}

    db = SessionLocal()

    try:
        now = datetime.utcnow()
        start_time = None
        end_time = now

        # -------------------------------------
        # 🕒 Detectar período
        # -------------------------------------
        if "hoje" in question:
            start_time = datetime(now.year, now.month, now.day)

        elif "ontem" in question:
            yesterday = now - timedelta(days=1)
            start_time = datetime(yesterday.year, yesterday.month, yesterday.day)
            end_time = start_time + timedelta(days=1)

        elif "24h" in question or "últimas 24" in question:
            start_time = now - timedelta(hours=24)

        # -------------------------------------
        # 🎯 Detectar severidade
        # -------------------------------------
        severity_filter = None
        for key in SEVERITY_KEYWORDS:
            if key in question:
                severity_filter = SEVERITY_KEYWORDS[key]

        # -------------------------------------
        # 🎯 Detectar status
        # -------------------------------------
        status_filter = None
        for key in STATUS_KEYWORDS:
            if key in question:
                status_filter = STATUS_KEYWORDS[key]

        # -------------------------------------
        # 🔎 Extrair palavras
        # -------------------------------------
        words = re.findall(r'[a-z0-9\-]{3,}', question)
        words = [w for w in words if w not in STOPWORDS]

        # =====================================================
        # 🔥 PERGUNTA GLOBAL (SEM HOST)
        # =====================================================
        if not words:

            query = db.query(Event)

            if start_time:
                query = query.filter(Event.created_at >= start_time)
                query = query.filter(Event.created_at <= end_time)

            if status_filter:
                query = query.filter(Event.status == status_filter)

            if severity_filter:
                query = query.filter(Event.severity == severity_filter)

            events = query.order_by(Event.created_at.desc())\
                          .limit(MAX_EVENTS_QUERY)\
                          .all()

            total = len(events)
            problems = sum(1 for e in events if e.status == "PROBLEM")

            if total == 0:
                return {"response": "Nenhum evento encontrado no período solicitado."}

            return {
                "response": f"Foram registrados {total} eventos. "
                            f"{problems} estão em estado PROBLEM."
            }

        # =====================================================
        # 🔎 BUSCA POR HOST
        # =====================================================
        sorted_hosts = find_hosts_by_keywords(db, words)

        if not sorted_hosts:
            return {"response": "Nenhum host correspondente encontrado."}

        sorted_hosts = sorted_hosts[:3]

        context_data = {}

        for host_name in sorted_hosts:

            query = db.query(Event).filter(Event.host == host_name)

            if start_time:
                query = query.filter(Event.created_at >= start_time)
                query = query.filter(Event.created_at <= end_time)

            if status_filter:
                query = query.filter(Event.status == status_filter)

            if severity_filter:
                query = query.filter(Event.severity == severity_filter)

            events = query.order_by(Event.created_at.desc())\
                          .limit(MAX_EVENTS_QUERY)\
                          .all()

            total = len(events)
            open_problems = sum(1 for e in events if e.status == "PROBLEM")
            last_event = events[0] if events else None

            context_data[host_name] = {
                "total_events": total,
                "open_problems": open_problems,
                "last_status": last_event.status if last_event else None,
                "last_event": last_event.trigger_name if last_event else None,
                "last_severity": last_event.severity if last_event else None,
                "last_time": str(last_event.created_at) if last_event else None
            }

        main_host = sorted_hosts[0]
        host_data = context_data[main_host]

        # =====================================================
        # 🔥 RESPOSTAS DIRETAS (SEM IA)
        # =====================================================
        if "fora" in question or "down" in question:
            if host_data["last_status"] == "PROBLEM":
                return {
                    "response": f"O equipamento {main_host} está com problema. "
                                f"Último alerta: {host_data['last_event']} "
                                f"(Severidade: {host_data['last_severity']})."
                }
            else:
                return {
                    "response": f"O equipamento {main_host} está normal no momento."
                }

        if "quantos" in question or "quantidade" in question:
            return {
                "response": f"O host {main_host} possui "
                            f"{host_data['total_events']} eventos no período. "
                            f"{host_data['open_problems']} ainda abertos."
            }

        # =====================================================
        # 🤖 PERGUNTAS ANALÍTICAS → IA
        # =====================================================
        ai_response = ask_ai(question, context_data)

        return {"response": ai_response}

    finally:
        db.close()