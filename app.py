"""
SHEKINAH TUTOR ICFES — App web con Claude API  v2.0
Comunidad Juvenil Shekinah · Parroquia San Luis Maria de Montfort
Villavicencio, Meta, Colombia

MEJORAS v2.0:
  1. Historial de chat persistente en PostgreSQL (sobrevive reinicios)
  2. Tabla puntajes_diagnostico: guarda resultados por area
  3. Panel admin ampliado: mapa de debilidades + estadisticas por area
  4. Modelo Sonnet para diagnostico, Haiku para chat libre
  5. Endpoint /ping para mantener el servidor despierto (cron-job.org)
  6. Frases dinamicas del Camino Shekinah por sesion
"""

import os
import json
import uuid
import csv
import io
import random
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, send_file
from anthropic import Anthropic
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Claude API Client ---
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

MODEL_CHAT        = "claude-haiku-4-5-20251001"   # Chat libre: economico y rapido
MODEL_DIAGNOSTICO = "claude-sonnet-4-6"            # Diagnostico: mas potente

# --- Frases dinamicas del Camino Shekinah ---
FRASES_SHEKINAH = [
    "\"Si me amas, habitare en tu corazon.\" (Jn 14,23) — Hoy estudias con Dios dentro de ti.",
    "Tu talento es un don de Dios. Honrarlo con esfuerzo es tu oracion de hoy.",
    "Shekinah significa la presencia de Dios. El esta aqui, contigo, en cada pregunta.",
    "La universidad es tu siguiente mision. El Camino Shekinah te preparo para esto.",
    "\"Todo lo puedo en Cristo que me fortalece.\" (Fil 4,13) — Incluido el ICFES.",
    "Cada error que corriges hoy es una victoria que celebras el dia del examen.",
    "Dios puso talentos unicos en ti. Este tutor existe para ayudarte a descubrirlos.",
    "La excelencia academica y la fe no se contradicen: se potencian.",
    "El P. Elver y toda Shekinah oran por tu exito. No estas solo en esto.",
    "\"Buscad primero el Reino de Dios... y todo lo demas se os dara por anadidura.\" (Mt 6,33)",
]

# --- Database Connection ---
DB_URL         = os.environ.get("DATABASE_URL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Totustuus2026")
ACCESS_CODE    = os.environ.get("ACCESS_CODE",    "Shekinah2026")

# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------

def get_db_connection():
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        return conn
    except Exception as e:
        print(f"Error conectando DB: {e}")
        return None


def init_db():
    """Crea o actualiza todas las tablas necesarias."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()

        # ── Tabla inscritos (original + columnas nuevas) ──────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inscritos (
                id             SERIAL PRIMARY KEY,
                nombre         VARCHAR(100) NOT NULL,
                grado          VARCHAR(50),
                fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address     VARCHAR(45),
                user_agent     TEXT,
                activo         BOOLEAN DEFAULT TRUE,
                bloqueado_en   TIMESTAMP,
                motivo_bloqueo VARCHAR(200)
            )
        """)
        for col, definition in [
            ("activo",         "BOOLEAN DEFAULT TRUE"),
            ("bloqueado_en",   "TIMESTAMP"),
            ("motivo_bloqueo", "VARCHAR(200)"),
        ]:
            try:
                cur.execute(f"ALTER TABLE inscritos ADD COLUMN IF NOT EXISTS {col} {definition}")
            except Exception:
                pass

        # ── NUEVA: historial de chat persistente ─────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         SERIAL PRIMARY KEY,
                session_id VARCHAR(60) NOT NULL,
                nombre     VARCHAR(100),
                role       VARCHAR(20) NOT NULL,
                content    TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id)")

        # ── NUEVA: puntajes del diagnostico por area ──────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS puntajes_diagnostico (
                id                SERIAL PRIMARY KEY,
                nombre            VARCHAR(100) NOT NULL,
                grado             VARCHAR(50),
                session_id        VARCHAR(60),
                lectura_critica   NUMERIC(4,1),
                matematicas       NUMERIC(4,1),
                ciencias_naturales NUMERIC(4,1),
                sociales          NUMERIC(4,1),
                ingles            NUMERIC(4,1),
                puntaje_global    NUMERIC(6,1),
                nivel_general     VARCHAR(50),
                resumen_texto     TEXT,
                fecha             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("✓ Base de datos inicializada (v2.0)")
    except Exception as e:
        print(f"Error inicializando DB: {e}")


# ---------------------------------------------------------------------------
# INSCRITOS
# ---------------------------------------------------------------------------

def save_registro(nombre, grado):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        ip         = request.remote_addr or "unknown"
        user_agent = request.headers.get('User-Agent', '')[:500]
        cur.execute(
            "INSERT INTO inscritos (nombre, grado, ip_address, user_agent) VALUES (%s,%s,%s,%s)",
            (nombre, grado, ip, user_agent)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error guardando registro: {e}")
        return False


def get_inscritos():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, nombre, grado, fecha_registro, ip_address,
                   activo, bloqueado_en, motivo_bloqueo
            FROM inscritos ORDER BY fecha_registro DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"Error obteniendo inscritos: {e}")
        return []


