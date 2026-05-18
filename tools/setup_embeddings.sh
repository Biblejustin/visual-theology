#!/bin/bash
# Bootstrap embedding model + dependencies on the external drive.
#
# Pins HuggingFace + torch caches and Python deps to
# /Volumes/External/Logos4/Data/ so we don't pollute the system disk.
# Run once after cloning the repo and before CoherenceChecker is first used.

set -euo pipefail

EXTERNAL_DATA=/Volumes/External/Logos4/Data
DEPS_DIR="$EXTERNAL_DATA/python-deps"
HF_HOME="$EXTERNAL_DATA/huggingface"
ST_HOME="$EXTERNAL_DATA/huggingface/sentence-transformers"
TORCH_HOME="$EXTERNAL_DATA/torch"

mkdir -p "$DEPS_DIR" "$HF_HOME" "$ST_HOME" "$TORCH_HOME"

export HF_HOME ST_HOME TORCH_HOME
export SENTENCE_TRANSFORMERS_HOME="$ST_HOME"

echo "Installing sentence-transformers, torch, numpy to $DEPS_DIR"
python3 -m pip install --upgrade --target "$DEPS_DIR" \
  sentence-transformers torch numpy

echo "Downloading BAAI/bge-large-en-v1.5 to $ST_HOME"
PYTHONPATH="$DEPS_DIR" python3 -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('BAAI/bge-large-en-v1.5')
print(f'Model loaded. dim={m.get_sentence_embedding_dimension()}')
"

echo
echo "Done. Disk used on $EXTERNAL_DATA:"
du -sh "$DEPS_DIR" "$HF_HOME" "$TORCH_HOME"
