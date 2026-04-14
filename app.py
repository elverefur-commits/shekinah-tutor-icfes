"""
SHEKINAH TUTOR ICFES — App web con Claude API
Comunidad Juvenil Shekinah · Parroquia San Luis Maria de Montfort
Villavicencio, Meta, Colombia
"""

import os
import json
import uuid
import csv
import io
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, send_file
from anthropic import Anthropic
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Claude API Client ---
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

MODEL = "claude-haiku-4-5-20251001"

# --- Almacenamiento en memoria de sesiones de chat ---
chat_sessions = {}

# --- Database Connection ---
DB_URL = os.environ.get("DATABASE_URL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Totustuus2026")
ACCESS_CODE = os.environ.get("ACCESS_CODE", "Shekinah2026")

def get_db_connection():
    """Get a connection to PostgreSQL"""
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        return conn
    except Exception as e:
        print(f"Error connecting to DB: {e}")
        return None

def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        # Create sessions table for persistent chat history
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions_db (
                session_id VARCHAR(64) PRIMARY KEY,
                messages JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Create inscritos table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inscritos (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100) NOT NULL,
                grado VARCHAR(50),
                fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address VARCHAR(45),
                user_agent TEXT,
                activo BOOLEAN DEFAULT TRUE,
                bloqueado_en TIMESTAMP,
                motivo_bloqueo VARCHAR(200)
            )
        """)
        # Add columns if they don't exist (for existing DBs)
        for col, definition in [
            ("activo", "BOOLEAN DEFAULT TRUE"),
            ("bloqueado_en", "TIMESTAMP"),
            ("motivo_bloqueo", "VARCHAR(200)")
        ]:
            try:
                cur.execute(f"ALTER TABLE inscritos ADD COLUMN IF NOT EXISTS {col} {definition}")
            except Exception:
                pass
        conn.commit()
        cur.close()
        conn.close()
        print("✓ Database initialized")
    except Exception as e:
        print(f"Error initializing DB: {e}")

def load_session(session_id):
    """Load chat session from DB, fallback to in-memory"""
    # Check in-memory first
    if session_id in chat_sessions:
        return chat_sessions[session_id]
    # Try DB
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT messages FROM chat_sessions_db WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            messages = row['messages'] if isinstance(row['messages'], list) else json.loads(row['messages'])
            chat_sessions[session_id] = messages  # cache in memory
            return messages
        return None
    except Exception as e:
        print(f"Error loading session: {e}")
        return None

def save_session(session_id, messages):
    """Save chat session to DB and in-memory cache"""
    chat_sessions[session_id] = messages
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO chat_sessions_db (session_id, messages, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id) DO UPDATE
            SET messages = EXCLUDED.messages, updated_at = CURRENT_TIMESTAMP
        """, (session_id, json.dumps(messages)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error saving session: {e}")

def delete_session(session_id):
    """Delete session from DB and memory"""
    chat_sessions.pop(session_id, None)
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_sessions_db WHERE session_id = %s", (session_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error deleting session: {e}")

