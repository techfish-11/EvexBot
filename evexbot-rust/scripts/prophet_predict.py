#!/usr/bin/env python3
"""
Prophet helper script for evexbot.

Reads JSON from stdin with the following format:
{
  "dates": ["YYYY-MM-DD", ...],
  "target": 123
}

Outputs JSON to stdout:
{
  "predicted_date": "YYYY-MM-DDT00:00:00Z" | null,
  "image_base64": "<base64-encoded PNG>" | null
}

Requirements: prophet (from prophet), pandas, matplotlib

This script avoids guessing by checking availability of packages and emitting nulls if not available.
"""
import sys
import json
import traceback
from datetime import datetime, timedelta

try:
    import pandas as pd
    from prophet import Prophet
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import io
    import base64
except Exception:
    # If dependencies are missing, we will fallback and output null prediction
    pd = None
    Prophet = None
    plt = None
    io = None
    base64 = None


def read_input():
    try:
        data = json.load(sys.stdin)
        dates = data.get("dates", [])
        target = int(data.get("target", 0))
        return dates, target
    except Exception:
        return [], 0


def output_result(predicted_date=None, image_bytes=None):
    out = {"predicted_date": None, "image_base64": None}
    if predicted_date is not None:
        # return RFC3339-ish timestamp in UTC at midnight
        out["predicted_date"] = predicted_date.replace(tzinfo=None).isoformat() + "Z"
    if image_bytes is not None:
        out["image_base64"] = base64.b64encode(image_bytes).decode("ascii")
    print(json.dumps(out))


def main():
    dates, target = read_input()
    if not dates or target <= 0:
        output_result(None, None)
        return

    if pd is None or Prophet is None or plt is None:
        # dependencies not available
        output_result(None, None)
        return

    try:
        ds = pd.to_datetime(dates)
        df = pd.DataFrame({"ds": ds, "y": range(1, len(ds) + 1)})
        m = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False)
        m.fit(df)

        # search up to 5 years ahead for date when y_pred >= target
        future_days = 365 * 5
        future = m.make_future_dataframe(periods=future_days)
        forecast = m.predict(future)
        # predicted cumulative counts: we find first date where forecast['yhat'] >= target
        first = None
        for row in forecast.itertuples():
            if row.yhat >= target:
                first = pd.to_datetime(row.ds)
                break

        img_bytes = None
        # generate plot showing historical and forecast if we have a date
        if first is not None:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(df['ds'], df['y'], label='actual')
            ax.plot(forecast['ds'], forecast['yhat'], label='forecast')
            ax.axvline(first, color='k', linestyle='--', label='predicted target')
            ax.legend()
            buf = io.BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight')
            plt.close(fig)
            img_bytes = buf.getvalue()

        output_result(first.to_pydatetime() if first is not None else None, img_bytes)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        output_result(None, None)


if __name__ == '__main__':
    main()
