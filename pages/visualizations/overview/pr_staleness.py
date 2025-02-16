from dash import html, dcc
import dash
import dash_bootstrap_components as dbc
from dash import callback
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
import pandas as pd
import logging
from dateutil.relativedelta import *  # type: ignore
import plotly.express as px
from pages.utils.graph_utils import get_graph_time_values, color_seq
from pages.utils.job_utils import nodata_graph
from queries.prs_query import prs_query as prq
import time
import io
from cache_manager.cache_manager import CacheManager as cm

gc_pr_staleness = dbc.Card(
    [
        dbc.CardBody(
            [
                html.H3(
                    "Pull Request Activity- Staleness",
                    className="card-title",
                    style={"text-align": "center"},
                ),
                dbc.Popover(
                    [
                        dbc.PopoverHeader("Graph Info:"),
                        dbc.PopoverBody(
                            "This visualization shows how many PRs have been open different buckets of time.\n\
                            It can tell you if there are PRS that are staying idly open."
                        ),
                    ],
                    id="overview-popover-prs",
                    target="overview-popover-target-prs",  # needs to be the same as dbc.Button id
                    placement="top",
                    is_open=False,
                ),
                dcc.Loading(
                    dcc.Graph(id="pr_staleness"),
                ),
                dbc.Form(
                    [
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Days Until Staling:",
                                    html_for="staling_days",
                                    width={"size": "auto"},
                                    # style={"font-weight": "bold"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="staling_days",
                                        type="number",
                                        min=1,
                                        max=120,
                                        step=1,
                                        value=7,
                                        size="sm",
                                    ),
                                    className="me-2",
                                    width=1,
                                ),
                                dbc.Label(
                                    "Days Until Stale:",
                                    html_for="stale_days",
                                    width={"size": "auto"},
                                    # style={"font-weight": "bold"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="stale_days",
                                        type="number",
                                        min=1,
                                        max=120,
                                        step=1,
                                        value=30,
                                        size="sm",
                                    ),
                                    className="me-2",
                                    width=1,
                                ),
                            ],
                            align="center",
                        ),
                        dbc.Alert(
                            children="Please ensure that 'Days Until Staling' is less than 'Days Until Stale'",
                            id="pr_staling_stale_check_alert",
                            dismissable=True,
                            fade=False,
                            is_open=False,
                            color="warning",
                        ),
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Date Interval:",
                                    html_for="pr-staleness-interval",
                                    width="auto",
                                    # style={"font-weight": "bold"},
                                ),
                                dbc.Col(
                                    [
                                        dbc.RadioItems(
                                            id="pr-staleness-interval",
                                            options=[
                                                {"label": "Trend", "value": "D"},
                                                {"label": "Month", "value": "M"},
                                                {"label": "Year", "value": "Y"},
                                            ],
                                            value="M",
                                            inline=True,
                                        ),
                                    ]
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "About Graph",
                                        id="overview-popover-target-prs",
                                        color="secondary",
                                        size="sm",
                                    ),
                                    width="auto",
                                    style={"padding-top": ".5em"},
                                ),
                            ],
                            align="center",
                        ),
                    ]
                ),
            ]
        )
    ],
    # color="light",
)


@callback(
    Output("overview-popover-prs", "is_open"),
    [Input("overview-popover-target-prs", "n_clicks")],
    [State("overview-popover-prs", "is_open")],
)
def toggle_popover_prs(n, is_open):
    if n:
        return not is_open
    return is_open


@callback(
    Output("pr_staleness", "figure"),
    Output("pr_staling_stale_check_alert", "is_open"),
    [
        Input("repo-choices", "data"),
        Input("pr-staleness-interval", "value"),
        Input("staling_days", "value"),
        Input("stale_days", "value"),
    ],
    background=True,
)
def new_staling_prs_graph(repolist, interval, staling_interval, stale_interval):

    if staling_interval > stale_interval:
        return dash.no_update, True

    if staling_interval is None or stale_interval is None:
        return dash.no_update, dash.no_update

    # wait for data to asynchronously download and become available.
    cache = cm()
    df = cache.grabm(func=prq, repos=repolist)
    while df is None:
        time.sleep(1.0)
        df = cache.grabm(func=prq, repos=repolist)

    start = time.perf_counter()
    logging.debug("PULL REQUEST STALENESS - START")

    # test if there is data
    if df.empty:
        logging.debug("PULL REQUEST STALENESS  - NO DATA AVAILABLE")
        return nodata_graph, False

    # function for all data pre processing
    df_status = process_data(df, interval, staling_interval, stale_interval)

    fig = create_figure(df_status, interval)

    logging.debug(f"PULL REQUEST STALENESS - END - {time.perf_counter() - start}")
    return fig, False


