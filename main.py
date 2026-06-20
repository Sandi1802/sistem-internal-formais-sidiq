import hashlib, os, json
from functools import wraps
from datetime import datetime, date
from flask import (Flask, render_template, request, session, redirect,
                   url_for, flash, send_file, jsonify)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from collections import defaultdict   # <-- tambahkan ini
from reportlab.lib.units import mm
import pymysql
import time
import uuid
from dotenv import load_dotenv

load_dotenv()

# ─── Flask config ───────────────────────────────────────────────
app = Flask(__name__, template_folder='template')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sidiq_formais-secret-key-pti-2026')

# Folder downloads (kalau di Vercel, hanya /tmp yang writable)
if os.environ.get('VERCEL'):
    DOWNLOAD_FOLDER = '/tmp/downloads'
else:
    DOWNLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@app.template_filter('date_month_name')
def date_month_name(date_val):
    if not date_val:
        return ''
    try:
        # Date can be datetime.date or string
        if isinstance(date_val, str):
            date_val = datetime.strptime(date_val, '%Y-%m-%d').date()
        months = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 
                  'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
        return months[date_val.month - 1]
    except Exception:
        return date_val

# ─── Database helpers ───────────────────────────────────────────
class MySQLWrapper:
    def __init__(self, **kwargs):
        self.conn = pymysql.connect(**kwargs)

    def execute(self, query, params=()):
        # Convert SQLite placeholders to MySQL placeholders
        
        mysql_query = query.replace('?', '%s')
        cursor = self.conn.cursor()
        cursor.execute(mysql_query, params)
        return cursor

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def get_db():
    return MySQLWrapper(
        host=os.environ.get('MYSQLHOST', 'localhost'),
        port=int(os.environ.get('MYSQLPORT', 3306)),
        user=os.environ.get('MYSQLUSER', 'root'),
        password=os.environ.get('MYSQLPASSWORD', ''),
        database=os.environ.get('MYSQLDATABASE', 'sidiq18'),
        cursorclass=pymysql.cursors.DictCursor
    )

# ─── Auth decorators ───────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu.', 'warning')
            return redirect(url_for('login'))
        return f(*a, **kw)
    return wrapper

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user_role = session.get('role', '').upper()
            if user_role not in [r.upper() for r in allowed_roles] and user_role != 'HC':
                flash(f"Akses ditolak — Anda tidak memiliki izin. Dibutuhkan role: {', '.join(allowed_roles)}", 'danger')
                return redirect(url_for('dashboard'))
            return f(*a, **kw)
        return wrapper
    return decorator

@app.context_processor
def inject_global_vars():
    vars = {}
    if 'user_id' in session:
        db = get_db()
        try:
            # Refresh user role from DB to ensure RBAC is immediate
            current_user = db.execute("SELECT role FROM user WHERE id_user = ?", (session['user_id'],)).fetchone()
            if current_user:
                session['role'] = current_user['role']

            booth_aktif = db.execute("SELECT id_booth FROM ent_booth WHERE status = 'Buka' ORDER BY id_booth DESC LIMIT 1").fetchone()
            vars['global_booth_aktif'] = booth_aktif
        except:
            vars['global_booth_aktif'] = None
        finally:
            db.close()
    return vars
# ─── Normalisasi nama divisi (biar variasi penulisan ke-gabung jadi satu) ───
DIVISI_ALIAS = {
    "syi'ar umat": "Syiar Umat",
    "syiar umat": "Syiar Umat",
    "media & humas": "Humas dan Media",
    "humas dan media": "Humas dan Media",
    "media dan humas": "Humas dan Media",
    "sekretaris": "Sekretariat",
    "sekretariat": "Sekretariat",
}

def normalize_divisi(raw):
    if not raw:
        return raw
    key = raw.strip().lower()
    return DIVISI_ALIAS.get(key, raw.strip())

