from flask import Flask, render_template, Response, jsonify, request, session, redirect, url_for
import cv2
import mediapipe as mp
import os
import logging
import sqlite3
import speech_recognition as sr
import threading
import time
from datetime import datetime, date, timedelta
import queue
from flask_socketio import SocketIO, emit
from groq import Groq
import numpy as np
import math
import random
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
FRONTEND_DIR = os.path.join(ROOT_DIR, 'frontend')
BACKEND_STATIC_DIR = os.path.join(BASE_DIR, 'static')
DATA_DIR = os.path.join(BASE_DIR, 'data')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKEND_STATIC_DIR, exist_ok=True)

load_dotenv(os.path.join(ROOT_DIR, '.env'))
load_dotenv(os.path.join(BASE_DIR, '.env'))
load_dotenv()

FRONTEND_BASE_URL = os.environ.get('FRONTEND_BASE_URL', '').rstrip('/')


def frontend_url(path='/'):
    normalized_path = path if path.startswith('/') else f'/{path}'
    if FRONTEND_BASE_URL:
        return f'{FRONTEND_BASE_URL}{normalized_path}'
    return normalized_path


try:
    _mp_has_solutions = hasattr(mp, 'solutions')
except Exception:
    _mp_has_solutions = False

USE_MEDIAPIPE = _mp_has_solutions
if not USE_MEDIAPIPE:
    print("⚠️  mediapipe 'solutions' not found — running without ML features.")

client = Groq(api_key=os.environ.get('GROQ_API_KEY'))
otp_storage = {}
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('absl').setLevel(logging.ERROR)

app = Flask(__name__, template_folder=FRONTEND_DIR, static_folder=BACKEND_STATIC_DIR)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-super-secret-key-change-this')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if FRONTEND_BASE_URL else 'Lax'
app.config['SESSION_COOKIE_SECURE'] = FRONTEND_BASE_URL.startswith('https://')
socketio = SocketIO(app, cors_allowed_origins=FRONTEND_BASE_URL or '*')


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if FRONTEND_BASE_URL:
        if origin == FRONTEND_BASE_URL:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    else:
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return response

DB_PATH = os.path.join(DATA_DIR, 'attendance.db')
SPEECH_DB_PATH = os.path.join(DATA_DIR, 'speech.db')

OTP_SENDER_EMAIL = 'smartwebcam1014@gmail.com'
OTP_SENDER_PASSWORD = 'sgnrzfylsoxbtiib'

speech_queue = queue.Queue()
is_listening = False
current_speech_text = ''
recognizer = sr.Recognizer()
microphone = None
present_count = 0
absent_count = 0
LOCKED_ABSENT = 'LOCKED ABSENT'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS attendance
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  userid TEXT NOT NULL,
                  date TEXT NOT NULL,
                  status TEXT NOT NULL,
                  last_updated TEXT NOT NULL,
                  is_locked INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()


