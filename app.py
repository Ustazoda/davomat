from random import randint
# Consolidate Flask imports
from flask import Flask, request, jsonify, make_response, render_template, url_for
from datetime import datetime, timedelta
from flask_socketio import SocketIO, emit, join_room, leave_room
import qrcode  # For generating QR codes
import io  # For handling image data in memory
import base64  # For encoding image data
import uuid
app = Flask(__name__)
# Important for SocketIO and sessions
app.config['SECRET_KEY'] = 'your_very_secret_key_here!'
# Allow all origins for simplicity in dev
socketio = SocketIO(app, cors_allowed_origins="*")

# Existing API Token Logic
token = randint(1000000000, 9999999999)
print(f"Initial API Token: {token}")

COOLDOWN_SECONDS = 30  # Example: 30 seconds cooldown
COOLDOWN_COOKIE_NAME = "post_cooldown"

# Cooldown for student registration per lesson (15 minutes)
STUDENT_REGISTRATION_COOLDOWN_SECONDS = 15 * 60
STUDENT_COOLDOWN_COOKIE_PREFIX = "student_lesson_cooldown_"

# New InMemory Storage for Teacher Dashboard
# In a real app, use a database
lessons_db = {}
# Structure of lessons_db:
# {
#   "lesson_id_1": {
#     "teacher_name": "...",
#     "teacher_family_name": "...",
#     "lesson_name": "...",
#     "qr_data_token": "unique_token_for_qr",  # This token changes to update QR
#     "attendances": [
#       {"name": "Student1", "family_name": "Fam1", "group": "G1"},
#       ...
#     ]
#   },
#   ...
# }




def generate_unique_id():
    return str(uuid.uuid4())


def generate_qr_code_base64(data_to_encode):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data_to_encode)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return img_str


def get_qr_data_for_lesson(lesson_id, qr_token):
    # This URL is what students would scan.
    # The /student/register endpoint is hypothetical for this example.
    # In a real app, this URL would point to your student registration page/API.
    # The qr_token makes the QR code content unique and allows it to be "refreshed".
    return f"http://{request.host}/student/register/{lesson_id}/{qr_token}"

# New Routes for Teacher Dashboard
@app.route('/')
def teacher_dashboard():
    return render_template('index.html')

@app.route('/create_journal', methods=['POST'])
def create_journal_route():
    data = request.get_json()
    teacher_name = data.get('teacher_name')
    teacher_family_name = data.get('teacher_family_name')
    lesson_name = data.get('lesson_name')

    if not all([teacher_name, teacher_family_name, lesson_name]):
        return jsonify({"error": "Iltimos, barcha maydonlarni to'ldiring."}), 400

    lesson_id = generate_unique_id()
    qr_data_token = generate_unique_id()  # Initial token for the QR code
    
    lessons_db[lesson_id] = {
        "teacher_name": teacher_name,
        "teacher_family_name": teacher_family_name,
        "lesson_name": lesson_name,
        "qr_data_token": qr_data_token,
        "attendances": []
    }
    initial_qr_code_url = get_qr_data_for_lesson(lesson_id, qr_data_token)
    initial_qr_code_base64 = generate_qr_code_base64(initial_qr_code_url)

    return jsonify({
        "message": "Jurnal muvaffaqiyatli yaratildi!",
        "lesson_id": lesson_id,
        "initial_qr_code_base64": initial_qr_code_base64
    })

@app.route('/lesson_session/<string:lesson_id>', methods=['GET'])
def get_lesson_session_route(lesson_id):
    lesson = lessons_db.get(lesson_id)
    if not lesson:
        return jsonify({"error": "Dars topilmadi"}), 404
    return jsonify({"attendances": lesson["attendances"]})

