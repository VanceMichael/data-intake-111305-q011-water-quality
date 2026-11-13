import json
import sys
from .sample import inspect_record

def main():
    sample = {"source": "station-a", "key": "demo-01", "revision": 1, "value": "synthetic"}
    print(json.dumps(inspect_record(sample), ensure_ascii=False, sort_keys=True))

if __name__ == "__main__":
    main()