# ═══════════════════════════════════════════════════════════════
#  ROUTES — AUTH
# ═══════════════════════════════════════════════════════════════
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        uname = request.form.get('username','').strip()
        pwd = request.form.get('password','')
        hashed = hashlib.md5(pwd.encode()).hexdigest()
        db = get_db()
        user = db.execute("SELECT * FROM user WHERE username=?", (uname,)).fetchone()
        db.close()
        if user and (user['password_hash'] == hashed or user['password_hash'] == pwd):
            session['user_id'] = user['id_user']
            session['username'] = user['username']
            session['role'] = user['role']
            session['id_anggota'] = user['id_anggota']
            # Get display name
            if user['id_anggota']:
                db2 = get_db()
                k = db2.execute("SELECT nama FROM anggota WHERE id_anggota=?", (user['id_anggota'],)).fetchone()
                session['user_name'] = k['nama'] if k else user['username']
                db2.close()
            else:
                session['user_name'] = 'Administrator FORMAIS'

            session['has_seen_reminder'] = False
            return redirect(url_for('dashboard'))
        flash('Username atau password salah!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — DASHBOARD
# ═══════════════════════════════════════════════════════════════
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    role = session.get('role')
    
    # --- Gamifikasi Leaderboard (Bulan Ini) ---
    import datetime
    now = datetime.datetime.now()
    current_month = now.month
    current_year = now.year
    
    leaderboard = db.execute('''
        SELECT a.nama, COALESCE(SUM(r.jumlah_poin), 0) as total_poin, a.id_anggota, a.foto_profil
        FROM riwayat_poin r
        JOIN anggota a ON r.id_anggota = a.id_anggota
        WHERE MONTH(r.tanggal) = ? AND YEAR(r.tanggal) = ?
        GROUP BY r.id_anggota
        ORDER BY total_poin DESC
        LIMIT 5
    ''', (current_month, current_year)).fetchall()
    # ------------------------------------------
    # --- Papan Pengumuman ---
    pengumuman_list = db.execute('''
        SELECT p.*, a.nama as nama_pembuat 
        FROM pengumuman p
        LEFT JOIN anggota a ON p.id_pembuat = a.id_anggota
        ORDER BY p.tanggal_dibuat DESC
        LIMIT 6
    ''').fetchall()
    # ------------------------

    if role in ['HC', 'TIM IT', 'ADMIN']:
        # Get count per tipe_anggota
        counts = db.execute("SELECT tipe_anggota, COUNT(*) c FROM anggota GROUP BY tipe_anggota").fetchall()
        counts_dict = {c['tipe_anggota']: c['c'] for c in counts}
        total_pengurus = counts_dict.get('Pengurus', 0)
        total_anggota_biasa = counts_dict.get('Anggota', 0)
        total_calon = counts_dict.get('Calon Anggota', 0)
        total_magang = counts_dict.get('Anak Magang', 0)
        total_mahasiswa = total_pengurus + total_anggota_biasa + total_calon + total_magang
        # Chart Data: Anggota per Divisi (sekalian ambil nama-nama anggotanya buat modal)
        dept_data_raw = db.execute(
            "SELECT divisi, nama FROM anggota WHERE divisi IS NOT NULL AND divisi != '' ORDER BY divisi"
        ).fetchall()

        divisi_map = defaultdict(list)
        for d in dept_data_raw:
            divisi_map[normalize_divisi(d['divisi'])].append(d['nama'])

        anggota_dept_chart = [
            {'name': divisi, 'y': len(nama_list), 'anggota': nama_list}
            for divisi, nama_list in divisi_map.items()
        ]

        # Persuratan
        surat_dibuat = db.execute("SELECT COUNT(*) c FROM persuratan WHERE status='Disetujui'").fetchone()['c']

        # Asset / Barang
        try:
            total_asset = int(db.execute("SELECT COALESCE(SUM(jumlah_total), 0) c FROM barang").fetchone()['c'])
            asset_baik = int(db.execute("SELECT COALESCE(SUM(jumlah_total), 0) c FROM barang WHERE kondisi='Baik'").fetchone()['c'])
            asset_rusak = total_asset - asset_baik
        except:
            total_asset = 0
            asset_baik = 0
            asset_rusak = 0

        # Program Kerja
        total_proker = db.execute("SELECT COUNT(*) c FROM proker").fetchone()['c']
        proker_berjalan = db.execute("SELECT COUNT(*) c FROM proker WHERE status='Dalam proses'").fetchone()['c']
        proker_selesai = db.execute("SELECT COUNT(*) c FROM proker WHERE status='Selesai'").fetchone()['c']
        proker_belum = db.execute("SELECT COUNT(*) c FROM proker WHERE status='Belum dimulai'").fetchone()['c']

        # Keuangan (Saldo Kas = total pemasukan - total pengeluaran)
        kas_masuk_total = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas WHERE jenis='Pemasukan'").fetchone()['s']
        kas_keluar_total = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas WHERE jenis='Pengeluaran'").fetchone()['s']
        saldo_kas = float(kas_masuk_total) - float(kas_keluar_total)

        # Kas Entrepreneur
        ke_masuk = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas_entrepreneur WHERE jenis='Pemasukan'").fetchone()['s']
        ke_keluar = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas_entrepreneur WHERE jenis='Pengeluaran'").fetchone()['s']
        kas_ent_saldo = float(ke_masuk) - float(ke_keluar)

        # Request Desain
        req_desain_total = db.execute("SELECT COUNT(*) c FROM request_desain").fetchone()['c']
        req_desain_proses = db.execute("SELECT COUNT(*) c FROM request_desain WHERE status='Dikerjakan'").fetchone()['c']
        req_desain_selesai = db.execute("SELECT COUNT(*) c FROM request_desain WHERE status='Selesai'").fetchone()['c']
        # Barang Dipinjam
        barang_dipinjam = db.execute("SELECT COUNT(*) c FROM peminjaman WHERE status='Dipinjam'").fetchone()['c']

        # Chart: Kas per bulan (tahun ini)
        import datetime as dt
        tahun = dt.date.today().year
        kas_masuk_chart = [0]*12
        kas_keluar_chart = [0]*12
        kas_bulanan = db.execute("SELECT MONTH(tanggal) m, jenis, SUM(jumlah) s FROM kas WHERE YEAR(tanggal)=? GROUP BY MONTH(tanggal), jenis", (tahun,)).fetchall()
        for row in kas_bulanan:
            idx = int(row['m']) - 1
            if row['jenis'] == 'Pemasukan':
                kas_masuk_chart[idx] = float(row['s'])
            else:
                kas_keluar_chart[idx] = float(row['s'])

        # Recent activity placeholder
        recent = []
        
        pengaturan_lb = db.execute("SELECT nilai FROM pengaturan WHERE kunci='publish_leaderboard'").fetchone()
        publish_lb = True if (not pengaturan_lb or pengaturan_lb['nilai'] == '1') else False

        db.close()
        return render_template('dashboard_hc.html',
                               total_mahasiswa=total_mahasiswa,
                               total_pengurus=total_pengurus,
                               total_anggota_biasa=total_anggota_biasa,
                               total_calon=total_calon,
                               total_magang=total_magang,
                               total_anggota=total_mahasiswa,
                               total_asset=total_asset,
                               surat_dibuat=surat_dibuat,
                               total_proker=total_proker,
                               proker_berjalan=proker_berjalan,
                               proker_selesai=proker_selesai,
                               proker_belum=proker_belum,
                               saldo_kas=saldo_kas,
                               kas_masuk_total=kas_masuk_total,
                               kas_keluar_total=kas_keluar_total,
                               kas_entrepreneur=kas_ent_saldo,
                               req_desain_total=req_desain_total,
                               req_desain_proses=req_desain_proses,
                               req_desain_selesai=req_desain_selesai,
                               barang_dipinjam=barang_dipinjam,
                               anggota_dept_chart=anggota_dept_chart,
                               kas_masuk_chart=kas_masuk_chart,
                               kas_keluar_chart=kas_keluar_chart,
                               recent=recent,
                               leaderboard=leaderboard,
                               pengumuman_list=pengumuman_list,
                               publish_lb=publish_lb)
    else:
        kid = session.get('id_anggota')
        tugas = db.execute('''
            SELECT p.id_penilai, p.kategori, p.status_pengisian, k.nama as dinilai_nama
            FROM penilai p JOIN anggota k ON p.id_anggota_dinilai=k.id_anggota
            WHERE p.id_anggota_penilai=? AND p.id_kuesioner=1
        ''', (kid,)).fetchall()
        hasil = db.execute('''
            SELECT dimensi, skor_atasan, skor_rekan, skor_bawahan, skor_diri, skor_akhir
            FROM hasil_penilaian
            WHERE id_anggota=? AND id_kuesioner=1
        ''', (kid,)).fetchall()

        # Selalu tampilkan chart, default 0 untuk semua kategori
        chart_data = {
            'Amanah': {'atasan':0, 'rekan':0, 'bawahan':0, 'diri':0, 'akhir':0},
            'Kompeten': {'atasan':0, 'rekan':0, 'bawahan':0, 'diri':0, 'akhir':0},
            'Harmonis': {'atasan':0, 'rekan':0, 'bawahan':0, 'diri':0, 'akhir':0},
            'Loyal': {'atasan':0, 'rekan':0, 'bawahan':0, 'diri':0, 'akhir':0},
            'Adaptif': {'atasan':0, 'rekan':0, 'bawahan':0, 'diri':0, 'akhir':0},
            'Kolaboratif': {'atasan':0, 'rekan':0, 'bawahan':0, 'diri':0, 'akhir':0}
        }

        if hasil:
            for r in hasil:
                chart_data[r['dimensi']] = {
                    'atasan': r['skor_atasan'] or 0,
                    'rekan': r['skor_rekan'] or 0,
                    'bawahan': r['skor_bawahan'] or 0,
                    'diri': r['skor_diri'] or 0,
                    'akhir': r['skor_akhir'] or 0
                }

        pending_count = sum(1 for t in tugas if t['status_pengisian'] != 'selesai')
        show_reminder = False
        if pending_count > 0 and not session.get('has_seen_reminder'):
            show_reminder = True
            session['has_seen_reminder'] = True

        # Keuangan
        kas_masuk_total = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas WHERE jenis='Pemasukan'").fetchone()['s']
        kas_keluar_total = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas WHERE jenis='Pengeluaran'").fetchone()['s']
        saldo_kas = float(kas_masuk_total) - float(kas_keluar_total)

        # Kas Entrepreneur
        ke_masuk = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas_entrepreneur WHERE jenis='Pemasukan'").fetchone()['s']
        ke_keluar = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas_entrepreneur WHERE jenis='Pengeluaran'").fetchone()['s']
        kas_ent_saldo = float(ke_masuk) - float(ke_keluar)

        # Request Desain
        req_desain_total = db.execute("SELECT COUNT(*) c FROM request_desain").fetchone()['c']
        req_desain_proses = db.execute("SELECT COUNT(*) c FROM request_desain WHERE status='Dikerjakan'").fetchone()['c']
        req_desain_selesai = db.execute("SELECT COUNT(*) c FROM request_desain WHERE status='Selesai'").fetchone()['c']
        
        pengaturan_lb = db.execute("SELECT nilai FROM pengaturan WHERE kunci='publish_leaderboard'").fetchone()
        publish_lb = True if (not pengaturan_lb or pengaturan_lb['nilai'] == '1') else False

        db.close()
        return render_template('dashboard_anggota.html', tugas=tugas, chart_data=chart_data,
                               show_reminder=show_reminder, pending_count=pending_count,
                               saldo_kas=saldo_kas, kas_ent_saldo=kas_ent_saldo, 
                               req_desain_total=req_desain_total, req_desain_proses=req_desain_proses, req_desain_selesai=req_desain_selesai,
                               leaderboard=leaderboard, pengumuman_list=pengumuman_list,
                               publish_lb=publish_lb)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    db = get_db()
    id_user = session.get('user_id')
    id_anggota = session.get('id_anggota')

    # Ambil data anggota jika ada
    anggota = None
    atasan = None
    if id_anggota:
        anggota = db.execute("SELECT * FROM anggota WHERE id_anggota=?", (id_anggota,)).fetchone()
        if anggota and anggota['id_atasan']:
            atasan = db.execute("SELECT nama FROM anggota WHERE id_anggota=?", (anggota['id_atasan'],)).fetchone()

    if request.method == 'POST':
        old_pw = request.form.get('old_password')
        new_pw = request.form.get('new_password')
        confirm_pw = request.form.get('confirm_password')

        # Validasi
        user = db.execute("SELECT password_hash FROM user WHERE id_user=?", (id_user,)).fetchone()
        old_hash = hashlib.md5(old_pw.encode()).hexdigest()

        if old_hash != user['password_hash']:
            flash('Password Lama yang Anda masukkan salah.', 'danger')
        elif new_pw != confirm_pw:
            flash('Konfirmasi Password Baru tidak cocok.', 'danger')
        elif len(new_pw) < 6:
            flash('Password Baru minimal 6 karakter.', 'warning')
        else:
            new_hash = hashlib.md5(new_pw.encode()).hexdigest()
            db.execute("UPDATE user SET password_hash=? WHERE id_user=?", (new_hash, id_user))
            db.commit()
            flash('Password Anda berhasil diubah! Silakan gunakan password baru pada saat login berikutnya.', 'success')

    db.close()
    return render_template('profile.html', anggota=anggota, atasan=atasan)

# ═══════════════════════════════════════════════════════════════
#  ROUTES — KARYAWAN CRUD (HC only)
# ═══════════════════════════════════════════════════════════════
@app.route('/anggota')
@role_required('TIM IT', 'ADMIN')
def anggota_list():
    db = get_db()
    tipe = request.args.get('tipe', 'Anggota')
    data = db.execute('''
        SELECT k.*, a.nama as atasan_nama,
               COALESCE((SELECT SUM(jumlah_poin) FROM riwayat_poin WHERE id_anggota = k.id_anggota), 0) as total_poin
        FROM anggota k
        LEFT JOIN anggota a ON k.id_atasan=a.id_anggota
        WHERE k.tipe_anggota=?
        ORDER BY k.id_anggota
    ''', (tipe,)).fetchall()
    
    # Ambil pengaturan
    pengaturan_lb = db.execute("SELECT nilai FROM pengaturan WHERE kunci='publish_leaderboard'").fetchone()
    publish_lb = True
    if pengaturan_lb and pengaturan_lb['nilai'] == '0':
        publish_lb = False
        
    db.close()
    return render_template('anggota.html', anggota_list=data, current_tipe=tipe, publish_lb=publish_lb)

@app.route('/anggota/toggle_leaderboard', methods=['POST'])
@role_required('TIM IT', 'ADMIN')
def toggle_leaderboard():
    db = get_db()
    current = db.execute("SELECT nilai FROM pengaturan WHERE kunci='publish_leaderboard'").fetchone()
    new_val = '0' if current and current['nilai'] == '1' else '1'
    db.execute("UPDATE pengaturan SET nilai=? WHERE kunci='publish_leaderboard'", (new_val,))
    db.commit()
    db.close()
    flash('Pengaturan publikasi Papan Peringkat berhasil diubah.', 'success')
    return redirect(url_for('anggota_list'))

@app.route('/anggota/riwayat_poin/<int:id>')
@login_required
def riwayat_poin(id):
    db = get_db()
    anggota = db.execute("SELECT * FROM anggota WHERE id_anggota=?", (id,)).fetchone()
    if not anggota:
        flash('Anggota tidak ditemukan.', 'danger')
        return redirect(url_for('anggota_list'))
    
    # Check permission (only Admin/HC/IT or the member themselves can view)
    user_role = session.get('role', '').upper()
    if user_role not in ['ADMIN', 'HC', 'TIM IT'] and session.get('id_anggota') != id:
        flash('Akses ditolak.', 'danger')
        return redirect(url_for('dashboard'))

    riwayat = db.execute('''
        SELECT r.*, p.nama as pemberi_nama 
        FROM riwayat_poin r 
        LEFT JOIN anggota p ON r.id_pemberi=p.id_anggota
        WHERE r.id_anggota=?
        ORDER BY r.tanggal DESC
    ''', (id,)).fetchall()
    db.close()
    return render_template('riwayat_poin.html', anggota=anggota, riwayat=riwayat)

@app.route('/anggota/riwayat_poin/hapus/<int:id_poin>', methods=['POST'])
@role_required('TIM IT', 'ADMIN')
def hapus_riwayat_poin(id_poin):
    db = get_db()
    poin_data = db.execute("SELECT id_anggota FROM riwayat_poin WHERE id_poin=?", (id_poin,)).fetchone()
    if poin_data:
        db.execute("DELETE FROM riwayat_poin WHERE id_poin=?", (id_poin,))
        db.commit()
        flash('Riwayat poin berhasil dihapus.', 'success')
        id_anggota = poin_data['id_anggota']
        db.close()
        return redirect(url_for('riwayat_poin', id=id_anggota))
    
    db.close()
    flash('Riwayat poin tidak ditemukan.', 'danger')
    return redirect(url_for('anggota_list'))

@app.route('/anggota/tambah', methods=['GET','POST'])
@role_required('TIM IT', 'ADMIN')
def tambah_anggota():
    db = get_db()
    if request.method == 'POST':
        nim = request.form['nim'].strip()
        nama = request.form['nama'].strip()
        jabatan = request.form['jabatan'].strip()
        divisi = request.form['divisi'].strip()
        angkatan = request.form.get('angkatan', '').strip()
        no_wa = request.form.get('no_wa', '').strip()
        status_keanggotaan = request.form.get('status_keanggotaan', 'Aktif').strip()
        tipe_anggota = request.form.get('tipe_anggota', 'Anggota').strip()
        id_atasan = request.form.get('id_atasan') or None
        try:
            c = db.execute("INSERT INTO anggota (nim,nama,jabatan,divisi,angkatan,no_wa,status_keanggotaan,tipe_anggota,id_atasan) VALUES (?,?,?,?,?,?,?,?,?)",
                       (nim, nama, jabatan, divisi, angkatan, no_wa, status_keanggotaan, tipe_anggota, id_atasan))
            kid = c.lastrowid

            # Buat akun otomatis (username = NIM, password = NIM123)
            pw_hash = hashlib.md5((nim + "123").encode()).hexdigest()
            db.execute("INSERT INTO user (username,password_hash,role,id_anggota) VALUES (?,?,'anggota',?)",
                       (nim, pw_hash, kid))
            db.commit()
            flash('Anggota berhasil ditambahkan!', 'success')
        except Exception as e:
            flash(f'Gagal menambahkan anggota: {e}', 'danger')
        db.close()
        return redirect(url_for('anggota_list', tipe=tipe_anggota))
    atasan = db.execute("SELECT id_anggota, nama, jabatan FROM anggota ORDER BY nama").fetchall()
    db.close()
    return render_template('tambah_anggota.html', atasan_list=atasan)

@app.route('/anggota/edit/<int:id>', methods=['GET','POST'])
@role_required('TIM IT', 'ADMIN')
def edit_anggota(id):
    db = get_db()
    if request.method == 'POST':
        angkatan = request.form.get('angkatan', '').strip()
        no_wa = request.form.get('no_wa', '').strip()
        status_keanggotaan = request.form.get('status_keanggotaan', 'Aktif').strip()
        tipe_anggota = request.form.get('tipe_anggota', 'Anggota').strip()
        db.execute("UPDATE anggota SET nim=?,nama=?,jabatan=?,divisi=?,angkatan=?,no_wa=?,status_keanggotaan=?,tipe_anggota=?,id_atasan=? WHERE id_anggota=?",
                   (request.form['nim'], request.form['nama'], request.form['jabatan'],
                    request.form['divisi'], angkatan, no_wa, status_keanggotaan, tipe_anggota, request.form.get('id_atasan') or None, id))
        db.commit(); db.close()
        flash('Data anggota berhasil diperbarui!', 'success')
        return redirect(url_for('anggota_list', tipe=tipe_anggota))
    k = db.execute("SELECT * FROM anggota WHERE id_anggota=?", (id,)).fetchone()
    atasan = db.execute("SELECT id_anggota, nama, jabatan FROM anggota WHERE id_anggota!=? ORDER BY nama", (id,)).fetchall()
    db.close()
    if not k:
        flash('Anggota tidak ditemukan!', 'danger')
        return redirect(url_for('anggota_list'))
    return render_template('edit_anggota.html', anggota=k, atasan_list=atasan)

@app.route('/anggota/hapus/<int:id>')
@role_required('TIM IT', 'ADMIN')
def hapus_anggota(id):
    db = get_db()
    try:
        db.execute("DELETE FROM anggota WHERE id_anggota=?", (id,))
        db.commit()
        flash('Anggota berhasil dihapus!', 'success')
    except:
        flash('Gagal menghapus. Anggota mungkin masih terhubung dengan data lain.', 'danger')
    db.close()
    return redirect(url_for('anggota_list'))

@app.route('/anggota/beri_poin/<int:id>', methods=['POST'])
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def beri_poin(id):
    db = get_db()
    jumlah = request.form.get('jumlah_poin')
    keterangan = request.form.get('keterangan')
    id_pemberi = session.get('id_anggota')
    
    if jumlah and keterangan:
        db.execute('''
            INSERT INTO riwayat_poin (id_anggota, jumlah_poin, keterangan, id_pemberi)
            VALUES (?, ?, ?, ?)
        ''', (id, jumlah, keterangan, id_pemberi))
        db.commit()
        flash(f'Berhasil memberikan {jumlah} poin.', 'success')
    else:
        flash('Data poin tidak lengkap.', 'danger')
        
    db.close()
    return redirect(request.referrer or url_for('anggota_list'))

# ═══════════════════════════════════════════════════════════════
# MANAJEMEN ROLE (HANYA HC)
# ═══════════════════════════════════════════════════════════════
@app.route('/role')
@role_required('HC')
def role_list():
    db = get_db()
    users = db.execute('''
        SELECT u.id_user, u.username, u.role, a.nama 
        FROM user u 
        LEFT JOIN anggota a ON u.id_anggota = a.id_anggota
        ORDER BY CASE WHEN u.role='HC' THEN 1 ELSE 2 END, a.nama ASC
    ''').fetchall()
    return render_template('role.html', users=users)

@app.route('/role/update/<int:id_user>', methods=['POST'])
@role_required('HC')
def role_update(id_user):
    new_role = request.form.get('role', 'ANGGOTA').upper()
    valid_roles = ['HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'ANGGOTA']
    if new_role not in valid_roles:
        flash('Role tidak valid.', 'danger')
        return redirect(url_for('role_list'))
    
    db = get_db()
    db.execute("UPDATE user SET role = ? WHERE id_user = ?", (new_role, id_user))
    db.commit()
    flash('Role berhasil diperbarui!', 'success')
    return redirect(url_for('role_list'))

@app.route('/anggota/import', methods=['POST'])
@role_required('TIM IT', 'ADMIN')
def import_anggota():
    tipe = request.form.get('tipe', 'Anggota')
    if 'file' not in request.files:
        flash('Tidak ada file yang diunggah.', 'danger')
        return redirect(url_for('anggota_list', tipe=tipe))

    file = request.files['file']
    if file.filename == '':
        flash('File tidak valid.', 'danger')
        return redirect(url_for('anggota_list', tipe=tipe))

    try:
        import pandas as pd
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        required_cols = ['NIM', 'Nama', 'Jabatan', 'Divisi']
        for col in required_cols:
            if col not in df.columns:
                flash(f'Kolom wajib {col} tidak ditemukan dalam file!', 'danger')
                return redirect(url_for('anggota_list', tipe=tipe))


        db = get_db()
        success = 0

        # Pass 1: Insert anggota baru
        for _, row in df.iterrows():
            # Hapus .0 jika terbaca sebagai float oleh pandas
            nim = str(row['NIM']).strip().replace('.0', '')
            nama = str(row['Nama']).strip() if pd.notna(row['Nama']) else ''
            if not nama:
                continue  # skip baris yang nama-nya kosong
            jabatan = str(row['Jabatan']).strip()
            divisi = str(row['Divisi']).strip()

            exist = db.execute("SELECT id_anggota FROM anggota WHERE nim=?", (nim,)).fetchone()
            if not exist:
                c = db.execute("INSERT INTO anggota (nim,nama,jabatan,divisi,tipe_anggota,id_atasan) VALUES (?,?,?,?,?,NULL)",
                          (nim, nama, jabatan, divisi, tipe))
                kid = c.lastrowid
                pw_hash = hashlib.md5((nim + "123").encode()).hexdigest()
                db.execute("INSERT INTO user (username,password_hash,role,id_anggota) VALUES (?,?,'anggota',?)",
                          (nim, pw_hash, kid))
                success += 1

        # Pass 2: Update id_atasan
        if 'NIM_Atasan' in df.columns:
            for _, row in df.iterrows():
                nim = str(row['NIM']).strip().replace('.0', '')
                nim_atasan = str(row['NIM_Atasan']).strip().replace('.0', '') if pd.notna(row['NIM_Atasan']) else None

                if nim_atasan and nim_atasan != 'nan' and nim_atasan != 'None' and nim_atasan != '':
                    # Cari id_atasan berdasarkan nim_atasan
                    atasan = db.execute("SELECT id_anggota FROM anggota WHERE nim=?", (nim_atasan,)).fetchone()
                    if atasan:
                        # Update anggota yang bersangkutan
                        db.execute("UPDATE anggota SET id_atasan=? WHERE nim=?", (atasan['id_anggota'], nim))

        db.commit()
        db.close()
        flash(f'Berhasil mengimpor {success} {tipe} baru.', 'success')
    except Exception as e:
        flash(f'Gagal mengimpor file: {str(e)}', 'danger')

    return redirect(url_for('anggota_list', tipe=tipe))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — KUESIONER
# ═══════════════════════════════════════════════════════════════
@app.route('/kuesioner')
@login_required
def kuesioner():
    db = get_db()
    role = session.get('role')
    if role == 'admin':
        kuesioner_list = db.execute("SELECT * FROM kuesioner ORDER BY id_kuesioner DESC").fetchall()
        # Count stats per kuesioner
        stats = {}
        for q in kuesioner_list:
            total = db.execute("SELECT COUNT(*) c FROM penilai WHERE id_kuesioner=?", (q['id_kuesioner'],)).fetchone()['c']
            done = db.execute("SELECT COUNT(*) c FROM penilai WHERE id_kuesioner=? AND status_pengisian='selesai'", (q['id_kuesioner'],)).fetchone()['c']
            stats[q['id_kuesioner']] = {'total': total, 'done': done}
        db.close()
        return render_template('kuesioner_list.html', kuesioner_list=kuesioner_list, stats=stats)
    else:
        kid = session.get('id_anggota')
        tugas = db.execute('''
            SELECT p.id_penilai, p.kategori, p.status_pengisian, k.nama as dinilai_nama,
                   q.nama_kuesioner, q.periode
            FROM penilai p
            JOIN anggota k ON p.id_anggota_dinilai=k.id_anggota
            JOIN kuesioner q ON p.id_kuesioner=q.id_kuesioner
            WHERE p.id_anggota_penilai=? ORDER BY p.status_pengisian ASC
        ''', (kid,)).fetchall()
        db.close()
        return render_template('kuesioner_list.html', tugas=tugas)

@app.route('/kuesioner/kelola', methods=['GET','POST'])
@app.route('/kuesioner/kelola/<int:id>', methods=['GET','POST'])
@role_required('TIM IT', 'ADMIN')
def kuesioner_kelola(id=None):
    db = get_db()
    if request.method == 'POST':
        nama = request.form['nama_kuesioner']
        periode = request.form['periode']
        tgl_mulai = request.form['tanggal_mulai']
        tgl_selesai = request.form['tanggal_selesai']
        status = request.form.get('status', 'draft')
        if id:
            db.execute("UPDATE kuesioner SET nama_kuesioner=?,periode=?,tanggal_mulai=?,tanggal_selesai=?,status=? WHERE id_kuesioner=?",
                       (nama, periode, tgl_mulai, tgl_selesai, status, id))
        else:
            c = db.execute("INSERT INTO kuesioner (nama_kuesioner,periode,tanggal_mulai,tanggal_selesai,status) VALUES (?,?,?,?,?)",
                       (nama, periode, tgl_mulai, tgl_selesai, status))
            id_q = c.lastrowid

            # Auto-seed standard AKHLAK questions
            questions = [
                ('Amanah', 1, 'Memenuhi janji dan komitmen.'),
                ('Amanah', 2, 'Bertanggung jawab atas tugas, keputusan, dan tindakan yang dilakukan.'),
                ('Amanah', 3, 'Berpegang teguh kepada nilai moral dan etika.'),
                ('Kompeten', 1, 'Meningkatkan kompetensi diri untuk menjawab tantangan yang selalu berubah.'),
                ('Kompeten', 2, 'Membantu orang lain belajar.'),
                ('Kompeten', 3, 'Menyelesaikan tugas dengan kualitas terbaik.'),
                ('Harmonis', 1, 'Menghargai setiap orang apapun latar belakangnya.'),
                ('Harmonis', 2, 'Suka menolong orang lain.'),
                ('Harmonis', 3, 'Membangun lingkungan kerja yang kondusif.'),
                ('Loyal', 1, 'Menjaga nama baik sesama anggota, pimpinan, BUMN, dan Negara.'),
                ('Loyal', 2, 'Rela berkorban untuk mencapai tujuan yang lebih besar.'),
                ('Loyal', 3, 'Patuh kepada pimpinan sepanjang tidak bertentangan dengan hukum dan etika.'),
                ('Adaptif', 1, 'Cepat menyesuaikan diri untuk menjadi lebih baik.'),
                ('Adaptif', 2, 'Terus-menerus melakukan perbaikan mengikuti perkembangan teknologi.'),
                ('Adaptif', 3, 'Bertindak proaktif.'),
                ('Kolaboratif', 1, 'Memberi kesempatan kepada berbagai pihak untuk berkontribusi.'),
                ('Kolaboratif', 2, 'Terbuka dalam bekerja sama untuk menghasilkan nilai tambah.'),
                ('Kolaboratif', 3, 'Menggerakkan pemanfaatan berbagai sumber daya untuk tujuan bersama.')
            ]
            for q_dim, q_urut, q_teks in questions:
                db.execute("INSERT INTO pertanyaan (id_kuesioner, dimensi_akhlak, urutan, teks_pertanyaan) VALUES (?, ?, ?, ?)",
                           (id_q, q_dim, q_urut, q_teks))

        db.commit()
        flash('Kuesioner berhasil disimpan!', 'success')
        db.close()
        return redirect(url_for('kuesioner'))
    q = None
    if id:
        q = db.execute("SELECT * FROM kuesioner WHERE id_kuesioner=?", (id,)).fetchone()
    db.close()
    return render_template('kuesioner_kelola.html', kuesioner=q)

# ═══════════════════════════════════════════════════════════════
#  ROUTES — KONFIGURASI PENILAI
# ═══════════════════════════════════════════════════════════════
@app.route('/penilai_config', methods=['GET'])
@role_required('TIM IT', 'ADMIN')
def penilai_config():
    db = get_db()
    kuesioner = db.execute("SELECT * FROM kuesioner ORDER BY id_kuesioner DESC").fetchall()
    anggota = db.execute("SELECT id_anggota, nim, nama, jabatan FROM anggota ORDER BY nama").fetchall()

    id_kuesioner = request.args.get('id_kuesioner', type=int)
    penilai_list = []
    if id_kuesioner:
        penilai_list = db.execute('''
            SELECT p.id_penilai, p.kategori, k_penilai.nama as penilai_nama, k_dinilai.nama as dinilai_nama, k_dinilai.id_anggota as dinilai_id
            FROM penilai p
            JOIN anggota k_penilai ON p.id_anggota_penilai = k_penilai.id_anggota
            JOIN anggota k_dinilai ON p.id_anggota_dinilai = k_dinilai.id_anggota
            WHERE p.id_kuesioner = ?
            ORDER BY k_dinilai.nama, p.kategori
        ''', (id_kuesioner,)).fetchall()

    db.close()
    return render_template('penilai_config.html', kuesioner=kuesioner, anggota=anggota,
                           id_kuesioner=id_kuesioner, penilai_list=penilai_list)

@app.route('/penilai_config/add', methods=['POST'])
@role_required('TIM IT', 'ADMIN')
def penilai_config_add():
    db = get_db()
    id_kuesioner = request.form['id_kuesioner']
    id_dinilai = request.form['id_dinilai']
    id_penilai = request.form['id_penilai']
    kategori = request.form['kategori']

    # Check exists
    exists = db.execute("SELECT id_penilai FROM penilai WHERE id_kuesioner=? AND id_anggota_dinilai=? AND id_anggota_penilai=?",
                        (id_kuesioner, id_dinilai, id_penilai)).fetchone()
    if exists:
        flash('Penilai tersebut sudah ditugaskan untuk anggota ini!', 'warning')
    else:
        db.execute("INSERT INTO penilai (id_kuesioner, id_anggota_penilai, id_anggota_dinilai, kategori) VALUES (?,?,?,?)",
                   (id_kuesioner, id_penilai, id_dinilai, kategori))
        db.commit()
        flash('Berhasil menambahkan penilai.', 'success')

    db.close()
    return redirect(url_for('penilai_config', id_kuesioner=id_kuesioner))

@app.route('/penilai_config/delete/<int:id_penilai>')
@role_required('TIM IT', 'ADMIN')
def penilai_config_delete(id_penilai):
    db = get_db()
    p = db.execute("SELECT id_kuesioner FROM penilai WHERE id_penilai=?", (id_penilai,)).fetchone()
    if p:
        db.execute("DELETE FROM penilai WHERE id_penilai=?", (id_penilai,))
        db.commit()
        flash('Berhasil menghapus penilai.', 'success')
        id_kuesioner = p['id_kuesioner']
    else:
        id_kuesioner = None
    db.close()
    return redirect(url_for('penilai_config', id_kuesioner=id_kuesioner))

@app.route('/kuesioner/isi/<int:id_penilai>', methods=['GET','POST'])
@login_required
def kuesioner_isi(id_penilai):
    db = get_db()
    pnl = db.execute('''
        SELECT p.*, k.nama as dinilai_nama, q.nama_kuesioner
        FROM penilai p
        JOIN anggota k ON p.id_anggota_dinilai=k.id_anggota
        JOIN kuesioner q ON p.id_kuesioner=q.id_kuesioner
        WHERE p.id_penilai=?
    ''', (id_penilai,)).fetchone()
    if not pnl or pnl['id_anggota_penilai'] != session.get('id_anggota'):
        flash('Akses ditolak.', 'danger')
        return redirect(url_for('kuesioner'))

    pertanyaan = db.execute('''
        SELECT * FROM pertanyaan WHERE id_kuesioner=? ORDER BY dimensi_akhlak, urutan
    ''', (pnl['id_kuesioner'],)).fetchall()

    # Group by dimensi
    grouped = {}
    for p in pertanyaan:
        dim = p['dimensi_akhlak']
        if dim not in grouped:
            grouped[dim] = []
        grouped[dim].append(dict(p))

    # Existing answers
    existing = {}
    for j in db.execute("SELECT id_pertanyaan, skor FROM jawaban WHERE id_penilai=?", (id_penilai,)).fetchall():
        existing[j['id_pertanyaan']] = j['skor']

    if request.method == 'POST':
        # Save all answers
        db.execute("DELETE FROM jawaban WHERE id_penilai=?", (id_penilai,))
        is_draft = 1 if request.form.get('action') == 'draft' else 0
        for p in pertanyaan:
            skor = request.form.get(f'q_{p["id_pertanyaan"]}')
            if skor:
                db.execute("INSERT INTO jawaban (id_penilai,id_pertanyaan,skor,is_draft) VALUES (?,?,?,?)",
                           (id_penilai, p['id_pertanyaan'], int(skor), is_draft))
        status = 'draft' if is_draft else 'selesai'
        db.execute("UPDATE penilai SET status_pengisian=? WHERE id_penilai=?", (status, id_penilai))
        db.commit()
        db.close()
        if is_draft:
            flash('Draft berhasil disimpan!', 'info')
        else:
            flash('Kuesioner berhasil disubmit!', 'success')
        return redirect(url_for('kuesioner'))

    total = len(pertanyaan)
    answered = len(existing)
    progress = round(answered / total * 100) if total > 0 else 0
    db.close()
    dimensi_order = ['Amanah','Kompeten','Harmonis','Loyal','Adaptif','Kolaboratif']
    return render_template('kuesioner_isi.html', penilai=pnl, grouped=grouped,
                           existing=existing, progress=progress, dimensi_order=dimensi_order)

@app.route('/kuesioner/status')
@role_required('TIM IT', 'ADMIN')
def kuesioner_status():
    db = get_db()
    data = db.execute('''
        SELECT k.nama as anggota_nama, k.divisi, k.jabatan,
               k2.nama as dinilai_nama, p.kategori, p.status_pengisian, p.id_penilai
        FROM penilai p
        JOIN anggota k ON p.id_anggota_penilai=k.id_anggota
        JOIN anggota k2 ON p.id_anggota_dinilai=k2.id_anggota
        WHERE p.id_kuesioner=1
        ORDER BY k.nama
    ''').fetchall()
    # Summary
    total = len(data)
    selesai = sum(1 for d in data if d['status_pengisian'] == 'selesai')
    draft = sum(1 for d in data if d['status_pengisian'] == 'draft')
    belum = sum(1 for d in data if d['status_pengisian'] == 'belum')
    db.close()
    return render_template('kuesioner_status.html', status_list=data,
                           total=total, selesai=selesai, draft=draft, belum=belum)

# ═══════════════════════════════════════════════════════════════
#  ROUTES — HASIL PENILAIAN
# ═══════════════════════════════════════════════════════════════
@app.route('/hasil')
@login_required
def hasil():
    db = get_db()
    role = session.get('role')
    if role == 'admin':
        # Get all anggota with their average scores
        data = db.execute('''
            SELECT k.id_anggota, k.nim, k.nama, k.jabatan, k.divisi,
                   ROUND(AVG(h.skor_akhir),2) as rata_rata
            FROM anggota k
            LEFT JOIN hasil_penilaian h ON k.id_anggota=h.id_anggota
            GROUP BY k.id_anggota
            ORDER BY rata_rata DESC
        ''').fetchall()
    else:
        kid = session.get('id_anggota')
        data = db.execute('''
            SELECT k.id_anggota, k.nim, k.nama, k.jabatan, k.divisi,
                   ROUND(AVG(h.skor_akhir),2) as rata_rata
            FROM anggota k
            LEFT JOIN hasil_penilaian h ON k.id_anggota=h.id_anggota
            WHERE k.id_anggota=?
            GROUP BY k.id_anggota
        ''', (kid,)).fetchall()
    db.close()
    return render_template('hasil_penilaian.html', hasil_list=data)

@app.route('/hasil/detail/<int:id_anggota>')
@login_required
def hasil_detail(id_anggota):
    db = get_db()
    role = session.get('role')
    kid = session.get('id_anggota')
    if role != 'admin' and kid != id_anggota:
        flash('Akses ditolak.', 'danger')
        return redirect(url_for('hasil'))
    k = db.execute("SELECT * FROM anggota WHERE id_anggota=?", (id_anggota,)).fetchone()
    skor = db.execute("SELECT * FROM hasil_penilaian WHERE id_anggota=? AND id_kuesioner=1 ORDER BY dimensi", (id_anggota,)).fetchall()
    db.close()
    if not k:
        flash('Anggota tidak ditemukan.', 'danger')
        return redirect(url_for('hasil'))
    chart_data = {}
    for s in skor:
        chart_data[s['dimensi']] = {
            'atasan': s['skor_atasan'], 'rekan': s['skor_rekan'],
            'bawahan': s['skor_bawahan'], 'diri': s['skor_diri'],
            'akhir': s['skor_akhir']
        }
    return render_template('hasil_detail.html', anggota=k, skor_list=skor,
                           chart_data=json.dumps(chart_data))

# ═══════════════════════════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════════════════════════
@app.route('/api/autosave', methods=['POST'])
@login_required
def api_autosave():
    data = request.get_json()
    id_penilai = data.get('id_penilai')
    answers = data.get('answers', {})
    db = get_db()
    for pid_str, skor in answers.items():
        pid = int(pid_str)
        existing = db.execute("SELECT id_jawaban FROM jawaban WHERE id_penilai=? AND id_pertanyaan=?",
                              (id_penilai, pid)).fetchone()
        if existing:
            db.execute("UPDATE jawaban SET skor=?,is_draft=1 WHERE id_jawaban=?",
                       (int(skor), existing['id_jawaban']))
        else:
            db.execute("INSERT INTO jawaban (id_penilai,id_pertanyaan,skor,is_draft) VALUES (?,?,?,1)",
                       (id_penilai, pid, int(skor)))
    db.execute("UPDATE penilai SET status_pengisian='draft' WHERE id_penilai=?", (id_penilai,))
    db.commit()
    db.close()
    return jsonify({'status': 'ok', 'message': 'Draft tersimpan'})

@app.route('/api/hitung-skor/<int:id_kuesioner>', methods=['POST'])
@role_required('TIM IT', 'ADMIN')
def api_hitung_skor(id_kuesioner):
    db = get_db()
    dimensi_list = ['Amanah','Kompeten','Harmonis','Loyal','Adaptif','Kolaboratif']
    anggota_ids = [r['id_anggota'] for r in db.execute("SELECT DISTINCT id_anggota_dinilai as id_anggota FROM penilai WHERE id_kuesioner=?", (id_kuesioner,)).fetchall()]
    db.execute("DELETE FROM hasil_penilaian WHERE id_kuesioner=?", (id_kuesioner,))
    count = 0
    for kid in anggota_ids:
        for dim in dimensi_list:
            scores = {}
            for kat, bobot in [('atasan',0.4),('rekan',0.2),('bawahan',0.3),('diri_sendiri',0.1)]:
                row = db.execute('''
                    SELECT AVG(j.skor) as avg_skor FROM jawaban j
                    JOIN penilai p ON j.id_penilai=p.id_penilai
                    JOIN pertanyaan pt ON j.id_pertanyaan=pt.id_pertanyaan
                    WHERE p.id_anggota_dinilai=? AND p.id_kuesioner=?
                    AND p.kategori=? AND pt.dimensi_akhlak=? AND p.status_pengisian='selesai'
                ''', (kid, id_kuesioner, kat, dim)).fetchone()
                scores[kat] = float(row['avg_skor']) if (row and row['avg_skor'] is not None) else 0
            akhir = scores['atasan']*0.4 + scores['rekan']*0.2 + scores['bawahan']*0.3 + scores['diri_sendiri']*0.1
            if any(v > 0 for v in scores.values()):
                db.execute("""INSERT INTO hasil_penilaian (id_kuesioner,id_anggota,dimensi,
                    skor_atasan,skor_rekan,skor_bawahan,skor_diri,skor_akhir) VALUES (?,?,?,?,?,?,?,?)""",
                    (id_kuesioner, kid, dim, round(scores['atasan'],2), round(scores['rekan'],2),
                     round(scores['bawahan'],2), round(scores['diri_sendiri'],2), round(akhir,2)))
                count += 1
    db.commit()
    db.close()
    flash(f'Skor berhasil dihitung! {count} hasil penilaian diperbarui.', 'success')
    return redirect(url_for('hasil'))

@app.route('/api/chart-data/<int:id_anggota>')
@login_required
def api_chart_data(id_anggota):
    db = get_db()
    skor = db.execute("SELECT * FROM hasil_penilaian WHERE id_anggota=? AND id_kuesioner=1", (id_anggota,)).fetchall()
    db.close()
    result = {}
    for s in skor:
        result[s['dimensi']] = {
            'atasan': s['skor_atasan'], 'rekan': s['skor_rekan'],
            'bawahan': s['skor_bawahan'], 'diri': s['skor_diri'], 'akhir': s['skor_akhir']
        }
    return jsonify(result)

# ═══════════════════════════════════════════════════════════════
#  ROUTES — PROGRAM KERJA (PROKER)
# ═══════════════════════════════════════════════════════════════
@app.route('/proker')
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def proker_list():
    db = get_db()
    tipe = request.args.get('tipe', 'Akbar')
    proker = db.execute('''
        SELECT p.*, a.nama as pic_nama 
        FROM proker p 
        LEFT JOIN anggota a ON p.penanggung_jawab = a.id_anggota 
        WHERE p.tipe_proker = ?
        ORDER BY p.tanggal_mulai ASC
    ''', (tipe,)).fetchall()
    
    anggota_list = db.execute("SELECT id_anggota, nama, jabatan FROM anggota ORDER BY nama").fetchall()
    db.close()
    return render_template('proker.html', proker=proker, anggota_list=anggota_list, current_tipe=tipe)

@app.route('/proker/tambah', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def tambah_proker():
    db = get_db()
    tipe_proker = request.form.get('tipe_proker', 'Kecil')
    nama_proker = request.form['nama_proker']
    divisi = request.form.get('divisi', '')
    penanggung_jawab = request.form.get('penanggung_jawab') or None
    target = request.form.get('target', '')
    tanggal_mulai = request.form.get('tanggal_mulai') or None
    tanggal_selesai = request.form.get('tanggal_selesai') or None
    anggaran = request.form.get('anggaran', 0)
    
    db.execute('''
        INSERT INTO proker (nama_proker, tipe_proker, divisi, penanggung_jawab, target, tanggal_mulai, tanggal_selesai, anggaran)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (nama_proker, tipe_proker, divisi, penanggung_jawab, target, tanggal_mulai, tanggal_selesai, anggaran))
    db.commit()
    db.close()
    flash('Program kerja berhasil ditambahkan.', 'success')
    return redirect(url_for('proker_list', tipe=tipe_proker))

@app.route('/proker/edit/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def edit_proker(id):
    db = get_db()
    tipe_proker = request.form.get('tipe_proker', 'Kecil')
    nama_proker = request.form['nama_proker']
    divisi = request.form.get('divisi', '')
    penanggung_jawab = request.form.get('penanggung_jawab') or None
    target = request.form.get('target', '')
    tanggal_mulai = request.form.get('tanggal_mulai') or None
    tanggal_selesai = request.form.get('tanggal_selesai') or None
    anggaran = request.form.get('anggaran', 0)
    
    db.execute('''
        UPDATE proker SET 
            nama_proker=?, divisi=?, penanggung_jawab=?, target=?, 
            tanggal_mulai=?, tanggal_selesai=?, anggaran=?
        WHERE id_proker=?
    ''', (nama_proker, divisi, penanggung_jawab, target, tanggal_mulai, tanggal_selesai, anggaran, id))
    db.commit()
    db.close()
    flash('Program kerja berhasil diperbarui.', 'success')
    return redirect(url_for('proker_list', tipe=tipe_proker))

@app.route('/proker/hapus/<int:id>')
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def hapus_proker(id):
    db = get_db()
    tipe_proker = request.args.get('tipe', 'Akbar')
    db.execute("DELETE FROM proker WHERE id_proker=?", (id,))
    db.commit()
    db.close()
    flash('Program kerja berhasil dihapus.', 'success')
    return redirect(request.referrer or url_for('proker_list', tipe=tipe_proker))

@app.route('/proker/update_status/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def update_status_proker(id):
    from werkzeug.utils import secure_filename
    db = get_db()
    tipe_proker = request.form.get('tipe_proker', 'Kecil')
    status = request.form.get('status', 'Belum dimulai')
    catatan = request.form.get('catatan', '')
    
    file = request.files.get('file_hasil')
    file_name = None
    
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        # using the same download logic as other uploads in flask or just save to static/uploads
        upload_folder = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, filename))
        file_name = filename
    
    if file_name:
        db.execute("UPDATE proker SET status=?, catatan=?, file_hasil=? WHERE id_proker=?", 
                   (status, catatan, file_name, id))
    else:
        db.execute("UPDATE proker SET status=?, catatan=? WHERE id_proker=?", 
                   (status, catatan, id))
                   
    db.commit()
    db.close()
    flash('Status program kerja diperbarui.', 'success')
    return redirect(url_for('proker_list', tipe=tipe_proker))

@app.route('/proker/import', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def import_proker():
    import pandas as pd
    tipe_proker = request.form.get('tipe_proker', 'Kecil')
    
    if 'file' not in request.files:
        flash('Tidak ada file yang diunggah.', 'danger')
        return redirect(url_for('proker_list', tipe=tipe_proker))

    file = request.files['file']
    if file.filename == '':
        flash('File tidak valid.', 'danger')
        return redirect(url_for('proker_list', tipe=tipe_proker))

    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Expected columns: Nama Program, Divisi, Target, Anggaran, Tanggal Mulai, Tanggal Selesai, Status
        db = get_db()
        success = 0

        for _, row in df.iterrows():
            nama_proker = str(row.get('Nama Program', '')).strip()
            if not nama_proker or nama_proker == 'nan':
                continue
                
            divisi = str(row.get('Divisi', '')).strip() if pd.notna(row.get('Divisi')) else ''
            target = str(row.get('Target', '')).strip() if pd.notna(row.get('Target')) else ''
            
            # Parsing Anggaran
            anggaran_val = row.get('Anggaran', 0)
            try:
                anggaran = float(str(anggaran_val).replace('Rp', '').replace('.', '').replace(',', '').strip())
            except:
                anggaran = 0
                
            # Parsing Tanggal
            tgl_mulai = row.get('Tanggal Mulai')
            tanggal_mulai = str(tgl_mulai).strip() if pd.notna(tgl_mulai) else None
            if tanggal_mulai == 'nan': tanggal_mulai = None
            
            tgl_selesai = row.get('Tanggal Selesai')
            tanggal_selesai = str(tgl_selesai).strip() if pd.notna(tgl_selesai) else None
            if tanggal_selesai == 'nan': tanggal_selesai = None
            
            status = str(row.get('Status', 'Belum dimulai')).strip() if pd.notna(row.get('Status')) else 'Belum dimulai'

            db.execute('''
                INSERT INTO proker (nama_proker, tipe_proker, divisi, target, anggaran, tanggal_mulai, tanggal_selesai, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (nama_proker, tipe_proker, divisi, target, anggaran, tanggal_mulai, tanggal_selesai, status))
            success += 1

        db.commit()
        db.close()
        flash(f'Berhasil mengimpor {success} Program Kerja ({tipe_proker}) baru dari file Excel.', 'success')
    except Exception as e:
        flash(f'Gagal mengimpor file Excel: {str(e)}', 'danger')

    return redirect(url_for('proker_list', tipe=tipe_proker))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — PRESENTASI (SLIDESHOW)
# ═══════════════════════════════════════════════════════════════
@app.route('/presentasi')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'MEDIA')
def presentasi_list():
    db = get_db()
    presentasi = db.execute('''
        SELECT p.*, a.nama as nama_pembuat 
        FROM presentasi p
        LEFT JOIN anggota a ON p.id_pembuat = a.id_anggota
        ORDER BY p.tanggal_dibuat DESC
    ''').fetchall()
    db.close()
    return render_template('presentasi_list.html', presentasi=presentasi)

@app.route('/presentasi/tambah', methods=['POST'])
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'MEDIA')
def tambah_presentasi():
    db = get_db()
    judul = request.form['judul']
    topik = request.form['topik']
    id_pembuat = session.get('id_anggota')
    
    db.execute("INSERT INTO presentasi (id_pembuat, judul, topik) VALUES (?, ?, ?)", (id_pembuat, judul, topik))
    db.commit()
    db.close()
    flash('Presentasi berhasil dibuat. Silakan tambahkan slide.', 'success')
    return redirect(url_for('presentasi_list'))

@app.route('/presentasi/<int:id_pres>/slide', methods=['GET', 'POST'])
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'MEDIA')
def presentasi_slide(id_pres):
    db = get_db()
    pres = db.execute("SELECT * FROM presentasi WHERE id_presentasi=?", (id_pres,)).fetchone()
    if not pres:
        db.close()
        return redirect(url_for('presentasi_list'))
        
    if request.method == 'POST':
        urutan = request.form['urutan']
        judul_slide = request.form['judul_slide']
        konten = request.form['konten']
        
        # Handle gambar
        gambar_filename = None
        if 'gambar' in request.files:
            file = request.files['gambar']
            if file.filename != '':
                import uuid
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                from werkzeug.utils import secure_filename
                filename = secure_filename(f"slide_{id_pres}_{urutan}_{uuid.uuid4().hex[:6]}.{ext}")
                file.save(os.path.join(app.root_path, 'static/uploads/presentasi', filename))
                gambar_filename = filename
                
        db.execute('''
            INSERT INTO presentasi_slide (id_presentasi, urutan, judul_slide, konten, gambar)
            VALUES (?, ?, ?, ?, ?)
        ''', (id_pres, urutan, judul_slide, konten, gambar_filename))
        db.commit()
        flash('Slide berhasil ditambahkan!', 'success')
        
    slides = db.execute("SELECT * FROM presentasi_slide WHERE id_presentasi=? ORDER BY urutan ASC", (id_pres,)).fetchall()
    db.close()
    return render_template('presentasi_editor.html', pres=pres, slides=slides)

@app.route('/presentasi/slide/hapus/<int:id_slide>')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'MEDIA')
def hapus_slide(id_slide):
    db = get_db()
    slide = db.execute("SELECT id_presentasi, gambar FROM presentasi_slide WHERE id_slide=?", (id_slide,)).fetchone()
    if slide:
        if slide['gambar']:
            try:
                os.remove(os.path.join(app.root_path, 'static/uploads/presentasi', slide['gambar']))
            except: pass
        db.execute("DELETE FROM presentasi_slide WHERE id_slide=?", (id_slide,))
        db.commit()
    db.close()
    flash('Slide dihapus.', 'success')
    return redirect(request.referrer or url_for('presentasi_list'))

