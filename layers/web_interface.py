import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import requests
import pandas as pd

app = dash.Dash(__name__)

# Disable SSL verification warnings
requests.packages.urllib3.disable_warnings()

# URLs for API endpoints
METRICS_URL = "https://127.0.0.1:8001/metrics"
SERVICES_URL = "https://127.0.0.1:8001/services"

# Helper to fetch data
def fetch_data(url):
    try:
        response = requests.get(url, verify=False, timeout=5)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# Layout
app.layout = html.Div([
    html.H1("Monitoring Dashboard", style={"textAlign": "center"}),

    dcc.Tabs(id="tabs", value="metrics", children=[
        dcc.Tab(label="Metrics", value="metrics"),
        dcc.Tab(label="Health Summary", value="health"),
        dcc.Tab(label="Services Info", value="services"),
    ]),

    html.Div(id="tabs-content", style={"padding": 20})
])

@app.callback(Output("tabs-content", "children"), Input("tabs", "value"))
def render_tab_content(tab):
    if tab == "metrics":
        data = fetch_data(METRICS_URL)
        if "error" in data:
            return html.Div(f"Ошибка получения метрик: {data['error']}")

        graphs = []
        for service, metrics in data.items():
            if isinstance(metrics, dict):
                metric_cards = []
                for k, v in metrics.items():
                    if k == "timestamp":
                        continue
                    value = v.get("value") if isinstance(v, dict) else v
                    if value is None:
                        continue
                    fig = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=value,
                        title={'text': k},
                        gauge={'axis': {'range': [None, value * 1.5]}, 'bar': {'color': 'royalblue'}}
                    ))
                    fig.update_layout(height=250, margin=dict(t=40, b=0, l=0, r=0))
                    metric_cards.append(html.Div(dcc.Graph(figure=fig), style={"display": "inline-block", "width": "30%", "padding": "10px"}))

                graphs.append(html.Div([
                    html.H3(f"{service} Metrics"),
                    html.Div(metric_cards)
                ], style={"marginBottom": "30px"}))
        return html.Div(graphs)

    elif tab == "health":
        data = fetch_data(METRICS_URL)
        summary = data.get("health_summary", {})
        if not summary:
            return html.Div("Нет данных health_summary.")

        df = pd.DataFrame([
            {"Service": s, **v} for s, v in summary.items()
        ])
        table = dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in df.columns],
            data=df.to_dict("records"),
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "left", "padding": "5px"}
        )
        return html.Div([html.H3("Health Summary"), table])

    elif tab == "services":
        data = fetch_data(SERVICES_URL)
        if "error" in data:
            return html.Div(f"Ошибка получения сервисов: {data['error']}")

        cards = []
        for name, svc in data.items():
            info = svc.get("info", {})
            cards.append(html.Div([
                html.H4(info.get("name", name)),
                html.P(f"Версия: {info.get('version', 'N/A')}"),
                html.P(f"Описание: {info.get('description', 'Нет описания')}"),
                html.P(f"Статус: {svc.get('status', 'unknown')}")
            ], style={"border": "1px solid #ccc", "padding": 10, "marginBottom": 10, "borderRadius": 5}))

        return html.Div(cards)

    else:
        return html.Div("Выберите вкладку.")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8701)
