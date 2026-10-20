#!/bin/bash
set -e
SC="python3 /ffxi/Server/server_control.py"
cd /ffxi/Server

# Optional settings from the environment (same as: server_control.py name / welcome / address)
[ -n "$SERVER_NAME" ]    && { $SC name "$SERVER_NAME"       || echo "warning: could not set name"; }
[ -n "$SERVER_WELCOME" ] && { $SC welcome "$SERVER_WELCOME" || echo "warning: could not set welcome"; }
[ -n "$SERVER_ADDRESS" ] && { $SC address "$SERVER_ADDRESS" || echo "warning: could not set address"; }

stop() { echo "Stopping server..."; $SC stop || true; exit 0; }
trap stop TERM INT

# Web UI first, so it is reachable while the (slow) first start runs
python3 /webui.py &

# "start" returns once everything is up, so the container has to stay alive by itself
$SC start
echo "Server started."

while true; do sleep 3600 & wait $!; done