def is_student_active(nombre):
    conn = get_db_connection()
    if not conn:
        return True
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT activo FROM inscritos WHERE LOWER(nombre)=LOWER(%s) ORDER BY fecha_registro DESC LIMIT 1",
            (nombre,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return True if row is None else row['activo']
    except Exception as e:
        print(f"Error verificando estudiante: {e}")
        return True


# ---------------------------------------------------------------------------
# HISTORIAL DE CHAT PERSISTENTE (MEJORA 1)
# ---------------------------------------------------------------------------

def load_chat_history(session_id: str) -> list:
    """Carga el historial desde PostgreSQL."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT role, content FROM chat_messages WHERE session_id=%s ORDER BY id ASC",
            (session_id,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception as e:
        print(f"Error cargando historial: {e}")
        return []


def save_message(session_id: str, role: str, content: str, nombre: str = ""):
    """Guarda un mensaje en PostgreSQL."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_messages (session_id, nombre, role, content) VALUES (%s,%s,%s,%s)",
            (session_id, nombre, role, content)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error guardando mensaje: {e}")


# ---------------------------------------------------------------------------
# PUNTAJES DEL DIAGNOSTICO (MEJORA 2)
# ---------------------------------------------------------------------------

def save_puntaje_diagnostico(nombre, grado, session_id, puntajes: dict, resumen: str):
    """
    Guarda el resultado del diagnostico.
    puntajes = {"lectura": 4, "matematicas": 3, "ciencias": 2, "sociales": 5, "ingles": 3}
    """
    conn = get_db_connection()
    if not conn:
        return False
    try:
        # Formula global ICFES: (Mat*3 + Lect*3 + Soc*3 + CN*3 + Ing) / 13  → escalar a 500
        lect = puntajes.get("lectura", 0)
        mat  = puntajes.get("matematicas", 0)
        cn   = puntajes.get("ciencias", 0)
        soc  = puntajes.get("sociales", 0)
        ing  = puntajes.get("ingles", 0)

        # Convertir puntajes sobre 5 a escala 0-100
        def to100(x): return round((x / 5) * 100, 1)

        global_score = round((to100(mat)*3 + to100(lect)*3 + to100(soc)*3 + to100(cn)*3 + to100(ing)) / 13, 1)

        if global_score >= 80:
            nivel = "Optimizacion (nivel 4)"
        elif global_score >= 60:
            nivel = "Consolidacion (nivel 3)"
        elif global_score >= 40:
            nivel = "Refuerzo (nivel 2)"
        else:
            nivel = "Rescate urgente (nivel 1)"

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO puntajes_diagnostico
                (nombre, grado, session_id, lectura_critica, matematicas,
                 ciencias_naturales, sociales, ingles, puntaje_global, nivel_general, resumen_texto)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (nombre, grado, session_id,
              to100(lect), to100(mat), to100(cn), to100(soc), to100(ing),
              global_score, nivel, resumen[:2000]))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error guardando puntaje: {e}")
        return False