def process_data(df: pd.DataFrame, interval, staling_interval, stale_interval):

    # convert to datetime objects rather than strings
    df["created"] = pd.to_datetime(df["created"], utc=True)
    df["merged"] = pd.to_datetime(df["merged"], utc=True)
    df["closed"] = pd.to_datetime(df["closed"], utc=True)

    # order values chronologically by creation date
    df = df.sort_values(by="created", axis=0, ascending=True)

    # first and last elements of the dataframe are the
    # earliest and latest events respectively
    earliest = df["created"].min()
    latest = max(df["created"].max(), df["closed"].max())

    # generating buckets beginning to the end of time by the specified interval
    dates = pd.date_range(start=earliest, end=latest, freq=interval, inclusive="both")

    # df for new, staling, and stale prs for time interval
    df_status = dates.to_frame(index=False, name="Date")

    df_status["New"], df_status["Staling"], df_status["Stale"] = zip(
        *df_status.apply(
            lambda row: get_new_staling_stale_up_to(df, row.Date, staling_interval, stale_interval),
            axis=1,
        )
    )

    if interval == "M":
        df_status["Date"] = df_status["Date"].dt.strftime("%Y-%m")
    elif interval == "Y":
        df_status["Date"] = df_status["Date"].dt.year

    return df_status


def create_figure(df_status: pd.DataFrame, interval):

    # time values for graph
    x_r, x_name, hover, period = get_graph_time_values(interval)

    # making a line graph if the bin-size is small enough.
    if interval == "D":
        fig = go.Figure(
            [
                go.Scatter(
                    name="New",
                    x=df_status["Date"],
                    y=df_status["New"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="PRs New: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                    marker=dict(color=color_seq[0]),
                ),
                go.Scatter(
                    name="Staling",
                    x=df_status["Date"],
                    y=df_status["Staling"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="PRs Staling: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                    marker=dict(color=color_seq[3]),
                ),
                go.Scatter(
                    name="Stale",
                    x=df_status["Date"],
                    y=df_status["Stale"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="PRs Stale: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                    marker=dict(color=color_seq[5]),
                ),
            ]
        )
    else:
        fig = px.bar(df_status, x="Date", y=["New", "Staling", "Stale"], color_discrete_sequence=color_seq)

        # edit hover values
        fig.update_traces(hovertemplate=hover + "<br>PRs: %{y}<br>" + "<extra></extra>")

    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Pull Requests",
        legend_title="Type",
        font=dict(size=14),
    )

    return fig


def get_new_staling_stale_up_to(df, date, staling_interval, stale_interval):

    # drop rows that are more recent than the date limit
    df_created = df[df["created"] <= date]

    # drop rows that have been closed before date
    df_in_range = df_created[df_created["closed"] > date]

    # include rows that have a null closed value
    df_in_range = pd.concat([df_in_range, df_created[df_created.closed.isnull()]])

    # time difference for the amount of days before the threshold date
    staling_days = date - relativedelta(days=+staling_interval)

    # time difference for the amount of days before the threshold date
    stale_days = date - relativedelta(days=+stale_interval)

    # PRs still open at the specified date
    numTotal = df_in_range.shape[0]

    # num of currently open PRs that have been create in the last staling_value amount of days
    numNew = df_in_range[df_in_range["created"] >= staling_days].shape[0]

    staling = df_in_range[df_in_range["created"] > stale_days]
    numStaling = staling[staling["created"] < staling_days].shape[0]

    numStale = numTotal - (numNew + numStaling)

    return [numNew, numStaling, numStale]