@app.route('/presentasi/hapus/<int:id_pres>')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN')
def hapus_presentasi(id_pres):
    db = get_db()
    slides = db.execute("SELECT gambar FROM presentasi_slide WHERE id_presentasi=?", (id_pres,)).fetchall()
    for s in slides:
        if s['gambar']:
            try:
                os.remove(os.path.join(app.root_path, 'static/uploads/presentasi', s['gambar']))
            except: pass
    db.execute("DELETE FROM presentasi_slide WHERE id_presentasi=?", (id_pres,))
    db.execute("DELETE FROM presentasi WHERE id_presentasi=?", (id_pres,))
    db.commit()
    db.close()
    flash('Presentasi berhasil dihapus secara keseluruhan.', 'success')
    return redirect(url_for('presentasi_list'))

@app.route('/presentasi/play/<int:id_pres>')
@login_required
def presentasi_play(id_pres):
    db = get_db()
    pres = db.execute("SELECT * FROM presentasi WHERE id_presentasi=?", (id_pres,)).fetchone()
    slides = db.execute("SELECT * FROM presentasi_slide WHERE id_presentasi=? ORDER BY urutan ASC", (id_pres,)).fetchall()
    db.close()
    return render_template('presentasi_play.html', pres=pres, slides=slides)
# ═══════════════════════════════════════════════════════════════
#  ROUTES — REQUEST DESAIN
# ═══════════════════════════════════════════════════════════════
@app.route('/request_desain')
@login_required
def request_desain_list():
    db = get_db()
    user_role = session.get('role', 'ANGGOTA').upper()
    id_anggota = session.get('id_anggota')
    
    # Kueri untuk mendapatkan semua request desain
    if user_role in ['HC', 'TIM IT', 'ADMIN', 'MEDIA']:
        reqs = db.execute('''
            SELECT r.*, a.nama as nama_pemohon 
            FROM request_desain r
            LEFT JOIN anggota a ON r.id_pemohon = a.id_anggota
            ORDER BY r.tanggal_request DESC
        ''').fetchall()
    else:
        reqs = db.execute('''
            SELECT r.*, a.nama as nama_pemohon 
            FROM request_desain r
            LEFT JOIN anggota a ON r.id_pemohon = a.id_anggota
            WHERE r.id_pemohon = ?
            ORDER BY r.tanggal_request DESC
        ''', (id_anggota,)).fetchall()
        
    db.close()
    return render_template('request_desain_list.html', reqs=reqs)

