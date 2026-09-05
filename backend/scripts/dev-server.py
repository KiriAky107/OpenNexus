"""Development reload watches application code, never imported extension packages."""
from pathlib import Path
import uvicorn

if __name__ == '__main__':
    backend = Path(__file__).resolve().parents[1]
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000, app_dir=str(backend),
                reload=True, reload_dirs=[str(backend / 'app')])
