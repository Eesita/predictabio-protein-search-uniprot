#!/bin/bash
# Monitor container memory usage

CONTAINER_NAME="${1:-uniprot-app}"

echo "Monitoring memory for container: $CONTAINER_NAME"
echo "Press Ctrl+C to stop"
echo ""

while true; do
    echo "=== $(date) ==="
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}" $CONTAINER_NAME
    echo ""
    sleep 5
done