def init_speech_db():
    conn = sqlite3.connect(SPEECH_DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS speech_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            text_content TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def set_attendance(userid, status):
    today_str = date.today().isoformat()
    now_str = datetime.now().isoformat(timespec='seconds')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, status, is_locked FROM attendance WHERE userid=? AND date=?', (userid, today_str))
    row = c.fetchone()
    if row:
        record_id, _, is_locked = row
        if is_locked == 1:
            conn.close()
            return False
        c.execute('UPDATE attendance SET status=?, last_updated=? WHERE id=?', (status, now_str, record_id))
    else:
        c.execute('INSERT INTO attendance (userid, date, status, last_updated) VALUES (?, ?, ?, ?)',
                  (userid, today_str, status, now_str))
    conn.commit()
    conn.close()
    return True


def save_speech_record(user_id, text_content):
    now = datetime.now()
    conn = sqlite3.connect(SPEECH_DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO speech_records (user_id, date, time, text_content) VALUES (?, ?, ?, ?)',
              (user_id, now.date().isoformat(), now.time().isoformat(timespec='seconds'), text_content))
    conn.commit()
    conn.close()


def get_attendance_counts(userid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM attendance WHERE userid=? AND status="Present"', (userid,))
    present = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM attendance WHERE userid=? AND status="Absent"', (userid,))
    absent = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM attendance WHERE userid=? AND is_locked=1', (userid,))
    locked = c.fetchone()[0]
    conn.close()
    return present, absent, locked


def send_otp_email(email, otp):
    try:
        msg = MIMEMultipart()
        msg['From'] = OTP_SENDER_EMAIL
        msg['To'] = email
        msg['Subject'] = '🔐 Smart Attendance OTP'
        msg.attach(MIMEText(f'Your OTP is: **{otp}**\n\nExpires in 5 minutes.', 'plain'))

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls(context=context)
            server.login(OTP_SENDER_EMAIL, OTP_SENDER_PASSWORD)
            server.sendmail(OTP_SENDER_EMAIL, email, msg.as_string())
        print('✅ Email sent!')
        return True
    except Exception as e:
        print(f'❌ Email error: {e}')
        return False


def generate_otp():
    return str(random.randint(100000, 999999))


def speech_listener():
    global is_listening, current_speech_text, microphone
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True

    try:
        microphone = sr.Microphone()
        print('🎤 Microphone initialized successfully')
    except Exception as e:
        print('❌ Mic error:', e)
        return

    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)

    while True:
        if is_listening:
            try:
                with microphone as source:
                    print('🎧 Listening...')
                    audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
                text = recognizer.recognize_google(audio)
                current_speech_text = text
                save_speech_record('student1', text)
                print('✅ HEARD:', text)
            except sr.UnknownValueError:
                print("🤷 Couldn't understand")
            except Exception as e:
                print('Speech error:', e)
        time.sleep(0.2)


init_db()
init_speech_db()

camera = cv2.VideoCapture(0)
DETECT_WIDTH, DETECT_HEIGHT = 320, 240
DISPLAY_WIDTH, DISPLAY_HEIGHT = 640, 480
camera.set(cv2.CAP_PROP_FRAME_WIDTH, DISPLAY_WIDTH)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, DISPLAY_HEIGHT)

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
smile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_smile.xml')
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')

if USE_MEDIAPIPE:
    mp_hands = mp.solutions.hands
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    hands = mp_hands.Hands(min_detection_confidence=0.7)
    mp_draw = mp.solutions.drawing_utils
else:
    mp_hands = None
    mp_face_mesh = None
    face_mesh = None
    hands = None
    mp_draw = None

face_detected = False
expression = 'neutral'
gesture = 'none'
current_filter = 'normal'
filters = ['normal', 'bw', 'red', 'blur', 'cartoon']
CURRENT_USERID = ''
attendance_status = 'Absent'


def fingers_up(hand, hand_label):
    tips = [4, 8, 12, 16, 20]
    fingers = []
    if hand_label == 'Right':
        fingers.append(hand.landmark[tips[0]].x < hand.landmark[tips[0] - 1].x)
    else:
        fingers.append(hand.landmark[tips[0]].x > hand.landmark[tips[0] - 1].x)
    for i in range(1, 5):
        fingers.append(hand.landmark[tips[i]].y < hand.landmark[tips[i] - 2].y)
    return fingers


def detect_gesture(hand, hand_label):
    f = fingers_up(hand, hand_label)
    if f == [0, 0, 0, 0, 0]:
        return '✊'
    if f == [1, 1, 1, 1, 1]:
        return '🤚'
    if f == [1, 0, 0, 0, 0]:
        return '👍'
    if f == [0, 1, 1, 0, 0]:
        return '✌️'
    if f == [0, 1, 0, 0, 0]:
        return '☝️'
    if f == [0, 1, 1, 1, 0]:
        return '🤟'
    if f[0] == 1 and hand.landmark[4].y > hand.landmark[3].y:
        return '👎'
    thumb = hand.landmark[4]
    index = hand.landmark[8]
    dist = math.hypot(thumb.x - index.x, thumb.y - index.y)
    if dist < 0.04:
        return '👌'
    return 'none'


