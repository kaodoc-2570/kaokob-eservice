from datetime import datetime
import functools
import json
import os
import sqlite3
import traceback
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
import requests
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'kaokob_eservice_secret_key_2026'

# ใช้ Absolute Path เพื่อป้องกันปัญหาเรื่อง Directory บน Render
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, 'eservice.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    'LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_LINE_CHANNEL_ACCESS_TOKEN'
)
LINE_TARGET_ID = os.environ.get('LINE_TARGET_ID', 'YOUR_LINE_USER_OR_GROUP_ID')


def send_line_notification(
    tracking_no,
    fullname,
    phone,
    service_type,
    details,
    latitude='',
    longitude='',
):
  if (
      not LINE_CHANNEL_ACCESS_TOKEN
      or 'YOUR_LINE' in LINE_CHANNEL_ACCESS_TOKEN
  ):
    return

  try:
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}',
    }
    msg_text = (
        f'🏛️ มีคำร้อง E-Service ใหม่ (อบต.เขากอบ)\n'
        f'📌 เลขที่: {tracking_no}\n'
        f'📂 ประเภท: {service_type}\n'
        f'👤 ผู้ยื่น: {fullname}\n'
        f'📞 เบอร์โทร: {phone}\n'
        f'📝 รายละเอียด: {details}\n'
    )
    if latitude and longitude:
      msg_text += (
          f'📍 พิกัด: https://www.google.com/maps?q={latitude},{longitude}\n'
      )

    payload = {
        'to': LINE_TARGET_ID,
        'messages': [{'type': 'text', 'text': msg_text}],
    }
    requests.post(url, headers=headers, data=json.dumps(payload), timeout=3)
  except Exception as e:
    print(f'LINE Error: {e}')


def allowed_file(filename):
  return (
      '.' in filename
      and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
  )


