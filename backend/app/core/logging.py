import logging, json
from datetime import datetime, UTC
class JsonFormatter(logging.Formatter):
    def format(self, record):
        data={"timestamp":datetime.now(UTC).isoformat(),"level":record.levelname,"message":record.getMessage()}
        if hasattr(record,"extra_data"): data.update(record.extra_data)
        return json.dumps(data, ensure_ascii=False)
def setup_logging():
    h=logging.StreamHandler(); h.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h], force=True)
