web: gunicorn app.app:app --bind 0.0.0.0:$PORT --timeout 90 --workers 1 --threads 4
monitor: python monitor.py
