#!/bin/bash
# SQL Workbench Launcher
# Double-click this file to launch the app

# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change to the app directory
cd "$DIR"

# Activate virtual environment and run
source venv/bin/activate
python main.py

# Keep terminal open if there's an error
if [ $? -ne 0 ]; then
    echo ""
    echo "Press any key to close..."
    read -n 1
fi
