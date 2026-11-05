"""Launcher for Windows: use the installed dependencies if present, otherwise set up .venv in this folder."""
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def main():
    if sys.version_info<(3,10): raise SystemExit('Gunakan Python 3.10 atau lebih baru.')
    python=Path(sys.executable)
    check='import numpy, sklearn; import paho.mqtt.client'
    if subprocess.run([str(python),'-c',check],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        target=ROOT/'.venv'
        python=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.exists():
            print('Menyiapkan environment Python (cukup sekali)...',flush=True)
            subprocess.run([sys.executable,'-m','venv',str(target)],check=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
    return subprocess.call([str(python),str(ROOT/'app.py'),'--open',*sys.argv[1:]],cwd=ROOT)

if __name__=='__main__':
    try:raise SystemExit(main())
    except subprocess.CalledProcessError:
        raise SystemExit('Penyiapan dependensi gagal. Periksa koneksi internet dan jalankan peluncur lagi.')
