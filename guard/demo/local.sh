#!/usr/bin/env bash
set -euo pipefail

# Run the whole Hyperion Guard demo on a local Anvil chain: deploy, set up,
# and the six scenes. Needs Foundry and the service's Python deps
# (python3 -m venv guard/service/.venv && guard/service/.venv/bin/pip install -e guard/service).
#
# The keys below are Anvil's well-known development accounts. They're public
# and only ever used against this throwaway local chain.

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${ANVIL_PORT:-8546}"
RPC="http://127.0.0.1:${PORT}"
PY="${PYTHON:-$ROOT/guard/service/.venv/bin/python}"

OWNER_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
SIGNER_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
AGENT_KEY=0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a
GUARDIAN_KEY=0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6

anvil --port "$PORT" --silent &
ANVIL=$!
trap 'kill $ANVIL 2>/dev/null' EXIT
sleep 2

cd "$ROOT/guard/contracts"
OWNER=$(cast wallet address "$OWNER_KEY")
GUARD=$(cast compute-address "$OWNER" --nonce 0 | awk '{print $NF}')
GUARD_SIGNER=$(cast wallet address "$SIGNER_KEY") \
  forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC" --private-key "$OWNER_KEY" --broadcast >/dev/null
EXECUTOR=$(cast compute-address "$OWNER" --nonce 2 | awk '{print $NF}')   # nonce 1 is registerAgent
VENUE=$(cast compute-address "$OWNER" --nonce 3 | awk '{print $NF}')
GUARD="$GUARD" AGENT=$(cast wallet address "$AGENT_KEY") GUARDIAN=$(cast wallet address "$GUARDIAN_KEY") \
  forge script script/Deploy.s.sol:DemoSetup --rpc-url "$RPC" --private-key "$OWNER_KEY" --broadcast >/dev/null

cd "$ROOT"
GUARD_RPC_URL="$RPC" GUARD_CHAIN_ID=31337 GUARD_CONTRACT="$GUARD" GUARD_SIGNER_KEY="$SIGNER_KEY" \
AGENT_KEY="$AGENT_KEY" GUARDIAN_KEY="$GUARDIAN_KEY" EXECUTOR="$EXECUTOR" VENUE="$VENUE" \
GUARD_FIXED_PRICES='{"BTC-USD": "65000"}' EXPLORER="local" \
  "$PY" guard/demo/demo.py
