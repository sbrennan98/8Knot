from dash import html, dcc, callback
import dash
import dash_bootstrap_components as dbc
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
import pandas as pd
import logging
from dateutil.relativedelta import *  # type: ignore
import plotly.express as px
from pages.utils.graph_utils import get_graph_time_values, color_seq
from queries.contributors_query import contributors_query as ctq
import io
from cache_manager.cache_manager import CacheManager as cm
from pages.utils.job_utils import nodata_graph

import time

gc_active_drifting_contributors = dbc.Card(
    [
        dbc.CardBody(
            [
                html.H3(
                    "Contributor Growth by Engagement",
                    className="card-title",
                    style={"text-align": "center"},
                ),
                dbc.Popover(
                    [
                        dbc.PopoverHeader("Graph Info:"),
                        dbc.PopoverBody(
                            "Select the intervals that make sense for your community!\n\
                            <ACTIVE> contributors that have a contribution within the drifting parameter.\n\
                            <DRIFTING> contributors that are views as drifting away from the community, they have a contribution between the two intervals.\n\
                            <AWAY> Contributors that are considered no longer participating members of the community"
                        ),
                    ],
                    id="overview-popover-4",
                    target="overview-popover-target-4",  # needs to be the same as dbc.Button id
                    placement="top",
                    is_open=False,
                ),
                dcc.Loading(
                    dcc.Graph(id="active_drifting_contributors"),
                ),
                dbc.Form(
                    [
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Months Until Drifting:",
                                    html_for="drifting_months",
                                    width={"size": "auto"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="drifting_months", type="number", min=1, max=120, step=1, value=6, size="sm"
                                    ),
                                    className="me-2",
                                    width=1,
                                ),
                                dbc.Label(
                                    "Months Until Away:",
                                    html_for="away_months",
                                    width={"size": "auto"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="away_months", type="number", min=1, max=120, step=1, value=12, size="sm"
                                    ),
                                    className="me-2",
                                    width=1,
                                ),
                                dbc.Alert(
                                    children="Please ensure that 'Months Until Drifting' is less than 'Months Until Away'",
                                    id="drifting_away_check_alert",
                                    dismissable=True,
                                    fade=False,
                                    is_open=False,
                                    color="warning",
                                ),
                            ],
                            align="center",
                        ),
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Date Interval:",
                                    html_for="active-drifting-interval",
                                    width="auto",
                                ),
                                dbc.Col(
                                    [
                                        dbc.RadioItems(
                                            id="active-drifting-interval",
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
                                        id="overview-popover-target-4",
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

# call backs for card graph 4 - Active Drifting Away Over Time
@callback(
    Output("overview-popover-4", "is_open"),
    [Input("overview-popover-target-4", "n_clicks")],
    [State("overview-popover-4", "is_open")],
)
def toggle_popover_4(n, is_open):
    if n:
        return not is_open
    return is_open


@callback(
    Output("active_drifting_contributors", "figure"),
    Output("drifting_away_check_alert", "is_open"),
    [
        Input("repo-choices", "data"),
        Input("active-drifting-interval", "value"),
        Input("drifting_months", "value"),
        Input("away_months", "value"),
    ],
    background=True,
)
def active_drifting_contributors_graph(repolist, interval, drift_interval, away_interval):

    if drift_interval is None or away_interval is None:
        return dash.no_update, dash.no_update

    if drift_interval > away_interval:
        return dash.no_update, True

    # wait for data to asynchronously download and become available.
    cache = cm()
    df = cache.grabm(func=ctq, repos=repolist)
    while df is None:
        time.sleep(1.0)
        df = cache.grabm(func=ctq, repos=repolist)

    logging.debug(f"ACTIVE_DRIFTING_CONTRIBUTOR_GROWTH_VIZ - START")
    start = time.perf_counter()

    # test if there is data
    if df.empty:
        logging.debug("PULL REQUEST STALENESS - NO DATA AVAILABLE")
        return nodata_graph, False

    # function for all data pre processing
    df_status = process_data(df, interval, drift_interval, away_interval)

    fig = create_figure(df_status, interval)

    logging.debug(f"ACTIVE_DRIFTING_CONTRIBUTOR_GROWTH_VIZ - END - {time.perf_counter() - start}")
    return fig, False


def process_data(df: pd.DataFrame, interval, drift_interval, away_interval):

    # convert to datetime objects with consistent column name
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df.rename(columns={"created_at": "created"}, inplace=True)

    # order from beginning of time to most recent
    df = df.sort_values("created", axis=0, ascending=True)

    # first and last elements of the dataframe are the
    # earliest and latest events respectively
    earliest, latest = df["created"].min(), df["created"].max()

    # beginning to the end of time by the specified interval
    dates = pd.date_range(start=earliest, end=latest, freq=interval, inclusive="both")

    # df for active, driving, and away contributors for time interval
    df_status = dates.to_frame(index=False, name="Date")

    df_status["Active"], df_status["Drifting"], df_status["Away"] = zip(
        *df_status.apply(
            lambda row: get_active_drifting_away_up_to(df, row.Date, drift_interval, away_interval),
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
                    name="Active",
                    x=df_status["Date"],
                    y=df_status["Active"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="Contributors Active: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                    marker=dict(color=color_seq[0]),
                ),
                go.Scatter(
                    name="Drifting",
                    x=df_status["Date"],
                    y=df_status["Drifting"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="Contributors Drifting: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                    marker=dict(color=color_seq[3]),
                ),
                go.Scatter(
                    name="Away",
                    x=df_status["Date"],
                    y=df_status["Away"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="Contributors Away: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                    marker=dict(color=color_seq[5]),
                ),
            ]
        )
    else:
        fig = px.bar(df_status, x="Date", y=["Active", "Drifting", "Away"], color_discrete_sequence=color_seq)

        # edit hover values
        fig.update_traces(hovertemplate=hover + "<br>Contributors: %{y}<br>" + "<extra></extra>")

    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Number of Contributors",
        legend_title="Type",
        font=dict(size=14),
    )

    return fig


def get_active_drifting_away_up_to(df, date, drift_interval, away_interval):

    # drop rows that are more recent than the date limit
    df_lim = df[df["created"] <= date]

    # keep more recent contribution per ID
    df_lim = df_lim.drop_duplicates(subset="cntrb_id", keep="last")

    # time difference, drifting_months before the threshold date
    drift_mos = date - relativedelta(months=+drift_interval)

    # time difference, away_months before the threshold date
    away_mos = date - relativedelta(months=+away_interval)

    # number of total contributors up until date
    numTotal = df_lim.shape[0]

    # number of 'active' contributors, people with contributions before the drift time
    numActive = df_lim[df_lim["created"] >= drift_mos].shape[0]

    # set of contributions that are before the away time
    drifting = df_lim[df_lim["created"] > away_mos]

    # number of the set of contributions that are after the drift time, but before away
    numDrifting = drifting[drifting["created"] < drift_mos].shape[0]

    # difference of the total to get the away value
    numAway = numTotal - (numActive + numDrifting)

    return [numActive, numDrifting, numAway]
