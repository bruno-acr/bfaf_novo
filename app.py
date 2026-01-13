import os
import csv
import io
from flask import Flask, render_template, request, redirect, url_for, Response
from flask_basicauth import BasicAuth

import db
from questions import QUESTOES

# ======================================================
# APP CONFIG
# ======================================================
app = Flask(__name__)

# ======================================================
# BANCO - Inicialização
# ======================================================
db.init_db()

# ======================================================
# AUTH ADMIN - Configuração Segura
# ======================================================
# Se as variáveis não existirem no Render, usa os padrões abaixo
app.config['BASIC_AUTH_USERNAME'] = os.environ.get('ADMIN_USER', 'CPAFF')
app.config['BASIC_AUTH_PASSWORD'] = os.environ.get('ADMIN_PASS', 'CPAFF_@dmin')

# Mantemos FALSE para o paciente conseguir responder sem senha. 
# Apenas as rotas com @basic_auth.required pedirão login.
app.config['BASIC_AUTH_FORCE'] = False

basic_auth = BasicAuth(app)

# ======================================================
# MAPA DE QUESTÕES
# ======================================================
QUESTION_MAP = {q["id"]: q for q in QUESTOES}

# ======================================================
# ROTA: HOME
# ======================================================
@app.route('/')
def index():
    intro = (
        "O objetivo desse questionário é identificar a presença de barreiras e facilitadores "
        "para a adesão à farmacoterapia em condições crônicas de saúde e, com isso, nortear "
        "as intervenções dos profissionais de saúde."
    )
    return render_template("index.html", intro=intro)

# ======================================================
# ROTA: DADOS DO PACIENTE
# ======================================================
@app.route('/patient', methods=['GET', 'POST'])
def patient():
    if request.method == 'POST':
        patient_id = db.save_patient(
            request.form.get("patient_name", ""),
            request.form.get("medication", ""),
            request.form.get("disease", ""),
            request.form.get("professional_name", "")
        )
        return redirect(url_for("questions", patient_id=patient_id))
    return render_template("patient.html")

# ======================================================
# ROTA: QUESTIONÁRIO
# ======================================================
@app.route('/questions/<int:patient_id>', methods=['GET', 'POST'])
def questions(patient_id):
    if request.method == 'POST':
        for q in QUESTOES:
            qid = q["id"]
            alternativa = request.form.get(f"q_{qid}_alt")
            open_text = request.form.get(f"q_{qid}_open", "").strip()

            # --- TRAVA NA RAIZ (PYTHON) PARA A QUESTÃO 8 ---
            if qid == 8 and open_text:
                try:
                    valor_num = int(open_text)
                    if valor_num > 110:
                        open_text = "110" # Se for maior, o Python força para 110
                except ValueError:
                    open_text = "0" # Se não for número, o Python limpa
            # -----------------------------------------------

            valor = q.get("alternativas", {}).get(alternativa, 0)
            is_barreira = int(alternativa in q.get("barreira_if", []))
            is_facilitador = int(alternativa in q.get("facilitador_if", []))
            
            # Salvando no banco (limpo, sem judgement)
            db.save_response(
                patient_id, qid, alternativa or "", valor, 
                open_text, "", is_barreira, is_facilitador, 
                q.get("classificacao_texto", {}).get(alternativa, "")
            )

        return redirect(url_for("result", patient_id=patient_id))
    return render_template("questions.html", questoes=QUESTOES, patient_id=patient_id)

# ======================================================
# ROTA: RESULTADO INDIVIDUAL
# ======================================================
@app.route('/result/<int:patient_id>')
def result(patient_id):
    stats = db.calculate_score_and_counts(patient_id)
    respostas_raw = db.get_responses_by_patient(patient_id)
    respostas, barreiras, facilitadores = [], [], []

    for r in respostas_raw:
        q = QUESTION_MAP.get(r["question_id"], {})
        enriched = {
            "question_text": q.get("texto", ""),
            "alternativa_label": q.get("labels", {}).get(r.get("alternativa"), ""),
            "open_text": r.get("open_text"),
            "judgement": r.get("judgement"),
            "valor": r.get("valor"),
            "is_barreira": r.get("is_barreira"),
            "is_facilitador": r.get("is_facilitador"),
            "classificacao_texto": r.get("classificacao_texto")
        }
        respostas.append(enriched)
        if r["is_barreira"]: barreiras.append(enriched)
        if r["is_facilitador"]: facilitadores.append(enriched)

    return render_template("result.html", stats=stats, respostas=respostas, barreiras=barreiras, facilitadores=facilitadores)

# ======================================================
# ADMIN - Protegido por Senha
# ======================================================
@app.route('/admin')
@basic_auth.required
def admin():
    patients = db.get_all_patients()
    dataset = []
    for p in patients:
        dataset.append({
            "patient": p,
            "respostas": db.get_responses_by_patient(p["id"]),
            "stats": db.calculate_score_and_counts(p["id"])
        })
    return render_template("admin.html", dataset=dataset)

# ======================================================
# EXPORTAÇÃO CSV DETALHADA
# ======================================================
@app.route('/admin/export')
@basic_auth.required
def admin_export():
    patients = db.get_all_patients()
    def generate():
        header = ["patient_id","patient_name","medication","disease","professional_name","question_id","alternativa","open_text","valor","barreira","facilitador"]
        yield ",".join(header) + "\n"
        for p in patients:
            for r in db.get_responses_by_patient(p["id"]):
                yield ",".join(map(str, [p["id"], p["patient_name"], p["medication"], p["disease"], p["professional_name"], r["question_id"], r["alternativa"], r["open_text"], r["valor"], r["is_barreira"], r["is_facilitador"]])) + "\n"
    return Response(generate(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=export_detalhado.csv"})

# ======================================================
# EXPORTAÇÃO CSV AMPLA (WIDE)
# ======================================================
@app.route('/admin/export_wide')
@basic_auth.required
def admin_export_wide():
    patients = db.get_all_patients()
    qids = [q["id"] for q in QUESTOES]
    output = io.StringIO()
    writer = csv.writer(output)

    header = ["patient_id","patient_name","medication","disease","professional_name"]
    for qid in qids:
        header.extend([f"Q{qid}_resposta", f"Q{qid}_valor"])
    writer.writerow(header)

    for p in patients:
        row = [p["id"], p["patient_name"], p["medication"], p["disease"], p["professional_name"]]
        respostas = {r["question_id"]: r for r in db.get_responses_by_patient(p["id"])}
        for qid in qids:
            r = respostas.get(qid)
            row.extend([r.get("open_text") or r.get("alternativa"), r.get("valor")] if r else ["",""])
        writer.writerow(row)

    output.seek(0)
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=export_wide.csv"})

if __name__ == "__main__":
    app.run(debug=True)