@app.route('/request_desain/tambah', methods=['GET', 'POST'])
@login_required
def tambah_request_desain():
    if request.method == 'POST':
        db = get_db()
        id_pemohon = session.get('id_anggota')
        judul_desain = request.form['judul_desain']
        jenis_desain = request.form['jenis_desain']
        deadline = request.form['deadline']
        deskripsi = request.form['deskripsi']
        link_referensi = request.form.get('link_referensi', '')
        
        db.execute('''
            INSERT INTO request_desain (id_pemohon, judul_desain, jenis_desain, deadline, deskripsi, link_referensi)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (id_pemohon, judul_desain, jenis_desain, deadline, deskripsi, link_referensi))
        db.commit()
        db.close()
        
        flash('Request Desain berhasil diajukan!', 'success')
        return redirect(url_for('request_desain_list'))
    
    return render_template('request_desain_form.html')

@app.route('/request_desain/update_status/<int:id>', methods=['POST'])
@role_required('HC', 'TIM IT', 'ADMIN', 'MEDIA')
def update_status_request_desain(id):
    db = get_db()
    status = request.form['status']
    link_hasil = request.form.get('link_hasil', '')
    
    db.execute("UPDATE request_desain SET status=?, link_hasil=? WHERE id_request=?", (status, link_hasil, id))
    db.commit()
    db.close()
    
    flash(f'Status Request Desain diperbarui menjadi {status}.', 'success')
    return redirect(url_for('request_desain_list'))

@app.route('/request_desain/hapus/<int:id>')
@login_required
def hapus_request_desain(id):
    db = get_db()
    req = db.execute("SELECT id_pemohon FROM request_desain WHERE id_request=?", (id,)).fetchone()
    if not req:
        db.close()
        return redirect(url_for('request_desain_list'))
        
    user_role = session.get('role', '').upper()
    if req['id_pemohon'] != session.get('id_anggota') and user_role not in ['HC', 'TIM IT', 'ADMIN']:
        db.close()
        flash('Anda tidak berhak menghapus request ini.', 'danger')
        return redirect(url_for('request_desain_list'))
        
    db.execute("DELETE FROM request_desain WHERE id_request=?", (id,))
    db.commit()
    db.close()
    
    flash('Request desain berhasil dihapus.', 'success')
    return redirect(url_for('request_desain_list'))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — KEUANGAN (BENDAHARA)
# ═══════════════════════════════════════════════════════════════
@app.route('/keuangan')
@role_required('TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA')
def keuangan_list():
    db = get_db()
    tab = request.args.get('tab', 'Arus Kas')
    
    # Calculate Summaries
    total_pemasukan = db.execute("SELECT COALESCE(SUM(jumlah), 0) s FROM kas WHERE jenis='Pemasukan'").fetchone()['s']
    total_pengeluaran = db.execute("SELECT COALESCE(SUM(jumlah), 0) s FROM kas WHERE jenis='Pengeluaran'").fetchone()['s']
    saldo_saat_ini = total_pemasukan - total_pengeluaran
    
    # Fetch data based on tab
    if tab == 'Pemasukan':
        kas_data = db.execute("SELECT * FROM kas WHERE jenis='Pemasukan' ORDER BY tanggal DESC, id_kas DESC").fetchall()
    elif tab == 'Pengeluaran':
        kas_data = db.execute("SELECT * FROM kas WHERE jenis='Pengeluaran' ORDER BY tanggal DESC, id_kas DESC").fetchall()
    else:
        kas_data = db.execute("SELECT * FROM kas ORDER BY tanggal DESC, id_kas DESC").fetchall()
        
    db.close()
    return render_template('keuangan.html', 
                           kas=kas_data, 
                           current_tab=tab,
                           total_pemasukan=total_pemasukan,
                           total_pengeluaran=total_pengeluaran,
                           saldo_saat_ini=saldo_saat_ini)

@app.route('/keuangan/tambah', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA')
def tambah_keuangan():
    from werkzeug.utils import secure_filename
    db = get_db()
    tab_current = request.form.get('tab_current', 'Arus Kas')
    jenis = request.form['jenis']
    tanggal = request.form['tanggal']
    keterangan = request.form['keterangan']
    kategori = request.form.get('kategori', '')
    jumlah = request.form['jumlah']
    
    file = request.files.get('bukti_file')
    file_name = None
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        upload_folder = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, filename))
        file_name = filename
        
    db.execute('''
        INSERT INTO kas (jenis, kategori, keterangan, jumlah, tanggal, bukti_file, dicatat_oleh)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (jenis, kategori, keterangan, jumlah, tanggal, file_name, session.get('id_user')))
    db.commit()
    db.close()
    
    flash('Transaksi keuangan berhasil ditambahkan.', 'success')
    return redirect(url_for('keuangan_list', tab=tab_current))

