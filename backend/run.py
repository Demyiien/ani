import os
import uvicorn
from ani import config
if __name__ == '__main__':
    # One worker owns the durable queue; reload=True picks up backend edits automatically in local dev.
    reload_enabled = os.getenv('ANI_RELOAD', '1') not in ('0', 'false', 'False')
    uvicorn.run('ani.main:app', host='127.0.0.1', port=int(os.getenv('ANI_PORT', '8000')), workers=1, reload=reload_enabled)
