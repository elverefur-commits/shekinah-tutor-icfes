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
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "shekinah2024")

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
        # Create inscritos table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inscritos (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100) NOT NULL,
                grado VARCHAR(50),
                fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address VARCHAR(45),
                user_agent TEXT
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("✓ Database initialized")
    except Exception as e:
        print(f"Error initializing DB: {e}")

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
            SELECT nombre, grado, fecha_registro, ip_address
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

    if not session_id or session_id not in chat_sessions:
        session_id = str(uuid.uuid4())
        chat_sessions[session_id] = []

    chat_sessions[session_id].append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=chat_sessions[session_id],
        )

        assistant_text = ""
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text

        chat_sessions[session_id].append(
            {"role": "assistant", "content": assistant_text}
        )

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
    chat_sessions[session_id] = []

    init_message = f"[INICIAR_DIAGNOSTICO] Mi nombre es {nombre}." if nombre else "[INICIAR_DIAGNOSTICO]"
    if grado:
        init_message += f" Estoy en grado {grado}."
    chat_sessions[session_id].append({"role": "user", "content": init_message})

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=chat_sessions[session_id],
        )

        assistant_text = ""
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text

        chat_sessions[session_id].append(
            {"role": "assistant", "content": assistant_text}
        )

        return jsonify({"response": assistant_text, "session_id": session_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/registro", methods=["POST"])
def registro():
    """Save a new student registration"""
    data = request.json or {}
    nombre = data.get("nombre", "").strip()
    grado = data.get("grado", "").strip()

    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400

    # Save to database
    saved = save_registro(nombre, grado)

    return jsonify({
        "status": "ok",
        "saved_to_db": saved,
        "nombre": nombre,
        "grado": grado
    })


@app.route("/api/admin/inscritos", methods=["GET"])
def get_inscritos_endpoint():
    """Get list of registered students (requires admin password)"""
    password = request.args.get("pwd", "")

    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Contraseña incorrecta"}), 401

    registros = get_inscritos()

    # Generate CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['Nombre', 'Grado', 'Fecha Registro', 'IP'])
    writer.writeheader()

    for reg in registros:
        writer.writerow({
            'Nombre': reg['nombre'],
            'Grado': reg['grado'] or '-',
            'Fecha Registro': reg['fecha_registro'].strftime('%Y-%m-%d %H:%M') if reg['fecha_registro'] else '-',
            'IP': reg['ip_address'] or '-'
        })

    # Return as file download
    csv_data = output.getvalue()
    bytes_data = io.BytesIO(csv_data.encode())

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
    if session_id in chat_sessions:
        del chat_sessions[session_id]
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