@app.route('/keuangan/edit/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA')
def edit_keuangan(id):
    from werkzeug.utils import secure_filename
    db = get_db()
    tab_current = request.form.get('tab_current', 'Arus Kas')
    jenis = request.form['jenis']
    tanggal = request.form['tanggal']
    keterangan = request.form['keterangan']
    kategori = request.form.get('kategori', '')
    jumlah = request.form['jumlah']
    
    file = request.files.get('bukti_file')
    file_name = None
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        upload_folder = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, filename))
        file_name = filename
        
    if file_name:
        db.execute('''
            UPDATE kas SET jenis=?, kategori=?, keterangan=?, jumlah=?, tanggal=?, bukti_file=?
            WHERE id_kas=?
        ''', (jenis, kategori, keterangan, jumlah, tanggal, file_name, id))
    else:
        db.execute('''
            UPDATE kas SET jenis=?, kategori=?, keterangan=?, jumlah=?, tanggal=?
            WHERE id_kas=?
        ''', (jenis, kategori, keterangan, jumlah, tanggal, id))
        
    db.commit()
    db.close()
    
    flash('Transaksi keuangan berhasil diperbarui.', 'success')
    return redirect(url_for('keuangan_list', tab=tab_current))

@app.route('/keuangan/hapus/<int:id>')
@role_required('TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA')
def hapus_keuangan(id):
    db = get_db()
    tab = request.args.get('tab', 'Arus Kas')
    db.execute("DELETE FROM kas WHERE id_kas=?", (id,))
    db.commit()
    db.close()
    flash('Transaksi keuangan berhasil dihapus.', 'success')
    return redirect(url_for('keuangan_list', tab=tab))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — INVENTARIS (BARANG)