def init_db():
  """สร้างโฟลเดอร์และตารางฐานข้อมูล SQLite"""
  if not os.path.exists(UPLOAD_FOLDER):
    try:
      os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    except Exception as e:
      print(f'Upload folder creation error: {e}')

  try:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                fullname TEXT NOT NULL
            )
        ''')

    cursor.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_no TEXT UNIQUE NOT NULL,
                citizen_id TEXT NOT NULL,
                fullname TEXT NOT NULL,
                phone TEXT NOT NULL,
                service_type TEXT NOT NULL,
                details TEXT NOT NULL,
                image_filename TEXT DEFAULT '',
                latitude TEXT DEFAULT '',
                longitude TEXT DEFAULT '',
                status TEXT DEFAULT 'รอเจ้าหน้าที่รับเรื่อง',
                assigned_officer TEXT DEFAULT '',
                admin_note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')

    cursor.execute('''
            CREATE TABLE IF NOT EXISTS service_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                staff_name TEXT NOT NULL,
                action_status TEXT NOT NULL,
                log_detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES requests (id)
            )
        ''')

    cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    if cursor.fetchone()[0] == 0:
      default_password_hash = generate_password_hash('kaokob1234')
      cursor.execute(
          '''
                INSERT INTO users (username, password, fullname)
                VALUES (?, ?, ?)
            ''',
          ('admin', default_password_hash, 'เจ้าหน้าที่ อบต.เขากอบ'),
      )

    conn.commit()
    conn.close()
  except Exception as e:
    print(f'DB Init Error: {e}')


# สั่งสร้างฐานข้อมูลทันทีเมื่อแอปเริ่มทำงานผ่าน App Context (รองรับ Gunicorn)
with app.app_context():
  init_db()


def login_required(f):

  @functools.wraps(f)
  def decorated_function(*args, **kwargs):
    if not session.get('logged_in'):
      flash('กรุณาล็อคอินเข้าสู่ระบบเจ้าหน้าที่ก่อนใช้งาน', 'warning')
      return redirect(url_for('index', tab='officer'))
    return f(*args, **kwargs)

  return decorated_function


@app.route('/', methods=['GET'])
def index():
  try:
    active_tab = request.args.get('tab', 'submit')
    track_query = request.args.get('track_query', '').strip()
    track_results = None

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if track_query:
      cursor.execute(
          'SELECT * FROM requests WHERE tracking_no = ? OR citizen_id = ? ORDER'
          ' BY id DESC',
          (track_query, track_query),
      )
      requests_data = cursor.fetchall()
      track_results = []
      for req in requests_data:
        cursor.execute(
            'SELECT * FROM service_logs WHERE request_id = ? ORDER BY id DESC',
            (req['id'],),
        )
        logs = cursor.fetchall()
        track_results.append({'req': req, 'logs': logs})

    officer_requests = []
    count_pending = count_process = count_success = 0

    if session.get('logged_in'):
      filter_status = request.args.get('status', '')
      search_keyword = request.args.get('search', '').strip()

      query = 'SELECT * FROM requests WHERE 1=1'
      params = []

      if filter_status:
        query += ' AND status = ?'
        params.append(filter_status)

      if search_keyword:
        query += (
            ' AND (tracking_no LIKE ? OR fullname LIKE ? OR citizen_id LIKE ? OR'
            ' service_type LIKE ?)'
        )
        kw = f'%{search_keyword}%'
        params.extend([kw, kw, kw, kw])

      query += ' ORDER BY id DESC'
      cursor.execute(query, params)
      officer_requests = cursor.fetchall()

      cursor.execute(
          "SELECT COUNT(*) FROM requests WHERE status = 'รอเจ้าหน้าที่รับเรื่อง'"
      )
      count_pending = cursor.fetchone()[0]

      cursor.execute(
          "SELECT COUNT(*) FROM requests WHERE status = 'กำลังดำเนินการ'"
      )
      count_process = cursor.fetchone()[0]

      cursor.execute(
          "SELECT COUNT(*) FROM requests WHERE status = 'ดำเนินการเสร็จสิ้น'"
      )
      count_success = cursor.fetchone()[0]

    conn.close()

    return render_template(
        'index.html',
        active_tab=active_tab,
        track_query=track_query,
        track_results=track_results,
        officer_requests=officer_requests,
        count_pending=count_pending,
        count_process=count_process,
        count_success=count_success,
        filter_status=request.args.get('status', ''),
        search_keyword=request.args.get('search', ''),
    )

  except Exception as e:
    # หากเกิด Error จะแสดงรายละเอียดบนหน้าจอทันทีเพื่อตรวจสอบ
    return (
        f'<h2>❌ เกิดข้อผิดพลาด (Diagnostic Mode):</h2><pre>'
        f'{traceback.format_exc()}</pre>',
        500,
    )


@app.route('/submit', methods=['POST'])
def submit():
  service_type = request.form.get('service_type')
  citizen_id = request.form.get('citizen_id')
  fullname = request.form.get('fullname')
  phone = request.form.get('phone')
  details = request.form.get('details')
  latitude = request.form.get('latitude', '')
  longitude = request.form.get('longitude', '')

  image_filename = ''
  if 'request_image' in request.files:
    file = request.files['request_image']
    if file and file.filename != '' and allowed_file(file.filename):
      try:
        filename = secure_filename(file.filename)
        timestamp_str = datetime.now().strftime('%Y%m%d%H%M%S_')
        image_filename = timestamp_str + filename
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
          os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
      except Exception as e:
        print(f'Save file error: {e}')

  date_str = datetime.now().strftime('%Y%m%d')
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  cursor.execute(
      'SELECT COUNT(*) FROM requests WHERE created_at LIKE ?', (f'{date_str}%',)
  )
  count = cursor.fetchone()[0] + 1
  tracking_no = f'ES-{date_str}-{count:04d}'
  created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

  cursor.execute(
      '''
        INSERT INTO requests (tracking_no, citizen_id, fullname, phone, service_type, details, image_filename, latitude, longitude, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''',
      (
          tracking_no,
          citizen_id,
          fullname,
          phone,
          service_type,
          details,
          image_filename,
          latitude,
          longitude,
          created_at,
      ),
  )

  req_id = cursor.lastrowid
  cursor.execute(
      '''
        INSERT INTO service_logs (request_id, staff_name, action_status, log_detail, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''',
      (
          req_id,
          'ระบบออนไลน์',
          'รอเจ้าหน้าที่รับเรื่อง',
          'ประชาชนยื่นคำร้องผ่านระบบออนไลน์เรียบร้อยแล้ว',
          created_at,
      ),
  )

  conn.commit()
  conn.close()

  send_line_notification(
      tracking_no,
      fullname,
      phone,
      service_type,
      details,
      latitude,
      longitude,
  )

  flash(f'ส่งคำร้องสำเร็จ! เลขที่ติดตามของคุณคือ: {tracking_no}', 'success_submit')
  return redirect(url_for('index', tab='track', track_query=tracking_no))


@app.route('/login', methods=['POST'])
def login():
  username = request.form.get('username')
  password = request.form.get('password')

  conn = sqlite3.connect(DB_NAME)
  conn.row_factory = sqlite3.Row
  cursor = conn.cursor()
  cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
  user = cursor.fetchone()
  conn.close()

  if user and check_password_hash(user['password'], password):
    session['logged_in'] = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['fullname'] = user['fullname']
    flash('เข้าสู่ระบบเจ้าหน้าที่สำเร็จ!', 'success')
  else:
    flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง!', 'danger')

  return redirect(url_for('index', tab='officer'))


@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
  old_password = request.form.get('old_password')
  new_password = request.form.get('new_password')
  confirm_password = request.form.get('confirm_password')

  if new_password != confirm_password:
    flash('รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน!', 'danger')
    return redirect(url_for('index', tab='officer'))

  user_id = session.get('user_id')
  conn = sqlite3.connect(DB_NAME)
  conn.row_factory = sqlite3.Row
  cursor = conn.cursor()

  cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
  user = cursor.fetchone()

  if user and check_password_hash(user['password'], old_password):
    new_hash = generate_password_hash(new_password)
    cursor.execute(
        'UPDATE users SET password = ? WHERE id = ?', (new_hash, user_id)
    )
    conn.commit()
    conn.close()
    flash('เปลี่ยนรหัสผ่านสำเร็จเรียบร้อยแล้ว!', 'success')
  else:
    conn.close()
    flash('รหัสผ่านเดิมไม่ถูกต้อง!', 'danger')

  return redirect(url_for('index', tab='officer'))


@app.route('/logout')
def logout():
  session.clear()
  flash('ออกจากระบบเรียบร้อยแล้ว', 'info')
  return redirect(url_for('index', tab='officer'))


@app.route('/officer/update/<int:req_id>', methods=['POST'])
@login_required
def officer_update(req_id):
  staff_name = request.form.get('staff_name', '').strip()
  action_status = request.form.get('action_status')
  log_detail = request.form.get('log_detail', '').strip()
  created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  cursor.execute(
      '''
        UPDATE requests 
        SET status = ?, assigned_officer = ?, admin_note = ? 
        WHERE id = ?
    ''',
      (action_status, staff_name, log_detail, req_id),
  )

  cursor.execute(
      '''
        INSERT INTO service_logs (request_id, staff_name, action_status, log_detail, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''',
      (req_id, staff_name, action_status, log_detail, created_at),
  )

  conn.commit()
  conn.close()

  flash('บันทึกการดำเนินการเรียบร้อยแล้ว', 'success')
  return redirect(url_for('index', tab='officer'))


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000, debug=True)