def save_registro(nombre, grado):
    """Save a new registration to the database"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        ip = request.remote_addr or "unknown"
        user_agent = request.headers.get('User-Agent', '')[:500]

        cur.execute("""
            INSERT INTO inscritos (nombre, grado, ip_address, user_agent)
            VALUES (%s, %s, %s, %s)
        """, (nombre, grado, ip, user_agent))

        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving registro: {e}")
        return False

def get_inscritos():
    """Get all registrations from database"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, nombre, grado, fecha_registro, ip_address,
                   activo, bloqueado_en, motivo_bloqueo
            FROM inscritos
            ORDER BY fecha_registro DESC
        """)
        registros = cur.fetchall()
        cur.close()
        conn.close()
        return registros
    except Exception as e:
        print(f"Error getting inscritos: {e}")
        return []

def is_student_active(nombre):
    """Check if a student is active (not blocked)"""
    conn = get_db_connection()
    if not conn:
        return True  # If no DB, allow by default

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT activo FROM inscritos
            WHERE LOWER(nombre) = LOWER(%s)
            ORDER BY fecha_registro DESC
            LIMIT 1
        """, (nombre,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row is None:
            return True  # Not registered yet, allow
        return row['activo']
    except Exception as e:
        print(f"Error checking student status: {e}")
        return True

# --- System Prompt personalizado para Shekinah ---
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
- El Asesor Espiritual es el P. Elver Urrego.
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

PROTOCOLO DE SESION (siempre que des una clase):
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
Despues de CADA pregunta respondida, SIEMPRE aplica:
- Respuesta correcta: letra + texto + por que es correcta (paso a paso)
- Por que son incorrectas las otras: tipo de distractor + razon especifica
  (Tipo 1: Verdad parcial, Tipo 2: Inversion, Tipo 3: Generalizacion,
   Tipo 4: Contradiccion directa, Tipo 5: Fuera de contexto)
- Si el estudiante fallo: MICROCLASE DE CORRECCION (nombrar concepto, explicar
  con ABP, aplicar a la pregunta, verificar con pregunta analoga)

PROTOCOLO DE EXAMEN DIAGNOSTICO:
Cuando el estudiante pida diagnostico o el sistema lo active:

1. Presentar 5 preguntas por area (25 total), nivel progresivo (1 facil, 2 medio, 2 dificil)
2. Las preguntas deben ser tipo ICFES real con 4 opciones (A, B, C, D)
3. Presentar UNA pregunta a la vez, esperar respuesta
4. Aplicar retroalimentacion COMPLETA despues de cada respuesta
5. Al final, generar INFORME DIAGNOSTICO:
   - Puntaje por area (sobre 5)
   - Nivel estimado (1-4) por area
   - Clasificacion: Rescate urgente / Consolidacion / Optimizacion
   - Mapa de debilidades especificas
   - Plan de accion sugerido (prioridades y cronograma)

FORMATO PARA CUALQUIER PREGUNTA TIPO ICFES (practica o diagnostico):
Cada vez que presentes una pregunta con opciones de respuesta, SIEMPRE usa
exactamente este formato en lineas separadas:
A. [texto de la opcion]
B. [texto de la opcion]
C. [texto de la opcion]
D. [texto de la opcion]
Nunca cambies este formato. No uses parentesis, no uses negritas en las letras,
no uses "a)" ni "A)" ni "A:" ni "A.-". Siempre "A. " con punto y espacio.

PRINCIPIOS IRRENUNCIABLES:
- Competencia > memorizacion
- El error es datos, no fracaso
- Conectar siempre con la meta universitaria del joven
- El tiempo es variable pedagogica (gestion del reloj)
- Distractores como herramienta de aprendizaje
- Repeticion espaciada vence al repaso masivo

MODO DIAGNOSTICO:
Cuando recibas el mensaje especial "[INICIAR_DIAGNOSTICO]", activa el examen
diagnostico completo. Empieza presentandote, preguntando el nombre del
estudiante, su grado, la carrera que suena, y luego inicia con la primera
pregunta de Lectura Critica.

Las 5 areas del diagnostico en orden:
1. Lectura Critica (5 preguntas)
2. Matematicas (5 preguntas)
3. Ciencias Naturales (5 preguntas)
4. Sociales y Ciudadanas (5 preguntas)
5. Ingles (5 preguntas)

FORMATO DE PREGUNTA (OBLIGATORIO - SIEMPRE usar este formato exacto):
Presenta asi cada pregunta:

AREA: [nombre del area] | Pregunta [n] de 5

[Contexto o texto de lectura si aplica]

[Enunciado de la pregunta]

A. [opcion A]
B. [opcion B]
C. [opcion C]
D. [opcion D]

Toca la opcion que consideres correcta.

IMPORTANTE: Las opciones SIEMPRE deben estar en lineas separadas, empezando
con la letra mayuscula seguida de un punto y espacio (A. B. C. D.).
Nunca uses otro formato para las opciones. Este formato permite que el
estudiante toque botones en la pantalla de su celular.

Al terminar las 25 preguntas, genera el informe diagnostico completo con
recomendaciones especificas para el plan de estudio del joven.

MOTIVACION SHEKINAH:
- Cierra cada sesion importante con una frase de animo conectada con la fe:
  "Recuerda que Dios habita en ti (Shekinah). Tu esfuerzo hoy es semilla
   del futuro que El suena para ti."
- Conecta el esfuerzo academico con el lema de la comunidad.
"""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "")
    session_id = data.get("session_id", "")

    # Load existing session or create new one
    messages = None
    if session_id:
        messages = load_session(session_id)
    if messages is None:
        session_id = str(uuid.uuid4())
        messages = []

    messages.append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        assistant_text = ""
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text

        messages.append({"role": "assistant", "content": assistant_text})
        save_session(session_id, messages)

        return jsonify(
            {
                "response": assistant_text,
                "session_id": session_id,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/diagnostico", methods=["POST"])
def diagnostico():
    """Inicia un examen diagnostico nuevo."""
    data = request.json or {}
    nombre = data.get("nombre", "")
    grado = data.get("grado", "")

    session_id = str(uuid.uuid4())
    messages = []

    init_message = f"[INICIAR_DIAGNOSTICO] Mi nombre es {nombre}." if nombre else "[INICIAR_DIAGNOSTICO]"
    if grado:
        init_message += f" Estoy en grado {grado}."
    messages.append({"role": "user", "content": init_message})

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        assistant_text = ""
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text

        messages.append({"role": "assistant", "content": assistant_text})
        save_session(session_id, messages)

        return jsonify({"response": assistant_text, "session_id": session_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/registro", methods=["POST"])
def registro():
    """Save a new student registration"""
    data = request.json or {}
    nombre = data.get("nombre", "").strip()
    grado = data.get("grado", "").strip()
    codigo = data.get("codigo", "").strip()

    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400

    # Validate access code
    if codigo.lower() != ACCESS_CODE.lower():
        return jsonify({"error": "Codigo de acceso incorrecto"}), 403

    # Save to database
    saved = save_registro(nombre, grado)

    return jsonify({
        "status": "ok",
        "saved_to_db": saved,
        "nombre": nombre,
        "grado": grado
    })


@app.route("/admin")
def admin_panel():
    """Admin panel HTML page"""
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return """
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Admin - Shekinah</title>
            <style>
                body { font-family: sans-serif; display: flex; justify-content: center;
                       align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }
                .box { background: white; padding: 32px; border-radius: 16px;
                       box-shadow: 0 4px 20px rgba(0,0,0,0.1); text-align: center; max-width: 360px; width: 90%; }
                h2 { color: #1a237e; margin-bottom: 8px; }
                p { color: #757575; font-size: 13px; margin-bottom: 20px; }
                input { width: 100%; padding: 12px; border: 2px solid #e0e0e0;
                        border-radius: 10px; font-size: 16px; box-sizing: border-box; margin-bottom: 12px; }
                button { width: 100%; padding: 13px; background: #1a237e; color: white;
                         border: none; border-radius: 10px; font-size: 15px;
                         font-weight: 700; cursor: pointer; }
            </style>
        </head>
        <body>
            <div class="box">
                <h2>Panel Admin</h2>
                <p>Shekinah Tutor ICFES</p>
                <input type="password" id="pwd" placeholder="Contrasena de administrador"
                       onkeydown="if(event.key==='Enter') login()">
                <button onclick="login()">Entrar</button>
            </div>
            <script>
                function login() {
                    const pwd = document.getElementById('pwd').value;
                    window.location.href = '/admin?pwd=' + encodeURIComponent(pwd);
                }
            </script>
        </body>
        </html>
        """, 401

    registros = get_inscritos()
    total = len(registros)
    activos = sum(1 for r in registros if r['activo'])
    bloqueados = total - activos

    rows_html = ""
    for r in registros:
        activo = r['activo']
        fecha = r['fecha_registro'].strftime('%d/%m/%Y %H:%M') if r['fecha_registro'] else '-'
        ip = r['ip_address'] or 'Desconocida'
        grado = r['grado'] or '-'
        nombre_safe = r['nombre'].replace("'", "\\'")

        estado_badge = (
            '<span style="background:#e8f5e9;color:#2e7d32;padding:4px 12px;'
            'border-radius:20px;font-size:12px;font-weight:700;">✓ Activo</span>'
            if activo else
            '<span style="background:#ffebee;color:#c62828;padding:4px 12px;'
            'border-radius:20px;font-size:12px;font-weight:700;">✗ Bloqueado</span>'
        )
        btn_label = "🔒 Bloquear" if activo else "✓ Activar"
        btn_color = "#c62828" if activo else "#2e7d32"
        btn_action = f"toggleEstado({r['id']}, {str(activo).lower()}, '{nombre_safe}', '{ip}', '{grado}', '{fecha}')"

        rows_html += f"""
        <tr id="row-{r['id']}" style="{'background:#fff9f9;' if not activo else ''}">
            <td style="padding:12px 10px;">
                <div style="font-weight:700;font-size:15px;">{r['nombre']}</div>
                <div style="font-size:11px;color:#1565c0;margin-top:2px;font-family:monospace;">IP: {ip}</div>
            </td>
            <td style="padding:12px 10px;color:#555;font-size:13px;">{grado}</td>
            <td style="padding:12px 10px;color:#757575;font-size:12px;">{fecha}</td>
            <td style="padding:12px 10px;">{estado_badge}</td>
            <td style="padding:12px 10px;">
                <button onclick="{btn_action}"
                    style="background:{btn_color};color:white;border:none;padding:8px 16px;
                    border-radius:8px;cursor:pointer;font-size:13px;font-weight:700;
                    white-space:nowrap;">
                    {btn_label}
                </button>
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin - Shekinah Tutor ICFES</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                   background: #f5f5f5; color: #212121; }}
            header {{ background: linear-gradient(135deg, #1a237e, #3949ab);
                     color: white; padding: 16px 24px; display: flex;
                     align-items: center; justify-content: space-between; }}
            header h1 {{ font-size: 18px; }}
            header p {{ font-size: 12px; opacity: 0.8; }}
            .stats {{ display: flex; gap: 16px; padding: 20px 24px; flex-wrap: wrap; }}
            .stat-card {{ background: white; border-radius: 12px; padding: 16px 24px;
                         box-shadow: 0 2px 8px rgba(0,0,0,0.08); flex: 1; min-width: 120px; }}
            .stat-card .num {{ font-size: 32px; font-weight: 800; color: #1a237e; }}
            .stat-card .label {{ font-size: 12px; color: #757575; margin-top: 4px; }}
            .table-container {{ margin: 0 24px 24px; background: white;
                               border-radius: 12px; overflow: hidden;
                               box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
            .table-header {{ padding: 16px 20px; border-bottom: 1px solid #e0e0e0;
                            display: flex; justify-content: space-between; align-items: center; }}
            .table-header h2 {{ font-size: 16px; color: #1a237e; }}
            table {{ width: 100%; border-collapse: collapse; }}
            thead tr {{ background: #f5f5f5; }}
            thead th {{ padding: 10px 8px; text-align: left; font-size: 12px;
                       color: #757575; font-weight: 600; text-transform: uppercase; }}
            tbody tr {{ border-bottom: 1px solid #f5f5f5; }}
            tbody tr:hover {{ background: #fafafa; }}
            .btn-download {{ background: #1a237e; color: white; border: none;
                            padding: 8px 16px; border-radius: 8px; cursor: pointer;
                            font-size: 13px; font-weight: 600; text-decoration: none;
                            display: inline-block; }}
            .empty {{ padding: 40px; text-align: center; color: #757575; }}
            @media (max-width: 600px) {{
                .stats {{ padding: 16px; gap: 10px; }}
                .table-container {{ margin: 0 12px 24px; }}
                thead th:nth-child(3), tbody td:nth-child(3) {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <header>
            <div>
                <h1>Panel de Administrador</h1>
                <p>Shekinah Tutor ICFES — Parroquia San Luis Maria de Montfort</p>
            </div>
        </header>

        <div class="stats">
            <div class="stat-card">
                <div class="num">{total}</div>
                <div class="label">Total inscritos</div>
            </div>
            <div class="stat-card">
                <div class="num" style="color:#2e7d32;">{activos}</div>
                <div class="label">Activos</div>
            </div>
            <div class="stat-card">
                <div class="num" style="color:#c62828;">{bloqueados}</div>
                <div class="label">Bloqueados</div>
            </div>
        </div>

        <div class="table-container">
            <div class="table-header">
                <h2>Jovenes inscritos</h2>
                <a class="btn-download"
                   href="/api/admin/inscritos?pwd={password}">
                   Descargar Excel
                </a>
            </div>
            {"<table><thead><tr><th>Nombre</th><th>Grado</th><th>Fecha</th><th>Estado</th><th>Accion</th></tr></thead><tbody>" + rows_html + "</tbody></table>"
             if registros else
             '<div class="empty">Aun no hay jovenes registrados.<br>Cuando entren a la app apareceran aqui.</div>'}
        </div>

        <!-- Modal de confirmacion -->
        <div id="modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;
             background:rgba(0,0,0,0.5);z-index:999;align-items:center;justify-content:center;">
            <div style="background:white;border-radius:16px;padding:28px 24px;max-width:380px;
                        width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.2);">
                <h3 id="modal-titulo" style="margin-bottom:16px;font-size:17px;"></h3>
                <div style="background:#f5f5f5;border-radius:10px;padding:14px;margin-bottom:20px;">
                    <div style="margin-bottom:8px;">
                        <span style="font-size:12px;color:#757575;">NOMBRE</span><br>
                        <span id="modal-nombre" style="font-weight:700;font-size:16px;"></span>
                    </div>
                    <div style="margin-bottom:8px;">
                        <span style="font-size:12px;color:#757575;">GRADO</span><br>
                        <span id="modal-grado" style="font-weight:600;"></span>
                    </div>
                    <div style="margin-bottom:8px;">
                        <span style="font-size:12px;color:#757575;">IP DEL DISPOSITIVO</span><br>
                        <span id="modal-ip" style="font-weight:600;font-family:monospace;color:#1565c0;"></span>
                    </div>
                    <div>
                        <span style="font-size:12px;color:#757575;">REGISTRO</span><br>
                        <span id="modal-fecha" style="font-weight:600;font-size:13px;"></span>
                    </div>
                </div>
                <div style="display:flex;gap:10px;">
                    <button onclick="cerrarModal()"
                        style="flex:1;padding:12px;background:#f5f5f5;color:#212121;
                        border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;">
                        Cancelar
                    </button>
                    <button id="modal-btn-confirmar"
                        style="flex:1;padding:12px;color:white;border:none;
                        border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;">
                        Confirmar
                    </button>
                </div>
            </div>
        </div>

        <script>
            const PWD = '{password}';
            let pendingId = null;

            function toggleEstado(id, activo, nombre, ip, grado, fecha) {{
                pendingId = id;
                const accion = activo ? 'Bloquear' : 'Activar';
                const color = activo ? '#c62828' : '#2e7d32';

                document.getElementById('modal-titulo').textContent =
                    (activo ? '🔒 Bloquear acceso' : '✓ Activar acceso');
                document.getElementById('modal-titulo').style.color = color;
                document.getElementById('modal-nombre').textContent = nombre;
                document.getElementById('modal-grado').textContent = grado;
                document.getElementById('modal-ip').textContent = ip;
                document.getElementById('modal-fecha').textContent = fecha;

                const btn = document.getElementById('modal-btn-confirmar');
                btn.textContent = accion;
                btn.style.background = color;
                btn.onclick = () => confirmarToggle();

                document.getElementById('modal').style.display = 'flex';
            }}

            function cerrarModal() {{
                document.getElementById('modal').style.display = 'none';
                pendingId = null;
            }}

            async function confirmarToggle() {{
                if (!pendingId) return;
                cerrarModal();
                const res = await fetch('/api/admin/toggle/' + pendingId + '?pwd=' + encodeURIComponent(PWD), {{
                    method: 'POST'
                }});
                if (res.ok) {{
                    window.location.reload();
                }} else {{
                    alert('Error al cambiar estado. Intente de nuevo.');
                }}
            }}

            // Cerrar modal al tocar afuera
            document.getElementById('modal').addEventListener('click', function(e) {{
                if (e.target === this) cerrarModal();
            }});
        </script>
    </body>
    </html>
    """


@app.route("/api/admin/toggle/<int:student_id>", methods=["POST"])
def toggle_student(student_id):
    """Toggle student active/blocked status"""
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Contrasena incorrecta"}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Toggle activo status
        cur.execute("""
            UPDATE inscritos
            SET activo = NOT activo,
                bloqueado_en = CASE WHEN activo = TRUE THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE id = %s
            RETURNING nombre, activo
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


@app.route("/api/verificar", methods=["POST"])
def verificar_acceso():
    """Verify if a student is allowed to use the app"""
    data = request.json or {}
    nombre = data.get("nombre", "").strip()
    if not nombre:
        return jsonify({"activo": True})
    activo = is_student_active(nombre)
    return jsonify({"activo": activo})


@app.route("/api/admin/inscritos", methods=["GET"])
def get_inscritos_endpoint():
    """Download CSV list of registered students"""
    password = request.args.get("pwd", "")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Contrasena incorrecta"}), 401

    registros = get_inscritos()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['Nombre', 'Grado', 'Fecha Registro', 'IP', 'Estado'])
    writer.writeheader()

    for reg in registros:
        writer.writerow({
            'Nombre': reg['nombre'],
            'Grado': reg['grado'] or '-',
            'Fecha Registro': reg['fecha_registro'].strftime('%Y-%m-%d %H:%M') if reg['fecha_registro'] else '-',
            'IP': reg['ip_address'] or '-',
            'Estado': 'Activo' if reg['activo'] else 'Bloqueado'
        })

    csv_data = output.getvalue()
    bytes_data = io.BytesIO(csv_data.encode('utf-8-sig'))  # utf-8-sig for Excel compatibility

    return send_file(
        bytes_data,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'shekinah_inscritos_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )


@app.route("/api/reset", methods=["POST"])
def reset():
    data = request.json
    session_id = data.get("session_id", "")
    if session_id:
        delete_session(session_id)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n" + "=" * 60)
        print("  SHEKINAH TUTOR ICFES")
        print("=" * 60)
        print("\n  ERROR: Falta la API Key de Anthropic.")
        print("\n  Configura la variable de entorno asi:")
        print('  set ANTHROPIC_API_KEY=sk-ant-api03-...')
        print("\n  Luego ejecuta de nuevo: python app.py")
        print("=" * 60 + "\n")
    else:
        # Initialize database
        init_db()

        print("\n" + "=" * 60)
        print("  + SHEKINAH TUTOR ICFES +")
        print("  Comunidad Juvenil Shekinah")
        print("  Parroquia San Luis Maria de Montfort")
        print("=" * 60)
        print("\n  Abre en tu navegador: http://localhost:5000")
        print("  Admin panel: /api/admin/inscritos?pwd=" + ADMIN_PASSWORD)
        print("=" * 60 + "\n")
        app.run(debug=True, port=5000)