# ═══════════════════════════════════════════════════════════════
@app.route('/inventaris')
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def inventaris_list():
    db = get_db()
    
    # Summary
    total_barang = db.execute("SELECT COALESCE(SUM(jumlah_total), 0) s FROM barang").fetchone()['s']
    baik = db.execute("SELECT COALESCE(SUM(jumlah_total), 0) s FROM barang WHERE kondisi='Baik'").fetchone()['s']
    rusak = float(total_barang or 0) - float(baik or 0)
    
    barang_data = db.execute("SELECT * FROM barang ORDER BY id_barang DESC").fetchall()
    db.close()
    
    return render_template('inventaris.html', 
                           barang=barang_data,
                           total_barang=int(total_barang or 0),
                           baik=int(baik or 0),
                           rusak=int(rusak or 0))

@app.route('/inventaris/tambah', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def tambah_inventaris():
    from werkzeug.utils import secure_filename
    db = get_db()
    
    nama_barang = request.form['nama_barang']
    kategori = request.form.get('kategori', '')
    jumlah_total = request.form['jumlah_total']
    jumlah_tersedia = request.form['jumlah_tersedia']
    kondisi = request.form['kondisi']
    lokasi = request.form.get('lokasi', '')
    keterangan = request.form.get('keterangan', '')
    
    file = request.files.get('foto')
    foto_name = None
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        upload_folder = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, filename))
        foto_name = filename
        
    db.execute('''
        INSERT INTO barang (nama_barang, kategori, jumlah_total, jumlah_tersedia, kondisi, lokasi, foto, keterangan)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (nama_barang, kategori, jumlah_total, jumlah_tersedia, kondisi, lokasi, foto_name, keterangan))
    
    db.commit()
    db.close()
    flash('Barang berhasil ditambahkan ke inventaris.', 'success')
    return redirect(url_for('inventaris_list'))

@app.route('/inventaris/edit/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def edit_inventaris(id):
    from werkzeug.utils import secure_filename
    db = get_db()
    
    nama_barang = request.form['nama_barang']
    kategori = request.form.get('kategori', '')
    jumlah_total = request.form['jumlah_total']
    jumlah_tersedia = request.form['jumlah_tersedia']
    kondisi = request.form['kondisi']
    lokasi = request.form.get('lokasi', '')
    keterangan = request.form.get('keterangan', '')
    
    file = request.files.get('foto')
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        upload_folder = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, filename))
        
        db.execute('''
            UPDATE barang SET nama_barang=?, kategori=?, jumlah_total=?, jumlah_tersedia=?, kondisi=?, lokasi=?, foto=?, keterangan=?
            WHERE id_barang=?
        ''', (nama_barang, kategori, jumlah_total, jumlah_tersedia, kondisi, lokasi, filename, keterangan, id))
    else:
        db.execute('''
            UPDATE barang SET nama_barang=?, kategori=?, jumlah_total=?, jumlah_tersedia=?, kondisi=?, lokasi=?, keterangan=?
            WHERE id_barang=?
        ''', (nama_barang, kategori, jumlah_total, jumlah_tersedia, kondisi, lokasi, keterangan, id))
        
    db.commit()
    db.close()
    flash('Data barang berhasil diperbarui.', 'success')
    return redirect(url_for('inventaris_list'))

@app.route('/inventaris/hapus/<int:id>')
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def hapus_inventaris(id):
    db = get_db()
    db.execute("DELETE FROM barang WHERE id_barang=?", (id,))
    db.commit()
    db.close()
    flash('Barang berhasil dihapus.', 'success')
    return redirect(url_for('inventaris_list'))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — TIM ENTREPRENEUR
# ═══════════════════════════════════════════════════════════════
@app.route('/entrepreneur')
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def entrepreneur_list():
    db = get_db()
    tab = request.args.get('tab', 'Dashboard')
    
    # Init vars
    produk = []
    penjualan = []
    kas = []
    saldo_kas_ent = 0
    total_omzet = 0
    total_laba = 0
    total_qty = 0
    summary_kegiatan = []
    
    # Global Kas Entrepreneur Saldo
    ke_masuk = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas_entrepreneur WHERE jenis='Pemasukan'").fetchone()['s']
    ke_keluar = db.execute("SELECT COALESCE(SUM(jumlah),0) s FROM kas_entrepreneur WHERE jenis='Pengeluaran'").fetchone()['s']
    saldo_kas_ent = float(ke_masuk) - float(ke_keluar)

    if tab == 'Produk':
        produk = db.execute("SELECT * FROM ent_produk ORDER BY nama_produk ASC").fetchall()
    elif tab == 'Penjualan':
        penjualan = db.execute('''
            SELECT p.*, pr.nama_produk 
            FROM ent_penjualan p 
            JOIN ent_produk pr ON p.id_produk = pr.id_produk 
            ORDER BY p.tanggal DESC, p.id_penjualan DESC
        ''').fetchall()
        produk = db.execute("SELECT * FROM ent_produk ORDER BY nama_produk ASC").fetchall()
    elif tab == 'Kas':
        kas = db.execute("SELECT * FROM kas_entrepreneur ORDER BY tanggal DESC, id_kas_ent DESC").fetchall()
    else:
        # Dashboard stats
        total_omzet = db.execute("SELECT COALESCE(SUM(total_pendapatan),0) s FROM ent_penjualan").fetchone()['s']
        total_laba = db.execute("SELECT COALESCE(SUM(laba_bersih),0) s FROM ent_penjualan").fetchone()['s']
        total_qty = db.execute("SELECT COALESCE(SUM(jumlah_terjual),0) s FROM ent_penjualan").fetchone()['s']
        summary_kegiatan = db.execute('''
            SELECT kegiatan, COALESCE(SUM(jumlah_terjual),0) as qty, COALESCE(SUM(total_pendapatan),0) as omzet, COALESCE(SUM(laba_bersih),0) as laba 
            FROM ent_penjualan GROUP BY kegiatan ORDER BY omzet DESC
        ''').fetchall()

    db.close()
    return render_template('entrepreneur.html', 
                           current_tab=tab,
                           produk=produk,
                           penjualan=penjualan,
                           kas=kas,
                           saldo_kas_ent=saldo_kas_ent,
                           total_omzet=total_omzet,
                           total_laba=total_laba,
                           total_qty=total_qty,
                           summary_kegiatan=summary_kegiatan)

# -- Produk --
@app.route('/entrepreneur/produk/tambah', methods=['POST'])
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def tambah_produk():
    db = get_db()
    nama_produk = request.form['nama_produk']
    kategori = request.form.get('kategori', '')
    stok = request.form['stok']
    harga_modal = request.form['harga_modal']
    harga_jual = request.form['harga_jual']
    
    db.execute('''
        INSERT INTO ent_produk (nama_produk, kategori, stok, harga_modal, harga_jual)
        VALUES (?, ?, ?, ?, ?)
    ''', (nama_produk, kategori, stok, harga_modal, harga_jual))
    db.commit()
    db.close()
    flash('Produk berhasil ditambahkan.', 'success')
    return redirect(url_for('entrepreneur_list', tab='Produk'))

@app.route('/entrepreneur/produk/edit/<int:id>', methods=['POST'])
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def edit_produk(id):
    db = get_db()
    nama_produk = request.form['nama_produk']
    kategori = request.form.get('kategori', '')
    stok = request.form['stok']
    harga_modal = request.form['harga_modal']
    harga_jual = request.form['harga_jual']
    
    db.execute('''
        UPDATE ent_produk SET nama_produk=?, kategori=?, stok=?, harga_modal=?, harga_jual=?
        WHERE id_produk=?
    ''', (nama_produk, kategori, stok, harga_modal, harga_jual, id))
    db.commit()
    db.close()
    flash('Produk berhasil diupdate.', 'success')
    return redirect(url_for('entrepreneur_list', tab='Produk'))

@app.route('/entrepreneur/produk/hapus/<int:id>')
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def hapus_produk(id):
    db = get_db()
    db.execute("DELETE FROM ent_produk WHERE id_produk=?", (id,))
    db.commit()
    db.close()
    flash('Produk berhasil dihapus.', 'success')
    return redirect(url_for('entrepreneur_list', tab='Produk'))

# -- Penjualan --
@app.route('/entrepreneur/penjualan/tambah', methods=['POST'])
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def tambah_penjualan():
    db = get_db()
    tanggal = request.form['tanggal']
    kegiatan = request.form['kegiatan']
    id_produk = request.form['id_produk']
    jumlah = int(request.form['jumlah'])
    
    # Get produk details
    produk = db.execute("SELECT * FROM ent_produk WHERE id_produk=?", (id_produk,)).fetchone()
    if not produk or produk['stok'] < jumlah:
        flash('Stok tidak mencukupi untuk penjualan ini.', 'danger')
        db.close()
        return redirect(url_for('entrepreneur_list', tab='Penjualan'))
        
    harga_modal = float(produk['harga_modal'])
    harga_jual = float(produk['harga_jual'])
    total_pendapatan = harga_jual * jumlah
    laba_bersih = (harga_jual - harga_modal) * jumlah
    
    # Insert Penjualan
    db.execute('''
        INSERT INTO ent_penjualan (tanggal, kegiatan, id_produk, jumlah_terjual, total_pendapatan, laba_bersih)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (tanggal, kegiatan, id_produk, jumlah, total_pendapatan, laba_bersih))
    
    # Kurangi stok
    db.execute("UPDATE ent_produk SET stok = stok - ? WHERE id_produk=?", (jumlah, id_produk))
    
    # Masukkan otomatis ke kas_entrepreneur
    keterangan_kas = f"Penjualan dari {kegiatan} ({jumlah}x {produk['nama_produk']})"
    db.execute('''
        INSERT INTO kas_entrepreneur (tanggal, keterangan, jenis, kategori, jumlah, dicatat_oleh)
        VALUES (?, ?, 'Pemasukan', 'Penjualan', ?, ?)
    ''', (tanggal, keterangan_kas, total_pendapatan, session.get('id_user')))
    
    db.commit()
    db.close()
    flash('Penjualan berhasil dicatat! Stok dipotong dan Kas Entrepreneur bertambah.', 'success')
    return redirect(url_for('entrepreneur_list', tab='Penjualan'))

