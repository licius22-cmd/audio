#!/bin/bash
echo "Instalando dependências (se necessário)..."
python3 -m flask run --host=0.0.0.0 --port=5001

echo "Iniciando servidor Audio Criativo..."
echo "Acesse http://0.0.0.0:5001 no seu navegador"
python3 app.py
