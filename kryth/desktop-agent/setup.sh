#!/bin/bash
# Desktop Agent Setup Script
# This script sets up the development environment and installs dependencies.

set -e  # Exit on error

echo "=== Desktop Agent Setup ==="
echo ""

# Check Python version
python3 --version || { echo "Python 3 is required but not installed."; exit 1; }

# Create virtual environment
echo "[1/5] Creating virtual environment..."
if [ -d "venv" ]; then
    echo "Virtual environment already exists. Skipping..."
else
    python3 -m venv venv
fi

# Activate virtual environment
echo "[2/5] Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "[3/5] Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "[4/5] Installing dependencies..."
pip install -r requirements.txt

# Install package in editable mode
echo "[5/5] Installing desktop-agent package..."
pip install -e .

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "To activate the virtual environment, run:"
echo "  source venv/bin/activate"
echo ""
echo "To run the Phase 1 demo:"
echo "  python main.py"
echo ""
echo "To run tests:"
echo "  pytest tests/ -v"
echo ""
echo "To run with coverage:"
echo "  pytest tests/ -v --cov=desktop_agent"