@app.route('/entrepreneur/penjualan/hapus/<int:id>')
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def hapus_penjualan(id):
    db = get_db()
    penjualan = db.execute("SELECT * FROM ent_penjualan WHERE id_penjualan=?", (id,)).fetchone()
    if penjualan:
        # Kembalikan stok
        db.execute("UPDATE ent_produk SET stok = stok + ? WHERE id_produk=?", (penjualan['jumlah_terjual'], penjualan['id_produk']))
        # Hapus penjualan
        db.execute("DELETE FROM ent_penjualan WHERE id_penjualan=?", (id,))
        db.commit()
        flash('Data penjualan dihapus. Stok produk telah dikembalikan.', 'success')
    db.close()
    return redirect(url_for('entrepreneur_list', tab='Penjualan'))

# -- Kas Entrepreneur (Manual) --
@app.route('/entrepreneur/kas/tambah', methods=['POST'])
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def tambah_kas_ent():
    db = get_db()
    jenis = request.form['jenis']
    tanggal = request.form['tanggal']
    keterangan = request.form['keterangan']
    kategori = request.form.get('kategori', '')
    jumlah = request.form['jumlah']
    
    db.execute('''
        INSERT INTO kas_entrepreneur (jenis, tanggal, keterangan, kategori, jumlah, dicatat_oleh)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (jenis, tanggal, keterangan, kategori, jumlah, session.get('id_user')))
    db.commit()
    db.close()
    flash('Kas Entrepreneur berhasil dicatat.', 'success')
    return redirect(url_for('entrepreneur_list', tab='Kas'))

@app.route('/entrepreneur/kas/hapus/<int:id>')
@role_required('HC', 'TIM IT', 'ADMIN', 'BENDAHARA', 'ENTREPRENEUR')
def hapus_kas_ent(id):
    db = get_db()
    db.execute("DELETE FROM kas_entrepreneur WHERE id_kas_ent=?", (id,))
    db.commit()
    db.close()
    flash('Catatan kas dihapus.', 'success')
    return redirect(url_for('entrepreneur_list', tab='Kas'))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — KALENDER KEGIATAN
# ═══════════════════════════════════════════════════════════════
@app.route('/kalender')
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'ANGGOTA')
def kalender():
    return render_template('kalender.html')

@app.route('/kalender/api')
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE', 'BENDAHARA', 'ENTREPRENEUR', 'ANGGOTA')
def kalender_api():
    from flask import jsonify
    db = get_db()
    divisi = request.args.get('divisi', '')
    
    if divisi:
        events = db.execute("SELECT * FROM kalender_kegiatan WHERE divisi=?", (divisi,)).fetchall()
    else:
        events = db.execute("SELECT * FROM kalender_kegiatan").fetchall()
    
    db.close()
    
    # Format data untuk FullCalendar
    calendar_events = []
    for e in events:
        event = {
            'id': e['id_kegiatan'],
            'title': e['judul'],
            'start': e['tanggal_mulai'].isoformat() if e['tanggal_mulai'] else None,
            'end': e['tanggal_selesai'].isoformat() if e['tanggal_selesai'] else None,
            'backgroundColor': e['warna'] or '#1D9E75',
            'borderColor': e['warna'] or '#1D9E75',
            'extendedProps': {
                'deskripsi': e['deskripsi'],
                'lokasi': e['lokasi'],
                'divisi': e['divisi']
            }
        }
        calendar_events.append(event)
        
    return jsonify(calendar_events)

@app.route('/kalender/tambah', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def kalender_tambah():
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    db = get_db()
    
    judul = request.form['judul']
    tanggal_mulai_str = request.form['tanggal_mulai']
    tanggal_selesai_str = request.form.get('tanggal_selesai', '')
    divisi = request.form.get('divisi', 'Lainnya')
    warna = request.form.get('warna', '#1D9E75')
    lokasi = request.form.get('lokasi', '')
    deskripsi = request.form.get('deskripsi', '')
    
    tipe_rutin = request.form.get('tipe_rutin', '')
    batas_rutin_str = request.form.get('batas_rutin', '')
    
    # Parsing tanggal
    fmt = '%Y-%m-%dT%H:%M'
    start_dt = datetime.strptime(tanggal_mulai_str, fmt)
    end_dt = datetime.strptime(tanggal_selesai_str, fmt) if tanggal_selesai_str else None
    
    # Jika tidak rutin, insert 1 kali
    if not tipe_rutin:
        db.execute('''
            INSERT INTO kalender_kegiatan (judul, tanggal_mulai, tanggal_selesai, divisi, warna, lokasi, deskripsi, dibuat_oleh)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (judul, start_dt, end_dt, divisi, warna, lokasi, deskripsi, session.get('id_user')))
    else:
        # Rutinan
        if batas_rutin_str:
            batas_dt = datetime.strptime(batas_rutin_str, '%Y-%m-%d').replace(hour=23, minute=59)
            current_start = start_dt
            current_end = end_dt
            
            while current_start <= batas_dt:
                db.execute('''
                    INSERT INTO kalender_kegiatan (judul, tanggal_mulai, tanggal_selesai, divisi, warna, lokasi, deskripsi, dibuat_oleh)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (judul, current_start, current_end, divisi, warna, lokasi, deskripsi, session.get('id_user')))
                
                # Increment time
                if tipe_rutin == 'Harian':
                    current_start += relativedelta(days=1)
                    if current_end: current_end += relativedelta(days=1)
                elif tipe_rutin == 'Mingguan':
                    current_start += relativedelta(weeks=1)
                    if current_end: current_end += relativedelta(weeks=1)
                elif tipe_rutin == 'Bulanan':
                    current_start += relativedelta(months=1)
                    if current_end: current_end += relativedelta(months=1)
                else:
                    break
    
    db.commit()
    db.close()
    flash('Kegiatan berhasil ditambahkan ke kalender.', 'success')
    return redirect(url_for('kalender'))

@app.route('/kalender/hapus/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def kalender_hapus(id):
    db = get_db()
    db.execute("DELETE FROM kalender_kegiatan WHERE id_kegiatan=?", (id,))
    db.commit()
    db.close()
    flash('Kegiatan berhasil dihapus dari kalender.', 'success')
    return redirect(url_for('kalender'))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — PERSURATAN
# ═══════════════════════════════════════════════════════════════
@app.route('/persuratan')
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def persuratan():
    db = get_db()
    surat = db.execute("SELECT * FROM persuratan ORDER BY id_surat DESC").fetchall()
    db.close()
    return render_template('persuratan.html', surat=surat)

@app.route('/persuratan/tambah', methods=['GET', 'POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def persuratan_tambah():
    if request.method == 'POST':
        import json
        db = get_db()
        
        jenis_surat = request.form['jenis_surat']
        nomor_surat = request.form['nomor_surat']
        tanggal_surat = request.form['tanggal_surat']
        perihal = request.form['perihal']
        tujuan = request.form['tujuan']
        
        # Simpan seluruh form data sebagai JSON
        data_surat_dict = dict(request.form)
        data_surat_json = json.dumps(data_surat_dict)
        
        db.execute('''
            INSERT INTO persuratan (jenis_surat, nomor_surat, perihal, tanggal_surat, tujuan_pengirim, status, created_by, data_surat)
            VALUES (?, ?, ?, ?, ?, 'Draft', ?, ?)
        ''', (jenis_surat, nomor_surat, perihal, tanggal_surat, tujuan, session.get('id_anggota'), data_surat_json))
        
        db.commit()
        db.close()
        flash('Surat berhasil dibuat dan tersimpan sebagai Draft.', 'success')
        return redirect(url_for('persuratan'))
        
    # GET Request
    from datetime import datetime
    today_date = datetime.now().strftime('%Y-%m-%d')
    return render_template('persuratan_form.html', today_date=today_date)

@app.route('/persuratan/cetak/<int:id>')
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def persuratan_cetak(id):
    import json
    db = get_db()
    s = db.execute("SELECT * FROM persuratan WHERE id_surat=?", (id,)).fetchone()
    db.close()
    
    if not s:
        flash('Surat tidak ditemukan.', 'danger')
        return redirect(url_for('persuratan'))
        
    data = {}
    if s['data_surat']:
        try:
            data = json.loads(s['data_surat'])
        except:
            pass
            
    return render_template('surat_cetak.html', s=s, data=data)

@app.route('/persuratan/setuju/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def persuratan_setuju(id):
    db = get_db()
    db.execute("UPDATE persuratan SET status='Disetujui' WHERE id_surat=?", (id,))
    db.commit()
    db.close()
    flash('Surat telah disetujui.', 'success')
    return redirect(url_for('persuratan'))

@app.route('/persuratan/hapus/<int:id>', methods=['POST'])
@role_required('TIM IT', 'ADMIN', 'SEKRE')
def persuratan_hapus(id):
    db = get_db()
    db.execute("DELETE FROM persuratan WHERE id_surat=?", (id,))
    db.commit()
    db.close()
    flash('Surat berhasil dihapus.', 'success')
    return redirect(url_for('persuratan'))

# ═══════════════════════════════════════════════════════════════
#  ROUTES — BOOTH KEJUJURAN (ENTREPRENEUR)
# ═══════════════════════════════════════════════════════════════
@app.route('/ent/booth')
@login_required
def ent_booth():
    db = get_db()
    booth_list = db.execute("SELECT * FROM ent_booth ORDER BY id_booth DESC").fetchall()
    
    # Cek apakah ada yang masih buka
    booth_aktif = db.execute("SELECT * FROM ent_booth WHERE status = 'Buka' ORDER BY id_booth DESC LIMIT 1").fetchone()
    
    import json
    from datetime import datetime
    
    # Generate WA Format dynamically
    for b in booth_list:
        try:
            data = json.loads(b['data_laporan'])
            tanggal_str = b['tanggal'].strftime('%A %d %B %Y') if isinstance(b['tanggal'], datetime) else b['tanggal']
            
            # Format text
            wa_text = f"*BOOTH KEJUJURAN*\n\n*{tanggal_str}*\n"
            wa_text += f"Opening: {b['opening_by']}\nLaporan:\n"
            for idx, item in enumerate(data.get('items', []), 1):
                wa_text += f"{idx}. {item['nama']}: {item['stok_awal']}\n"
            wa_text += f"\nJumlah Uang: -\n\n"
            
            if b['status'] == 'Tutup':
                wa_text += f"Closing: {b['closing_by']}\nLaporan:\n"
                for idx, item in enumerate(data.get('items', []), 1):
                    sisa = item.get('stok_sisa', item['stok_awal'])
                    wa_text += f"{idx}. {item['nama']}: {sisa}\n"
                # formatting currecy safely
                uang = "{:,.0f}".format(b['uang_fisik']).replace(',', '.')
                wa_text += f"\nJumlah Uang: {uang}\n"
            
            # Replace newline with HTML br for JS copy to maintain structure or just use literal \n
            # Actually innerText will keep \n if we use white-space: pre-wrap or just literal \n inside display:none
            b['format_wa'] = wa_text
        except:
            b['format_wa'] = "Format tidak tersedia."
            
    db.close()
    return render_template('ent_booth.html', booth_list=booth_list, booth_aktif=booth_aktif)

@app.route('/ent/booth/open', methods=['GET', 'POST'])
@login_required
def ent_booth_open():
    db = get_db()
    # Pastikan tidak ada booth yang sedang buka
    aktif = db.execute("SELECT id_booth FROM ent_booth WHERE status = 'Buka'").fetchone()
    if aktif:
        flash('Masih ada Booth yang sedang Buka. Silakan Tutup terlebih dahulu.', 'warning')
        return redirect(url_for('ent_booth'))
        
    if request.method == 'POST':
        import json
        from datetime import datetime
        
        opening_by = request.form['opening_by']
        tanggal = request.form['tanggal']
        
        id_produk = request.form.getlist('id_produk[]')
        nama_produk = request.form.getlist('nama_produk[]')
        stok_awal = request.form.getlist('stok_awal[]')
        
        items = []
        for i in range(len(id_produk)):
            items.append({
                'id': id_produk[i],
                'nama': nama_produk[i],
                'stok_awal': int(stok_awal[i])
            })
            
        data_laporan = json.dumps({'items': items})
        waktu_buka = datetime.now()
        
        db.execute('''
            INSERT INTO ent_booth (tanggal, opening_by, status, data_laporan, waktu_buka)
            VALUES (?, ?, 'Buka', ?, ?)
        ''', (tanggal, opening_by, data_laporan, waktu_buka))
        
        db.commit()
        db.close()
        flash('Booth berhasil dibuka!', 'success')
        return redirect(url_for('ent_booth'))
        
    from datetime import datetime
    today_date = datetime.now().strftime('%Y-%m-%d')
    produk = db.execute("SELECT id_produk, nama_produk, stok FROM ent_produk WHERE kategori = 'Booth Kejujuran' OR kategori IS NULL").fetchall()
    db.close()
    return render_template('ent_booth_open.html', today_date=today_date, produk=produk)

@app.route('/ent/booth/close/<int:id>', methods=['GET', 'POST'])
@login_required
def ent_booth_close(id):
    db = get_db()
    booth = db.execute("SELECT * FROM ent_booth WHERE id_booth = ?", (id,)).fetchone()
    if not booth or booth['status'] == 'Tutup':
        flash('Sesi booth tidak valid atau sudah ditutup.', 'danger')
        return redirect(url_for('ent_booth'))
        
    import json
    data_laporan = json.loads(booth['data_laporan'])
    
    if request.method == 'POST':
        from datetime import datetime
        closing_by = request.form['closing_by']
        uang_fisik = float(request.form['uang_fisik'])
        
        items = data_laporan['items']
        total_pendapatan_sistem = 0
        
        for item in items:
            sisa_key = f"stok_sisa_{item['id']}"
            if sisa_key in request.form:
                stok_sisa = int(request.form[sisa_key])
                terjual = item['stok_awal'] - stok_sisa
                item['stok_sisa'] = stok_sisa
                item['terjual'] = terjual
                
                if terjual > 0:
                    # Ambil harga_jual dari tabel produk
                    p = db.execute("SELECT harga_modal, harga_jual FROM ent_produk WHERE id_produk = ?", (item['id'],)).fetchone()
                    if p:
                        harga_jual = p['harga_jual'] or 0
                        harga_modal = p['harga_modal'] or 0
                        pendapatan = terjual * harga_jual
                        laba = terjual * (harga_jual - harga_modal)
                        total_pendapatan_sistem += pendapatan
                        
                        # 1. Update stok di ent_produk
                        db.execute("UPDATE ent_produk SET stok = ? WHERE id_produk = ?", (stok_sisa, item['id']))
                        
                        # 2. Insert ke ent_penjualan
                        db.execute('''
                            INSERT INTO ent_penjualan (tanggal, kegiatan, id_produk, jumlah_terjual, total_pendapatan, laba_bersih)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (booth['tanggal'], 'Booth Kejujuran', item['id'], terjual, pendapatan, laba))
                        
        # 3. Insert Pemasukan ke kas_entrepreneur
        if uang_fisik > 0:
            db.execute('''
                INSERT INTO kas_entrepreneur (jenis, kategori, keterangan, jumlah, tanggal, dicatat_oleh)
                VALUES ('Pemasukan', 'Penjualan Booth', 'Penjualan Booth Kejujuran Harian', ?, ?, NULL)
            ''', (uang_fisik, booth['tanggal']))
            
        data_laporan_baru = json.dumps({'items': items})
        waktu_tutup = datetime.now()
        
        db.execute('''
            UPDATE ent_booth 
            SET closing_by = ?, status = 'Tutup', data_laporan = ?, uang_fisik = ?, waktu_tutup = ?
            WHERE id_booth = ?
        ''', (closing_by, data_laporan_baru, uang_fisik, waktu_tutup, id))
        
        db.commit()
        db.close()
        flash('Booth berhasil ditutup dan uang masuk ke Kas Entrepreneur!', 'success')
        return redirect(url_for('ent_booth'))
        
    db.close()
    return render_template('ent_booth_close.html', booth=booth, data_laporan=data_laporan)

# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
#  ROUTES — PENGUMUMAN (MADING)
# ═══════════════════════════════════════════════════════════════
@app.route('/pengumuman/tambah', methods=['POST'])
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def tambah_pengumuman():
    db = get_db()
    judul = request.form['judul']
    isi = request.form['isi_teks']
    prioritas = request.form['prioritas']
    id_pembuat = session.get('id_anggota')
    
    db.execute('''
        INSERT INTO pengumuman (id_pembuat, judul, isi_teks, prioritas)
        VALUES (?, ?, ?, ?)
    ''', (id_pembuat, judul, isi, prioritas))
    db.commit()
    db.close()
    flash('Pengumuman berhasil ditambahkan di Mading.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/pengumuman/hapus/<int:id>')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def hapus_pengumuman(id):
    db = get_db()
    db.execute("DELETE FROM pengumuman WHERE id_pengumuman=?", (id,))
    db.commit()
    db.close()
    flash('Pengumuman dihapus.', 'success')
    return redirect(url_for('dashboard'))

import socket

# ═══════════════════════════════════════════════════════════════
#  ROUTES — ABSENSI DIGITAL (QR CODE)
# ═══════════════════════════════════════════════════════════════

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def generate_absensi_token(id_sesi, kode_rahasia, window_offset=0):
    window_size = 1800 # 30 menit
    current_window = int(time.time()) // window_size + window_offset
    raw_str = f"{id_sesi}_{kode_rahasia}_{current_window}"
    return hashlib.md5(raw_str.encode()).hexdigest()

@app.route('/absensi')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def absensi_list():
    db = get_db()
    sesi_list = db.execute('''
        SELECT s.*, a.nama as nama_pembuat,
               (SELECT COUNT(*) FROM absensi_kehadiran k WHERE k.id_sesi = s.id_sesi) as total_hadir
        FROM absensi_sesi s
        LEFT JOIN anggota a ON s.id_pembuat = a.id_anggota
        ORDER BY s.tanggal DESC
    ''').fetchall()
    db.close()
    return render_template('absensi_list.html', sesi_list=sesi_list)

@app.route('/absensi/buat', methods=['POST'])
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def absensi_buat():
    db = get_db()
    nama_kegiatan = request.form['nama_kegiatan']
    id_pembuat = session.get('id_anggota')
    kode_rahasia = str(uuid.uuid4())[:8]
    
    db.execute('''
        INSERT INTO absensi_sesi (nama_kegiatan, kode_rahasia, id_pembuat)
        VALUES (?, ?, ?)
    ''', (nama_kegiatan, kode_rahasia, id_pembuat))
    db.commit()
    db.close()
    flash('Sesi absensi baru berhasil dibuat!', 'success')
    return redirect(url_for('absensi_list'))

@app.route('/absensi/tutup/<int:id_sesi>')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def absensi_tutup(id_sesi):
    db = get_db()
    db.execute("UPDATE absensi_sesi SET status_buka=0 WHERE id_sesi=?", (id_sesi,))
    db.commit()
    db.close()
    flash('Sesi absensi ditutup.', 'success')
    return redirect(url_for('absensi_list'))

@app.route('/absensi/proyektor/<int:id_sesi>')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def absensi_proyektor(id_sesi):
    db = get_db()
    sesi = db.execute("SELECT * FROM absensi_sesi WHERE id_sesi=?", (id_sesi,)).fetchone()
    if not sesi or not sesi['status_buka']:
        flash('Sesi tidak valid atau sudah ditutup.', 'danger')
        return redirect(url_for('absensi_list'))
    
    hadir = db.execute('''
        SELECT a.nama, a.foto_profil, k.waktu_hadir 
        FROM absensi_kehadiran k
        JOIN anggota a ON k.id_anggota = a.id_anggota
        WHERE k.id_sesi=?
        ORDER BY k.waktu_hadir DESC
    ''', (id_sesi,)).fetchall()
    db.close()
    
    return render_template('absensi_proyektor.html', sesi=sesi, hadir=hadir)

@app.route('/absensi/api/token/<int:id_sesi>')
def absensi_api_token(id_sesi):
    # Dihit oleh AJAX di halaman proyektor tiap X detik (skrg bebas, karena token berubah per 30 menit)
    db = get_db()
    sesi = db.execute("SELECT * FROM absensi_sesi WHERE id_sesi=?", (id_sesi,)).fetchone()
    
    hadir_count = db.execute("SELECT COUNT(*) as c FROM absensi_kehadiran WHERE id_sesi=?", (id_sesi,)).fetchone()['c']
    
    # Ambil 5 terakhir yang absen
    recent = db.execute('''
        SELECT a.nama, k.waktu_hadir 
        FROM absensi_kehadiran k
        JOIN anggota a ON k.id_anggota = a.id_anggota
        WHERE k.id_sesi=?
        ORDER BY k.waktu_hadir DESC LIMIT 5
    ''', (id_sesi,)).fetchall()
    
    db.close()
    
    if not sesi or not sesi['status_buka']:
        return jsonify({'status': 'closed'})
        
    token = generate_absensi_token(id_sesi, sesi['kode_rahasia'])
    local_ip = get_local_ip()
    base_url = f"http://{local_ip}:5001"
    scan_url = base_url + url_for('absensi_scan', id_sesi=id_sesi, token=token)
    
    recent_list = [{'nama': r['nama'], 'waktu': r['waktu_hadir'].strftime('%H:%M:%S')} for r in recent]
    
    return jsonify({
        'status': 'open',
        'token': token,
        'scan_url': scan_url,
        'hadir_count': hadir_count,
        'recent': recent_list
    })

@app.route('/absensi/scan/<int:id_sesi>')
@login_required
def absensi_scan(id_sesi):
    token_input = request.args.get('token')
    id_anggota = session.get('id_anggota')
    
    db = get_db()
    sesi = db.execute("SELECT * FROM absensi_sesi WHERE id_sesi=?", (id_sesi,)).fetchone()
    
    if not sesi:
        db.close()
        return render_template('absensi_scan_result.html', status='error', message='Sesi tidak ditemukan.')
        
    if not sesi['status_buka']:
        db.close()
        return render_template('absensi_scan_result.html', status='error', message='Sesi absensi sudah ditutup.')

    # Cek token (Window sekarang atau window sebelumnya untuk toleransi pergantian 30 menit)
    token_now = generate_absensi_token(id_sesi, sesi['kode_rahasia'], 0)
    token_prev = generate_absensi_token(id_sesi, sesi['kode_rahasia'], -1)
    
    if token_input not in [token_now, token_prev]:
        db.close()
        return render_template('absensi_scan_result.html', status='error', message='QR Code sudah kadaluarsa (lebih dari 30 menit). Silakan scan ulang dari proyektor.')
        
    # Validasi Berhasil. Cek apakah sudah absen
    cek = db.execute("SELECT * FROM absensi_kehadiran WHERE id_sesi=? AND id_anggota=?", (id_sesi, id_anggota)).fetchone()
    if cek:
        db.close()
        return render_template('absensi_scan_result.html', status='warning', message='Anda sudah melakukan absensi pada sesi ini sebelumnya.')
        
    # Catat Kehadiran
    db.execute("INSERT INTO absensi_kehadiran (id_sesi, id_anggota) VALUES (?, ?)", (id_sesi, id_anggota))
    db.commit()
    db.close()
    
    return render_template('absensi_scan_result.html', status='success', message='Absensi Berhasil! Data kehadiran Anda telah tercatat.', nama_kegiatan=sesi['nama_kegiatan'])

@app.route('/absensi/detail/<int:id_sesi>')
@login_required
@role_required('HC', 'TIM IT', 'ADMIN', 'SEKRE')
def absensi_detail(id_sesi):
    db = get_db()
    sesi = db.execute("SELECT * FROM absensi_sesi WHERE id_sesi=?", (id_sesi,)).fetchone()
    hadir = db.execute('''
        SELECT a.nama, a.nim, a.divisi, k.waktu_hadir 
        FROM absensi_kehadiran k
        JOIN anggota a ON k.id_anggota = a.id_anggota
        WHERE k.id_sesi=?
        ORDER BY k.waktu_hadir ASC
    ''', (id_sesi,)).fetchall()
    db.close()
    return render_template('absensi_detail.html', sesi=sesi, hadir=hadir)


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)