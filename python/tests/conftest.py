import sys
from pathlib import Path

# The Python code lives in python/ as scripts and one package, not an
# installed distribution; put that directory on the path the way the scripts do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
