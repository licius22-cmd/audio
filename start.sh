#!/bin/bash
echo "Instalando dependências (se necessário)..."
python3 -m pip install -r requirements.txt

echo "Iniciando servidor Audio Criativo..."
echo "Acesse http://127.0.0.1:5001 no seu navegador"
python3 app.py