def get_puntajes_diagnostico():
    """Obtiene todos los diagnosticos para el panel admin."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT nombre, grado, lectura_critica, matematicas, ciencias_naturales,
                   sociales, ingles, puntaje_global, nivel_general, fecha
            FROM puntajes_diagnostico
            ORDER BY fecha DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"Error obteniendo puntajes: {e}")
        return []


# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
Eres el TUTOR ICFES SHEKINAH, un pedagogo experto en preparacion para el Examen
de Estado Saber 11 de Colombia. Trabajas exclusivamente para la Comunidad Juvenil
Shekinah de la Parroquia San Luis Maria de Montfort, Villavicencio, Meta.

TU IDENTIDAD:
- Nombre: Tutor ICFES Shekinah
- Mision: Llevar a cada joven de Shekinah desde su punto de partida real hasta
  su maximo potencial en el ICFES Saber 11.
- Estilo: Cercano, motivador, exigente pero carinoso. Usas lenguaje colombiano
  natural. Tratas al estudiante de "tu" y con calidez pastoral.
- Lema de Shekinah: "Si me amas, habitare en tu corazon" (cf. Jn 14,23)

CONTEXTO DE SHEKINAH:
- Comunidad juvenil parroquial (14-24 anos), carisma cristocentrico y mariano.
- Los jovenes vienen de diversos contextos socioeconomicos de Villavicencio.
- El Asesor Espiritual es el P. Elver Urrego Beltran (MPPV).
- Queremos que nuestros jovenes accedan a la universidad publica como herramienta
  de transformacion social y testimonio cristiano.

METODOLOGIAS QUE INTEGRAS:
1. ABP (Aprendizaje Basado en Problemas): conceptos desde situaciones reales.
2. Repeticion espaciada: revision en intervalos crecientes.
3. Analisis de distractores: por que las incorrectas parecen correctas.
4. Gestion de ansiedad: tecnicas cognitivo-conductuales para examenes.

DATOS DEL EXAMEN ICFES SABER 11:
- 5 pruebas: Lectura Critica, Matematicas, Ciencias Naturales, Sociales y Ciudadanas, Ingles
- ~254 preguntas de seleccion multiple, unica respuesta
- 2 sesiones de 4h 30min (9 horas total)
- Puntaje por area: 0-100. Global max 500.
- Formula global: (Mat*3 + Lect*3 + Soc*3 + CN*3 + Ing) / 13
- El ingles pesa MENOS. Las 4 areas x3 son las de mayor rentabilidad.

PROTOCOLO DE SESION:
1. REVISION (verificar tarea anterior, resolver dudas)
2. TEORIA ACTIVA CON ABP (partir de un problema, preguntas socraticas)
3. PRACTICA GUIADA (4-6 preguntas tipo ICFES con protocolo LEAD)
4. PRACTICA INDEPENDIENTE (5-8 preguntas solo)
5. RETROALIMENTACION OBLIGATORIA (formato completo por pregunta)

PROTOCOLO LEAD (para cada pregunta):
L - Leer el enunciado completo antes de ver opciones
E - Eliminar opciones absurdas
A - Analizar los dos candidatos finales (cual es el distractor?)
D - Decidir y justificar la respuesta

RETROALIMENTACION OBLIGATORIA POR PREGUNTA:
- Respuesta correcta: letra + texto + por que es correcta (paso a paso)
- Por que son incorrectas las otras: tipo de distractor + razon especifica
  (Tipo 1: Verdad parcial, Tipo 2: Inversion, Tipo 3: Generalizacion,
   Tipo 4: Contradiccion directa, Tipo 5: Fuera de contexto)
- Si el estudiante fallo: MICROCLASE DE CORRECCION (nombrar concepto,
  explicar con ABP, aplicar a la pregunta, verificar con pregunta analoga)

PROTOCOLO DE EXAMEN DIAGNOSTICO:
Cuando recibas [INICIAR_DIAGNOSTICO]:
1. Presentar 5 preguntas por area (25 total), nivel progresivo
2. Preguntas tipo ICFES real con 4 opciones (A, B, C, D)
3. Presentar UNA pregunta a la vez, esperar respuesta
4. Retroalimentacion COMPLETA despues de cada respuesta
5. Al terminar las 25, generar INFORME DIAGNOSTICO con este formato EXACTO:

---INFORME_DIAGNOSTICO---
LECTURA_CRITICA: [puntaje]/5
MATEMATICAS: [puntaje]/5
CIENCIAS_NATURALES: [puntaje]/5
SOCIALES: [puntaje]/5
INGLES: [puntaje]/5
NIVEL: [Rescate urgente|Refuerzo|Consolidacion|Optimizacion]
RESUMEN: [2-3 oraciones con las principales fortalezas y debilidades]
---FIN_INFORME---

Luego continua con el plan de accion personalizado.

FORMATO PARA PREGUNTAS (OBLIGATORIO):
A. [texto de la opcion]
B. [texto de la opcion]
C. [texto de la opcion]
D. [texto de la opcion]

Nunca uses parentesis, negritas en letras, "a)", "A:", ni "A.-".
Siempre "A. " con punto y espacio. Este formato permite botones en celular.

PRINCIPIOS IRRENUNCIABLES:
- Competencia > memorizacion
- El error es datos, no fracaso
- Conectar siempre con la meta universitaria del joven
- El tiempo es variable pedagogica
- Distractores como herramienta de aprendizaje
- Repeticion espaciada vence al repaso masivo

CIERRE DE SESION:
Termina cada sesion importante con:
"Recuerda: Dios habita en ti (Shekinah). Tu esfuerzo hoy es semilla
del futuro que El suena para ti. Nos vemos en la proxima sesion."
"""


# ---------------------------------------------------------------------------
# RUTAS PRINCIPALES
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ping")
def ping():
    """Endpoint para cron-job.org — mantiene el servidor despierto."""
    return jsonify({"status": "alive", "timestamp": datetime.utcnow().isoformat()})


@app.route("/api/frase")
def frase_shekinah():
    """Devuelve una frase aleatoria del Camino Shekinah (MEJORA 3)."""
    return jsonify({"frase": random.choice(FRASES_SHEKINAH)})


