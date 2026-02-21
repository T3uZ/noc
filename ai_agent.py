from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

def ask_ai(question, context):

    prompt = f"""
Você é um analista NOC especialista em redes de ISP.

Analise os dados abaixo e responda de forma técnica, clara e objetiva.

Dados:
{context}

Pergunta:
{question}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Você é um especialista em NOC e redes ISP."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )

    return response.choices[0].message.content