def filter_bw(frame):
    return cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)


def filter_red(frame):
    red = frame.copy()
    red[:, :, 2] = cv2.add(red[:, :, 2], 60)
    return red


def filter_blur(frame):
    return cv2.GaussianBlur(frame, (21, 21), 0)


def filter_cartoon(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 9)
    color = cv2.bilateralFilter(frame, 9, 300, 300)
    return cv2.bitwise_and(color, color, mask=edges)


def generate_frames():
    global face_detected, expression, gesture, current_filter, attendance_status, current_speech_text
    global present_count, absent_count

    while True:
        success, frame = camera.read()
        if not success:
            break
        frame = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        face_results = None
        if USE_MEDIAPIPE and face_mesh:
            face_results = face_mesh.process(rgb_frame)

        if face_results and getattr(face_results, 'multi_face_landmarks', None):
            face_detected = True
            landmarks = face_results.multi_face_landmarks[0].landmark
            mouth_width = math.dist([landmarks[61].x, landmarks[61].y], [landmarks[291].x, landmarks[291].y])
            mouth_height = math.dist([landmarks[13].x, landmarks[13].y], [landmarks[14].x, landmarks[14].y])
            if mouth_width > 0.08:
                expression = 'Happy 😊'
            elif mouth_height > 0.03:
                expression = 'Surprised 😲'
            else:
                expression = 'Neutral 😐'
            if mp_draw and face_mesh:
                mp_draw.draw_landmarks(
                    frame,
                    face_results.multi_face_landmarks[0],
                    face_mesh.FACEMESH_CONTOURS,
                    mp_draw.DrawingSpec(color=(0, 255, 0), thickness=1, circle_radius=1)
                )
        else:
            face_detected = False
            expression = 'None'

        new_status = 'Present' if face_detected else 'Absent'
        if new_status == 'Present':
            present_count += 1
        else:
            absent_count += 1

        if absent_count >= 5000 and attendance_status != LOCKED_ABSENT:
            set_attendance(CURRENT_USERID, 'Absent')
            attendance_status = LOCKED_ABSENT
        elif new_status != attendance_status and attendance_status != LOCKED_ABSENT:
            updated = set_attendance(CURRENT_USERID, new_status)
            if updated:
                attendance_status = new_status

        if USE_MEDIAPIPE and hands:
            hand_results = hands.process(rgb_frame)
            if hand_results and getattr(hand_results, 'multi_hand_landmarks', None):
                for hand_landmarks, handedness in zip(hand_results.multi_hand_landmarks, hand_results.multi_handedness):
                    gesture = detect_gesture(hand_landmarks, handedness.classification[0].label)
                    if mp_draw and mp_hands:
                        mp_draw.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


@app.route('/')
def index():
    if FRONTEND_BASE_URL:
        return redirect(frontend_url('/front_page.html'))
    return render_template('front_page.html')


@app.route('/login')
def login_page():
    if FRONTEND_BASE_URL:
        return redirect(frontend_url('/middle_page.html'))
    return render_template('middle_page.html')


@app.route('/otp')
def otp_page():
    if FRONTEND_BASE_URL:
        return redirect(frontend_url('/otp_page.html'))
    if 'otp_verified' not in session:
        return render_template('otp_page.html', error='Please enter your email first!')
    return render_template('otp_page.html')


