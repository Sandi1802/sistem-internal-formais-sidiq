-- ═══════════════════════════════════════════════════════════════
--  SIDIQ FORMAIS - MySQL Database Schema
-- ═══════════════════════════════════════════════════════════════
CREATE DATABASE IF NOT EXISTS sidiq18;;
USE sidiq18;

-- 1. Table: anggota (sebelumnya karyawan)
CREATE TABLE IF NOT EXISTS anggota (
    id_anggota INT AUTO_INCREMENT PRIMARY KEY,
    nim VARCHAR(50) UNIQUE NOT NULL,
    nama VARCHAR(150) NOT NULL,
    jabatan VARCHAR(100) NOT NULL,
    divisi VARCHAR(100) NOT NULL,
    angkatan VARCHAR(20) NULL,
    no_wa VARCHAR(20) NULL,
    foto_profil VARCHAR(255) NULL,
    status_keanggotaan VARCHAR(50) DEFAULT 'Aktif',
    tipe_anggota VARCHAR(50) DEFAULT 'Anggota',
    id_atasan INT NULL,
    FOREIGN KEY (id_atasan) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. Table: user
CREATE TABLE IF NOT EXISTS user (
    id_user INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,
    id_anggota INT NULL,
    FOREIGN KEY (id_anggota) REFERENCES anggota(id_anggota) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. Table: kuesioner
CREATE TABLE IF NOT EXISTS kuesioner (
    id_kuesioner INT AUTO_INCREMENT PRIMARY KEY,
    nama_kuesioner VARCHAR(255) NOT NULL,
    periode VARCHAR(50) NOT NULL,
    tanggal_mulai DATE,
    tanggal_selesai DATE,
    status VARCHAR(20) DEFAULT 'draft'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. Table: pertanyaan
CREATE TABLE IF NOT EXISTS pertanyaan (
    id_pertanyaan INT AUTO_INCREMENT PRIMARY KEY,
    id_kuesioner INT NOT NULL,
    dimensi_akhlak VARCHAR(100) NOT NULL,
    urutan INT NOT NULL,
    teks_pertanyaan TEXT NOT NULL,
    FOREIGN KEY (id_kuesioner) REFERENCES kuesioner(id_kuesioner) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. Table: penilai
CREATE TABLE IF NOT EXISTS penilai (
    id_penilai INT AUTO_INCREMENT PRIMARY KEY,
    id_kuesioner INT NOT NULL,
    id_anggota_dinilai INT NOT NULL,
    id_anggota_penilai INT NOT NULL,
    kategori VARCHAR(50) NOT NULL,
    status_pengisian VARCHAR(20) DEFAULT 'belum',
    tanggal_pengisian DATETIME NULL,
    FOREIGN KEY (id_kuesioner) REFERENCES kuesioner(id_kuesioner) ON DELETE CASCADE,
    FOREIGN KEY (id_anggota_dinilai) REFERENCES anggota(id_anggota) ON DELETE CASCADE,
    FOREIGN KEY (id_anggota_penilai) REFERENCES anggota(id_anggota) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. Table: jawaban
CREATE TABLE IF NOT EXISTS jawaban (
    id_jawaban INT AUTO_INCREMENT PRIMARY KEY,
    id_penilai INT NOT NULL,
    id_pertanyaan INT NOT NULL,
    skor INT NOT NULL,
    is_draft INT DEFAULT 1,
    FOREIGN KEY (id_penilai) REFERENCES penilai(id_penilai) ON DELETE CASCADE,
    FOREIGN KEY (id_pertanyaan) REFERENCES pertanyaan(id_pertanyaan) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7. Table: hasil_penilaian
CREATE TABLE IF NOT EXISTS hasil_penilaian (
    id_hasil INT AUTO_INCREMENT PRIMARY KEY,
    id_kuesioner INT NOT NULL,
    id_anggota INT NOT NULL,
    dimensi VARCHAR(50) NOT NULL,
    skor_atasan FLOAT DEFAULT 0,
    skor_rekan FLOAT DEFAULT 0,
    skor_bawahan FLOAT DEFAULT 0,
    skor_diri FLOAT DEFAULT 0,
    skor_akhir FLOAT DEFAULT 0,
    FOREIGN KEY (id_kuesioner) REFERENCES kuesioner(id_kuesioner) ON DELETE CASCADE,
    FOREIGN KEY (id_anggota) REFERENCES anggota(id_anggota) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8. Table: persuratan
CREATE TABLE IF NOT EXISTS persuratan (
    id_surat INT AUTO_INCREMENT PRIMARY KEY,
    nomor_surat VARCHAR(100) NULL,
    jenis_surat VARCHAR(50) NOT NULL DEFAULT 'Surat Keluar',
    perihal VARCHAR(255) NOT NULL,
    tujuan VARCHAR(255) NULL,
    tanggal_surat DATE NOT NULL,
    tanggal_diterima DATE NULL,
    isi_surat TEXT NULL,
    file_surat VARCHAR(255) NULL,
    status VARCHAR(50) DEFAULT 'Draft',
    dibuat_oleh INT NULL,
    disetujui_oleh INT NULL,
    tanggal_disetujui DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dibuat_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL,
    FOREIGN KEY (disetujui_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 9. Table: proker (Program Kerja)
CREATE TABLE IF NOT EXISTS proker (
    id_proker INT AUTO_INCREMENT PRIMARY KEY,
    nama_proker VARCHAR(255) NOT NULL,
    tipe_proker VARCHAR(50) DEFAULT 'Kecil',
    deskripsi TEXT NULL,
    divisi VARCHAR(100) NULL,
    penanggung_jawab INT NULL,
    target VARCHAR(255) NULL,
    tanggal_mulai DATE NULL,
    tanggal_selesai DATE NULL,
    anggaran DECIMAL(15,2) DEFAULT 0,
    status VARCHAR(50) DEFAULT 'Direncanakan',
    progress INT DEFAULT 0,
    catatan TEXT NULL,
    file_hasil VARCHAR(255) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (penanggung_jawab) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 10. Table: kas (Keuangan - Arus Kas)
CREATE TABLE IF NOT EXISTS kas (
    id_kas INT AUTO_INCREMENT PRIMARY KEY,
    jenis VARCHAR(20) NOT NULL DEFAULT 'Pemasukan',
    kategori VARCHAR(100) NULL,
    keterangan VARCHAR(255) NOT NULL,
    jumlah DECIMAL(15,2) NOT NULL DEFAULT 0,
    tanggal DATE NOT NULL,
    bukti_file VARCHAR(255) NULL,
    dicatat_oleh INT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dicatat_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 11. Table: kas_entrepreneur
CREATE TABLE IF NOT EXISTS kas_entrepreneur (
    id_kas_ent INT AUTO_INCREMENT PRIMARY KEY,
    jenis VARCHAR(20) NOT NULL DEFAULT 'Pemasukan',
    kategori VARCHAR(100) NULL,
    keterangan VARCHAR(255) NOT NULL,
    jumlah DECIMAL(15,2) NOT NULL DEFAULT 0,
    tanggal DATE NOT NULL,
    bukti_file VARCHAR(255) NULL,
    dicatat_oleh INT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dicatat_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 12. Table: desain (Request Desain)
CREATE TABLE IF NOT EXISTS desain (
    id_desain INT AUTO_INCREMENT PRIMARY KEY,
    judul VARCHAR(255) NOT NULL,
    deskripsi TEXT NULL,
    jenis_desain VARCHAR(100) NULL,
    ukuran VARCHAR(100) NULL,
    deadline DATE NULL,
    file_referensi VARCHAR(255) NULL,
    file_hasil VARCHAR(255) NULL,
    status VARCHAR(50) DEFAULT 'Pending',
    diminta_oleh INT NULL,
    dikerjakan_oleh INT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (diminta_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL,
    FOREIGN KEY (dikerjakan_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 13. Table: barang (Inventaris)
CREATE TABLE IF NOT EXISTS barang (
    id_barang INT AUTO_INCREMENT PRIMARY KEY,
    nama_barang VARCHAR(255) NOT NULL,
    kategori VARCHAR(100) NULL,
    jumlah_total INT DEFAULT 1,
    jumlah_tersedia INT DEFAULT 1,
    kondisi VARCHAR(50) DEFAULT 'Baik',
    lokasi VARCHAR(255) NULL,
    foto VARCHAR(255) NULL,
    keterangan TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 14. Table: peminjaman (Peminjaman Barang)
CREATE TABLE IF NOT EXISTS peminjaman (
    id_peminjaman INT AUTO_INCREMENT PRIMARY KEY,
    id_barang INT NOT NULL,
    id_peminjam INT NOT NULL,
    jumlah_pinjam INT DEFAULT 1,
    tanggal_pinjam DATE NOT NULL,
    tanggal_kembali_rencana DATE NULL,
    tanggal_kembali_aktual DATE NULL,
    status VARCHAR(50) DEFAULT 'Dipinjam',
    keterangan TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_barang) REFERENCES barang(id_barang) ON DELETE CASCADE,
    FOREIGN KEY (id_peminjam) REFERENCES anggota(id_anggota) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 15. Table: arsip (Repository/Arsip Dokumen)
CREATE TABLE IF NOT EXISTS arsip (
    id_arsip INT AUTO_INCREMENT PRIMARY KEY,
    judul VARCHAR(255) NOT NULL,
    kategori VARCHAR(100) NULL,
    deskripsi TEXT NULL,
    file_path VARCHAR(255) NULL,
    tipe_file VARCHAR(50) NULL,
    diunggah_oleh INT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (diunggah_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 16. Table: kalender_kegiatan
CREATE TABLE IF NOT EXISTS kalender_kegiatan (
    id_kegiatan INT AUTO_INCREMENT PRIMARY KEY,
    judul VARCHAR(255) NOT NULL,
    deskripsi TEXT NULL,
    tanggal_mulai DATETIME NOT NULL,
    tanggal_selesai DATETIME NULL,
    lokasi VARCHAR(255) NULL,
    divisi VARCHAR(100) NULL,
    warna VARCHAR(20) DEFAULT '#1D9E75',
    dibuat_oleh INT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dibuat_oleh) REFERENCES anggota(id_anggota) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════
-- Seed Data Awal
-- ═══════════════════════════════════════════════════════════════

-- Insert Admin
INSERT INTO user (username, password_hash, role, id_anggota) 
VALUES ('admin', MD5('admin'), 'admin', NULL)
ON DUPLICATE KEY UPDATE id_user=id_user;