# ---------------------------------------------------------------------------
# CHAT (con historial persistente — MEJORA 1)
# ---------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
def chat():
    data         = request.json or {}
    user_message = data.get("message", "")
    session_id   = data.get("session_id", "")
    nombre       = data.get("nombre", "")

    # Crear nueva sesion si no existe
    if not session_id:
        session_id = str(uuid.uuid4())

    # Cargar historial desde DB
    history = load_chat_history(session_id)

    # Guardar mensaje del usuario
    save_message(session_id, "user", user_message, nombre)
    history.append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model=MODEL_CHAT,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=history,
        )

        assistant_text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        # Guardar respuesta del asistente
        save_message(session_id, "assistant", assistant_text, nombre)

        return jsonify({
            "response":   assistant_text,
            "session_id": session_id,
            "usage": {
                "input_tokens":  response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# DIAGNOSTICO (Sonnet + guarda puntajes — MEJORAS 2 y 4)
# ---------------------------------------------------------------------------

@app.route("/api/diagnostico", methods=["POST"])
def diagnostico():
    """Inicia un examen diagnostico nuevo con modelo Sonnet."""
    data    = request.json or {}
    nombre  = data.get("nombre", "")
    grado   = data.get("grado", "")

    session_id = str(uuid.uuid4())

    init_msg = f"[INICIAR_DIAGNOSTICO] Mi nombre es {nombre}." if nombre else "[INICIAR_DIAGNOSTICO]"
    if grado:
        init_msg += f" Estoy en grado {grado}."

    save_message(session_id, "user", init_msg, nombre)

    try:
        response = client.messages.create(
            model=MODEL_DIAGNOSTICO,   # Sonnet para diagnostico
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": init_msg}],
        )

        assistant_text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        save_message(session_id, "assistant", assistant_text, nombre)

        return jsonify({"response": assistant_text, "session_id": session_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat_diagnostico", methods=["POST"])
def chat_diagnostico():
    """
    Continua un diagnostico en curso (Sonnet).
    Detecta el INFORME_DIAGNOSTICO al final y lo guarda automaticamente.
    """
    data         = request.json or {}
    user_message = data.get("message", "")
    session_id   = data.get("session_id", "")
    nombre       = data.get("nombre", "")
    grado        = data.get("grado", "")

    history = load_chat_history(session_id)
    save_message(session_id, "user", user_message, nombre)
    history.append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model=MODEL_DIAGNOSTICO,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=history,
        )

        assistant_text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        save_message(session_id, "assistant", assistant_text, nombre)

        # ── Detectar y guardar informe final ────────────────────────────
        puntajes_guardados = False
        if "---INFORME_DIAGNOSTICO---" in assistant_text:
            puntajes = _extraer_puntajes(assistant_text)
            resumen  = _extraer_resumen(assistant_text)
            if puntajes:
                save_puntaje_diagnostico(nombre, grado, session_id, puntajes, resumen)
                puntajes_guardados = True

        return jsonify({
            "response":           assistant_text,
            "session_id":         session_id,
            "puntajes_guardados": puntajes_guardados,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _extraer_puntajes(texto: str) -> dict:
    """Extrae los puntajes del bloque ---INFORME_DIAGNOSTICO---."""
    import re
    puntajes = {}
    mapa = {
        "LECTURA_CRITICA":    "lectura",
        "MATEMATICAS":        "matematicas",
        "CIENCIAS_NATURALES": "ciencias",
        "SOCIALES":           "sociales",
        "INGLES":             "ingles",
    }
    for clave, campo in mapa.items():
        m = re.search(rf"{clave}:\s*([\d.]+)", texto)
        if m:
            try:
                puntajes[campo] = float(m.group(1))
            except ValueError:
                pass
    return puntajes


def _extraer_resumen(texto: str) -> str:
    """Extrae el RESUMEN del bloque de informe."""
    import re
    m = re.search(r"RESUMEN:\s*(.+?)(?:---FIN_INFORME---|$)", texto, re.DOTALL)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# REGISTRO Y VERIFICACION
# ---------------------------------------------------------------------------

@app.route("/api/registro", methods=["POST"])
def registro():
    data   = request.json or {}
    nombre = data.get("nombre", "").strip()
    grado  = data.get("grado",  "").strip()
    codigo = data.get("codigo", "").strip()

    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400
    if codigo.lower() != ACCESS_CODE.lower():
        return jsonify({"error": "Codigo de acceso incorrecto"}), 403

    saved = save_registro(nombre, grado)
    return jsonify({"status": "ok", "saved_to_db": saved, "nombre": nombre, "grado": grado})


@app.route("/api/verificar", methods=["POST"])
def verificar_acceso():
    data   = request.json or {}
    nombre = data.get("nombre", "").strip()
    if not nombre:
        return jsonify({"activo": True})
    return jsonify({"activo": is_student_active(nombre)})


@app.route("/api/reset", methods=["POST"])
def reset():
    """Borra el historial de una sesion de la DB."""
    data       = request.json or {}
    session_id = data.get("session_id", "")
    if not session_id:
        return jsonify({"status": "ok"})

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"Error borrando sesion: {e}")
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# PANEL ADMINISTRADOR (MEJORA 2: incluye mapa de debilidades)
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_panel():
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return _login_page(), 401

    registros  = get_inscritos()
    puntajes   = get_puntajes_diagnostico()
    total      = len(registros)
    activos    = sum(1 for r in registros if r['activo'])
    bloqueados = total - activos
    n_diag     = len(puntajes)

    # Promedios globales por area
    def avg(field):
        vals = [float(p[field]) for p in puntajes if p[field] is not None]
        return round(sum(vals)/len(vals), 1) if vals else 0

    promedios = {
        "Lectura Critica":    avg("lectura_critica"),
        "Matematicas":        avg("matematicas"),
        "Ciencias Naturales": avg("ciencias_naturales"),
        "Sociales":           avg("sociales"),
        "Ingles":             avg("ingles"),
    }

    # Area mas debil
    area_debil = min(promedios, key=promedios.get) if promedios else "—"

    rows_inscritos = _render_rows_inscritos(registros, password)
    rows_puntajes  = _render_rows_puntajes(puntajes)
    barras         = _render_barras(promedios)

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin — Shekinah Tutor ICFES</title>
        <style>
            *{{margin:0;padding:0;box-sizing:border-box}}
            body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                  background:#f5f5f5;color:#212121}}
            header{{background:linear-gradient(135deg,#1a237e,#3949ab);color:white;
                    padding:16px 24px;display:flex;align-items:center;justify-content:space-between}}
            header h1{{font-size:18px}} header p{{font-size:12px;opacity:.8}}
            .tabs{{display:flex;background:white;border-bottom:2px solid #e0e0e0;
                   padding:0 24px;gap:4px}}
            .tab{{padding:14px 20px;cursor:pointer;font-size:14px;font-weight:600;
                  color:#757575;border-bottom:3px solid transparent;transition:all .2s}}
            .tab.active{{color:#1a237e;border-bottom-color:#1a237e}}
            .panel{{display:none;padding:20px 24px}}
            .panel.active{{display:block}}
            .stats{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}}
            .stat-card{{background:white;border-radius:12px;padding:16px 20px;
                        box-shadow:0 2px 8px rgba(0,0,0,.08);flex:1;min-width:110px}}
            .stat-card .num{{font-size:30px;font-weight:800;color:#1a237e}}
            .stat-card .label{{font-size:11px;color:#757575;margin-top:4px}}
            .card{{background:white;border-radius:12px;padding:20px;
                   box-shadow:0 2px 8px rgba(0,0,0,.08);margin-bottom:20px}}
            .card h2{{font-size:15px;color:#1a237e;margin-bottom:16px;
                      display:flex;justify-content:space-between;align-items:center}}
            table{{width:100%;border-collapse:collapse;font-size:13px}}
            thead tr{{background:#f5f5f5}}
            thead th{{padding:10px 8px;text-align:left;font-size:11px;color:#757575;
                      font-weight:600;text-transform:uppercase}}
            tbody tr{{border-bottom:1px solid #f5f5f5}}
            tbody tr:hover{{background:#fafafa}}
            td{{padding:12px 8px;vertical-align:middle}}
            .badge-ok{{background:#e8f5e9;color:#2e7d32;padding:3px 10px;
                       border-radius:20px;font-size:11px;font-weight:700}}
            .badge-err{{background:#ffebee;color:#c62828;padding:3px 10px;
                        border-radius:20px;font-size:11px;font-weight:700}}
            .btn{{border:none;padding:7px 14px;border-radius:8px;cursor:pointer;
                  font-size:12px;font-weight:700;white-space:nowrap}}
            .btn-dl{{background:#1a237e;color:white;text-decoration:none;
                     display:inline-block;padding:7px 14px;border-radius:8px;
                     font-size:12px;font-weight:700}}
            .bar-row{{display:flex;align-items:center;gap:12px;margin-bottom:14px}}
            .bar-label{{width:150px;font-size:13px;color:#424242;flex-shrink:0}}
            .bar-track{{flex:1;height:18px;background:#e0e0e0;border-radius:9px;overflow:hidden}}
            .bar-fill{{height:100%;border-radius:9px;transition:width .6s}}
            .bar-val{{width:46px;font-size:13px;font-weight:700;color:#1a237e;text-align:right}}
            .alerta{{background:#fff3e0;border-left:4px solid #f57c00;
                     padding:12px 16px;border-radius:0 8px 8px 0;font-size:13px;
                     color:#e65100;margin-bottom:16px}}
            /* Modal */
            #modal{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;
                    background:rgba(0,0,0,.5);z-index:999;align-items:center;justify-content:center}}
            .modal-box{{background:white;border-radius:16px;padding:28px 24px;
                        max-width:380px;width:90%}}
            @media(max-width:600px){{
                .stats{{padding:0}} .panel{{padding:16px}}
                thead th:nth-child(3),tbody td:nth-child(3){{display:none}}
            }}
        </style>
    </head>
    <body>
    <header>
        <div><h1>Panel de Administrador</h1>
             <p>Shekinah Tutor ICFES — P. Elver Urrego · SLMM · Villavicencio</p></div>
    </header>

    <div class="tabs">
        <div class="tab active" onclick="showTab('inscritos',this)">Jovenes inscritos</div>
        <div class="tab" onclick="showTab('diagnosticos',this)">Diagnosticos</div>
        <div class="tab" onclick="showTab('debilidades',this)">Mapa de debilidades</div>
    </div>

    <!-- TAB 1: INSCRITOS -->
    <div class="panel active" id="panel-inscritos">
        <div class="stats">
            <div class="stat-card"><div class="num">{total}</div><div class="label">Total inscritos</div></div>
            <div class="stat-card"><div class="num" style="color:#2e7d32">{activos}</div><div class="label">Activos</div></div>
            <div class="stat-card"><div class="num" style="color:#c62828">{bloqueados}</div><div class="label">Bloqueados</div></div>
            <div class="stat-card"><div class="num" style="color:#1565c0">{n_diag}</div><div class="label">Diagnosticos</div></div>
        </div>
        <div class="card">
            <h2>Jovenes inscritos
                <a class="btn-dl" href="/api/admin/inscritos?pwd={password}">Descargar CSV</a>
            </h2>
            {"<table><thead><tr><th>Nombre</th><th>Grado</th><th>Fecha</th><th>Estado</th><th>Accion</th></tr></thead><tbody>" + rows_inscritos + "</tbody></table>"
             if registros else '<p style="color:#757575;padding:20px 0">Aun no hay jovenes registrados.</p>'}
        </div>
    </div>

    <!-- TAB 2: DIAGNOSTICOS -->
    <div class="panel" id="panel-diagnosticos">
        <div class="card">
            <h2>Resultados de diagnostico
                <a class="btn-dl" href="/api/admin/diagnosticos?pwd={password}">Descargar CSV</a>
            </h2>
            {"<table><thead><tr><th>Nombre</th><th>Grado</th><th>Lectura</th><th>Mat</th><th>CN</th><th>Soc</th><th>Ing</th><th>Global</th><th>Nivel</th><th>Fecha</th></tr></thead><tbody>" + rows_puntajes + "</tbody></table>"
             if puntajes else '<p style="color:#757575;padding:20px 0">Aun no hay diagnosticos completados.</p>'}
        </div>
    </div>

    <!-- TAB 3: MAPA DE DEBILIDADES -->
    <div class="panel" id="panel-debilidades">
        {"<div class='alerta'>⚠️ Area mas debil del grupo: <strong>" + area_debil + f" ({promedios[area_debil]}%)</strong> — Se recomienda refuerzo grupal.</div>" if puntajes else ""}
        <div class="card">
            <h2>Promedio grupal por area ({n_diag} diagnosticos)</h2>
            {barras if puntajes else '<p style="color:#757575;padding:20px 0">Completa al menos un diagnostico para ver el mapa.</p>'}
        </div>
        {"<div class='card'><h2>Interpretacion pastoral</h2>" + _interpretacion_pastoral(promedios, area_debil) + "</div>" if puntajes else ""}
    </div>

    <!-- Modal confirmacion -->
    <div id="modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;
         background:rgba(0,0,0,.5);z-index:999;align-items:center;justify-content:center">
        <div class="modal-box">
            <h3 id="modal-titulo" style="margin-bottom:16px;font-size:17px"></h3>
            <div style="background:#f5f5f5;border-radius:10px;padding:14px;margin-bottom:20px">
                <div style="margin-bottom:8px"><span style="font-size:11px;color:#757575">NOMBRE</span><br>
                    <span id="modal-nombre" style="font-weight:700;font-size:15px"></span></div>
                <div style="margin-bottom:8px"><span style="font-size:11px;color:#757575">GRADO</span><br>
                    <span id="modal-grado" style="font-weight:600"></span></div>
                <div><span style="font-size:11px;color:#757575">REGISTRO</span><br>
                    <span id="modal-fecha" style="font-weight:600;font-size:13px"></span></div>
            </div>
            <div style="display:flex;gap:10px">
                <button onclick="cerrarModal()"
                    style="flex:1;padding:12px;background:#f5f5f5;color:#212121;
                    border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer">
                    Cancelar</button>
                <button id="modal-btn"
                    style="flex:1;padding:12px;color:white;border:none;
                    border-radius:10px;font-size:14px;font-weight:700;cursor:pointer">
                    Confirmar</button>
            </div>
        </div>
    </div>

    <script>
    const PWD = '{password}';
    let pendingId = null;

    function showTab(id, el) {{
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.getElementById('panel-' + id).classList.add('active');
        el.classList.add('active');
    }}

    function toggleEstado(id, activo, nombre, grado, fecha) {{
        pendingId = id;
        const color = activo ? '#c62828' : '#2e7d32';
        document.getElementById('modal-titulo').textContent = activo ? '🔒 Bloquear acceso' : '✓ Activar acceso';
        document.getElementById('modal-titulo').style.color = color;
        document.getElementById('modal-nombre').textContent = nombre;
        document.getElementById('modal-grado').textContent = grado;
        document.getElementById('modal-fecha').textContent = fecha;
        const btn = document.getElementById('modal-btn');
        btn.textContent = activo ? 'Bloquear' : 'Activar';
        btn.style.background = color;
        btn.onclick = confirmarToggle;
        document.getElementById('modal').style.display = 'flex';
    }}

    function cerrarModal() {{
        document.getElementById('modal').style.display = 'none';
        pendingId = null;
    }}

    async function confirmarToggle() {{
        if (!pendingId) return;
        cerrarModal();
        const res = await fetch('/api/admin/toggle/' + pendingId + '?pwd=' + encodeURIComponent(PWD), {{method:'POST'}});
        if (res.ok) window.location.reload();
        else alert('Error al cambiar estado.');
    }}

    document.getElementById('modal').addEventListener('click', function(e) {{
        if (e.target === this) cerrarModal();
    }});
    </script>
    </body></html>
    """


def _login_page():
    return """<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>Admin — Shekinah</title>
    <style>body{{font-family:sans-serif;display:flex;justify-content:center;
    align-items:center;height:100vh;margin:0;background:#f5f5f5}}
    .box{{background:white;padding:32px;border-radius:16px;
    box-shadow:0 4px 20px rgba(0,0,0,.1);text-align:center;max-width:360px;width:90%}}
    h2{{color:#1a237e;margin-bottom:8px}} p{{color:#757575;font-size:13px;margin-bottom:20px}}
    input{{width:100%;padding:12px;border:2px solid #e0e0e0;border-radius:10px;
    font-size:16px;box-sizing:border-box;margin-bottom:12px}}
    button{{width:100%;padding:13px;background:#1a237e;color:white;border:none;
    border-radius:10px;font-size:15px;font-weight:700;cursor:pointer}}</style>
    </head><body><div class="box"><h2>Panel Admin</h2><p>Shekinah Tutor ICFES</p>
    <input type="password" id="pwd" placeholder="Contrasena de administrador"
           onkeydown="if(event.key==='Enter')login()">
    <button onclick="login()">Entrar</button></div>
    <script>function login(){{const p=document.getElementById('pwd').value;
    window.location.href='/admin?pwd='+encodeURIComponent(p);}}</script>
    </body></html>"""


def _render_rows_inscritos(registros, password):
    rows = ""
    for r in registros:
        activo  = r['activo']
        fecha   = r['fecha_registro'].strftime('%d/%m/%Y %H:%M') if r['fecha_registro'] else '-'
        grado   = r['grado'] or '-'
        nombre_s = r['nombre'].replace("'", "\\'")

        badge = ('<span class="badge-ok">✓ Activo</span>' if activo
                 else '<span class="badge-err">✗ Bloqueado</span>')
        btn_color  = "#c62828" if activo else "#2e7d32"
        btn_label  = "🔒 Bloquear" if activo else "✓ Activar"
        btn_action = f"toggleEstado({r['id']},{str(activo).lower()},'{nombre_s}','{grado}','{fecha}')"

        rows += f"""<tr id="row-{r['id']}" style="{'background:#fff9f9' if not activo else ''}">
            <td><div style="font-weight:700">{r['nombre']}</div></td>
            <td style="color:#555">{grado}</td>
            <td style="color:#757575;font-size:12px">{fecha}</td>
            <td>{badge}</td>
            <td><button class="btn" onclick="{btn_action}"
                style="background:{btn_color};color:white">{btn_label}</button></td>
        </tr>"""
    return rows


def _render_rows_puntajes(puntajes):
    rows = ""
    for p in puntajes:
        fecha = p['fecha'].strftime('%d/%m/%Y') if p['fecha'] else '-'
        g = float(p['puntaje_global']) if p['puntaje_global'] else 0
        color = "#2e7d32" if g >= 70 else ("#f57c00" if g >= 40 else "#c62828")

        def fmt(v): return f"{float(v):.0f}%" if v is not None else "-"

        rows += f"""<tr>
            <td style="font-weight:700">{p['nombre']}</td>
            <td style="color:#555">{p['grado'] or '-'}</td>
            <td>{fmt(p['lectura_critica'])}</td>
            <td>{fmt(p['matematicas'])}</td>
            <td>{fmt(p['ciencias_naturales'])}</td>
            <td>{fmt(p['sociales'])}</td>
            <td>{fmt(p['ingles'])}</td>
            <td style="font-weight:800;color:{color}">{g:.0f}%</td>
            <td style="font-size:11px;color:#555">{p['nivel_general'] or '-'}</td>
            <td style="font-size:11px;color:#757575">{fecha}</td>
        </tr>"""
    return rows


def _render_barras(promedios):
    colores = {
        "Lectura Critica":    "#1565c0",
        "Matematicas":        "#4caf50",
        "Ciencias Naturales": "#9c27b0",
        "Sociales":           "#f57c00",
        "Ingles":             "#e53935",
    }
    html = ""
    for area, valor in sorted(promedios.items(), key=lambda x: x[1]):
        color = colores.get(area, "#1a237e")
        nivel = "Rescate" if valor < 40 else ("Refuerzo" if valor < 60 else ("Consolidacion" if valor < 80 else "Optimizacion"))
        html += f"""<div class="bar-row">
            <div class="bar-label">{area}</div>
            <div class="bar-track">
                <div class="bar-fill" style="width:{valor}%;background:{color}"></div>
            </div>
            <div class="bar-val">{valor}%</div>
            <div style="font-size:11px;color:#757575;width:90px">{nivel}</div>
        </div>"""
    return html


def _interpretacion_pastoral(promedios, area_debil):
    if not promedios:
        return ""
    promedio_global = round(sum(promedios.values()) / len(promedios), 1)
    debiles = [a for a, v in promedios.items() if v < 50]
    fuertes = [a for a, v in promedios.items() if v >= 70]

    html = f"""<div style="font-size:13px;line-height:1.8;color:#424242">
        <p style="margin-bottom:12px">
            <strong>Promedio grupal:</strong> {promedio_global}%
            {"— El grupo esta en nivel de Rescate urgente. Se recomienda plan intensivo antes del examen." if promedio_global < 40
             else ("— El grupo esta en Refuerzo. Hay bases, pero hay brechas importantes que cerrar." if promedio_global < 60
             else ("— El grupo esta en Consolidacion. Buen nivel general con oportunidades de optimizacion." if promedio_global < 80
             else "— El grupo esta en Optimizacion. Excelente nivel. Afinar estrategia y tiempo."))}
        </p>
        {"<p style='margin-bottom:12px'><strong>Areas prioritarias para refuerzo grupal:</strong> " + ", ".join(debiles) + "</p>" if debiles else ""}
        {"<p style='margin-bottom:12px'><strong>Fortalezas del grupo:</strong> " + ", ".join(fuertes) + "</p>" if fuertes else ""}
        <p style="background:#e8f5e9;border-radius:8px;padding:12px;color:#2e7d32;font-style:italic">
            "Dios puso en cada joven de Shekinah talentos unicos. Este mapa muestra donde
            necesitan mas acompanamiento. El P. Elver y los animadores pueden usar estos datos
            para personalizar el acompanamiento pastoral-academico."
        </p>
    </div>"""
    return html


# ---------------------------------------------------------------------------
# ENDPOINTS ADMIN ADICIONALES
# ---------------------------------------------------------------------------

@app.route("/api/admin/toggle/<int:student_id>", methods=["POST"])
def toggle_student(student_id):
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Contrasena incorrecta"}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE inscritos
            SET activo = NOT activo,
                bloqueado_en = CASE WHEN activo = TRUE THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE id = %s RETURNING nombre, activo
        """, (student_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if row:
            return jsonify({"status": "ok", "nombre": row['nombre'], "activo": row['activo']})
        return jsonify({"error": "No encontrado"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/inscritos", methods=["GET"])
def get_inscritos_csv():
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Contrasena incorrecta"}), 401

    registros = get_inscritos()
    output    = io.StringIO()
    writer    = csv.DictWriter(output, fieldnames=['Nombre', 'Grado', 'Fecha Registro', 'IP', 'Estado'])
    writer.writeheader()
    for reg in registros:
        writer.writerow({
            'Nombre':          reg['nombre'],
            'Grado':           reg['grado'] or '-',
            'Fecha Registro':  reg['fecha_registro'].strftime('%Y-%m-%d %H:%M') if reg['fecha_registro'] else '-',
            'IP':              reg['ip_address'] or '-',
            'Estado':          'Activo' if reg['activo'] else 'Bloqueado',
        })
    bytes_data = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    return send_file(bytes_data, mimetype='text/csv', as_attachment=True,
                     download_name=f'shekinah_inscritos_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')


@app.route("/api/admin/diagnosticos", methods=["GET"])
def get_diagnosticos_csv():
    """Descarga CSV de todos los diagnosticos completados."""
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Contrasena incorrecta"}), 401

    puntajes = get_puntajes_diagnostico()
    output   = io.StringIO()
    writer   = csv.DictWriter(output, fieldnames=[
        'Nombre', 'Grado', 'Lectura Critica', 'Matematicas',
        'Ciencias Naturales', 'Sociales', 'Ingles', 'Global', 'Nivel', 'Fecha'
    ])
    writer.writeheader()
    for p in puntajes:
        def fmt(v): return f"{float(v):.1f}" if v is not None else "0"
        writer.writerow({
            'Nombre':           p['nombre'],
            'Grado':            p['grado'] or '-',
            'Lectura Critica':  fmt(p['lectura_critica']),
            'Matematicas':      fmt(p['matematicas']),
            'Ciencias Naturales': fmt(p['ciencias_naturales']),
            'Sociales':         fmt(p['sociales']),
            'Ingles':           fmt(p['ingles']),
            'Global':           fmt(p['puntaje_global']),
            'Nivel':            p['nivel_general'] or '-',
            'Fecha':            p['fecha'].strftime('%Y-%m-%d %H:%M') if p['fecha'] else '-',
        })
    bytes_data = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    return send_file(bytes_data, mimetype='text/csv', as_attachment=True,
                     download_name=f'shekinah_diagnosticos_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')


# ---------------------------------------------------------------------------
# INICIO
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n" + "="*60)
        print("  ERROR: Falta ANTHROPIC_API_KEY")
        print("  Configura: set ANTHROPIC_API_KEY=sk-ant-...")
        print("="*60 + "\n")
    else:
        init_db()
        print("\n" + "="*60)
        print("  + SHEKINAH TUTOR ICFES v2.0 +")
        print("  Comunidad Juvenil Shekinah — SLMM — Villavicencio")
        print("="*60)
        print("  http://localhost:5000")
        print(f"  Admin: /admin?pwd={ADMIN_PASSWORD}")
        print(f"  Ping:  /ping  (para cron-job.org)")
        print("="*60 + "\n")
        app.run(debug=True, port=5000)
