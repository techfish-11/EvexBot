Prophet helper script

This directory contains a small helper script `prophet_predict.py` which reads JSON from stdin and writes JSON to stdout.

Input format:
{"dates": ["YYYY-MM-DD", ...], "target": 123}

Output format:
{"predicted_date": "YYYY-MM-DDTHH:MM:SSZ" | null, "image_base64": "base64png" | null}

Dependencies:
- prophet (pip install prophet)
- pandas
- matplotlib

The script is defensive: if dependencies are missing it will output nulls instead of failing the calling process.