@app.route('/attendance-all')
def attendance_all():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, userid, date, status, last_updated, is_locked FROM attendance ORDER BY date DESC, id DESC LIMIT 100')
    rows = c.fetchall()
    conn.close()
    html = '<!DOCTYPE html><html><head><title>Attendance Records</title><style>body{font-family:Arial;margin:40px;background:#f5f5f5;}table{width:100%;border-collapse:collapse;background:white;box-shadow:0 4px 12px rgba(0,0,0,0.1);}th,td{padding:12px;text-align:left;border-bottom:1px solid #eee;}th{background:linear-gradient(135deg,#28a745,#20c997);color:white;}.present{background:#d4edda;}.absent{background:#f8d7da;}.locked{background:#fff3cd;}</style></head><body>'
    html += '<h2>📋 All Attendance Records</h2>'
    html += '<table><tr><th>ID</th><th>User</th><th>Date</th><th>Status</th><th>Time</th><th>Lock</th></tr>'
    for r in rows:
        status_class = 'present' if r[3] == 'Present' else 'absent'
        lock = '🔒 LOCKED' if r[5] else ''
        html += f'<tr class="{status_class}"><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4][11:]}</td><td>{lock}</td></tr>'
    html += '</table></body></html>'
    return html


@app.route('/send-otp', methods=['POST'])
def send_otp():
    if not request.form:
        return jsonify({'success': False, 'message': '❌ No form data received!'}), 400

    email = request.form.get('email')
    if not email:
        return jsonify({'success': False, 'message': '❌ Please enter your email address!'}), 400

    email = email.strip()
    if '@' not in email or not email.endswith(('.com', '.in', '.org', '.edu')):
        return jsonify({'success': False, 'message': '❌ Please enter a valid email!'}), 400

    otp = generate_otp()
    session['otp'] = otp
    session['email'] = email
    session['otp_time'] = time.time()
    session['otp_attempts'] = 0

    username = email.split('@')[0]
    global CURRENT_USERID
    CURRENT_USERID = username

    print(f'🔢 Generated OTP: {otp} for {email}')

    if send_otp_email(email, otp):
        return jsonify({
            'success': True,
            'message': f'✅ OTP sent to {email}! Check inbox/spam.',
            'redirect': frontend_url('/otp_page.html')
        })
    session.clear()
    return jsonify({'success': False, 'message': '❌ Email failed. Check Gmail App Password!'}), 500


@app.route('/verify-otp', methods=['POST'])
def verify_otp():
    if not request.form:
        return jsonify({'success': False, 'message': '❌ No form data received!'}), 400

    user_otp = request.form.get('otp')
    if not user_otp:
        return jsonify({'success': False, 'message': '❌ Please enter OTP!'}), 400

    user_otp = user_otp.strip()
    if len(user_otp) != 6 or not user_otp.isdigit():
        return jsonify({'success': False, 'message': '❌ OTP must be 6 digits!'}), 400

    email = session.get('email')
    stored_otp = session.get('otp')

    if not email or not stored_otp:
        return jsonify({'success': False, 'message': '❌ Session expired. Please resend OTP!'}), 400

    otp_age = time.time() - session.get('otp_time', 0)
    attempts = session.get('otp_attempts', 0)

    if otp_age > 300:
        session.clear()
        return jsonify({'success': False, 'message': '⏰ OTP expired! Click RESEND.', 'expired': True}), 400

    if attempts >= 3:
        session.clear()
        return jsonify({'success': False, 'message': '❌ Too many failed attempts!'}), 400

    if user_otp == stored_otp:
        session['otp_verified'] = True
        session['verified_email'] = email
        session['login_time'] = time.time()
        session.pop('otp', None)
        session.pop('otp_time', None)
        session.pop('otp_attempts', None)
        print(f'✅ OTP verified for {email}')
        return jsonify({'success': True, 'message': '🎉 Verification successful!', 'redirect': frontend_url('/index.html')})

    attempts += 1
    session['otp_attempts'] = attempts
    remaining = 3 - attempts
    return jsonify({'success': False, 'message': f'❌ Wrong OTP! {remaining} attempts left.', 'attempts_left': remaining})