# New Route for Student Registration
@app.route('/student/register/<string:lesson_id>/<string:qr_token>', methods=['GET', 'POST'])
def student_register_route(lesson_id, qr_token):
    lesson = lessons_db.get(lesson_id)

    if not lesson:
        return render_template('student_register.html', error="Xatolik: Dars mavjud emas yoki yopilgan.", lesson_id=lesson_id, qr_token=qr_token), 404

    # Validate the QR token
    if lesson.get('qr_data_token') != qr_token:
        return render_template('student_register.html', error="Xatolik: QR kod eskirgan yoki noto'g'ri. Yangi QR kodni skanerlang.", lesson_id=lesson_id, qr_token=qr_token), 403

    if request.method == 'POST':
        # Check for student registration cooldown cookie for this specific lesson
        student_cooldown_cookie_name = f"{STUDENT_COOLDOWN_COOKIE_PREFIX}{lesson_id}"
        cooldown_active = request.cookies.get(student_cooldown_cookie_name)

        if cooldown_active:
            return render_template('student_register.html', error=f"Siz bu darsga yaqinda ro'yxatdan o'tgansiz. Iltimos, {STUDENT_REGISTRATION_COOLDOWN_SECONDS // 60} daqiqadan so'ng qayta urinib ko'ring.", lesson_id=lesson_id, qr_token=qr_token), 429

        student_name = request.form.get('student_name')
        student_family_name = request.form.get('student_family_name')
        student_group = request.form.get('student_group')

        if not all([student_name, student_family_name, student_group]):
            return render_template('student_register.html', error="Iltimos, barcha maydonlarni to'ldiring.", lesson_id=lesson_id, qr_token=qr_token), 400

        student_data = {
            "name": student_name,
            "family_name": student_family_name,
            "group": student_group,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # Check if student already registered (simple check by name and family name for this example)
        # In a real app, you might use a unique student ID
        already_registered = any(
            s['name'] == student_name and s['family_name'] == student_family_name
            for s in lesson['attendances']
        )
        if already_registered:
            return render_template('student_register.html', message=f"{student_name} {student_family_name}, siz allaqachon bu darsga ro'yxatdan o'tgansiz.", lesson_id=lesson_id, qr_token=qr_token)

        # Call the function to update teacher's view and regenerate QR token
        notify_teacher_about_student_registration(lesson_id, student_data)

        # Prepare response to set the cooldown cookie
        response = make_response(render_template('student_register.html', message="Muvaffaqiyatli ro'yxatdan o'tdingiz!", lesson_id=lesson_id, qr_token=qr_token))
        
        expires_time = datetime.now() + timedelta(seconds=STUDENT_REGISTRATION_COOLDOWN_SECONDS)
        response.set_cookie(student_cooldown_cookie_name, "true", expires=expires_time, httponly=True, samesite="Lax")
        
        return response
        
    # For GET request, just display the form
    return render_template('student_register.html', lesson_id=lesson_id, qr_token=qr_token)

# Existing API Endpoint
@app.route('/api/v1/<int:received_token>', methods=['POST'])
def get_users(received_token):
    global token  # Declare intent to modify the global 'token' variable

    cooldown_cookie = request.cookies.get(COOLDOWN_COOKIE_NAME)

    if cooldown_cookie:
        return jsonify({"message": f"Please wait {COOLDOWN_SECONDS} seconds before posting again."}), 429

    if received_token == token:
        content = request.json
        
        if content is None:
            return jsonify({"message": "Invalid JSON content"}), 400

        # Generate a new token after successful use
        token = randint(1000000000, 9999999999)
        print(f"New API Token: {token}")
        
        response = make_response(jsonify({"message": "Users fetched successfully", "content": content}))
        
        expires_time = datetime.now() + timedelta(seconds=COOLDOWN_SECONDS)
        
        response.set_cookie(COOLDOWN_COOKIE_NAME, "true", expires=expires_time, httponly=True, samesite="Lax")
        
        return response
    else:
        return jsonify({"message": "Invalid token"}), 401

# Socket.IO Event Handlers for Teacher Dashboard
@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')
    # Here you could also implement logic to leave all rooms the user was in,
    # though FlaskSocketIO handles this for basic cases.

@socketio.on('join_lesson_session')
def handle_join_lesson_session(data):
    lesson_id = data.get('lesson_id')
    if lesson_id and lesson_id in lessons_db:
        join_room(lesson_id)
        print(f"Client {request.sid} joined room: {lesson_id}")
        # Optionally, send current state to the client that just joined
        # socketio.emit('qr_code_updated', {
        #     'lesson_id': lesson_id,
        #     'new_qr_code_base64': generate_qr_code_base64(get_qr_data_for_lesson(lesson_id, lessons_db[lesson_id]['qr_data_token'])),
        #     'students_list': lessons_db[lesson_id]['attendances']
        # }, room=request.sid)
    else:
        print(f"Client {request.sid} tried to join invalid room: {lesson_id}")

# This function would be called from your student registration logic
def notify_teacher_about_student_registration(lesson_id, student_data):
    lesson = lessons_db.get(lesson_id)
    if lesson:
        lesson['attendances'].append(student_data)
        # Regenerate QR token to make the QR code "new"
        lesson['qr_data_token'] = generate_unique_id()
        new_qr_url = get_qr_data_for_lesson(lesson_id, lesson['qr_data_token'])
        new_qr_base64 = generate_qr_code_base64(new_qr_url)

        socketio.emit('qr_code_updated', {
            'lesson_id': lesson_id,
            'new_qr_code_base64': new_qr_base64,
            'students_list': lesson['attendances']
        }, room=lesson_id)
        print(f"Sent update to room {lesson_id} for new student: {student_data.get('name')}")

if __name__ == '__main__':
    # app.run(debug=True) # This line should be removed when using Flask-SocketIO
    # Use socketio.run for FlaskSocketIO applications
    # The host '0.0.0.0' makes it accessible on your network
    # The port 8000 matches what's in index.html
    socketio.run(app, host='0.0.0.0', port=8000, debug=True, use_reloader=True)