@app.route('/resend-otp', methods=['POST'])
def resend_otp():
    if not request.form:
        return jsonify({'success': False, 'message': '❌ No form data!'}), 400

    email = request.form.get('email') or session.get('email')
    if not email:
        return jsonify({'success': False, 'message': '❌ No email found!'}), 400

    session.pop('otp', None)
    session.pop('otp_time', None)
    session.pop('otp_attempts', None)

    otp = generate_otp()
    session['otp'] = otp
    session['email'] = email
    session['otp_time'] = time.time()
    session['otp_attempts'] = 0

    print(f'🔄 RESENT OTP: {otp} for {email}')

    if send_otp_email(email, otp):
        return jsonify({'success': True, 'message': f'✅ New OTP sent to {email}!', 'redirect': frontend_url('/otp_page.html')})
    return jsonify({'success': False, 'message': '❌ Failed to send OTP!'}), 500


@app.route('/dashboard')
def dashboard():
    if not session.get('otp_verified'):
        return redirect(frontend_url('/middle_page.html'))
    if FRONTEND_BASE_URL:
        return redirect(frontend_url('/index.html'))
    return render_template('index.html')


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/status')
def status():
    present, absent, locked = get_attendance_counts(CURRENT_USERID)
    return jsonify({
        'face': face_detected,
        'expression': expression,
        'gesture': gesture,
        'filter': current_filter,
        'attendance': attendance_status,
        'speech': current_speech_text,
        'listening': is_listening,
        'user': CURRENT_USERID,
        'present_count': present_count,
        'absent_count': absent_count,
        'total_present': present,
        'total_absent': absent,
        'locked': locked > 0,
        'verified': session.get('otp_verified', False)
    })


@app.route('/logout')
def logout():
    session.clear()
    return redirect(frontend_url('/middle_page.html'))


@app.route('/toggle-speech', methods=['POST'])
def toggle_speech():
    global is_listening
    is_listening = not is_listening
    print(f'Speech listening: {"ON" if is_listening else "OFF"}')
    return jsonify({'listening': is_listening})


@app.route('/filter/<name>')
def set_filter(name):
    global current_filter
    if name in filters:
        current_filter = name
    return jsonify({'filter': current_filter})


@app.route('/speech-records')
def speech_records():
    conn = sqlite3.connect(SPEECH_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, date, time, text_content FROM speech_records ORDER BY id DESC LIMIT 50')
    rows = c.fetchall()
    conn.close()
    html = '<!DOCTYPE html><html><head><title>Speech Records</title><style>body{font-family:Arial;margin:40px;background:#f5f5f5;}table{width:100%;border-collapse:collapse;background:white;box-shadow:0 4px 12px rgba(0,0,0,0.1);}th,td{padding:12px;text-align:left;border-bottom:1px solid #eee;}th{background:linear-gradient(135deg,#007bff,#0056b3);color:white;}</style></head><body>'
    html += '<h2>🎤 Speech Records (Last 50)</h2>'
    html += '<table><tr><th>ID</th><th>Date</th><th>Time</th><th>Text</th></tr>'
    for r in rows:
        html += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3][:80]}...</td></tr>'
    html += '</table></body></html>'
    return html


@socketio.on('message')
def handle_message(data):
    user_message = data['message']
    context = f"""
    You are an AI Attendance Assistant.
    Attendance: {attendance_status}
    Present count: {present_count}
    Absent count: {absent_count}
    Gesture: {gesture}
    Expression: {expression}
    Speech: {current_speech_text}
    """

    try:
        completion = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            messages=[
                {'role': 'system', 'content': context},
                {'role': 'user', 'content': user_message}
            ],
            stream=False
        )
        reply = completion.choices[0].message.content
        emit('response', {'message': reply})
    except Exception as e:
        emit('response', {'message': f'Groq AI error: {e}'})


if __name__ == '__main__':
    speech_thread = threading.Thread(target=speech_listener, daemon=True)
    speech_thread.start()
    print('🚀 Smart Attendance System with OTP Started!')
    print('📧 Update OTP_SENDER_EMAIL and OTP_SENDER_PASSWORD first!')
    print('🌐 Login: http://localhost:5000/login')
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
